from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import MaintenanceSettings, MigrationSettings, Settings
from app.versions import (
    ADAPTIVE_PARSER_VERSION,
    CHUNKING_VERSION,
    FRAGMENT_CHUNKING_VERSION,
    PARSER_VERSION,
    active_chunking_version,
    active_parser_version,
)


def test_frozen_model_and_retrieval_defaults() -> None:
    settings = Settings(
        object_storage_access_key_id="",
        object_storage_secret_access_key="",
    )

    assert settings.embedding_model == "qwen3-embedding:0.6b"
    assert settings.embedding_dimension == 1024
    assert settings.generation_model == "qwen3:8b"
    assert settings.retrieve_k == 20
    assert settings.context_k == 6
    assert settings.ingestion_worker_count == 1
    assert settings.ocr_cpu_threads == 10
    assert settings.enable_v6_adaptive_parsing is True
    assert settings.ocr_page_batch_size == 8
    assert settings.embedding_batch_size == 16
    assert settings.maximum_pdf_pages == 500
    assert settings.maximum_ocr_pages == 128
    assert str(settings.object_storage_endpoint_url) == "http://127.0.0.1:9000/"
    assert settings.object_storage_region == "us-east-1"
    assert settings.object_storage_bucket == "rag-originals"
    assert settings.object_storage_force_path_style is True
    assert settings.object_storage_use_tls is False
    assert settings.object_storage_access_key_id.get_secret_value() == ""
    assert settings.object_storage_secret_access_key.get_secret_value() == ""


def test_single_adaptive_flag_maps_to_distinct_stored_version_identity() -> None:
    assert active_parser_version(adaptive_page_routing=False) == PARSER_VERSION
    assert active_chunking_version(visual_supplement_ocr=False) == CHUNKING_VERSION
    assert active_parser_version(adaptive_page_routing=True) == ADAPTIVE_PARSER_VERSION
    assert (
        active_chunking_version(visual_supplement_ocr=True) == FRAGMENT_CHUNKING_VERSION
    )
    assert ADAPTIVE_PARSER_VERSION != PARSER_VERSION
    assert FRAGMENT_CHUNKING_VERSION != CHUNKING_VERSION


def test_rejects_nonpositive_ocr_cpu_threads() -> None:
    with pytest.raises(ValidationError, match="ocr_cpu_threads"):
        Settings(ocr_cpu_threads=0)


def test_system_ocr_client_accepts_only_optional_loopback_credentials() -> None:
    settings = Settings(ocr_service_token="")
    assert str(settings.ocr_service_base_url) == "http://127.0.0.1:8101/"
    with pytest.raises(ValidationError, match="loopback HTTP origin"):
        Settings(ocr_service_base_url="http://192.168.1.20:8101")
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(ocr_service_token="too-short")


def test_system_controller_accepts_only_optional_loopback_credentials() -> None:
    settings = Settings(controller_service_token="")
    assert str(settings.controller_base_url) == "http://127.0.0.1:8102/"
    with pytest.raises(ValidationError, match="controller_base_url"):
        Settings(controller_base_url="http://192.168.1.20:8102")
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(controller_service_token="too-short")


def test_role_specific_database_urls_have_no_api_fallback() -> None:
    settings = Settings()
    assert not hasattr(settings, "migration_database_url")
    assert not hasattr(settings, "maintenance_database_url")
    with pytest.raises(ValidationError, match="migration_database_url"):
        MigrationSettings()
    with pytest.raises(ValidationError, match="maintenance_database_url"):
        MaintenanceSettings()

    migration = MigrationSettings(
        migration_database_url="postgresql+psycopg://rag_migrator:x@db/rag",
    )
    maintenance = MaintenanceSettings(
        maintenance_database_url="postgresql+psycopg://rag_maintenance:x@db/rag",
    )
    assert "rag_migrator" in migration.migration_database_url
    assert "rag_maintenance" in maintenance.maintenance_database_url


def test_rejects_invalid_chunk_bounds() -> None:
    with pytest.raises(ValidationError, match="chunk_target_tokens"):
        Settings(chunk_target_tokens=901, chunk_max_tokens=900)


def test_rejects_partial_object_storage_credentials() -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(
            object_storage_access_key_id="access-only",
            object_storage_secret_access_key="",
        )


def test_rejects_object_storage_tls_scheme_mismatch() -> None:
    with pytest.raises(ValidationError, match="scheme must match"):
        Settings(
            object_storage_endpoint_url="http://127.0.0.1:9000",
            object_storage_use_tls=True,
        )


def test_rejects_invalid_object_storage_bounds() -> None:
    with pytest.raises(ValidationError, match="object_storage_blocking_concurrency"):
        Settings(object_storage_blocking_concurrency=0)


def test_personal_profile_freezes_loopback_origin_and_http_cookie_contract() -> None:
    settings = Settings(
        product_profile="personal",
        canonical_origin="http://127.0.0.1:3000",
        canonical_host="127.0.0.1",
        cors_origins=[],
    )

    assert settings.allowed_request_origins == {"http://127.0.0.1:3000"}
    assert settings.cookie_secure is False
    assert settings.setup_enabled is True
    assert settings.setup_code_ttl_seconds == 15 * 60
    assert settings.setup_challenge_ttl_seconds == 10 * 60
    assert settings.setup_max_attempts == 5

    with pytest.raises(ValidationError, match="canonical_origin"):
        Settings(product_profile="personal", cors_origins=[])
    with pytest.raises(ValidationError, match="CORS"):
        Settings(
            product_profile="personal",
            canonical_origin="http://127.0.0.1:3000",
            canonical_host="127.0.0.1",
        )


def test_prepare_host_paths_only_creates_configured_directories(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()
    root = tmp_path / "external-data"
    settings = Settings(data_root=root, ocr_python_executable=executable)

    settings.prepare_host_paths()

    assert settings.ocr_work_path.is_dir()
    assert settings.upload_work_path.is_dir()
    assert settings.object_work_path.is_dir()
    assert {path.name for path in tmp_path.iterdir()} == {
        "python.exe",
        "external-data",
    }


def test_prepare_host_paths_rejects_missing_ocr_executable(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        ocr_python_executable=tmp_path / "missing.exe",
    )

    with pytest.raises(ValueError, match="does not exist"):
        settings.prepare_host_paths()
