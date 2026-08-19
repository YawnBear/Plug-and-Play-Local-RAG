import re
from functools import lru_cache
from ipaddress import IPv4Address, ip_network
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, PositiveInt, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    product_profile: Literal[
        "personal", "team_lan", "team_lan_preview_unsigned", "contributor"
    ] = "team_lan"
    rag_lan_ipv4: IPv4Address | None = None
    deployment_id: str = ""
    environment: str = "development"
    canonical_origin: str = "https://rag.home.arpa"
    canonical_host: str = "rag.home.arpa"
    csrf_signing_secret: SecretStr = SecretStr("development-only-csrf-secret-change-me")
    session_idle_seconds: int = Field(default=30 * 60, ge=30 * 60, le=30 * 60)
    activation_ttl_seconds: int = Field(default=30 * 60, ge=60, le=30 * 60)
    preauth_csrf_ttl_seconds: int = Field(default=15 * 60, ge=60, le=30 * 60)
    setup_code_ttl_seconds: int = Field(default=15 * 60, ge=15 * 60, le=15 * 60)
    setup_challenge_ttl_seconds: int = Field(default=10 * 60, ge=10 * 60, le=10 * 60)
    setup_max_attempts: int = Field(default=5, ge=5, le=5)
    password_hash_concurrency: int = Field(default=2, ge=1, le=8)
    cors_origins: list[AnyHttpUrl] = Field(
        default_factory=lambda: [
            AnyHttpUrl("http://localhost:3000"),
            AnyHttpUrl("http://127.0.0.1:3000"),
        ]
    )
    database_url: str = (
        "postgresql+psycopg://rag:replace-with-a-local-password@127.0.0.1:5432/rag"
    )
    database_pool_size: PositiveInt = 5
    database_max_overflow: int = Field(default=5, ge=0)
    database_pool_timeout_seconds: PositiveInt = 30

    object_storage_endpoint_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:9000")
    object_storage_region: str = "us-east-1"
    object_storage_bucket: str = "rag-originals"
    object_storage_access_key_id: SecretStr = SecretStr("")
    object_storage_secret_access_key: SecretStr = SecretStr("")
    object_storage_force_path_style: bool = True
    object_storage_use_tls: bool = False
    object_storage_connect_timeout_seconds: PositiveInt = 5
    object_storage_read_timeout_seconds: PositiveInt = 60
    object_storage_max_attempts: PositiveInt = 3
    object_storage_connection_pool_size: PositiveInt = 10
    object_storage_blocking_concurrency: int = Field(default=8, ge=1, le=64)
    object_storage_operation_max_attempts: int = Field(default=3, ge=1, le=10)
    object_storage_retry_base_seconds: float = Field(default=1.0, gt=0, le=60)
    object_deletion_lease_seconds: int = Field(default=60, ge=5, le=3600)
    object_deletion_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    object_deletion_error_limit: int = Field(default=500, ge=100, le=2000)
    upload_orphan_grace_seconds: int = Field(default=900, ge=60, le=86400)
    rustfs_image_reference: str = (
        "rustfs/rustfs:1.0.0-beta.10@sha256:"
        "60f4f2f41ce95216f8cac676e69f9d90c0bfec458a3bc7fd7fb9b7c2452ac57a"
    )

    data_root: Path = Path("C:/local-rag/data")
    ocr_python_executable: Path = Path("C:/local-rag/ocr/.venv/Scripts/python.exe")
    ocr_timeout_seconds: PositiveInt = 900
    ocr_pipeline_version: str = "v1.6"
    ocr_device: str = "cpu"
    ocr_cpu_threads: PositiveInt = 10
    ocr_page_batch_size: int = Field(default=8, ge=1, le=32)
    ocr_service_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8101")
    ocr_service_token: SecretStr = SecretStr("")
    embedding_batch_size: int = Field(default=16, ge=1, le=32)
    external_batch_max_attempts: int = Field(default=2, ge=1, le=4)

    ollama_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:11434")
    generation_model: str = "qwen3:8b"
    embedding_model: str = "qwen3-embedding:0.6b"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    embedding_dimension: int = Field(default=1024, frozen=True)
    maximum_upload_bytes: PositiveInt = 100 * 1024 * 1024
    accepted_mime_types: tuple[str, ...] = ("application/pdf",)
    meaningful_text_threshold: int = Field(default=50, ge=0)
    enable_v6_adaptive_parsing: bool = True
    maximum_pdf_pages: int = Field(default=500, ge=1, le=100_000)
    maximum_ocr_pages: int = Field(default=128, ge=1, le=100_000)
    maximum_document_chunks: int = Field(default=5_000, ge=1, le=100_000)
    maximum_extracted_text_characters: int = Field(
        default=10_000_000, ge=1, le=100_000_000
    )
    maximum_staged_ocr_result_bytes: int = Field(
        default=64 * 1024 * 1024, ge=1024, le=512 * 1024 * 1024
    )
    ingestion_processing_timeout_seconds: int = Field(
        default=3_600, ge=60, le=24 * 60 * 60
    )
    chunk_target_tokens: PositiveInt = 750
    chunk_max_tokens: PositiveInt = 900
    chunk_overlap_tokens: int = Field(default=125, ge=0)
    retrieve_k: PositiveInt = 20
    context_k: int = Field(default=6, ge=5, le=8)
    maximum_generation_context: PositiveInt = 16_384
    maximum_generation_output: PositiveInt = 3_072
    generation_timeout_seconds: PositiveInt = 300
    coordinator_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8100")
    coordinator_service_token: SecretStr = SecretStr("")
    controller_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8102")
    controller_service_token: SecretStr = SecretStr("")
    ingestion_worker_count: int = Field(default=1, ge=1, le=1)
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)

    @model_validator(mode="after")
    def validate_bounds(self) -> "Settings":
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("environment must be development, test, or production")
        if self.product_profile == "personal":
            if self.canonical_origin != "http://127.0.0.1:3000":
                raise ValueError(
                    "Personal canonical_origin must be http://127.0.0.1:3000"
                )
            if self.canonical_host != "127.0.0.1":
                raise ValueError("Personal canonical_host must be 127.0.0.1")
            if self.cors_origins:
                raise ValueError("Personal credentialed CORS must be disabled")
        else:
            if self.canonical_origin != "https://rag.home.arpa":
                raise ValueError("canonical_origin must be https://rag.home.arpa")
            if self.canonical_host != "rag.home.arpa":
                raise ValueError("canonical_host must be rag.home.arpa")
        if self.product_profile == "team_lan_preview_unsigned":
            private_networks = (
                ip_network("10.0.0.0/8"),
                ip_network("172.16.0.0/12"),
                ip_network("192.168.0.0/16"),
            )
            if self.rag_lan_ipv4 is None or not any(
                self.rag_lan_ipv4 in network for network in private_networks
            ):
                raise ValueError(
                    "Team/LAN preview requires an RFC1918 RAG_LAN_IPV4"
                )
        elif self.rag_lan_ipv4 is not None:
            raise ValueError(
                "RAG_LAN_IPV4 is only valid for the Team/LAN preview profile"
            )
        if len(self.csrf_signing_secret.get_secret_value()) < 32:
            raise ValueError("csrf_signing_secret must contain at least 32 characters")
        if self.environment == "production":
            if self.cors_origins:
                raise ValueError("credentialed CORS must be disabled in production")
            if self.csrf_signing_secret.get_secret_value().startswith(
                "development-only"
            ):
                raise ValueError(
                    "production requires a non-development CSRF signing secret"
                )
            if len(self.coordinator_service_token.get_secret_value()) < 32:
                raise ValueError(
                    "production requires a 32-character coordinator service token"
                )
            if len(self.controller_service_token.get_secret_value()) < 32:
                raise ValueError(
                    "production requires a 32-character controller service token"
                )
        ocr_endpoint = self.ocr_service_base_url
        if (
            ocr_endpoint.scheme != "http"
            or ocr_endpoint.host not in {"127.0.0.1", "localhost"}
            or ocr_endpoint.path not in {"", "/"}
            or ocr_endpoint.query
            or ocr_endpoint.fragment
        ):
            raise ValueError("ocr_service_base_url must be a loopback HTTP origin")
        ocr_token = self.ocr_service_token.get_secret_value()
        if ocr_token and len(ocr_token) < 32:
            raise ValueError("ocr_service_token must contain at least 32 characters")
        controller_endpoint = self.controller_base_url
        if (
            controller_endpoint.scheme != "http"
            or controller_endpoint.host not in {"127.0.0.1", "localhost"}
            or controller_endpoint.path not in {"", "/"}
            or controller_endpoint.query
            or controller_endpoint.fragment
        ):
            raise ValueError("controller_base_url must be a loopback HTTP origin")
        controller_token = self.controller_service_token.get_secret_value()
        if controller_token and len(controller_token) < 32:
            raise ValueError(
                "controller_service_token must contain at least 32 characters"
            )
        if self.deployment_id and not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{5,63}", self.deployment_id
        ):
            raise ValueError("deployment_id must be a safe 6-64 character identifier")
        if self.chunk_target_tokens > self.chunk_max_tokens:
            raise ValueError("chunk_target_tokens cannot exceed chunk_max_tokens")
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than target")
        if self.maximum_generation_output >= self.maximum_generation_context:
            raise ValueError("maximum_generation_output must be smaller than context")
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("database_url must use postgresql+psycopg")
        endpoint = self.object_storage_endpoint_url
        expected_scheme = "https" if self.object_storage_use_tls else "http"
        if endpoint.scheme != expected_scheme:
            raise ValueError(
                "object_storage_endpoint_url scheme must match object_storage_use_tls"
            )
        if endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment:
            raise ValueError(
                "object_storage_endpoint_url must not include a path/query"
            )
        if not self.object_storage_region.strip():
            raise ValueError("object_storage_region cannot be empty")
        bucket = self.object_storage_bucket
        if bucket != bucket.strip() or not bucket or "/" in bucket or "\\" in bucket:
            raise ValueError("object_storage_bucket must be a nonempty bucket name")
        access_key = self.object_storage_access_key_id.get_secret_value()
        secret_key = self.object_storage_secret_access_key.get_secret_value()
        if bool(access_key) != bool(secret_key):
            raise ValueError(
                "object storage access key and secret key must be configured together"
            )
        return self

    @property
    def cors_origin_strings(self) -> list[str]:
        if self.environment == "production":
            return []
        return [str(origin).rstrip("/") for origin in self.cors_origins]

    @property
    def allowed_request_origins(self) -> frozenset[str]:
        return frozenset({self.canonical_origin, *self.cors_origin_strings})

    @property
    def cookie_secure(self) -> bool:
        return self.product_profile != "personal"

    @property
    def setup_enabled(self) -> bool:
        return self.product_profile in {"personal", "team_lan_preview_unsigned"}

    @property
    def ocr_work_path(self) -> Path:
        return self.data_root / "ocr-work"

    @property
    def upload_work_path(self) -> Path:
        return self.data_root / "upload-work"

    @property
    def object_work_path(self) -> Path:
        return self.data_root / "object-work"

    def _resolve_data_root(self) -> None:
        root = self.data_root.expanduser()
        if not root.is_absolute():
            raise ValueError("data_root must resolve to an absolute path")
        self.data_root = root.resolve()

    def prepare_api_paths(self) -> None:
        self._resolve_data_root()
        self.upload_work_path.mkdir(parents=True, exist_ok=True)
        self.object_work_path.mkdir(parents=True, exist_ok=True)

    def prepare_ocr_paths(self) -> None:
        self._resolve_data_root()
        executable = self.ocr_python_executable.expanduser()
        if not executable.is_absolute():
            raise ValueError("ocr_python_executable must be an absolute path")
        self.ocr_python_executable = executable.resolve()
        if not self.ocr_python_executable.is_file():
            raise ValueError(
                f"OCR Python executable does not exist: {self.ocr_python_executable}"
            )
        self.ocr_work_path.mkdir(parents=True, exist_ok=True)

    def prepare_host_paths(self) -> None:
        self.prepare_api_paths()
        self.prepare_ocr_paths()


@lru_cache
def get_settings() -> Settings:
    return Settings()


class MigrationSettings(BaseSettings):
    """Migration-only settings; never construct this in a supervised process."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    migration_database_url: str

    @model_validator(mode="after")
    def validate_database_url(self) -> "MigrationSettings":
        if not self.migration_database_url.startswith("postgresql+psycopg://"):
            raise ValueError("migration_database_url must use postgresql+psycopg")
        return self


class MaintenanceSettings(Settings):
    """Offline maintenance settings loaded only by the stopped-service CLI."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    maintenance_database_url: str

    @model_validator(mode="after")
    def validate_maintenance_database_url(self) -> "MaintenanceSettings":
        if not self.maintenance_database_url.startswith("postgresql+psycopg://"):
            raise ValueError("maintenance_database_url must use postgresql+psycopg")
        return self


def get_migration_settings() -> MigrationSettings:
    return MigrationSettings()


def get_maintenance_settings() -> MaintenanceSettings:
    return MaintenanceSettings()
