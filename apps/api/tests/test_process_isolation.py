import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.dependencies import ApplicationContainer
from app.processes.deletion_worker import DeletionWorkerContainer
from app.processes.ingestion_worker import IngestionWorkerContainer
from app.processes.settings import (
    CoordinatorProcessSettings,
    OcrProcessSettings,
    WorkerProcessSettings,
)
from app.worker import ControlledServiceLease


def test_worker_settings_expose_only_worker_database_credential() -> None:
    settings = WorkerProcessSettings(
        _env_file=None,
        worker_database_url=(
            "postgresql+psycopg://rag_worker:secret@127.0.0.1:5432/rag"
        ),
        object_storage_access_key_id="worker-access",
        object_storage_secret_access_key="worker-secret",
        coordinator_service_token="c" * 32,
        ocr_service_token="o" * 32,
        database_url="postgresql+psycopg://rag_api:secret@127.0.0.1:5432/rag",
        maintenance_database_url=(
            "postgresql+psycopg://rag_maintenance:secret@127.0.0.1:5432/rag"
        ),
        migration_database_url=(
            "postgresql+psycopg://rag_migrator:secret@127.0.0.1:5432/rag"
        ),
    )

    assert settings.worker_database_url.startswith("postgresql+psycopg://rag_worker:")
    assert not hasattr(settings, "database_url")
    assert not hasattr(settings, "maintenance_database_url")
    assert not hasattr(settings, "migration_database_url")
    assert not hasattr(settings, "enable_v6_adaptive_parsing")


def test_worker_settings_fail_closed_without_worker_secret() -> None:
    with pytest.raises(ValidationError):
        WorkerProcessSettings(
            _env_file=None,
            object_storage_access_key_id="worker-access",
            object_storage_secret_access_key="worker-secret",
        )


def test_runtime_offline_flags_parse_real_environment_strings() -> None:
    coordinator = CoordinatorProcessSettings(
        _env_file=None,
        coordinator_service_token="c" * 32,
        coordinator_ownership_path="C:/runtime/coordinator.owner",
        reranker_model_path="C:/models/bge",
        hf_home="C:/runtime/coordinator/hf",
        hf_hub_cache="C:/runtime/coordinator/hf/hub",
        transformers_cache="C:/runtime/coordinator/transformers",
        xdg_cache_home="C:/runtime/coordinator/cache",
        hf_hub_offline="1",
        transformers_offline="1",
        tokenizers_parallelism="false",
    )
    ocr = OcrProcessSettings(
        _env_file=None,
        ocr_service_token="o" * 32,
        ocr_ownership_path="C:/runtime/ocr.owner",
        ocr_workspace_root="C:/runtime/ocr/work",
        ocr_python_executable="C:/runtime/ocr/python.exe",
        ocr_model_asset_root="C:/models/ocr",
        paddle_home="C:/models/ocr",
        hf_home="C:/runtime/ocr/hf",
        hf_hub_cache="C:/runtime/ocr/hf/hub",
        transformers_cache="C:/runtime/ocr/transformers",
        xdg_cache_home="C:/runtime/ocr/cache",
        hf_hub_offline="1",
        transformers_offline="1",
    )

    assert coordinator.hf_hub_offline is True
    assert coordinator.transformers_offline is True
    assert coordinator.tokenizers_parallelism is False
    assert ocr.hf_hub_offline is True
    assert ocr.transformers_offline is True
    assert ocr.ocr_process_count == 1


def test_ocr_process_settings_accept_cpu_or_numbered_gpu_only() -> None:
    common = {
        "ocr_service_token": "o" * 32,
        "ocr_ownership_path": "C:/runtime/ocr.owner",
        "ocr_workspace_root": "C:/runtime/ocr/work",
        "ocr_python_executable": "C:/runtime/ocr/python.exe",
        "ocr_model_asset_root": "C:/models/ocr",
        "paddle_home": "C:/models/ocr",
        "hf_home": "C:/runtime/ocr/hf",
        "hf_hub_cache": "C:/runtime/ocr/hf/hub",
        "transformers_cache": "C:/runtime/ocr/transformers",
        "xdg_cache_home": "C:/runtime/ocr/cache",
        "hf_hub_offline": "1",
        "transformers_offline": "1",
    }
    cpu = OcrProcessSettings(_env_file=None, ocr_device="cpu", **common)
    gpu = OcrProcessSettings(_env_file=None, ocr_device="gpu:0", **common)
    assert cpu.ocr_device == "cpu"
    assert gpu.ocr_device == "gpu:0"
    with pytest.raises(ValidationError, match="numbered GPU"):
        OcrProcessSettings(_env_file=None, ocr_device="auto", **common)


@pytest.mark.parametrize(
    ("settings_class", "arguments"),
    [
        (
            CoordinatorProcessSettings,
            {
                "coordinator_service_token": "c" * 32,
                "coordinator_ownership_path": "C:/runtime/coordinator.owner",
                "reranker_model_path": "C:/models/bge",
                "hf_home": "C:/runtime/coordinator/hf",
                "hf_hub_cache": "C:/runtime/coordinator/hf/hub",
                "transformers_cache": "C:/runtime/coordinator/transformers",
                "xdg_cache_home": "C:/runtime/coordinator/cache",
                "hf_hub_offline": "0",
                "transformers_offline": "1",
                "tokenizers_parallelism": "false",
            },
        ),
        (
            OcrProcessSettings,
            {
                "ocr_service_token": "o" * 32,
                "ocr_ownership_path": "C:/runtime/ocr.owner",
                "ocr_workspace_root": "C:/runtime/ocr/work",
                "ocr_python_executable": "C:/runtime/ocr/python.exe",
                "ocr_model_asset_root": "C:/models/ocr",
                "paddle_home": "C:/models/ocr",
                "hf_home": "C:/runtime/ocr/hf",
                "hf_hub_cache": "C:/runtime/ocr/hf/hub",
                "transformers_cache": "C:/runtime/ocr/transformers",
                "xdg_cache_home": "C:/runtime/ocr/cache",
                "hf_hub_offline": "0",
                "transformers_offline": "1",
            },
        ),
    ],
)
def test_runtime_model_loading_rejects_online_environment(
    settings_class: type, arguments: dict[str, str]
) -> None:
    with pytest.raises(ValidationError, match="offline"):
        settings_class(_env_file=None, **arguments)


def test_api_container_has_no_worker_or_deletion_worker_fields() -> None:
    fields = set(ApplicationContainer.__dataclass_fields__)
    assert "worker" not in fields
    assert "deletion_worker" not in fields


def test_real_api_container_wires_database_admin_gateway() -> None:
    source = inspect.getsource(ApplicationContainer.from_settings)
    assert "DatabaseAdminGateway(database.session_factory)" in source
    assert "UnavailableAdminGateway" not in source


def test_production_api_fails_closed_without_coordinator_credential() -> None:
    with pytest.raises(ValidationError, match="coordinator service token"):
        Settings(
            _env_file=None,
            environment="production",
            cors_origins=[],
            csrf_signing_secret="x" * 32,
        )


def test_production_api_wires_typed_coordinator_clients() -> None:
    source = inspect.getsource(ApplicationContainer.from_settings)
    assert "CoordinatorEmbeddingClient(coordinator)" in source
    assert "CoordinatorReranker(coordinator)" in source
    assert "CoordinatorGenerationClient(" in source


def test_worker_ownership_fails_closed_on_database_role_identity() -> None:
    source = inspect.getsource(ControlledServiceLease.start)
    assert "v4_runtime_identity('rag_worker')" in source
    assert "worker database identity validation failed" in source


def test_ingestion_worker_module_invokes_its_entrypoint() -> None:
    source = (
        Path(__file__).parents[1] / "app" / "processes" / "ingestion_worker.py"
    ).read_text(encoding="utf-8")

    assert (
        'if __name__ == "__main__":\n'
        '    run_with_startup_diagnostics("ingestion", main)'
    ) in source


@pytest.mark.parametrize(
    "container",
    [IngestionWorkerContainer, DeletionWorkerContainer],
)
def test_worker_process_exits_when_lease_or_worker_fails(container: type) -> None:
    source = inspect.getsource(container.run)
    assert "self.ownership.wait()" in source
    assert "self.worker.wait()" in source
    assert "asyncio.FIRST_COMPLETED" in source
