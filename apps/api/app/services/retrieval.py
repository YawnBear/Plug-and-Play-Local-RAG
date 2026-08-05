import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import (
    ChunkRepository,
    RetrievedChunk,
    VectorSearchTuning,
)
from app.security.actor import ActorContext
from app.services.ollama_embeddings import OllamaEmbeddingClient


@dataclass(frozen=True, slots=True)
class RecallEvaluation:
    exact_chunk_ids: tuple[uuid.UUID, ...]
    approximate_chunk_ids: tuple[uuid.UUID, ...]
    recall_at_k: float


@dataclass(frozen=True, slots=True)
class FusionEvidence:
    """Measured evidence required before dense/lexical fusion can be enabled."""

    dense_recall: float
    fused_recall: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.dense_recall <= 1.0:
            raise ValueError("dense_recall must be between 0 and 1")
        if not 0.0 <= self.fused_recall <= 1.0:
            raise ValueError("fused_recall must be between 0 and 1")

    @property
    def improvement(self) -> float:
        return self.fused_recall - self.dense_recall

    @property
    def qualifies(self) -> bool:
        return self.improvement >= 0.05 - 1e-12


class RetrievalService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: OllamaEmbeddingClient,
    ) -> None:
        # Kept for container construction compatibility only. Protected queries
        # must receive the already actor-activated request session explicitly.
        self._session_factory = session_factory
        self._embedder = embedder

    async def retrieve(
        self,
        actor: ActorContext,
        session: AsyncSession,
        query: str,
        *,
        limit: int = 20,
        document_ids: Sequence[uuid.UUID] | None = None,
        tuning: VectorSearchTuning | None = None,
    ) -> Sequence[RetrievedChunk]:
        vector = await self.embed_query(query)
        return await self.retrieve_vector(
            actor,
            session,
            vector,
            limit=limit,
            document_ids=document_ids,
            tuning=tuning,
        )

    async def embed_query(self, query: str) -> list[float]:
        vectors = await self._embedder.embed([query])
        return vectors[0]

    async def retrieve_vector(
        self,
        actor: ActorContext,
        session: AsyncSession,
        query_vector: list[float],
        *,
        limit: int = 20,
        document_ids: Sequence[uuid.UUID] | None = None,
        tuning: VectorSearchTuning | None = None,
    ) -> Sequence[RetrievedChunk]:
        return await ChunkRepository(session).retrieve(
            actor,
            query_vector,
            limit=limit,
            document_ids=document_ids,
            tuning=tuning,
        )

    async def evaluate_recall(
        self,
        actor: ActorContext,
        session: AsyncSession,
        query: str,
        *,
        limit: int = 20,
        document_ids: Sequence[uuid.UUID] | None = None,
        approximate: VectorSearchTuning | None = None,
    ) -> RecallEvaluation:
        """Compare ACL-filtered HNSW results with an ACL-filtered exact query."""
        vectors = await self._embedder.embed([query])
        vector = vectors[0]
        exact = await ChunkRepository(session).retrieve(
            actor,
            vector,
            limit=limit,
            document_ids=document_ids,
            tuning=VectorSearchTuning(exact=True),
        )
        estimated = await ChunkRepository(session).retrieve(
            actor,
            vector,
            limit=limit,
            document_ids=document_ids,
            tuning=approximate or VectorSearchTuning(),
        )
        exact_ids = tuple(item.chunk.id for item in exact)
        approximate_ids = tuple(item.chunk.id for item in estimated)
        recall = (
            len(set(exact_ids).intersection(approximate_ids)) / len(exact_ids)
            if exact_ids
            else 1.0
        )
        return RecallEvaluation(exact_ids, approximate_ids, recall)
