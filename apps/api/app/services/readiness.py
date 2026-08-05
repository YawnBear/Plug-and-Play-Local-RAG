from dataclasses import dataclass

from app.config import Settings
from app.db.session import DatabaseManager, DatabaseReadiness
from app.services.object_storage import BucketReadiness, ObjectStore
from app.services.ollama_generation import (
    OllamaGenerationClient,
    OllamaModelReadiness,
)
from app.services.reranker import BgeReranker

_DETAIL_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class ApplicationReadiness:
    database: bool
    vector_extension: bool
    migration_current: bool
    object_storage_endpoint: bool
    object_storage_bucket: bool
    ollama: bool
    generation_model: bool
    embedding_model: bool
    ocr_configured: bool
    reranker_loaded: bool
    detail: str

    @property
    def ready(self) -> bool:
        return all(
            (
                self.database,
                self.vector_extension,
                self.migration_current,
                self.object_storage_endpoint,
                self.object_storage_bucket,
                self.ollama,
                self.generation_model,
                self.embedding_model,
                self.ocr_configured,
            )
        )


class ReadinessService:
    def __init__(
        self,
        database: DatabaseManager,
        generator: OllamaGenerationClient,
        reranker: BgeReranker,
        object_store: ObjectStore,
        settings: Settings,
    ) -> None:
        self._database = database
        self._generator = generator
        self._reranker = reranker
        self._object_store = object_store
        self._settings = settings

    async def check(self) -> ApplicationReadiness:
        database = await self._database.readiness()
        storage = await self._object_store.bucket_readiness()
        ollama = await self._generator.readiness(
            [self._settings.generation_model, self._settings.embedding_model]
        )
        ocr_configured, ocr_detail = self._ocr_readiness()
        failures = self._failure_details(
            database, storage, ollama, ocr_configured, ocr_detail
        )
        reranker_detail = (
            "reranker loaded"
            if self._reranker.loaded
            else "reranker not loaded (lazy; informational)"
        )
        detail = "; ".join(failures or ["ready", reranker_detail])
        return ApplicationReadiness(
            database=database.database,
            vector_extension=database.vector_extension,
            migration_current=database.migration_current,
            object_storage_endpoint=storage.endpoint,
            object_storage_bucket=storage.bucket,
            ollama=ollama.reachable,
            generation_model=self._settings.generation_model in ollama.available_models,
            embedding_model=self._settings.embedding_model in ollama.available_models,
            ocr_configured=ocr_configured,
            reranker_loaded=self._reranker.loaded,
            detail=_bounded(detail),
        )

    def _ocr_readiness(self) -> tuple[bool, str]:
        executable = self._settings.ocr_python_executable
        if not executable.is_absolute():
            return False, "OCR_PYTHON_EXECUTABLE must be an absolute path"
        if not executable.is_file():
            return False, f"OCR Python executable is missing: {executable}"
        if self._settings.ocr_pipeline_version != "v1.6":
            return False, "OCR_PIPELINE_VERSION must be v1.6"
        if self._settings.ocr_device != "cpu":
            return False, "OCR_DEVICE must be cpu for the approved baseline"
        return True, "ready"

    @staticmethod
    def _failure_details(
        database: DatabaseReadiness,
        storage: BucketReadiness,
        ollama: OllamaModelReadiness,
        ocr_configured: bool,
        ocr_detail: str,
    ) -> list[str]:
        failures: list[str] = []
        if not database.ready:
            failures.append(database.message)
        if not storage.ready:
            failures.append(storage.detail)
        if not ollama.reachable or ollama.detail != "ready":
            failures.append(ollama.detail)
        if not ocr_configured:
            failures.append(ocr_detail)
        return failures


def _bounded(detail: str) -> str:
    if len(detail) <= _DETAIL_LIMIT:
        return detail
    return f"{detail[: _DETAIL_LIMIT - 3]}..."
