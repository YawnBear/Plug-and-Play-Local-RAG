import asyncio
import hashlib
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories import DocumentRepository
from app.security.actor import ActorContext
from app.services.identity import document_uuid
from app.services.library import normalize_library_name
from app.services.object_lifecycle import (
    canonical_object_key,
    validate_remote_metadata,
)
from app.services.object_storage import ObjectStore
from app.versions import (
    EMBEDDING_VERSION,
    active_chunking_version,
    active_parser_version,
)


class UploadValidationError(ValueError):
    pass


class UploadTooLargeError(UploadValidationError):
    pass


class DeletionPendingError(RuntimeError):
    pass


class DocumentDeletionError(RuntimeError):
    pass


class DocumentUploadParentNotFound(RuntimeError):
    pass


class UploadReservationActive(RuntimeError):
    pass


class DuplicateUploadRequiresAccess(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StagedUpload:
    path: Path
    sha256: str
    object_key: str
    byte_size: int
    filename: str
    display_name: str
    name_key: str
    document_id: uuid.UUID
    job_id: uuid.UUID
    node_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class UploadPreflight:
    status: str
    reservation_id: uuid.UUID | None
    document_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    node_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    display_name: str | None = None
    logical_path: str | None = None
    duplicate_of: uuid.UUID | None = None
    location_reused: bool = False
    parser_version: str | None = None
    chunking_version: str | None = None
    embedding_version: str | None = None

    @property
    def upload_required(self) -> bool:
        return self.status == "upload_required"


@dataclass(frozen=True, slots=True)
class UploadResult:
    document_id: uuid.UUID
    job_id: uuid.UUID
    status: str
    duplicate_of: uuid.UUID | None
    node_id: uuid.UUID
    parent_id: uuid.UUID | None
    display_name: str
    logical_path: str
    location_reused: bool


class DocumentService:
    def __init__(
        self,
        settings: Settings,
        object_store: ObjectStore,
    ) -> None:
        self._settings = settings
        self._object_store = object_store

    async def stage(self, upload: UploadFile) -> StagedUpload:
        if upload.content_type not in self._settings.accepted_mime_types:
            await upload.close()
            raise UploadValidationError("only application/pdf uploads are accepted")
        filename = Path(upload.filename or "").name
        if not filename:
            await upload.close()
            raise UploadValidationError("upload filename is required")
        display_name, name_key = normalize_library_name(filename)
        self._settings.upload_work_path.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        first_bytes = b""
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=self._settings.upload_work_path,
                prefix="upload-",
                suffix=".tmp",
            ) as output:
                path = Path(output.name)
                while content := await upload.read(1024 * 1024):
                    if not first_bytes:
                        first_bytes = content[:5]
                    size += len(content)
                    if size > self._settings.maximum_upload_bytes:
                        raise UploadTooLargeError(
                            "upload exceeds "
                            f"{self._settings.maximum_upload_bytes} bytes"
                        )
                    digest.update(content)
                    output.write(content)
            if first_bytes != b"%PDF-":
                raise UploadValidationError("file content is not a PDF")
            checksum = digest.hexdigest()
            document_id = document_uuid(checksum)
            return StagedUpload(
                path=path,
                sha256=checksum,
                object_key=canonical_object_key(checksum),
                byte_size=size,
                filename=filename,
                display_name=display_name,
                name_key=name_key,
                document_id=document_id,
                job_id=uuid.uuid4(),
                node_id=uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"urn:local-rag:library-node:v1:{document_id}",
                ),
            )
        except BaseException:
            if path is not None:
                await asyncio.to_thread(path.unlink, missing_ok=True)
            raise
        finally:
            await upload.close()

    async def cleanup(self, staged: StagedUpload) -> None:
        await asyncio.to_thread(staged.path.unlink, missing_ok=True)

    async def preflight(
        self,
        actor: ActorContext,
        session: AsyncSession,
        staged: StagedUpload,
        folder_id: uuid.UUID | None,
        team_ids: tuple[uuid.UUID, ...] = (),
    ) -> UploadPreflight:
        version = (
            await session.execute(
                text(
                    "SELECT parser_version, chunking_version, embedding_version "
                    "FROM v10_effective_ingestion_version()"
                )
            )
        ).one()
        metadata = self._metadata(
            staged,
            folder_id,
            versions=(
                version.parser_version,
                version.chunking_version,
                version.embedding_version,
            ),
        )
        row = (
            await session.execute(
                text(
                    "SELECT * "
                    "FROM v4_admin_upload_preflight("
                    ":sha256, :object_key, :filename, :display_name, :name_key, "
                    ":mime_type, :byte_size, :parser_version, :chunking_version, "
                    ":embedding_version, :parent_id, :team_ids)"
                ),
                {
                    **metadata,
                    "team_ids": list(team_ids),
                },
            )
        ).one()
        if row.result_status == "duplicate_forbidden":
            raise DuplicateUploadRequiresAccess
        if row.result_status == "pending_deletion":
            raise DeletionPendingError(
                "an identical document is pending object deletion; retry later"
            )
        if row.result_status == "parent_not_found":
            raise DocumentUploadParentNotFound("library folder not found")
        if row.result_status == "reservation_active":
            raise UploadReservationActive("an identical upload is already in progress")
        if row.result_status not in {"duplicate", "upload_required"}:
            raise RuntimeError("unexpected upload preflight result")
        return UploadPreflight(
            status=row.result_status,
            reservation_id=row.reservation_id,
            document_id=row.document_id,
            job_id=row.job_id,
            node_id=row.node_id,
            parent_id=row.parent_id,
            display_name=row.display_name,
            logical_path=row.logical_path,
            duplicate_of=row.duplicate_of,
            location_reused=bool(row.location_reused),
            parser_version=version.parser_version,
            chunking_version=version.chunking_version,
            embedding_version=version.embedding_version,
        )

    async def put(self, staged: StagedUpload) -> bool:
        created = await self._object_store.put_if_absent(
            staged.object_key,
            staged.path,
            sha256=staged.sha256,
            byte_size=staged.byte_size,
            content_type="application/pdf",
        )
        metadata = await self._object_store.head(staged.object_key)
        validate_remote_metadata(
            metadata,
            key=staged.object_key,
            sha256=staged.sha256,
            byte_size=staged.byte_size,
        )
        return created

    async def commit(
        self,
        actor: ActorContext,
        session: AsyncSession,
        staged: StagedUpload,
        folder_id: uuid.UUID | None,
        reservation_id: uuid.UUID,
        team_ids: tuple[uuid.UUID, ...] = (),
        preflight: UploadPreflight | None = None,
    ) -> UploadResult:
        versions = None
        if preflight is not None:
            parser_version = preflight.parser_version
            chunking_version = preflight.chunking_version
            embedding_version = preflight.embedding_version
            if (
                parser_version is None
                or chunking_version is None
                or embedding_version is None
            ):
                raise UploadReservationActive("upload reservation has no version lock")
            versions = (
                parser_version,
                chunking_version,
                embedding_version,
            )
        parameters = {
            "reservation_id": reservation_id,
            "document_id": staged.document_id,
            "job_id": staged.job_id,
            "node_id": staged.node_id,
            **self._metadata(staged, folder_id, versions=versions),
            "team_ids": list(team_ids),
        }
        row = (
            await session.execute(
                text(
                    "SELECT * FROM v4_admin_commit_upload("
                    ":reservation_id, :document_id, :job_id, :node_id, "
                    ":sha256, :object_key, "
                    ":filename, :display_name, :name_key, :mime_type, "
                    ":byte_size, :parser_version, :chunking_version, "
                    ":embedding_version, :parent_id, :team_ids)"
                ),
                parameters,
            )
        ).one()
        if row.result_status == "duplicate_forbidden":
            raise DuplicateUploadRequiresAccess
        if row.result_status == "pending_deletion":
            raise DeletionPendingError(
                "an identical document is pending object deletion; retry later"
            )
        if row.result_status == "parent_not_found":
            raise DocumentUploadParentNotFound("library folder not found")
        if row.result_status == "invalid_reservation":
            raise UploadReservationActive("upload reservation is stale or invalid")
        if row.result_status not in {"created", "duplicate"}:
            raise RuntimeError("unexpected upload commit result")
        return UploadResult(
            document_id=row.document_id,
            job_id=row.job_id,
            status="queued" if row.result_status == "created" else "duplicate",
            duplicate_of=row.duplicate_of,
            node_id=row.node_id,
            parent_id=row.parent_id,
            display_name=row.display_name,
            logical_path=row.logical_path,
            location_reused=bool(row.location_reused),
        )

    @staticmethod
    def duplicate_result(preflight: UploadPreflight) -> UploadResult:
        if (
            preflight.status != "duplicate"
            or preflight.document_id is None
            or preflight.job_id is None
            or preflight.node_id is None
            or preflight.display_name is None
            or preflight.logical_path is None
        ):
            raise RuntimeError("duplicate preflight result is incomplete")
        return UploadResult(
            document_id=preflight.document_id,
            job_id=preflight.job_id,
            status="duplicate",
            duplicate_of=preflight.duplicate_of,
            node_id=preflight.node_id,
            parent_id=preflight.parent_id,
            display_name=preflight.display_name,
            logical_path=preflight.logical_path,
            location_reused=preflight.location_reused,
        )

    async def delete(
        self,
        actor: ActorContext,
        session: AsyncSession,
        document_id: uuid.UUID,
    ) -> bool:
        snapshot = await DocumentRepository(session).get(actor, document_id)
        if snapshot is None:
            return False
        try:
            expected_key = canonical_object_key(snapshot.sha256)
        except ValueError as exc:
            raise DocumentDeletionError("document has invalid object metadata") from exc
        if snapshot.object_key != expected_key or snapshot.byte_size <= 0:
            raise DocumentDeletionError("document has invalid object metadata")
        await session.execute(
            text("SELECT v4_admin_delete_document(:document_id, :deletion_id)"),
            {"document_id": document_id, "deletion_id": uuid.uuid4()},
        )
        return True

    def _metadata(
        self,
        staged: StagedUpload,
        folder_id: uuid.UUID | None,
        *,
        versions: tuple[str, str, str] | None = None,
    ) -> dict[str, object]:
        parser_version, chunking_version, embedding_version = versions or (
            active_parser_version(
                adaptive_page_routing=self._settings.enable_v6_adaptive_parsing
            ),
            active_chunking_version(
                visual_supplement_ocr=self._settings.enable_v6_adaptive_parsing
            ),
            EMBEDDING_VERSION,
        )
        return {
            "sha256": staged.sha256,
            "object_key": staged.object_key,
            "filename": staged.filename,
            "display_name": staged.display_name,
            "name_key": staged.name_key,
            "mime_type": "application/pdf",
            "byte_size": staged.byte_size,
            "parser_version": parser_version,
            "chunking_version": chunking_version,
            "embedding_version": embedding_version,
            "parent_id": folder_id,
        }
