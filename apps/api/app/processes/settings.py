import re
from pathlib import Path

from pydantic import AnyHttpUrl, Field, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ObjectWorkerSettings(BaseSettings):
    """Object-worker settings; API and maintenance database URLs are absent."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    worker_database_url: str
    worker_database_pool_size: PositiveInt = 2
    worker_database_max_overflow: int = Field(default=0, ge=0, le=2)
    worker_database_pool_timeout_seconds: PositiveInt = 30

    object_storage_endpoint_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:9000")
    object_storage_region: str = "us-east-1"
    object_storage_bucket: str = "rag-originals"
    object_storage_access_key_id: SecretStr
    object_storage_secret_access_key: SecretStr
    object_storage_force_path_style: bool = True
    object_storage_use_tls: bool = False
    object_storage_connect_timeout_seconds: PositiveInt = 5
    object_storage_read_timeout_seconds: PositiveInt = 60
    object_storage_max_attempts: PositiveInt = 3
    object_storage_connection_pool_size: PositiveInt = 4
    object_storage_blocking_concurrency: int = Field(default=2, ge=1, le=4)

    object_deletion_lease_seconds: int = Field(default=60, ge=5, le=3600)
    object_deletion_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    object_deletion_error_limit: int = Field(default=500, ge=100, le=2000)
    upload_orphan_grace_seconds: int = Field(default=900, ge=60, le=86400)
    object_storage_retry_base_seconds: float = Field(default=1.0, gt=0, le=60)

    service_lease_seconds: int = Field(default=30, ge=5, le=300)

    def model_post_init(self, _context: object) -> None:
        if not self.worker_database_url.startswith("postgresql+psycopg://"):
            raise ValueError("WORKER_DATABASE_URL must use postgresql+psycopg")
        if not self.object_storage_access_key_id.get_secret_value():
            raise ValueError("OBJECT_STORAGE_ACCESS_KEY_ID is required")
        if not self.object_storage_secret_access_key.get_secret_value():
            raise ValueError("OBJECT_STORAGE_SECRET_ACCESS_KEY is required")


class WorkerProcessSettings(ObjectWorkerSettings):
    """Ingestion-only settings with coordinator and OCR credentials."""

    coordinator_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8100")
    coordinator_service_token: SecretStr
    ocr_service_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8101")
    ocr_service_token: SecretStr
    object_work_path: Path = Path("C:/local-rag/data/object-work")
    ocr_work_path: Path = Path("C:/local-rag/data/ocr-work")
    meaningful_text_threshold: int = Field(default=50, ge=0)
    ocr_page_batch_size: int = Field(default=8, ge=1, le=32)
    embedding_batch_size: int = Field(default=16, ge=1, le=32)
    external_batch_max_attempts: int = Field(default=2, ge=1, le=4)
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
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    ingestion_lease_seconds: int = Field(default=60, ge=5, le=3600)
    ingestion_maximum_attempts: int = Field(default=3, ge=1, le=10)

    def model_post_init(self, _context: object) -> None:
        super().model_post_init(_context)
        if len(self.coordinator_service_token.get_secret_value()) < 32:
            raise ValueError(
                "COORDINATOR_SERVICE_TOKEN must contain at least 32 characters"
            )
        if len(self.ocr_service_token.get_secret_value()) < 32:
            raise ValueError("OCR_SERVICE_TOKEN must contain at least 32 characters")
        if self.chunk_target_tokens > self.chunk_max_tokens:
            raise ValueError("CHUNK_TARGET_TOKENS cannot exceed CHUNK_MAX_TOKENS")
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than target")


class CoordinatorProcessSettings(BaseSettings):
    """Coordinator-only settings; database and object credentials are absent."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    coordinator_service_token: SecretStr
    coordinator_ownership_path: Path
    ollama_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:11434")
    generation_model: str = "qwen3:8b"
    embedding_model: str = "qwen3-embedding:0.6b"
    reranker_model_path: Path
    hf_home: Path
    hf_hub_cache: Path
    transformers_cache: Path
    xdg_cache_home: Path
    hf_hub_offline: bool
    transformers_offline: bool
    tokenizers_parallelism: bool
    maximum_generation_context: PositiveInt = 16_384
    maximum_generation_output: PositiveInt = 3_072
    generation_timeout_seconds: PositiveInt = 300

    def model_post_init(self, _context: object) -> None:
        if len(self.coordinator_service_token.get_secret_value()) < 32:
            raise ValueError(
                "COORDINATOR_SERVICE_TOKEN must contain at least 32 characters"
            )
        if not self.coordinator_ownership_path.is_absolute():
            raise ValueError("COORDINATOR_OWNERSHIP_PATH must be absolute")
        if not self.reranker_model_path.is_absolute():
            raise ValueError("RERANKER_MODEL_PATH must be absolute")
        if not self.hf_hub_offline or not self.transformers_offline:
            raise ValueError("coordinator model loading must remain offline")
        if self.tokenizers_parallelism:
            raise ValueError("TOKENIZERS_PARALLELISM must remain disabled")
        if self.maximum_generation_output >= self.maximum_generation_context:
            raise ValueError("MAXIMUM_GENERATION_OUTPUT must be smaller than context")
        for name, path in (
            ("HF_HOME", self.hf_home),
            ("HF_HUB_CACHE", self.hf_hub_cache),
            ("TRANSFORMERS_CACHE", self.transformers_cache),
            ("XDG_CACHE_HOME", self.xdg_cache_home),
        ):
            if not path.is_absolute():
                raise ValueError(f"{name} must be absolute")
            if (
                path == self.reranker_model_path
                or self.reranker_model_path in path.parents
            ):
                raise ValueError(f"{name} must be separate from signed model assets")


class OcrProcessSettings(BaseSettings):
    """OCR-only settings; database and object credentials are absent."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    ocr_service_token: SecretStr
    ocr_ownership_path: Path
    ocr_workspace_root: Path
    ocr_python_executable: Path
    ocr_timeout_seconds: PositiveInt = 900
    ocr_pipeline_version: str = "v1.6"
    ocr_device: str = "cpu"
    ocr_cpu_threads: PositiveInt = 10
    ocr_process_count: int = Field(default=1, ge=1, le=16)
    ocr_model_asset_root: Path
    paddle_home: Path
    hf_home: Path
    hf_hub_cache: Path
    transformers_cache: Path
    xdg_cache_home: Path
    hf_hub_offline: bool
    transformers_offline: bool

    def model_post_init(self, _context: object) -> None:
        if len(self.ocr_service_token.get_secret_value()) < 32:
            raise ValueError("OCR_SERVICE_TOKEN must contain at least 32 characters")
        for name, path in (
            ("OCR_OWNERSHIP_PATH", self.ocr_ownership_path),
            ("OCR_WORKSPACE_ROOT", self.ocr_workspace_root),
            ("OCR_PYTHON_EXECUTABLE", self.ocr_python_executable),
            ("OCR_MODEL_ASSET_ROOT", self.ocr_model_asset_root),
            ("PADDLE_HOME", self.paddle_home),
            ("HF_HOME", self.hf_home),
            ("HF_HUB_CACHE", self.hf_hub_cache),
            ("TRANSFORMERS_CACHE", self.transformers_cache),
            ("XDG_CACHE_HOME", self.xdg_cache_home),
        ):
            if not path.is_absolute():
                raise ValueError(f"{name} must be absolute")
        if self.paddle_home != self.ocr_model_asset_root:
            raise ValueError("PADDLE_HOME must equal OCR_MODEL_ASSET_ROOT")
        if not self.hf_hub_offline or not self.transformers_offline:
            raise ValueError("OCR model loading must remain offline")
        for cache in (
            self.hf_home,
            self.hf_hub_cache,
            self.transformers_cache,
            self.xdg_cache_home,
        ):
            if (
                cache == self.ocr_model_asset_root
                or self.ocr_model_asset_root in cache.parents
            ):
                raise ValueError("OCR caches must be separate from signed model assets")
        if self.ocr_pipeline_version != "v1.6":
            raise ValueError("OCR_PIPELINE_VERSION must be v1.6")
        if re.fullmatch(r"(?:cpu|gpu:[0-9]+)", self.ocr_device) is None:
            raise ValueError("OCR_DEVICE must be cpu or a numbered GPU such as gpu:0")
