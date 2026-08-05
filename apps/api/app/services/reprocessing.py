from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import uuid
from contextlib import suppress
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain import validate_embedding
from app.services.answer_protocol import (
    INSUFFICIENT_CONTEXT_SENTINEL,
    is_explicit_insufficient_context,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReindexClaim:
    operation_id: uuid.UUID
    generation_id: uuid.UUID
    chunk_id: uuid.UUID
    chunk_text: str
    text_sha256: str
    dimension: int
    embedding_version: str
    lease_token: uuid.UUID
    fencing_token: int
    attempt: int


@dataclass(frozen=True, slots=True)
class QualificationClaim:
    operation_id: uuid.UUID
    generation_id: uuid.UUID
    dimension: int
    lease_token: uuid.UUID
    fencing_token: int


class ReprocessingWorker:
    """Build and qualify shadow embedding generations without changing live search."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: object,
        reranker: object,
        *,
        owner_id: str,
        poll_seconds: float,
        lease_seconds: int,
        batch_size: int,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._reranker = reranker
        self._owner_id = owner_id
        self._poll_seconds = poll_seconds
        self._lease_seconds = lease_seconds
        self._batch_size = min(batch_size, 32)
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="reprocessing-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def wait(self) -> None:
        if self._task is None:
            raise RuntimeError("reprocessing worker has not started")
        await self._task

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            claims = await self._claim_tasks()
            if claims:
                await self._process_tasks(claims)
                continue
            qualification = await self._claim_qualification()
            if qualification is not None:
                await self._qualify(qualification)
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_seconds
                )
            except TimeoutError:
                continue

    async def _claim_tasks(self) -> list[ReindexClaim]:
        async with self._session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM v10_claim_reindex_tasks("
                        ":owner_id, :lease_seconds, :batch_size)"
                    ),
                    {
                        "owner_id": self._owner_id,
                        "lease_seconds": self._lease_seconds,
                        "batch_size": self._batch_size,
                    },
                )
            ).all()
        return [ReindexClaim(**row._mapping) for row in rows]

    async def _process_tasks(self, claims: list[ReindexClaim]) -> None:
        try:
            vectors = await self._embedder.embed([claim.chunk_text for claim in claims])
            if len(vectors) != len(claims):
                raise ValueError("embedding result count does not match task count")
            for claim, vector in zip(claims, vectors, strict=True):
                validate_embedding(vector, dimension=claim.dimension)
                await self._commit_task(claim, vector)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("shadow embedding batch failed: %s", exc)
            for claim in claims:
                await self._fail_task(claim, str(exc) or "shadow embedding failed")

    async def _commit_task(self, claim: ReindexClaim, embedding: list[float]) -> None:
        async with self._session_factory() as session, session.begin():
            committed = await session.scalar(
                text(
                    "SELECT v10_commit_reindex_task("
                    ":operation_id, :chunk_id, :lease_token, :fencing_token, "
                    ":embedding)"
                ),
                {
                    "operation_id": claim.operation_id,
                    "chunk_id": claim.chunk_id,
                    "lease_token": claim.lease_token,
                    "fencing_token": claim.fencing_token,
                    "embedding": json.dumps(embedding, separators=(",", ":")),
                },
            )
        if not committed:
            logger.info("shadow embedding claim became stale: %s", claim.chunk_id)

    async def _fail_task(self, claim: ReindexClaim, error: str) -> None:
        async with self._session_factory() as session, session.begin():
            await session.scalar(
                text(
                    "SELECT v10_fail_reindex_task("
                    ":operation_id, :chunk_id, :lease_token, :fencing_token, "
                    ":error)"
                ),
                {
                    "operation_id": claim.operation_id,
                    "chunk_id": claim.chunk_id,
                    "lease_token": claim.lease_token,
                    "fencing_token": claim.fencing_token,
                    "error": (error.strip() or "shadow embedding failed")[:500],
                },
            )

    async def _claim_qualification(self) -> QualificationClaim | None:
        async with self._session_factory() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM v10_claim_reindex_qualification("
                        ":owner_id, :lease_seconds)"
                    ),
                    {
                        "owner_id": self._owner_id,
                        "lease_seconds": self._lease_seconds,
                    },
                )
            ).one_or_none()
        return None if row is None else QualificationClaim(**row._mapping)

    async def _qualify(self, claim: QualificationClaim) -> None:
        retrieval_passed = False
        rerank_passed = False
        citation_passed = False
        insufficient_passed = False
        reason = "candidate_qualification_failed"
        try:
            async with self._session_factory() as session, session.begin():
                samples = (
                    await session.execute(
                        text(
                            "SELECT * FROM v10_reindex_qualification_sample("
                            ":operation_id)"
                        ),
                        {"operation_id": claim.operation_id},
                    )
                ).all()
            if not samples:
                raise ValueError("qualification has no representative chunks")
            query = samples[0].chunk_text
            vectors = await self._embedder.embed([query])
            vector = vectors[0]
            validate_embedding(vector, dimension=claim.dimension)
            async with self._session_factory() as session, session.begin():
                candidates = (
                    await session.execute(
                        text(
                            "SELECT * FROM v10_candidate_retrieve("
                            ":operation_id, :query_vector, :limit)"
                        ),
                        {
                            "operation_id": claim.operation_id,
                            "query_vector": json.dumps(vector, separators=(",", ":")),
                            "limit": min(5, max(1, len(samples))),
                        },
                    )
                ).all()
            retrieval_passed = samples[0].chunk_id in {
                candidate.chunk_id for candidate in candidates
            }
            scores = await self._reranker.score(
                query, [sample.chunk_text for sample in samples]
            )
            rerank_passed = (
                len(scores) == len(samples)
                and all(math.isfinite(score) for score in scores)
                and scores[0] >= max(scores)
            )
            citation_passed = all(
                sample.filename.strip()
                and sample.page_start >= 1
                and sample.page_end >= sample.page_start
                and sample.citation_label.strip()
                and hashlib.sha256(sample.chunk_text.encode("utf-8")).hexdigest()
                == sample.text_sha256
                for sample in samples
            )
            insufficient_passed = is_explicit_insufficient_context(
                INSUFFICIENT_CONTEXT_SENTINEL
            )
            reason = "qualification_passed"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = (str(exc).strip() or reason)[:128]
            logger.warning("embedding generation qualification failed: %s", exc)
        async with self._session_factory() as session, session.begin():
            completed = await session.scalar(
                text(
                    "SELECT v10_complete_reindex_qualification("
                    ":operation_id, :lease_token, :fencing_token, "
                    ":retrieval_passed, :rerank_passed, :citation_passed, "
                    ":insufficient_passed, :reason)"
                ),
                {
                    "operation_id": claim.operation_id,
                    "lease_token": claim.lease_token,
                    "fencing_token": claim.fencing_token,
                    "retrieval_passed": retrieval_passed,
                    "rerank_passed": rerank_passed,
                    "citation_passed": citation_passed,
                    "insufficient_passed": insufficient_passed,
                    "reason": reason,
                },
            )
        if not completed:
            logger.info(
                "embedding qualification claim became stale: %s", claim.operation_id
            )
