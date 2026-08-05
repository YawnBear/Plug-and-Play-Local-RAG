import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import bindparam, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document, IngestionJob
from app.domain import JobStatus, validate_embedding
from app.security.actor import ActorContext

MIN_RETRIEVAL_LIMIT = 1
MAX_RETRIEVAL_LIMIT = 100
MIN_HNSW_EF_SEARCH = 1
MAX_HNSW_EF_SEARCH = 1_000


def _actor_document_filter(actor: ActorContext):
    """Defense in depth in addition to the forced-RLS document policy."""
    return (
        func.v4_current_actor_id() == actor.user_id,
        func.v4_can_read_document(Document.id),
    )


@dataclass(frozen=True, slots=True)
class VectorSearchTuning:
    """Per-query pgvector settings; all settings are transaction-local."""

    exact: bool = False
    ef_search: int = 100
    iterative_scan: str = "strict_order"

    def __post_init__(self) -> None:
        if not MIN_HNSW_EF_SEARCH <= self.ef_search <= MAX_HNSW_EF_SEARCH:
            raise ValueError("ef_search must be between 1 and 1000")
        if self.iterative_scan not in {"strict_order", "relaxed_order"}:
            raise ValueError("iterative_scan must be 'strict_order' or 'relaxed_order'")


async def apply_vector_search_tuning(
    session: AsyncSession, tuning: VectorSearchTuning
) -> None:
    """Apply pgvector planner knobs without leaking them through pooled sessions."""
    await session.execute(
        text("SELECT set_config('enable_indexscan', :value, true)"),
        {"value": "off" if tuning.exact else "on"},
    )
    if not tuning.exact:
        await session.execute(
            text("SELECT set_config('hnsw.ef_search', :value, true)"),
            {"value": str(tuning.ef_search)},
        )
        await session.execute(
            text("SELECT set_config('hnsw.iterative_scan', :value, true)"),
            {"value": tuning.iterative_scan},
        )


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, document: Document) -> Document:
        self.session.add(document)
        await self.session.flush()
        return document

    async def get(self, actor: ActorContext, document_id: uuid.UUID) -> Document | None:
        return await self.session.scalar(
            select(Document).where(
                Document.id == document_id, *_actor_document_filter(actor)
            )
        )

    async def find_by_sha256(self, actor: ActorContext, sha256: str) -> Document | None:
        return await self.session.scalar(
            select(Document).where(
                Document.sha256 == sha256, *_actor_document_filter(actor)
            )
        )

    async def list(
        self, actor: ActorContext, *, page: int = 1, limit: int = 100
    ) -> Sequence[Document]:
        if page < 1:
            raise ValueError("page must be positive")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        result = await self.session.scalars(
            select(Document)
            .where(*_actor_document_filter(actor))
            .order_by(Document.created_at.desc(), Document.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return result.all()

    async def delete(self, document_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            delete(Document).where(Document.id == document_id)
        )
        return bool(result.rowcount)


class IngestionJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, job: IngestionJob) -> IngestionJob:
        self.session.add(job)
        await self.session.flush()
        return job

    async def get(self, job_id: uuid.UUID) -> IngestionJob | None:
        return await self.session.get(IngestionJob, job_id)

    async def latest_for_document(self, document_id: uuid.UUID) -> IngestionJob | None:
        return await self.session.scalar(
            select(IngestionJob)
            .where(IngestionJob.document_id == document_id)
            .order_by(IngestionJob.created_at.desc())
            .limit(1)
        )

    async def interrupt_running(self) -> int:
        document_ids = list(
            await self.session.scalars(
                select(IngestionJob.document_id).where(
                    IngestionJob.status == JobStatus.RUNNING.value
                )
            )
        )
        result = await self.session.execute(
            update(IngestionJob)
            .where(IngestionJob.status == JobStatus.RUNNING.value)
            .values(
                status=JobStatus.INTERRUPTED.value,
                error="interrupted by application restart",
            )
        )
        if document_ids:
            await self.session.execute(
                update(Document)
                .where(Document.id.in_(document_ids))
                .values(
                    state="failed",
                    stage="failed",
                    error="ingestion interrupted by application restart",
                )
            )
        return int(result.rowcount or 0)

    async def claim_next(self) -> "ClaimedJob | None":
        job = await self.session.scalar(
            select(IngestionJob)
            .join(Document, Document.id == IngestionJob.document_id)
            .where(
                IngestionJob.status == JobStatus.QUEUED.value,
                IngestionJob.available_at <= datetime.now(UTC),
                Document.object_key.is_not(None),
            )
            .order_by(IngestionJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        document = await self.session.get(Document, job.document_id)
        if document is None:
            raise RuntimeError(f"queued job {job.id} has no document")
        now = datetime.now(UTC)
        job.status = JobStatus.RUNNING.value
        job.stage = "parsing"
        job.attempt += 1
        job.started_at = now
        job.heartbeat_at = now
        job.error = None
        document.state = "parsing"
        document.stage = "parsing"
        document.error = None
        await self.session.flush()
        return ClaimedJob(
            job_id=job.id,
            document_id=document.id,
            object_key=document.object_key,
            filename=document.original_filename,
            source_sha256=document.sha256,
            byte_size=document.byte_size,
            attempt=job.attempt,
        )

    async def set_stage(
        self, job_id: uuid.UUID, document_id: uuid.UUID, stage: str
    ) -> None:
        now = datetime.now(UTC)
        await self.session.execute(
            update(IngestionJob)
            .where(IngestionJob.id == job_id)
            .values(stage=stage, heartbeat_at=now)
        )
        await self.session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(state=stage, stage=stage)
        )


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: uuid.UUID
    document_id: uuid.UUID
    object_key: str
    filename: str
    source_sha256: str
    byte_size: int
    attempt: int


class ChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_all(self, chunks: Sequence[Chunk]) -> None:
        for chunk in chunks:
            if chunk.embedding is None:
                raise ValueError("chunk embedding is required")
            validate_embedding(chunk.embedding)
        self.session.add_all(chunks)
        await self.session.flush()

    async def delete_for_document(self, document_id: uuid.UUID) -> int:
        result = await self.session.execute(
            delete(Chunk).where(Chunk.document_id == document_id)
        )
        return int(result.rowcount or 0)

    async def retrieve(
        self,
        actor: ActorContext,
        query_vector: list[float],
        *,
        limit: int,
        document_ids: Sequence[uuid.UUID] | None = None,
        tuning: VectorSearchTuning | None = None,
    ) -> Sequence["RetrievedChunk"]:
        if not MIN_RETRIEVAL_LIMIT <= limit <= MAX_RETRIEVAL_LIMIT:
            raise ValueError("limit must be between 1 and 100")
        if document_ids is not None and not document_ids:
            return []
        await apply_vector_search_tuning(self.session, tuning or VectorSearchTuning())
        statement = text(
            "SELECT chunk_id, distance "
            "FROM v10_retrieve_active_chunks("
            ":query_vector, :limit, :document_ids)"
        ).bindparams(
            bindparam("query_vector", type_=VECTOR()),
            bindparam("document_ids", type_=ARRAY(UUID(as_uuid=True))),
        )
        ranked = (
            await self.session.execute(
                statement,
                {
                    "query_vector": query_vector,
                    "limit": limit,
                    "document_ids": list(document_ids)
                    if document_ids is not None
                    else None,
                },
            )
        ).all()
        if not ranked:
            return []
        chunks = (
            await self.session.scalars(
                select(Chunk)
                .join(Document, Document.id == Chunk.document_id)
                .where(
                    Chunk.id.in_([row.chunk_id for row in ranked]),
                    *_actor_document_filter(actor),
                )
            )
        ).all()
        by_id = {chunk.id: chunk for chunk in chunks}
        return [
            RetrievedChunk(chunk=by_id[row.chunk_id], distance=float(row.distance))
            for row in ranked
            if row.chunk_id in by_id
        ]


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk: Chunk
    distance: float
