import asyncio
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pypdf import PdfReader

import app.services.system as system_service_module
from app.config import Settings
from app.domain import ParseMethod
from app.schemas.system import (
    PersonalBackupOperation,
    RuntimeConfigurationChange,
    RuntimeConfigurationSelection,
    SystemOperation,
    SystemValidationEvidence,
)
from app.security.actor import ActorContext, ActorRole
from app.services.ollama_generation import GenerationChunk
from app.services.parsing.types import ParsedOcrBatch, ParsedPage
from app.services.system import SystemCapabilityDenied, SystemService, _load_registry
from app.system.fixtures import SYSTEM_OCR_FIXTURE_SHA256, system_ocr_fixture


def actor(role: ActorRole = ActorRole.ADMIN) -> ActorContext:
    return ActorContext(uuid4(), role, 1, 1, uuid4())


def test_registry_accepts_multiple_bounded_ocr_device_profiles(tmp_path: Path) -> None:
    source = (
        Path(__file__).parents[3]
        / "ops"
        / "windows"
        / "v8a"
        / "capability-profiles.json"
    )
    catalog = json.loads(source.read_text(encoding="utf-8"))
    cpu = next(item for item in catalog["profiles"] if item["function"] == "ocr")
    gpu = dict(cpu)
    gpu.update(
        {
            "profile_id": "ocr.paddleocr-vl-1.6.amd-gpu0.windows-x64",
            "accelerator_vendor": "amd",
            "runtime_device": "gpu:0",
            "minimum_vram_gib": 12,
        }
    )
    catalog["profiles"].append(gpu)
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")

    registry = _load_registry(path)

    assert [
        profile.runtime_device
        for profile in registry.profiles
        if profile.function == "ocr"
    ] == [None, "gpu:0"]


class Gateway:
    def __init__(self) -> None:
        self.evidence_values: dict[str, SystemValidationEvidence] = {}
        self.items: list[SystemOperation] = []
        self.changes: list[RuntimeConfigurationChange] = []
        self.grant_values: tuple[UUID, str, str] | None = None
        self.backup: PersonalBackupOperation | None = None
        self.configuration_values: dict[str, object] = {
            "effective_revision": "v8d-baseline-0001",
            "desired_revision": "v8d-baseline-0001",
            "state": "effective",
            "generation_profile_id": "generation.qwen3-8b.ollama.windows-x64",
            "reranker_profile_id": "reranking.bge-v2-m3.cpu.windows-x64",
            "ocr_mode": "auto",
            "ocr_profile_id": "ocr.paddleocr-vl-1.6.cpu.windows-x64",
            "ocr_preset_id": "balanced",
            "impact_digest": None,
            "operation_class": None,
            "prior_revision": None,
            "actor_user_id": None,
            "proposed_at": None,
            "reason_code": None,
            "backup_verified": False,
            "backup_verified_at": None,
        }
        self.effective_values: dict[str, object] = {
            key: self.configuration_values[key]
            for key in (
                "effective_revision",
                "generation_profile_id",
                "reranker_profile_id",
                "ocr_mode",
                "ocr_profile_id",
                "ocr_preset_id",
            )
        }

    async def counts(self, _token: str):
        return {
            "documents": {"ready": 3, "processing": 1, "failed": 0},
            "jobs": {"active": 1, "queued": 2},
            "service_leases": {"ingestion_worker": True, "deletion_worker": False},
        }

    async def evidence(self, _token: str):
        return self.evidence_values

    async def operations(self, _token: str, *, limit: int):
        return self.items[:limit]

    async def start(self, _token: str, **values: str) -> UUID:
        identifier = uuid4()
        self.items.insert(
            0,
            SystemOperation(
                operation_id=identifier,
                operation_type=values["operation_type"],
                profile_id=values["profile_id"],
                state="running",
                stage="preflight",
                reason_code=None,
                metrics={},
                created_at=datetime.now(UTC),
                finished_at=None,
            ),
        )
        return identifier

    async def complete(self, *, operation_id: UUID, succeeded: bool, **values: object):
        current = next(item for item in self.items if item.operation_id == operation_id)
        replacement = current.model_copy(
            update={
                "state": "effective" if succeeded else "failed",
                "stage": "effective" if succeeded else "failed",
                "reason_code": values["reason_code"],
                "metrics": values["metrics"],
                "finished_at": datetime.now(UTC),
            }
        )
        self.items[self.items.index(current)] = replacement

    async def advance(self, *, operation_id: UUID, stage: str, **_values: object):
        current = next(item for item in self.items if item.operation_id == operation_id)
        self.items[self.items.index(current)] = current.model_copy(
            update={"stage": stage}
        )

    async def configuration(self, _token: str):
        return self.configuration_values

    async def effective_configuration(self, _token: str):
        return self.effective_values

    async def preview_configuration(
        self,
        _token: str,
        _selection: RuntimeConfigurationSelection,
        **_values: str,
    ):
        return {
            "preview_id": UUID("5153f71b-a633-4a4a-9b9c-7e47c80e24fc"),
            "impact_digest": "a" * 64,
            "expires_at": datetime.now(UTC),
        }

    async def issue_reauthentication_grant(
        self,
        _token: str,
        *,
        preview_id: UUID,
        impact_digest: str,
        token_hash: str,
    ):
        self.grant_values = (preview_id, impact_digest, token_hash)
        return datetime.now(UTC)

    async def apply_configuration(self, _token: str, **_values: object) -> UUID:
        change_id = uuid4()
        self.changes = [
            RuntimeConfigurationChange(
                change_id=change_id,
                actor_user_id=uuid4(),
                prior_revision="v8d-baseline-0001",
                desired_revision="v8d-test-0002",
                impact_digest="a" * 64,
                operation_class="restart_scoped",
                state="pending",
                stage="queued",
                reason_code=None,
                created_at=datetime.now(UTC),
                finished_at=None,
            )
        ]
        return change_id

    async def configuration_changes(self, _token: str, *, limit: int):
        return self.changes[:limit]

    async def fail_configuration_delivery(self, _token: str, **_values: object):
        current = self.changes[0]
        self.changes[0] = current.model_copy(
            update={
                "state": "failed",
                "stage": "failed",
                "reason_code": "controller_unavailable",
                "finished_at": datetime.now(UTC),
            }
        )

    async def start_personal_backup(self, _token: str, **_values: object) -> UUID:
        identifier = uuid4()
        self.backup = PersonalBackupOperation(
            backup_run_id=identifier,
            state="pending",
            stage="queued",
            reason_code=None,
            created_at=datetime.now(UTC),
            finished_at=None,
            restore_verified=False,
            manifest_sha256=None,
        )
        return identifier

    async def personal_backup_status(self, _token: str):
        return self.backup

    async def personal_backup_history(self, _token: str, **_values: object):
        return [self.backup] if self.backup is not None else []

    async def fail_personal_backup_delivery(self, _token: str, **_values: object):
        assert self.backup is not None
        self.backup = self.backup.model_copy(
            update={
                "state": "failed",
                "stage": "failed",
                "reason_code": "controller_unavailable",
                "finished_at": datetime.now(UTC),
            }
        )


class Readiness:
    async def check(self):
        return SimpleNamespace(
            database=True,
            object_storage_bucket=False,
            ollama=True,
            generation_model=True,
            embedding_model=True,
            ocr_configured=True,
        )


class Generator:
    async def stream(self, _prompt: str, *, think: bool):
        assert think is False
        yield GenerationChunk("answer", "SYSTEM_OK")


class Embedder:
    async def embed(self, texts: list[str]):
        assert len(texts) == 1
        return [[0.0] * 1024]


class Reranker:
    async def score(self, _query: str, passages: list[str]):
        assert len(passages) == 2
        return [0.9, 0.1]


class Ocr:
    async def parse_pages(self, *_args: object, **_kwargs: object):
        return ParsedOcrBatch(
            {1: ParsedPage(1, "SYSTEM OCR 17", ParseMethod.OCR, ())},
            128,
            duration_seconds=1.25,
            peak_working_set_bytes=4096,
        )


def settings(tmp_path: Path) -> Settings:
    return Settings(data_root=tmp_path.resolve(), ocr_service_token="o" * 32)


def service(
    tmp_path: Path,
    gateway: Gateway | None = None,
    *,
    authentication: object | None = None,
    controller: object | None = None,
    personal: bool = False,
) -> tuple[SystemService, Gateway]:
    selected = gateway or Gateway()
    result = SystemService(
        selected,
        (
            Settings(
                product_profile="personal",
                canonical_origin="http://127.0.0.1:3000",
                canonical_host="127.0.0.1",
                cors_origins=[],
                data_root=tmp_path.resolve(),
                ocr_service_token="o" * 32,
            )
            if personal
            else settings(tmp_path)
        ),
        Readiness(),
        Generator(),
        Embedder(),
        Reranker(),
        ocr=Ocr(),
        authentication=authentication,
        controller=controller,
    )

    async def processor():
        return "Ollama reports accelerator memory in use"

    result._ollama_processor = processor  # type: ignore[method-assign]
    return result, selected


def test_capabilities_separate_release_support_from_local_validation(
    tmp_path: Path,
) -> None:
    system, gateway = service(tmp_path)
    response = asyncio.run(system.capabilities(actor(), "session"))

    assert {profile.release_support_class for profile in response.profiles} == {
        "release_qualified"
    }
    assert {profile.local_validation_state for profile in response.profiles} == {
        "installed"
    }
    assert all(not profile.selectable for profile in response.profiles)

    generation = response.profiles[0]
    gateway.evidence_values[generation.profile_id] = SystemValidationEvidence(
        state="locally_validated",
        reason_code="generation_fixture_passed",
        fixture_id="generation.exact-response-v1",
        evidence_at=datetime.now(UTC),
        metrics={},
    )
    updated = asyncio.run(system.capabilities(actor(), "session"))
    assert updated.profiles[0].selectable is True


def test_overview_survives_partial_outage_and_is_admin_only(tmp_path: Path) -> None:
    system, _gateway = service(tmp_path)
    response = asyncio.run(system.overview(actor(), "session"))
    assert response.overall_state == "unavailable"
    assert any(
        item.service_id == "storage" and item.state == "unavailable"
        for item in response.services
    )
    assert response.documents.ready == 3
    with pytest.raises(SystemCapabilityDenied):
        asyncio.run(system.overview(actor(ActorRole.MEMBER), "session"))


def test_configuration_reports_exact_effective_runtime_identities(
    tmp_path: Path,
) -> None:
    system, _gateway = service(tmp_path)
    current = asyncio.run(system.configuration(actor(), "session"))

    assert current.generation_model == "qwen3:8b"
    assert current.embedding_model == "qwen3-embedding:0.6b"
    assert current.reranker_model == "BAAI/bge-reranker-v2-m3"
    assert current.parser_identity == "paddleocr-vl-v1.6-adaptive-v2"
    assert current.ocr_device == "cpu"


def test_configuration_does_not_label_pending_desired_ocr_as_effective(
    tmp_path: Path,
) -> None:
    gateway = Gateway()
    gateway.configuration_values.update(
        {
            "desired_revision": "v8d-pending-0002",
            "state": "pending",
            "ocr_mode": "explicit",
            "ocr_profile_id": "ocr.uninstalled.gpu.windows-x64",
            "ocr_preset_id": "manual-t1-p2",
        }
    )
    system, _gateway = service(tmp_path, gateway)

    current = asyncio.run(system.configuration(actor(), "session"))

    assert current.state == "pending"
    assert current.desired_revision == "v8d-pending-0002"
    assert current.ocr_profile_id == "ocr.paddleocr-vl-1.6.cpu.windows-x64"
    assert current.ocr_mode == "auto"
    assert current.ocr_cpu_threads == 10
    assert current.ocr_process_count == 1


def test_fixed_validation_operations_and_sanitized_archive(tmp_path: Path) -> None:
    system, _gateway = service(tmp_path)
    profile_ids = asyncio.run(system.capabilities(actor(), "session")).profiles
    for profile in profile_ids:
        operation = asyncio.run(
            system.run_profile_operation(
                actor(), "session", profile_id=profile.profile_id, benchmark=False
            )
        )
        assert operation.state == "effective"
    ocr_profile = next(profile for profile in profile_ids if profile.function == "ocr")
    benchmark = asyncio.run(
        system.run_profile_operation(
            actor(), "session", profile_id=ocr_profile.profile_id, benchmark=True
        )
    )
    assert benchmark.metrics["fixture_sha256"] == SYSTEM_OCR_FIXTURE_SHA256
    assert benchmark.metrics["duration_seconds"] == 1.25

    archive = asyncio.run(
        system.diagnostics_archive(actor(), "SUPER_PRIVATE_SESSION_TOKEN")
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert set(bundle.namelist()) == {
            "versions.json",
            "readiness.json",
            "capabilities.json",
            "configuration.json",
            "operations.json",
            "manifest.json",
        }
        combined = b"".join(bundle.read(name) for name in bundle.namelist())
    assert b"SUPER_PRIVATE_SESSION_TOKEN" not in combined
    assert b"SYSTEM OCR 17" not in combined


def test_inactive_gpu_ocr_profile_cannot_be_validated_by_the_cpu_service(
    tmp_path: Path,
) -> None:
    system, gateway = service(tmp_path)
    cpu = next(
        profile for profile in system._registry.profiles if profile.function == "ocr"
    )
    gpu = cpu.model_copy(
        update={
            "profile_id": "ocr.paddleocr-vl-1.6.gpu.windows-x64",
            "accelerator_vendor": "nvidia",
            "runtime_device": "gpu:0",
        }
    )
    system._registry = system._registry.model_copy(
        update={"profiles": [*system._registry.profiles, gpu]}
    )

    with pytest.raises(ValueError, match="inactive OCR profiles"):
        asyncio.run(
            system.run_profile_operation(
                actor(), "session", profile_id=gpu.profile_id, benchmark=False
            )
        )

    assert gateway.items == []


class Authentication:
    def __init__(self) -> None:
        self.values: tuple[str, str] | None = None

    async def verify_current_password(self, session_token: str, password: str) -> None:
        self.values = (session_token, password)


class Controller:
    def __init__(self) -> None:
        self.change_id: UUID | None = None
        self.backup_id: UUID | None = None

    async def apply_configuration(self, change_id: UUID, _nonce: str) -> None:
        self.change_id = change_id

    async def create_backup(self, backup_run_id: UUID, _nonce: str) -> None:
        self.backup_id = backup_run_id


def test_configuration_preview_reauthentication_and_apply_are_digest_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_service_module.os, "cpu_count", lambda: 16)
    monkeypatch.setattr(
        system_service_module, "_system_memory_bytes", lambda: 32 * 1024**3
    )
    authentication = Authentication()
    controller = Controller()
    system, gateway = service(
        tmp_path, authentication=authentication, controller=controller
    )
    selection = RuntimeConfigurationSelection(
        base_revision="v8d-baseline-0001",
        generation_profile_id="generation.qwen3-8b.ollama.windows-x64",
        reranker_profile_id="reranking.bge-v2-m3.cpu.windows-x64",
        ocr_mode="explicit",
        ocr_profile_id="ocr.paddleocr-vl-1.6.cpu.windows-x64",
        ocr_cpu_threads=8,
        ocr_process_count=2,
    )
    admin = actor()
    preview = asyncio.run(system.preview_configuration(admin, "session", selection))
    assert preview.affected_services == ["ocr"]
    assert preview.waits_for == ["active_ocr_boundary"]
    grant = asyncio.run(
        system.reauthenticate_configuration(
            admin,
            "session",
            preview_id=preview.preview_id,
            password="correct password",
            impact_digest=preview.impact_digest,
        )
    )
    assert authentication.values == ("session", "correct password")
    assert gateway.grant_values is not None
    assert gateway.grant_values[:2] == (preview.preview_id, preview.impact_digest)
    result = asyncio.run(
        system.apply_configuration(
            admin,
            "session",
            preview_id=preview.preview_id,
            impact_digest=preview.impact_digest,
            grant_token=grant.grant_token,
        )
    )
    assert result.change.state == "pending"
    assert controller.change_id == result.change.change_id


def test_system_ocr_fixture_is_deterministic_image_only_pdf() -> None:
    first = system_ocr_fixture()
    second = system_ocr_fixture()
    assert first == second
    reader = PdfReader(io.BytesIO(first))
    assert len(reader.pages) == 1
    assert reader.pages[0].extract_text() in {"", None}


def test_personal_backup_starts_only_through_controller_and_returns_progress(
    tmp_path: Path,
) -> None:
    controller = Controller()
    system, gateway = service(tmp_path, controller=controller, personal=True)
    result = asyncio.run(system.start_personal_backup(actor(), "session"))
    assert result.operation.state == "pending"
    assert controller.backup_id == result.operation.backup_run_id
    status = asyncio.run(system.personal_backup_status(actor(), "session"))
    assert status.operation == gateway.backup
    history = asyncio.run(system.personal_backup_history(actor(), "session"))
    assert history.retention_mode == "keep_all"
    assert history.automatic_deletion is False
    assert history.operations == [gateway.backup]
