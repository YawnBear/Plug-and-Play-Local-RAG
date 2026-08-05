from pydantic import BaseModel

from app.services.readiness import ApplicationReadiness


class ReadinessResponse(BaseModel):
    ready: bool
    deployment_id: str = ""
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

    @classmethod
    def from_state(cls, state: ApplicationReadiness) -> "ReadinessResponse":
        return cls(
            ready=state.ready,
            database=state.database,
            vector_extension=state.vector_extension,
            migration_current=state.migration_current,
            object_storage_endpoint=state.object_storage_endpoint,
            object_storage_bucket=state.object_storage_bucket,
            ollama=state.ollama,
            generation_model=state.generation_model,
            embedding_model=state.embedding_model,
            ocr_configured=state.ocr_configured,
            reranker_loaded=state.reranker_loaded,
            detail=state.detail,
        )
