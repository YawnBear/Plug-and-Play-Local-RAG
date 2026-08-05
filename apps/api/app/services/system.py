from __future__ import annotations

import asyncio
import ctypes
import hashlib
import io
import json
import os
import re
import shutil
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.unit_of_work import AuthenticatedUnitOfWork
from app.runtime.controller_client import ControllerUnavailable
from app.schemas.system import (
    GenerationActionResponse,
    IngestionProfileSelection,
    IngestionProfileSelectionResponse,
    PersonalBackupHistoryResponse,
    PersonalBackupOperation,
    PersonalBackupResponse,
    PersonalBackupStatusResponse,
    ReprocessingOperation,
    ReprocessingOperationResponse,
    ReprocessingOperationsResponse,
    ReprocessingPreviewRequest,
    ReprocessingPreviewResponse,
    RuntimeConfigurationApplyResponse,
    RuntimeConfigurationChange,
    RuntimeConfigurationChangesResponse,
    RuntimeConfigurationPreviewResponse,
    RuntimeConfigurationSelection,
    SystemCapabilitiesResponse,
    SystemCapabilityProfile,
    SystemConfigurationResponse,
    SystemCounts,
    SystemDiagnosticsPreviewResponse,
    SystemDisk,
    SystemJobCounts,
    SystemOperation,
    SystemOperationsResponse,
    SystemOverviewResponse,
    SystemReauthenticationResponse,
    SystemServiceStatus,
    SystemValidationEvidence,
    VersionInventoryResponse,
)
from app.security.actor import ActorContext, ActorRole
from app.security.tokens import hash_opaque_token, issue_opaque_token
from app.services.parsing.types import OcrMode
from app.system.fixtures import (
    SYSTEM_OCR_FIXTURE_ID,
    SYSTEM_OCR_FIXTURE_SHA256,
    SYSTEM_OCR_FIXTURE_TEXT,
    system_ocr_fixture,
)
from app.system.ocr_tuning import (
    decode_ocr_preset,
    encode_ocr_preset,
    maximum_ocr_processes,
)

_PROFILE_IDS = {
    "generation": "generation.qwen3-8b.ollama.windows-x64",
    "embedding": "embedding.qwen3-0.6b-1024.ollama.windows-x64",
    "reranking": "reranking.bge-v2-m3.cpu.windows-x64",
    "ocr": "ocr.paddleocr-vl-1.6.cpu.windows-x64",
}
_DIAGNOSTIC_FILES = [
    "versions.json",
    "readiness.json",
    "capabilities.json",
    "configuration.json",
    "operations.json",
    "manifest.json",
]
_DIAGNOSTIC_EXCLUSIONS = [
    "credentials and private keys",
    "setup codes and session tokens",
    "raw environment files",
    "document text and filenames",
    "prompts and answers",
    "model binaries and unrestricted logs",
]


class SystemError(Exception):
    pass


class SystemUnavailable(SystemError):
    pass


class SystemConflict(SystemError):
    pass


class SystemCapabilityDenied(SystemError):
    pass


class RegistryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    function: str
    release_support_class: str
    operating_system: str
    architecture: str
    accelerator_vendor: str
    runtime_device: str | None = None
    engine: str
    model_identity: str
    artifact_ids: list[str]
    minimum_ram_gib: int
    minimum_vram_gib: int
    local_validation_fixture: str
    impact_class: str


class RegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    catalog_id: str
    catalog_revision: int
    profiles: list[RegistryProfile]


class SystemGateway(Protocol):
    async def version_inventory(self, session_token_hash: str) -> dict[str, object]: ...

    async def select_ingestion_profile(
        self, session_token_hash: str, selection: IngestionProfileSelection
    ) -> str: ...

    async def preview_reprocessing(
        self, session_token_hash: str, request: ReprocessingPreviewRequest
    ) -> dict[str, object]: ...

    async def issue_reprocessing_grant(
        self,
        session_token_hash: str,
        *,
        preview_id: UUID,
        impact_digest: str,
        token_hash: str,
    ) -> Any: ...

    async def start_reprocessing(
        self,
        session_token_hash: str,
        *,
        preview_id: UUID,
        impact_digest: str,
        grant_token_hash: str,
    ) -> UUID: ...

    async def reprocessing_operations(
        self, session_token_hash: str, *, limit: int
    ) -> list[ReprocessingOperation]: ...

    async def control_reprocessing(
        self, session_token_hash: str, *, operation_id: UUID, action: str
    ) -> bool: ...

    async def rollback_embedding_generation(
        self, session_token_hash: str, *, generation_id: UUID
    ) -> bool: ...

    async def cleanup_generation(
        self,
        session_token_hash: str,
        *,
        generation_type: str,
        generation_id: UUID,
    ) -> bool: ...

    async def counts(self, session_token_hash: str) -> dict[str, object]: ...

    async def evidence(
        self, session_token_hash: str
    ) -> dict[str, SystemValidationEvidence]: ...

    async def operations(
        self, session_token_hash: str, *, limit: int
    ) -> list[SystemOperation]: ...

    async def start(
        self,
        session_token_hash: str,
        *,
        operation_type: str,
        profile_id: str,
        completion_token_hash: str,
    ) -> UUID: ...

    async def complete(
        self,
        *,
        operation_id: UUID,
        completion_token_hash: str,
        succeeded: bool,
        reason_code: str,
        fixture_id: str,
        metrics: dict[str, str | int | float | None],
    ) -> None: ...

    async def advance(
        self,
        *,
        operation_id: UUID,
        completion_token_hash: str,
        stage: str,
    ) -> None: ...

    async def configuration(self, session_token_hash: str) -> dict[str, object]: ...

    async def effective_configuration(
        self, session_token_hash: str
    ) -> dict[str, object]: ...

    async def preview_configuration(
        self,
        session_token_hash: str,
        selection: RuntimeConfigurationSelection,
        *,
        ocr_profile_id: str,
        ocr_preset_id: str,
    ) -> dict[str, object]: ...

    async def issue_reauthentication_grant(
        self,
        session_token_hash: str,
        *,
        preview_id: UUID,
        impact_digest: str,
        token_hash: str,
    ) -> Any: ...

    async def apply_configuration(
        self,
        session_token_hash: str,
        *,
        preview_id: UUID,
        impact_digest: str,
        grant_token_hash: str,
        controller_nonce_hash: str,
    ) -> UUID: ...

    async def configuration_changes(
        self, session_token_hash: str, *, limit: int
    ) -> list[RuntimeConfigurationChange]: ...

    async def fail_configuration_delivery(
        self,
        session_token_hash: str,
        *,
        change_id: UUID,
        controller_nonce_hash: str,
    ) -> None: ...

    async def start_personal_backup(
        self, session_token_hash: str, *, controller_nonce_hash: str
    ) -> UUID: ...

    async def personal_backup_status(
        self, session_token_hash: str
    ) -> PersonalBackupOperation | None: ...

    async def personal_backup_history(
        self, session_token_hash: str, *, limit: int
    ) -> list[PersonalBackupOperation]: ...

    async def fail_personal_backup_delivery(
        self,
        session_token_hash: str,
        *,
        backup_run_id: UUID,
        controller_nonce_hash: str,
    ) -> None: ...


class DatabaseSystemGateway:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def version_inventory(self, session_token_hash: str) -> dict[str, object]:
        try:
            async with self._activated(session_token_hash) as session:
                payload = await session.scalar(
                    text("SELECT v10_admin_version_inventory()")
                )
            if not isinstance(payload, dict):
                raise SystemUnavailable("Version inventory is unavailable")
            return payload
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def select_ingestion_profile(
        self, session_token_hash: str, selection: IngestionProfileSelection
    ) -> str:
        try:
            async with self._activated(session_token_hash) as session:
                revision = await session.scalar(
                    text(
                        "SELECT v10_admin_set_ingestion_profile("
                        ":base_revision, :parser_profile_id)"
                    ),
                    selection.model_dump(),
                )
            return str(revision)
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def preview_reprocessing(
        self, session_token_hash: str, request: ReprocessingPreviewRequest
    ) -> dict[str, object]:
        try:
            async with self._activated(session_token_hash) as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT * FROM v10_admin_preview_reprocessing("
                            ":operation_type, :target_profile_id, "
                            ":source_parser_version)"
                        ),
                        request.model_dump(),
                    )
                ).one()
            return dict(row._mapping)
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def issue_reprocessing_grant(
        self,
        session_token_hash: str,
        *,
        preview_id: UUID,
        impact_digest: str,
        token_hash: str,
    ) -> Any:
        try:
            async with self._activated(session_token_hash) as session:
                return await session.scalar(
                    text(
                        "SELECT v10_admin_issue_reprocessing_grant("
                        ":preview_id, :impact_digest, :token_hash)"
                    ),
                    {
                        "preview_id": preview_id,
                        "impact_digest": impact_digest,
                        "token_hash": token_hash,
                    },
                )
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def start_reprocessing(
        self,
        session_token_hash: str,
        *,
        preview_id: UUID,
        impact_digest: str,
        grant_token_hash: str,
    ) -> UUID:
        try:
            async with self._activated(session_token_hash) as session:
                result = await session.scalar(
                    text(
                        "SELECT v10_admin_start_reprocessing("
                        ":preview_id, :impact_digest, :grant_token_hash)"
                    ),
                    {
                        "preview_id": preview_id,
                        "impact_digest": impact_digest,
                        "grant_token_hash": grant_token_hash,
                    },
                )
            return UUID(str(result))
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def reprocessing_operations(
        self, session_token_hash: str, *, limit: int
    ) -> list[ReprocessingOperation]:
        try:
            async with self._activated(session_token_hash) as session:
                rows = (
                    await session.execute(
                        text("SELECT * FROM v10_admin_reprocessing_operations(:limit)"),
                        {"limit": limit},
                    )
                ).all()
            return [ReprocessingOperation(**dict(row._mapping)) for row in rows]
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def control_reprocessing(
        self, session_token_hash: str, *, operation_id: UUID, action: str
    ) -> bool:
        functions = {
            "pause": "v10_admin_pause_reprocessing",
            "resume": "v10_admin_resume_reprocessing",
            "cancel": "v10_admin_cancel_reprocessing",
            "retry": "v10_admin_retry_reprocessing",
        }
        function = functions.get(action)
        if function is None:
            raise ValueError("invalid reprocessing action")
        try:
            async with self._activated(session_token_hash) as session:
                return bool(
                    await session.scalar(
                        text(f"SELECT {function}(:operation_id)"),
                        {"operation_id": operation_id},
                    )
                )
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def rollback_embedding_generation(
        self, session_token_hash: str, *, generation_id: UUID
    ) -> bool:
        try:
            async with self._activated(session_token_hash) as session:
                return bool(
                    await session.scalar(
                        text(
                            "SELECT v10_admin_rollback_embedding_generation("
                            ":generation_id)"
                        ),
                        {"generation_id": generation_id},
                    )
                )
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def cleanup_generation(
        self,
        session_token_hash: str,
        *,
        generation_type: str,
        generation_id: UUID,
    ) -> bool:
        try:
            async with self._activated(session_token_hash) as session:
                return bool(
                    await session.scalar(
                        text(
                            "SELECT v10_admin_cleanup_generation("
                            ":generation_type, :generation_id)"
                        ),
                        {
                            "generation_type": generation_type,
                            "generation_id": generation_id,
                        },
                    )
                )
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def counts(self, session_token_hash: str) -> dict[str, object]:
        async with self._activated(session_token_hash) as session:
            payload = await session.scalar(text("SELECT v8_admin_system_counts()"))
        if not isinstance(payload, dict):
            raise SystemUnavailable("System counts are unavailable")
        return payload

    async def evidence(
        self, session_token_hash: str
    ) -> dict[str, SystemValidationEvidence]:
        async with self._activated(session_token_hash) as session:
            rows = (
                await session.execute(text("SELECT * FROM v8_admin_system_evidence()"))
            ).all()
        return {
            row.profile_id: SystemValidationEvidence(
                state=row.validation_state,
                reason_code=row.reason_code,
                fixture_id=row.fixture_id,
                evidence_at=row.evidence_at,
                metrics=_bounded_metrics(row.metrics),
            )
            for row in rows
        }

    async def operations(
        self, session_token_hash: str, *, limit: int
    ) -> list[SystemOperation]:
        async with self._activated(session_token_hash) as session:
            rows = (
                await session.execute(
                    text("SELECT * FROM v8_admin_system_operations(:limit)"),
                    {"limit": limit},
                )
            ).all()
        return [
            SystemOperation(
                operation_id=row.operation_id,
                operation_type=row.operation_type,
                profile_id=row.profile_id,
                state=row.state,
                stage=row.stage,
                reason_code=row.reason_code,
                metrics=_bounded_metrics(row.metrics),
                created_at=row.created_at,
                finished_at=row.finished_at,
            )
            for row in rows
        ]

    async def start(
        self,
        session_token_hash: str,
        *,
        operation_type: str,
        profile_id: str,
        completion_token_hash: str,
    ) -> UUID:
        try:
            async with self._activated(session_token_hash) as session:
                result = await session.scalar(
                    text(
                        "SELECT v8_admin_start_system_operation("
                        ":operation_type, :profile_id, :completion_token_hash)"
                    ),
                    {
                        "operation_type": operation_type,
                        "profile_id": profile_id,
                        "completion_token_hash": completion_token_hash,
                    },
                )
            return UUID(str(result))
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def complete(
        self,
        *,
        operation_id: UUID,
        completion_token_hash: str,
        succeeded: bool,
        reason_code: str,
        fixture_id: str,
        metrics: dict[str, str | int | float | None],
    ) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "SELECT v8_complete_system_operation("
                        ":operation_id, :completion_token_hash, :succeeded, "
                        ":reason_code, :fixture_id, CAST(:metrics AS jsonb))"
                    ),
                    {
                        "operation_id": operation_id,
                        "completion_token_hash": completion_token_hash,
                        "succeeded": succeeded,
                        "reason_code": reason_code,
                        "fixture_id": fixture_id,
                        "metrics": json.dumps(metrics, separators=(",", ":")),
                    },
                )
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)

    async def advance(
        self,
        *,
        operation_id: UUID,
        completion_token_hash: str,
        stage: str,
    ) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "SELECT v8_advance_system_operation("
                        ":operation_id, :completion_token_hash, :stage)"
                    ),
                    {
                        "operation_id": operation_id,
                        "completion_token_hash": completion_token_hash,
                        "stage": stage,
                    },
                )
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)

    async def configuration(self, session_token_hash: str) -> dict[str, object]:
        try:
            async with self._activated(session_token_hash) as session:
                row = (
                    await session.execute(
                        text("SELECT * FROM v9_admin_runtime_configuration()")
                    )
                ).one()
            return dict(row._mapping)
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def preview_configuration(
        self,
        session_token_hash: str,
        selection: RuntimeConfigurationSelection,
        *,
        ocr_profile_id: str,
        ocr_preset_id: str,
    ) -> dict[str, object]:
        try:
            async with self._activated(session_token_hash) as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT * FROM v9_admin_preview_runtime_configuration("
                            ":base_revision, :generation_profile_id, "
                            ":reranker_profile_id, :ocr_mode, :ocr_profile_id, "
                            ":ocr_preset_id)"
                        ),
                        {
                            "base_revision": selection.base_revision,
                            "generation_profile_id": selection.generation_profile_id,
                            "reranker_profile_id": selection.reranker_profile_id,
                            "ocr_mode": selection.ocr_mode,
                            "ocr_profile_id": ocr_profile_id,
                            "ocr_preset_id": ocr_preset_id,
                        },
                    )
                ).one()
            return dict(row._mapping)
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def issue_reauthentication_grant(
        self,
        session_token_hash: str,
        *,
        preview_id: UUID,
        impact_digest: str,
        token_hash: str,
    ) -> Any:
        try:
            async with self._activated(session_token_hash) as session:
                return await session.scalar(
                    text(
                        "SELECT v9_admin_issue_reauthentication_grant("
                        ":preview_id, 'apply_runtime_configuration', "
                        ":impact_digest, :token_hash)"
                    ),
                    {
                        "preview_id": preview_id,
                        "impact_digest": impact_digest,
                        "token_hash": token_hash,
                    },
                )
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def apply_configuration(
        self,
        session_token_hash: str,
        *,
        preview_id: UUID,
        impact_digest: str,
        grant_token_hash: str,
        controller_nonce_hash: str,
    ) -> UUID:
        try:
            async with self._activated(session_token_hash) as session:
                result = await session.scalar(
                    text(
                        "SELECT v9_admin_apply_runtime_configuration("
                        ":preview_id, :impact_digest, :grant_token_hash, "
                        ":controller_nonce_hash)"
                    ),
                    {
                        "preview_id": preview_id,
                        "impact_digest": impact_digest,
                        "grant_token_hash": grant_token_hash,
                        "controller_nonce_hash": controller_nonce_hash,
                    },
                )
            return UUID(str(result))
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def configuration_changes(
        self, session_token_hash: str, *, limit: int
    ) -> list[RuntimeConfigurationChange]:
        try:
            async with self._activated(session_token_hash) as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT * FROM "
                            "v9_admin_runtime_configuration_changes(:limit)"
                        ),
                        {"limit": limit},
                    )
                ).all()
            return [RuntimeConfigurationChange(**dict(row._mapping)) for row in rows]
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def fail_configuration_delivery(
        self,
        session_token_hash: str,
        *,
        change_id: UUID,
        controller_nonce_hash: str,
    ) -> None:
        try:
            async with self._activated(session_token_hash) as session:
                await session.execute(
                    text(
                        "SELECT v9_admin_fail_runtime_configuration_delivery("
                        ":change_id, :controller_nonce_hash)"
                    ),
                    {
                        "change_id": change_id,
                        "controller_nonce_hash": controller_nonce_hash,
                    },
                )
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)

    async def start_personal_backup(
        self, session_token_hash: str, *, controller_nonce_hash: str
    ) -> UUID:
        try:
            async with self._activated(session_token_hash) as session:
                value = await session.scalar(
                    text("SELECT v9_admin_start_personal_backup(:nonce_hash)"),
                    {"nonce_hash": controller_nonce_hash},
                )
            return UUID(str(value))
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def personal_backup_status(
        self, session_token_hash: str
    ) -> PersonalBackupOperation | None:
        try:
            async with self._activated(session_token_hash) as session:
                row = (
                    await session.execute(
                        text("SELECT * FROM v9_admin_personal_backup_status()")
                    )
                ).one_or_none()
            return PersonalBackupOperation(**dict(row._mapping)) if row else None
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def personal_backup_history(
        self, session_token_hash: str, *, limit: int
    ) -> list[PersonalBackupOperation]:
        try:
            async with self._activated(session_token_hash) as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT * FROM "
                            "v11_admin_personal_backup_history(:limit)"
                        ),
                        {"limit": limit},
                    )
                ).all()
            return [PersonalBackupOperation(**dict(row._mapping)) for row in rows]
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def effective_configuration(
        self, session_token_hash: str
    ) -> dict[str, object]:
        try:
            async with self._activated(session_token_hash) as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT * FROM "
                            "v13_admin_effective_runtime_configuration()"
                        )
                    )
                ).one()
            return dict(row._mapping)
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)
            raise

    async def fail_personal_backup_delivery(
        self,
        session_token_hash: str,
        *,
        backup_run_id: UUID,
        controller_nonce_hash: str,
    ) -> None:
        try:
            async with self._activated(session_token_hash) as session:
                await session.execute(
                    text(
                        "SELECT v9_admin_fail_personal_backup_delivery("
                        ":backup_run_id, :nonce_hash)"
                    ),
                    {
                        "backup_run_id": backup_run_id,
                        "nonce_hash": controller_nonce_hash,
                    },
                )
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)

    @asynccontextmanager
    async def _activated(self, session_token_hash: str):
        try:
            async with AuthenticatedUnitOfWork(
                self._session_factory, session_token_hash
            ) as unit:
                if unit.session is None:
                    raise SystemUnavailable("System database functions are unavailable")
                yield unit.session
        except SQLAlchemyError as exc:
            _raise_system_database_error(exc)


class SystemService:
    def __init__(
        self,
        gateway: SystemGateway,
        settings: Settings,
        readiness: Any,
        generator: Any,
        embedder: Any,
        reranker: Any,
        *,
        ocr: Any | None = None,
        authentication: Any | None = None,
        controller: Any | None = None,
        registry_path: Path | None = None,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._readiness = readiness
        self._generator = generator
        self._embedder = embedder
        self._reranker = reranker
        self._ocr = ocr
        self._authentication = authentication
        self._controller = controller
        self._operation_lock = asyncio.Lock()
        self._registry = _load_registry(registry_path or _default_registry_path())

    async def version_inventory(
        self, actor: ActorContext, session_token: str
    ) -> VersionInventoryResponse:
        _require_admin(actor)
        payload = await self._gateway.version_inventory(
            hash_opaque_token(session_token)
        )
        return VersionInventoryResponse(**payload)

    async def select_ingestion_profile(
        self,
        actor: ActorContext,
        session_token: str,
        selection: IngestionProfileSelection,
    ) -> IngestionProfileSelectionResponse:
        _require_admin(actor)
        if selection.parser_profile_id not in {
            "parser.paddleocr-vl-1.6.adaptive-v2",
            "parser.paddleocr-vl-1.6.legacy-v1",
        }:
            raise ValueError("parser profile is unavailable")
        revision = await self._gateway.select_ingestion_profile(
            hash_opaque_token(session_token), selection
        )
        return IngestionProfileSelectionResponse(revision_id=revision)

    async def preview_reprocessing(
        self,
        actor: ActorContext,
        session_token: str,
        request: ReprocessingPreviewRequest,
    ) -> ReprocessingPreviewResponse:
        _require_admin(actor)
        row = await self._gateway.preview_reprocessing(
            hash_opaque_token(session_token), request
        )
        return ReprocessingPreviewResponse(**row)

    async def reauthenticate_reprocessing(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        preview_id: UUID,
        password: str,
        impact_digest: str,
    ) -> SystemReauthenticationResponse:
        _require_admin(actor)
        if self._authentication is None:
            raise SystemUnavailable("reauthentication is unavailable")
        await self._authentication.verify_current_password(session_token, password)
        grant = issue_opaque_token()
        expires_at = await self._gateway.issue_reprocessing_grant(
            hash_opaque_token(session_token),
            preview_id=preview_id,
            impact_digest=impact_digest,
            token_hash=grant.digest,
        )
        return SystemReauthenticationResponse(
            grant_token=grant.plaintext, expires_at=expires_at
        )

    async def start_reprocessing(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        preview_id: UUID,
        impact_digest: str,
        grant_token: str,
    ) -> ReprocessingOperationResponse:
        _require_admin(actor)
        token_hash = hash_opaque_token(session_token)
        operation_id = await self._gateway.start_reprocessing(
            token_hash,
            preview_id=preview_id,
            impact_digest=impact_digest,
            grant_token_hash=hash_opaque_token(grant_token),
        )
        operation = await self._find_reprocessing_operation(token_hash, operation_id)
        return ReprocessingOperationResponse(operation=operation)

    async def reprocessing_operations(
        self, actor: ActorContext, session_token: str, *, limit: int = 50
    ) -> ReprocessingOperationsResponse:
        _require_admin(actor)
        return ReprocessingOperationsResponse(
            operations=await self._gateway.reprocessing_operations(
                hash_opaque_token(session_token), limit=limit
            )
        )

    async def control_reprocessing(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        operation_id: UUID,
        action: str,
    ) -> ReprocessingOperationResponse:
        _require_admin(actor)
        token_hash = hash_opaque_token(session_token)
        changed = await self._gateway.control_reprocessing(
            token_hash, operation_id=operation_id, action=action
        )
        if not changed:
            raise SystemConflict(
                f"reprocessing operation cannot {action} at its current boundary"
            )
        operation = await self._find_reprocessing_operation(token_hash, operation_id)
        return ReprocessingOperationResponse(operation=operation)

    async def rollback_embedding_generation(
        self, actor: ActorContext, session_token: str, generation_id: UUID
    ) -> GenerationActionResponse:
        _require_admin(actor)
        succeeded = await self._gateway.rollback_embedding_generation(
            hash_opaque_token(session_token), generation_id=generation_id
        )
        if not succeeded:
            raise SystemConflict("embedding generation is not rollback-eligible")
        return GenerationActionResponse(succeeded=True)

    async def cleanup_generation(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        generation_type: str,
        generation_id: UUID,
    ) -> GenerationActionResponse:
        _require_admin(actor)
        if generation_type not in {"embedding", "document"}:
            raise ValueError("invalid generation type")
        succeeded = await self._gateway.cleanup_generation(
            hash_opaque_token(session_token),
            generation_type=generation_type,
            generation_id=generation_id,
        )
        if not succeeded:
            raise SystemConflict("generation is active or not cleanup-eligible")
        return GenerationActionResponse(succeeded=True)

    async def _find_reprocessing_operation(
        self, token_hash: str, operation_id: UUID
    ) -> ReprocessingOperation:
        operations = await self._gateway.reprocessing_operations(token_hash, limit=100)
        operation = next(
            (item for item in operations if item.operation_id == operation_id), None
        )
        if operation is None:
            raise SystemUnavailable("reprocessing operation is unavailable")
        return operation

    async def overview(
        self, actor: ActorContext, session_token: str
    ) -> SystemOverviewResponse:
        _require_admin(actor)
        token_hash = hash_opaque_token(session_token)
        readiness_result, counts_result = await asyncio.gather(
            self._safe_readiness(),
            self._safe_counts(token_hash),
        )
        services = self._service_statuses(readiness_result, counts_result)
        counts = counts_result if isinstance(counts_result, dict) else {}
        documents = counts.get("documents", {})
        jobs = counts.get("jobs", {})
        disk = await asyncio.to_thread(_disk_usage, self._settings.data_root)
        operations = await self._safe_operations(token_hash, limit=100)
        unavailable = [
            service for service in services if service.state == "unavailable"
        ]
        degraded = [service for service in services if service.state == "degraded"]
        if unavailable:
            overall = "unavailable"
            action = unavailable[0].message
        elif degraded:
            overall = "attention"
            action = degraded[0].message
        else:
            overall = "ready"
            action = "No action is required."
        return SystemOverviewResponse(
            product_profile=self._settings.product_profile,
            overall_state=overall,
            recommended_action=action,
            services=services,
            documents=SystemCounts(
                ready=_safe_count(documents, "ready"),
                processing=_safe_count(documents, "processing"),
                failed=_safe_count(documents, "failed"),
            ),
            jobs=SystemJobCounts(
                active=_safe_count(jobs, "active"),
                queued=_safe_count(jobs, "queued"),
            ),
            disk=SystemDisk(**disk),
            operation_count=len(operations),
        )

    async def capabilities(
        self, actor: ActorContext, session_token: str
    ) -> SystemCapabilitiesResponse:
        _require_admin(actor)
        token_hash = hash_opaque_token(session_token)
        evidence, readiness, processor, current = await asyncio.gather(
            self._safe_evidence(token_hash),
            self._safe_readiness(),
            self._ollama_processor(),
            self._safe_effective_configuration(token_hash),
        )
        effective_ids = {
            "generation": str(current.get("generation_profile_id", "")),
            "reranking": str(current.get("reranker_profile_id", "")),
            "ocr": str(current.get("ocr_profile_id", "")),
            "embedding": _PROFILE_IDS["embedding"],
        }
        profiles = [
            self._capability(
                profile,
                evidence.get(profile.profile_id),
                readiness,
                effective_ids,
            )
            for profile in self._registry.profiles
        ]
        logical_cpu_count = max(os.cpu_count() or 1, 1)
        system_memory_bytes = _system_memory_bytes()
        return SystemCapabilitiesResponse(
            catalog_id=self._registry.catalog_id,
            catalog_revision=self._registry.catalog_revision,
            profiles=profiles,
            observed_processor=processor,
            logical_cpu_count=logical_cpu_count,
            system_memory_bytes=system_memory_bytes,
            maximum_ocr_processes=maximum_ocr_processes(
                logical_cpu_count=logical_cpu_count,
                system_memory_bytes=system_memory_bytes,
            ),
        )

    async def configuration(
        self, actor: ActorContext, session_token: str
    ) -> SystemConfigurationResponse:
        _require_admin(actor)
        token_hash = hash_opaque_token(session_token)
        current, effective = await asyncio.gather(
            self._gateway.configuration(token_hash),
            self._gateway.effective_configuration(token_hash),
        )
        try:
            ocr_cpu_threads, ocr_process_count = decode_ocr_preset(
                str(effective["ocr_preset_id"])
            )
        except ValueError as exc:
            raise SystemUnavailable("stored OCR tuning is invalid") from exc
        ocr_profile = next(
            (
                profile
                for profile in self._registry.profiles
                if profile.profile_id == effective["ocr_profile_id"]
            ),
            None,
        )
        if ocr_profile is None:
            raise SystemUnavailable("stored OCR profile is unavailable")
        ocr_device = ocr_profile.runtime_device or (
            "cpu" if ocr_profile.accelerator_vendor == "cpu" else "gpu:0"
        )
        values = {
            "generation_profile_id": effective["generation_profile_id"],
            "generation_model": self._settings.generation_model,
            "embedding_profile_id": _PROFILE_IDS["embedding"],
            "embedding_model": self._settings.embedding_model,
            "reranker_profile_id": effective["reranker_profile_id"],
            "reranker_model": self._settings.reranker_model,
            "parser_identity": "paddleocr-vl-v1.6-adaptive-v2",
            "ocr_profile_id": effective["ocr_profile_id"],
            "ocr_device": ocr_device,
            "ocr_engine": "paddleocr-vl-1.6",
            "ocr_cpu_threads": ocr_cpu_threads,
            "ocr_process_count": ocr_process_count,
            "ocr_page_batch_size": self._settings.ocr_page_batch_size,
            "maximum_generation_context": self._settings.maximum_generation_context,
            "maximum_generation_output": self._settings.maximum_generation_output,
        }
        return SystemConfigurationResponse(
            effective_revision=str(effective["effective_revision"]),
            desired_revision=str(current["desired_revision"]),
            state=str(current["state"]),
            ocr_mode=str(effective["ocr_mode"]),
            ocr_preset_id=str(effective["ocr_preset_id"]),
            impact_digest=current.get("impact_digest"),
            operation_class=current.get("operation_class"),
            prior_revision=current.get("prior_revision"),
            proposed_by=current.get("actor_user_id"),
            proposed_at=current.get("proposed_at"),
            reason_code=current.get("reason_code"),
            backup_verified=bool(current["backup_verified"]),
            backup_verified_at=current.get("backup_verified_at"),
            **values,
        )

    async def preview_configuration(
        self,
        actor: ActorContext,
        session_token: str,
        selection: RuntimeConfigurationSelection,
    ) -> RuntimeConfigurationPreviewResponse:
        _require_admin(actor)
        current = await self.configuration(actor, session_token)
        if selection.base_revision != current.effective_revision:
            raise SystemConflict("runtime configuration preview is stale")
        profile_ids = {
            profile.profile_id: profile for profile in self._registry.profiles
        }
        evidence = await self._gateway.evidence(hash_opaque_token(session_token))
        logical_cpu_count = max(os.cpu_count() or 1, 1)
        if selection.ocr_cpu_threads > logical_cpu_count:
            raise ValueError(
                "OCR CPU threads cannot exceed this computer's "
                f"{logical_cpu_count} logical CPUs"
            )
        process_limit = maximum_ocr_processes(
            logical_cpu_count=logical_cpu_count,
            system_memory_bytes=_system_memory_bytes(),
        )
        if selection.ocr_process_count > process_limit:
            raise ValueError(
                "OCR process count cannot exceed this computer's hardware limit of "
                f"{process_limit}"
            )
        auto_ocr_profiles = sorted(
            (
                profile
                for profile in profile_ids.values()
                if profile.function == "ocr"
                and profile.release_support_class == "release_qualified"
                and (
                    profile.profile_id == current.ocr_profile_id
                    or (
                        profile.profile_id in evidence
                        and evidence[profile.profile_id].state == "locally_validated"
                    )
                )
            ),
            key=lambda profile: profile.accelerator_vendor == "cpu",
        )
        selected_ocr_profile_id = (
            auto_ocr_profiles[0].profile_id
            if selection.ocr_mode == "auto" and auto_ocr_profiles
            else selection.ocr_profile_id
        )
        for profile_id in (
            selection.generation_profile_id,
            selection.reranker_profile_id,
            selected_ocr_profile_id,
        ):
            profile = profile_ids.get(profile_id)
            if profile is None or profile.release_support_class != "release_qualified":
                raise ValueError(
                    "runtime configuration contains an unavailable profile"
                )
        changed_profiles = (
            (selection.generation_profile_id, current.generation_profile_id),
            (selection.reranker_profile_id, current.reranker_profile_id),
            (selected_ocr_profile_id, current.ocr_profile_id),
        )
        if any(
            selected != effective
            and (
                selected not in evidence
                or evidence[selected].state != "locally_validated"
            )
            for selected, effective in changed_profiles
        ):
            raise ValueError("selected profile has not passed local validation")
        ocr_preset_id = (
            "balanced"
            if selection.ocr_mode == "auto"
            else encode_ocr_preset(
                selection.ocr_cpu_threads, selection.ocr_process_count
            )
        )
        row = await self._gateway.preview_configuration(
            hash_opaque_token(session_token),
            selection,
            ocr_profile_id=selected_ocr_profile_id,
            ocr_preset_id=ocr_preset_id,
        )
        affected: list[str] = []
        waits: list[str] = []
        if (
            selection.generation_profile_id != current.generation_profile_id
            or selection.reranker_profile_id != current.reranker_profile_id
        ):
            affected.append("coordinator")
            waits.append("active_answer_boundary")
        if (
            selection.ocr_mode != current.ocr_mode
            or selected_ocr_profile_id != current.ocr_profile_id
            or ocr_preset_id != current.ocr_preset_id
        ):
            affected.append("ocr")
            waits.append("active_ocr_boundary")
        return RuntimeConfigurationPreviewResponse(
            preview_id=row["preview_id"],
            impact_digest=row["impact_digest"],
            expires_at=row["expires_at"],
            affected_services=affected,
            waits_for=waits,
            expected_interruption=(
                "The affected local service restarts after active work reaches "
                "a safe boundary."
            ),
        )

    async def reauthenticate_configuration(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        preview_id: UUID,
        password: str,
        impact_digest: str,
    ) -> SystemReauthenticationResponse:
        _require_admin(actor)
        if self._authentication is None:
            raise SystemUnavailable("reauthentication is unavailable")
        await self._authentication.verify_current_password(session_token, password)
        grant = issue_opaque_token()
        expires_at = await self._gateway.issue_reauthentication_grant(
            hash_opaque_token(session_token),
            preview_id=preview_id,
            impact_digest=impact_digest,
            token_hash=grant.digest,
        )
        return SystemReauthenticationResponse(
            grant_token=grant.plaintext, expires_at=expires_at
        )

    async def apply_configuration(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        preview_id: UUID,
        impact_digest: str,
        grant_token: str,
    ) -> RuntimeConfigurationApplyResponse:
        _require_admin(actor)
        if self._controller is None:
            raise SystemUnavailable("local controller is unavailable")
        nonce = issue_opaque_token()
        change_id = await self._gateway.apply_configuration(
            hash_opaque_token(session_token),
            preview_id=preview_id,
            impact_digest=impact_digest,
            grant_token_hash=hash_opaque_token(grant_token),
            controller_nonce_hash=nonce.digest,
        )
        try:
            await self._controller.apply_configuration(change_id, nonce.plaintext)
        except ControllerUnavailable:
            await self._gateway.fail_configuration_delivery(
                hash_opaque_token(session_token),
                change_id=change_id,
                controller_nonce_hash=nonce.digest,
            )
        changes = await self._gateway.configuration_changes(
            hash_opaque_token(session_token), limit=100
        )
        change = next((item for item in changes if item.change_id == change_id), None)
        if change is None:
            raise SystemUnavailable("runtime configuration change is unavailable")
        return RuntimeConfigurationApplyResponse(change=change)

    async def configuration_changes(
        self, actor: ActorContext, session_token: str, *, limit: int = 50
    ) -> RuntimeConfigurationChangesResponse:
        _require_admin(actor)
        return RuntimeConfigurationChangesResponse(
            changes=await self._gateway.configuration_changes(
                hash_opaque_token(session_token), limit=limit
            )
        )

    async def start_personal_backup(
        self, actor: ActorContext, session_token: str
    ) -> PersonalBackupResponse:
        _require_admin(actor)
        if self._settings.product_profile != "personal":
            raise SystemCapabilityDenied("attended backup is Personal-only")
        if self._controller is None:
            raise SystemUnavailable("local controller is unavailable")
        nonce = issue_opaque_token()
        token_hash = hash_opaque_token(session_token)
        backup_run_id = await self._gateway.start_personal_backup(
            token_hash, controller_nonce_hash=nonce.digest
        )
        try:
            await self._controller.create_backup(backup_run_id, nonce.plaintext)
        except ControllerUnavailable:
            await self._gateway.fail_personal_backup_delivery(
                token_hash,
                backup_run_id=backup_run_id,
                controller_nonce_hash=nonce.digest,
            )
            raise SystemUnavailable("local controller is unavailable") from None
        operation = await self._gateway.personal_backup_status(token_hash)
        if operation is None or operation.backup_run_id != backup_run_id:
            raise SystemUnavailable("backup operation is unavailable")
        return PersonalBackupResponse(operation=operation)

    async def personal_backup_status(
        self, actor: ActorContext, session_token: str
    ) -> PersonalBackupStatusResponse:
        _require_admin(actor)
        return PersonalBackupStatusResponse(
            operation=await self._gateway.personal_backup_status(
                hash_opaque_token(session_token)
            )
        )

    async def personal_backup_history(
        self, actor: ActorContext, session_token: str, *, limit: int = 25
    ) -> PersonalBackupHistoryResponse:
        _require_admin(actor)
        return PersonalBackupHistoryResponse(
            operations=await self._gateway.personal_backup_history(
                hash_opaque_token(session_token), limit=limit
            )
        )

    async def operations(
        self, actor: ActorContext, session_token: str, *, limit: int = 50
    ) -> SystemOperationsResponse:
        _require_admin(actor)
        return SystemOperationsResponse(
            operations=await self._gateway.operations(
                hash_opaque_token(session_token), limit=limit
            )
        )

    async def run_profile_operation(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        profile_id: str,
        benchmark: bool,
    ) -> SystemOperation:
        _require_admin(actor)
        profile = next(
            (item for item in self._registry.profiles if item.profile_id == profile_id),
            None,
        )
        if profile is None or profile.release_support_class != "release_qualified":
            raise ValueError("profile is not release-qualified")
        if benchmark and profile.function != "ocr":
            raise ValueError("benchmark is available only for the OCR profile")
        token_hash = hash_opaque_token(session_token)
        if profile.function == "ocr":
            current = await self._gateway.configuration(token_hash)
            effective = await self._gateway.effective_configuration(token_hash)
            if (
                current.get("state") != "effective"
                or effective.get("ocr_profile_id") != profile.profile_id
            ):
                raise ValueError(
                    "inactive OCR profiles must be validated during "
                    "runtime installation"
                )
        issued = issue_opaque_token()
        operation_type = "profile_benchmark" if benchmark else "profile_validation"
        operation_id = await self._gateway.start(
            token_hash,
            operation_type=operation_type,
            profile_id=profile_id,
            completion_token_hash=issued.digest,
        )
        async with self._operation_lock:
            await self._gateway.advance(
                operation_id=operation_id,
                completion_token_hash=issued.digest,
                stage="benchmarking" if benchmark else "validating",
            )
            succeeded = False
            reason_code = "validation_failed"
            fixture_id = profile.local_validation_fixture
            metrics: dict[str, str | int | float | None] = {}
            try:
                succeeded, reason_code, fixture_id, metrics = await self._execute(
                    profile, benchmark=benchmark
                )
            except Exception:
                reason_code = "validation_dependency_unavailable"
            await self._gateway.complete(
                operation_id=operation_id,
                completion_token_hash=issued.digest,
                succeeded=succeeded,
                reason_code=reason_code,
                fixture_id=fixture_id,
                metrics=metrics,
            )
        operations = await self._gateway.operations(
            token_hash, limit=100
        )
        completed = next(
            (
                operation
                for operation in operations
                if operation.operation_id == operation_id
            ),
            None,
        )
        if completed is None:
            raise SystemUnavailable("completed System operation is unavailable")
        return completed

    async def diagnostic_payloads(
        self, actor: ActorContext, session_token: str
    ) -> dict[str, object]:
        overview, capabilities, operations = await asyncio.gather(
            self.overview(actor, session_token),
            self.capabilities(actor, session_token),
            self.operations(actor, session_token),
        )
        configuration = await self.configuration(actor, session_token)
        return {
            "versions.json": {
                "application": "0.1.0",
                "schema": "0014_restart_without_backup",
                "profile_catalog": self._registry.catalog_id,
                "profile_catalog_revision": self._registry.catalog_revision,
            },
            "readiness.json": overview.model_dump(mode="json"),
            "capabilities.json": capabilities.model_dump(mode="json"),
            "configuration.json": configuration.model_dump(mode="json"),
            "operations.json": operations.model_dump(mode="json"),
        }

    async def controller_smoke_profile(self, profile_id: str) -> tuple[bool, str, str]:
        """Run a release-owned fixture for the local privileged controller."""
        profile = next(
            (item for item in self._registry.profiles if item.profile_id == profile_id),
            None,
        )
        if profile is None or profile.release_support_class != "release_qualified":
            raise ValueError("profile is not release-qualified")
        succeeded, reason_code, fixture_id, _metrics = await self._execute(
            profile, benchmark=False
        )
        return succeeded, reason_code, fixture_id

    @staticmethod
    def diagnostics_preview(actor: ActorContext) -> SystemDiagnosticsPreviewResponse:
        _require_admin(actor)
        return SystemDiagnosticsPreviewResponse(
            files=_DIAGNOSTIC_FILES,
            exclusions=_DIAGNOSTIC_EXCLUSIONS,
        )

    async def diagnostics_archive(
        self, actor: ActorContext, session_token: str
    ) -> bytes:
        payloads = await self.diagnostic_payloads(actor, session_token)
        files: dict[str, bytes] = {
            name: json.dumps(payload, sort_keys=True, indent=2).encode()
            for name, payload in payloads.items()
        }
        manifest = {
            "privacy_mode": True,
            "files": [
                {"name": name, "sha256": hashlib.sha256(content).hexdigest()}
                for name, content in sorted(files.items())
            ],
            "exclusions": _DIAGNOSTIC_EXCLUSIONS,
        }
        files["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode()
        target = io.BytesIO()
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(files.items()):
                archive.writestr(name, content)
        return target.getvalue()

    async def _execute(
        self, profile: RegistryProfile, *, benchmark: bool
    ) -> tuple[bool, str, str, dict[str, str | int | float | None]]:
        if profile.function == "generation":
            answer = ""
            async for chunk in self._generator.stream(
                "Reply with exactly SYSTEM_OK and nothing else.", think=False
            ):
                if chunk.type == "answer":
                    answer += chunk.text
            result = "".join(answer.split()).upper()
            return (
                result == "SYSTEM_OK",
                (
                    "generation_fixture_passed"
                    if result == "SYSTEM_OK"
                    else "generation_output_mismatch"
                ),
                profile.local_validation_fixture,
                {"result_sha256": hashlib.sha256(answer.encode()).hexdigest()},
            )
        if profile.function == "embedding":
            vectors = await self._embedder.embed(["Local RAG embedding validation."])
            dimension = len(vectors[0]) if len(vectors) == 1 else 0
            return (
                dimension == self._settings.embedding_dimension,
                "embedding_dimension_passed"
                if dimension == self._settings.embedding_dimension
                else "embedding_dimension_mismatch",
                profile.local_validation_fixture,
                {"embedding_dimension": dimension},
            )
        if profile.function == "reranking":
            scores = await self._reranker.score(
                "What is the capital of France?",
                ["Paris is the capital of France.", "Bananas are yellow fruit."],
            )
            passed = len(scores) == 2 and scores[0] > scores[1]
            return (
                passed,
                "reranker_ordering_passed" if passed else "reranker_ordering_failed",
                profile.local_validation_fixture,
                {
                    "relevant_score": scores[0] if len(scores) > 0 else None,
                    "irrelevant_score": scores[1] if len(scores) > 1 else None,
                },
            )
        if self._ocr is None:
            raise SystemUnavailable("OCR validation service is not configured")
        parent = self._settings.data_root.resolve() / "system-operations"
        root = parent / issue_opaque_token().plaintext[:32]
        input_directory = root / "input"
        output_directory = root / "output"
        input_directory.mkdir(parents=True, exist_ok=False)
        fixture = system_ocr_fixture()
        (input_directory / "page-000001.pdf").write_bytes(fixture)
        try:
            parsed = await self._ocr.parse_pages(
                input_directory,
                output_directory,
                {1},
                mode=OcrMode.FULL_PAGE,
            )
            normalized = " ".join(parsed[1].text.upper().split())
            passed = all(
                token in normalized for token in SYSTEM_OCR_FIXTURE_TEXT.split()
            )
            metrics = {
                "duration_seconds": parsed.duration_seconds,
                "peak_working_set_bytes": parsed.peak_working_set_bytes,
                "pages": 1,
                "samples": 1 if benchmark else None,
                "fixture_sha256": SYSTEM_OCR_FIXTURE_SHA256,
                "result_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
                "processor": profile.runtime_device
                or ("cpu" if profile.accelerator_vendor == "cpu" else "gpu:0"),
            }
            return (
                passed,
                (
                    "ocr_benchmark_passed"
                    if benchmark and passed
                    else "ocr_fixture_passed"
                    if passed
                    else "ocr_fixture_mismatch"
                ),
                SYSTEM_OCR_FIXTURE_ID,
                metrics,
            )
        finally:
            if root.parent == parent and parent in root.parents:
                shutil.rmtree(root, ignore_errors=True)

    async def _safe_readiness(self):
        try:
            return await self._readiness.check()
        except Exception:
            return None

    async def _safe_configuration(self, token_hash: str) -> dict[str, object]:
        try:
            return await self._gateway.configuration(token_hash)
        except Exception:
            return {}

    async def _safe_effective_configuration(
        self, token_hash: str
    ) -> dict[str, object]:
        try:
            return await self._gateway.effective_configuration(token_hash)
        except Exception:
            return {}

    async def _safe_counts(self, token_hash: str) -> dict[str, object] | None:
        try:
            return await self._gateway.counts(token_hash)
        except Exception:
            return None

    async def _safe_evidence(
        self, token_hash: str
    ) -> dict[str, SystemValidationEvidence]:
        try:
            return await self._gateway.evidence(token_hash)
        except Exception:
            return {}

    async def _safe_operations(
        self, token_hash: str, *, limit: int
    ) -> list[SystemOperation]:
        try:
            return await self._gateway.operations(token_hash, limit=limit)
        except Exception:
            return []

    async def _ollama_processor(self) -> str:
        try:
            async with httpx.AsyncClient(
                base_url=str(self._settings.ollama_base_url).rstrip("/"), timeout=3
            ) as client:
                response = await client.get("/api/ps")
                response.raise_for_status()
                payload = response.json()
            models = payload.get("models", []) if isinstance(payload, dict) else []
            if any(
                isinstance(model, dict) and int(model.get("size_vram", 0)) > 0
                for model in models
            ):
                return "Ollama reports accelerator memory in use"
            return "Ollama reports CPU or no loaded model"
        except (httpx.HTTPError, ValueError, TypeError):
            return "Processor observation unavailable"

    def _service_statuses(
        self, readiness: Any, counts: Any
    ) -> list[SystemServiceStatus]:
        leases = counts.get("service_leases", {}) if isinstance(counts, dict) else {}

        def item(service_id: str, label: str, ready: bool | None, action: str):
            state = (
                "ready"
                if ready is True
                else "unavailable"
                if ready is False
                else "unknown"
            )
            return SystemServiceStatus(
                service_id=service_id,
                label=label,
                state=state,
                reason_code=f"{service_id}_{state}",
                message="Ready" if state == "ready" else action,
            )

        return [
            item("web", "Web application", True, "Restart the web application."),
            item("api", "API", True, "Restart the API."),
            item(
                "database",
                "Database",
                getattr(readiness, "database", None),
                "Start PostgreSQL and check the protected database configuration.",
            ),
            item(
                "storage",
                "Document storage",
                getattr(readiness, "object_storage_bucket", None),
                "Start RustFS and verify the originals bucket.",
            ),
            item(
                "ollama",
                "Ollama",
                getattr(readiness, "ollama", None),
                "Start Ollama and verify the required models.",
            ),
            item(
                "inference",
                "Inference",
                getattr(readiness, "generation_model", None),
                "Start the inference coordinator and verify its fixed profiles.",
            ),
            item(
                "ocr",
                "OCR",
                getattr(readiness, "ocr_configured", None),
                "Verify the packaged CPU OCR runtime.",
            ),
            item(
                "ingestion",
                "Document processing",
                _lease(leases, "ingestion_worker"),
                "Start the ingestion worker.",
            ),
            item(
                "deletion",
                "Deletion worker",
                _lease(leases, "deletion_worker"),
                "Start the deletion worker.",
            ),
        ]

    def _capability(
        self,
        profile: RegistryProfile,
        evidence: SystemValidationEvidence | None,
        readiness: Any,
        effective_ids: dict[str, str],
    ) -> SystemCapabilityProfile:
        configured = effective_ids.get(profile.function) == profile.profile_id
        installed = {
            "generation": getattr(readiness, "generation_model", False),
            "embedding": getattr(readiness, "embedding_model", False),
            "reranking": configured,
            "ocr": (
                configured
                and getattr(readiness, "ocr_configured", False)
                and self._ocr is not None
            ),
        }[profile.function]
        local_state = (
            evidence.state
            if evidence is not None
            else ("installed" if installed else "package_available")
        )
        selectable = (
            profile.release_support_class == "release_qualified"
            and local_state == "locally_validated"
        )
        if selectable:
            reason = "Release-qualified and locally validated on this machine."
        elif evidence is not None and evidence.state == "failed":
            reason = (
                "The last fixed local validation failed. Check the service status, "
                "then run validation again."
            )
        elif installed:
            reason = (
                "Installed, but not qualified here. Run the fixed local validation."
            )
        else:
            reason = (
                "The required runtime is unavailable. Restore it, then run validation."
            )
        return SystemCapabilityProfile(
            profile_id=profile.profile_id,
            profile_revision=1,
            function=profile.function,
            release_support_class=profile.release_support_class,
            local_validation_state=local_state,
            engine=profile.engine,
            model_identity=profile.model_identity,
            accelerator_vendor=profile.accelerator_vendor,
            minimum_ram_gib=profile.minimum_ram_gib,
            minimum_vram_gib=profile.minimum_vram_gib,
            impact_class=profile.impact_class,
            effective=configured,
            selectable=selectable,
            reason=reason,
            evidence=evidence,
        )


def _default_registry_path() -> Path:
    return (
        Path(__file__).resolve().parents[4] / "ops/windows/v8a/capability-profiles.json"
    )


def _load_registry(path: Path) -> RegistryDocument:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        document = RegistryDocument.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemUnavailable("signed capability registry is unavailable") from exc
    by_function = {
        name: [profile for profile in document.profiles if profile.function == name]
        for name in _PROFILE_IDS
    }
    non_ocr_ids = {
        profile.profile_id
        for name in ("generation", "embedding", "reranking")
        for profile in by_function[name]
    }
    ocr_devices: set[str] = set()
    ocr_valid = bool(by_function["ocr"])
    for profile in by_function["ocr"]:
        device = profile.runtime_device
        if profile.accelerator_vendor == "cpu":
            ocr_valid = ocr_valid and device in (None, "cpu")
            device = "cpu"
        else:
            ocr_valid = ocr_valid and bool(
                device and re.fullmatch(r"gpu:[0-9]+", device)
            )
        if device in ocr_devices:
            ocr_valid = False
        if device is not None:
            ocr_devices.add(device)
    if (
        document.schema_version != 1
        or document.catalog_id != "local-rag-v8a-baseline"
        or non_ocr_ids != {
            _PROFILE_IDS["generation"],
            _PROFILE_IDS["embedding"],
            _PROFILE_IDS["reranking"],
        }
        or len(non_ocr_ids) != 3
        or len(by_function["generation"]) != 1
        or len(by_function["embedding"]) != 1
        or len(by_function["reranking"]) != 1
        or len(document.profiles) != 3 + len(by_function["ocr"])
        or _PROFILE_IDS["ocr"] not in {
            profile.profile_id for profile in by_function["ocr"]
        }
        or not ocr_valid
    ):
        raise SystemUnavailable("signed capability registry contract is invalid")
    return document


def _require_admin(actor: ActorContext) -> None:
    if actor.role is not ActorRole.ADMIN:
        raise SystemCapabilityDenied


def _raise_system_database_error(exc: SQLAlchemyError) -> None:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate in {"28000", "42501"}:
        raise SystemCapabilityDenied from exc
    if sqlstate in {"40001", "23505"}:
        raise SystemConflict("a matching System operation is already running") from exc
    if sqlstate == "22023":
        raise ValueError("invalid bounded System operation") from exc
    raise SystemUnavailable("System database functions are unavailable") from exc


def _bounded_metrics(value: object) -> dict[str, str | int | float | None]:
    if not isinstance(value, dict):
        return {}
    allowed: dict[str, str | int | float | None] = {}
    for key, item in value.items():
        if (
            isinstance(key, str)
            and re.fullmatch(r"[a-z0-9_]{1,64}", key)
            and (item is None or isinstance(item, str | int | float))
            and not isinstance(item, bool)
        ):
            allowed[key] = item[:256] if isinstance(item, str) else item
    return allowed


def _disk_usage(path: Path) -> dict[str, int]:
    try:
        usage = shutil.disk_usage(path.resolve())
        return {"total_bytes": usage.total, "free_bytes": usage.free}
    except OSError:
        return {"total_bytes": 0, "free_bytes": 0}


def _system_memory_bytes() -> int:
    if os.name == "nt":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_physical)
        except (AttributeError, OSError):
            return 0
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return 0


def _safe_count(group: object, key: str) -> int:
    if not isinstance(group, dict):
        return 0
    value = group.get(key, 0)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _lease(leases: object, key: str) -> bool | None:
    if not isinstance(leases, dict) or key not in leases:
        return None
    value = leases[key]
    return value if isinstance(value, bool) else None
