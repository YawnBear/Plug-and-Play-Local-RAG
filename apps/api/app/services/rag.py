import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document
from app.db.repositories import RetrievedChunk
from app.schemas.query import QueryRequest
from app.security.actor import ActorContext
from app.services.answer_protocol import is_explicit_insufficient_context
from app.services.chunking import count_tokens
from app.services.library import LibraryCorruption, LibraryLocation, LibraryService
from app.services.ollama_generation import (
    GenerationChunk,
    GenerationServiceError,
    OllamaGenerationClient,
)
from app.services.reranker import BgeReranker, RerankedChunk
from app.services.retrieval import RetrievalService

INSUFFICIENT_CONTEXT_ANSWER = (
    "I don't have sufficient context in the selected documents to answer that question."
)
_SOURCE_LABEL_PATTERN = re.compile(r"\[S(\d+)\]")
_SOURCE_LIKE_PATTERN = re.compile(r"\[S[^\]\r\n]*\]")
_MODEL_CONTEXT_TOKENS = 16_384
_OUTPUT_TOKENS = 3_072
_FORMATTING_RESERVE_TOKENS = 256


@dataclass(frozen=True, slots=True)
class PreparedSource:
    label: str
    chunk_id: UUID
    filename: str
    document_id: UUID
    display_name: str
    logical_path: str
    page_start: int
    page_end: int
    section: str | None
    text: str

    def citation_payload(self) -> dict[str, str | int]:
        return {
            "label": self.label,
            "chunk_id": str(self.chunk_id),
            "filename": self.filename,
            "document_id": str(self.document_id),
            "display_name": self.display_name,
            "logical_path": self.logical_path,
            "page_start": self.page_start,
            "page_end": self.page_end,
        }


@dataclass(frozen=True, slots=True)
class PreparedQuery:
    prompt: str | None
    sources: tuple[PreparedSource, ...]
    correlation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    actor: ActorContext | None = None


class RagAccessRevoked(RuntimeError):
    pass


QueryMonitor = Callable[[PreparedQuery], Awaitable[None]]


class RagService:
    def __init__(
        self,
        retrieval: RetrievalService,
        reranker: BgeReranker,
        generator: OllamaGenerationClient,
        library: LibraryService,
        *,
        prompt_path: Path | None = None,
    ) -> None:
        self._retrieval = retrieval
        self._reranker = reranker
        self._generator = generator
        self._library = library
        resolved_prompt_path = prompt_path or (
            Path(__file__).resolve().parents[1] / "prompts" / "rag_answer.txt"
        )
        self._prompt_template = resolved_prompt_path.read_text(encoding="utf-8")

    async def prepare(
        self,
        actor: ActorContext,
        session: AsyncSession,
        request: QueryRequest,
        *,
        correlation_id: uuid.UUID | None = None,
    ) -> PreparedQuery:
        request_id = correlation_id or uuid.uuid4()
        timings: dict[str, float] = {}
        started = time.perf_counter()
        candidates = await self._retrieval.retrieve(
            actor,
            session,
            request.question,
            limit=request.retrieve_k,
            document_ids=request.document_ids,
        )
        timings["dense_retrieval"] = _elapsed_ms(started)
        if not candidates:
            return PreparedQuery(None, (), request_id, timings)

        started = time.perf_counter()
        ranked = await self._reranker.rerank(
            request.question, candidates, limit=request.context_k
        )
        timings["rerank"] = _elapsed_ms(started)
        started = time.perf_counter()
        locations = await self._library.locations_for_documents(
            actor,
            session,
            list(dict.fromkeys(item.candidate.chunk.document_id for item in ranked)),
        )
        sources = self._materialize_sources(ranked, locations)
        timings["source_materialization"] = _elapsed_ms(started)
        if not sources:
            return PreparedQuery(None, (), request_id, timings)

        started = time.perf_counter()
        await self._generator.check_available()
        timings["model_availability"] = _elapsed_ms(started)
        source_blocks = "\n\n".join(self._format_source(source) for source in sources)
        prompt = self._prompt_template.format(
            question=request.question,
            sources=source_blocks,
        )
        _ensure_prompt_budget(prompt)
        return PreparedQuery(prompt, sources, request_id, timings)

    async def embed_query(self, request: QueryRequest) -> list[float]:
        return await self._retrieval.embed_query(request.question)

    async def retrieve_candidates(
        self,
        actor: ActorContext,
        session: AsyncSession,
        request: QueryRequest,
        query_vector: list[float],
    ) -> Sequence[RetrievedChunk]:
        return await self._retrieval.retrieve_vector(
            actor,
            session,
            query_vector,
            limit=request.retrieve_k,
            document_ids=request.document_ids,
        )

    async def rerank_candidates(
        self, request: QueryRequest, candidates: Sequence[RetrievedChunk]
    ) -> Sequence[RerankedChunk]:
        return await self._reranker.rerank(
            request.question, candidates, limit=request.context_k
        )

    async def prepare_sources(
        self,
        actor: ActorContext,
        session: AsyncSession,
        request: QueryRequest,
        ranked: Sequence[RerankedChunk],
        *,
        correlation_id: uuid.UUID | None = None,
    ) -> PreparedQuery:
        request_id = correlation_id or uuid.uuid4()
        if not ranked:
            return PreparedQuery(None, (), request_id, actor=actor)
        started = time.perf_counter()
        locations = await self._library.locations_for_documents(
            actor,
            session,
            list(dict.fromkeys(item.candidate.chunk.document_id for item in ranked)),
        )
        sources = self._materialize_sources(ranked, locations)
        timings = {"source_materialization": _elapsed_ms(started)}
        if not sources:
            return PreparedQuery(None, (), request_id, timings, actor)
        source_blocks = "\n\n".join(self._format_source(source) for source in sources)
        prompt = self._prompt_template.format(
            question=request.question,
            sources=source_blocks,
        )
        _ensure_prompt_budget(prompt)
        return PreparedQuery(prompt, sources, request_id, timings, actor)

    async def check_generation_available(
        self, prepared: PreparedQuery
    ) -> PreparedQuery:
        if prepared.prompt is not None and prepared.sources:
            await self._generator.check_available()
        return prepared

    async def monitor(
        self,
        actor: ActorContext,
        session: AsyncSession,
        prepared: PreparedQuery,
    ) -> None:
        if prepared.actor != actor:
            raise RagAccessRevoked("query actor changed")
        expected = {source.document_id for source in prepared.sources}
        if not expected:
            return
        visible = set(
            await session.scalars(select(Document.id).where(Document.id.in_(expected)))
        )
        if visible != expected:
            raise RagAccessRevoked("query source access was revoked")

    async def monitor_documents(
        self,
        actor: ActorContext,
        session: AsyncSession,
        document_ids: Sequence[uuid.UUID],
    ) -> None:
        expected = set(document_ids)
        if not expected:
            return
        visible = set(
            await session.scalars(select(Document.id).where(Document.id.in_(expected)))
        )
        if visible != expected:
            raise RagAccessRevoked("query source access was revoked")

    async def stream(
        self, prepared: PreparedQuery, *, monitor: QueryMonitor
    ) -> AsyncIterator[str]:
        await monitor(prepared)
        yield _sse_event(
            "sources",
            {"sources": [source.citation_payload() for source in prepared.sources]},
        )
        if prepared.prompt is None or not prepared.sources:
            yield self._declined_final()
            return

        draft_parts: list[str] = []
        stop_reason: str | None = None
        try:
            token_iterator = self._generator.stream(prepared.prompt).__aiter__()
            next_token: asyncio.Task[GenerationChunk] | None = None
            last_monitor = time.monotonic()
            try:
                while True:
                    if next_token is None:
                        next_token = asyncio.create_task(anext(token_iterator))
                    timeout = max(0.0, 1.0 - (time.monotonic() - last_monitor))
                    done, _ = await asyncio.wait({next_token}, timeout=timeout)
                    if not done:
                        await monitor(prepared)
                        last_monitor = time.monotonic()
                        continue
                    try:
                        chunk = next_token.result()
                    except StopAsyncIteration:
                        next_token = None
                        break
                    next_token = None
                    if time.monotonic() - last_monitor >= 1.0:
                        await monitor(prepared)
                        last_monitor = time.monotonic()
                    if chunk.type == "thinking":
                        continue
                    if chunk.type == "done":
                        _validate_generation_usage(chunk, prepared.prompt)
                        stop_reason = chunk.done_reason
                        break
                    draft_parts.append(chunk.text)
                    yield _sse_event("token", {"text": chunk.text})
            finally:
                if next_token is not None and not next_token.done():
                    next_token.cancel()
                    await asyncio.gather(next_token, return_exceptions=True)
                close = getattr(token_iterator, "aclose", None)
                if close is not None:
                    await close()
            if stop_reason != "stop":
                raise GenerationServiceError(
                    "generation reached its output limit before completion"
                    if stop_reason == "length"
                    else "generation ended without a terminal stop reason"
                )
        except RagAccessRevoked:
            yield _sse_event(
                "error",
                {
                    "code": "access_revoked",
                    "message": "access to a source was revoked",
                },
            )
            return
        except GenerationServiceError as exc:
            yield _sse_event(
                "error",
                {"code": "generation_failed", "message": str(exc)},
            )
            return

        answer = "".join(draft_parts).strip()
        citations = self._validated_citations(answer, prepared.sources)
        if citations is None:
            yield self._declined_final()
            return
        yield _sse_event(
            "final",
            {
                "answer": answer,
                "insufficient_context": False,
                "citations": [source.citation_payload() for source in citations],
            },
        )

    @staticmethod
    def _materialize_sources(
        ranked: Sequence[RerankedChunk],
        locations: dict[UUID, LibraryLocation],
    ) -> tuple[PreparedSource, ...]:
        materialized: list[PreparedSource] = []
        for index, item in enumerate(ranked, start=1):
            chunk = item.candidate.chunk
            location = locations.get(chunk.document_id)
            if location is None:
                raise LibraryCorruption(
                    f"document {chunk.document_id} has no canonical library node"
                )
            materialized.append(
                PreparedSource(
                    label=f"S{index}",
                    chunk_id=chunk.id,
                    filename=chunk.filename,
                    document_id=chunk.document_id,
                    display_name=location.display_name,
                    logical_path=location.logical_path,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    section=chunk.section,
                    text=chunk.text,
                )
            )
        return tuple(materialized)

    @staticmethod
    def _format_source(source: PreparedSource) -> str:
        page = (
            str(source.page_start)
            if source.page_start == source.page_end
            else f"{source.page_start}-{source.page_end}"
        )
        section = source.section or "Not specified"
        return (
            f"[{source.label}]\n"
            f"Filename: {source.filename}\n"
            f"Current display name: {source.display_name}\n"
            f"Logical path: {source.logical_path}\n"
            f"Page: {page}\n"
            f"Section: {section}\n"
            f"Content:\n{source.text}"
        )

    @staticmethod
    def _validated_citations(
        answer: str,
        sources: Sequence[PreparedSource],
    ) -> tuple[PreparedSource, ...] | None:
        if not answer or is_explicit_insufficient_context(answer):
            return None
        supplied = {source.label: source for source in sources}
        source_like_tokens = _SOURCE_LIKE_PATTERN.findall(answer)
        valid_tokens = {f"[{label}]" for label in supplied}
        if any(token not in valid_tokens for token in source_like_tokens):
            return None
        labels = [f"S{number}" for number in _SOURCE_LABEL_PATTERN.findall(answer)]
        if not labels or any(label not in supplied for label in labels):
            return None
        return tuple(supplied[label] for label in dict.fromkeys(labels))

    @staticmethod
    def _declined_final() -> str:
        return _sse_event(
            "final",
            {
                "answer": INSUFFICIENT_CONTEXT_ANSWER,
                "insufficient_context": True,
                "citations": [],
            },
        )


def _sse_event(event: str, payload: object) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _ensure_prompt_budget(prompt: str) -> None:
    if (
        count_tokens(prompt) + _OUTPUT_TOKENS + _FORMATTING_RESERVE_TOKENS
        > _MODEL_CONTEXT_TOKENS
    ):
        raise RuntimeError("query prompt exceeds the configured model context budget")


def _validate_generation_usage(chunk: GenerationChunk, prompt: str) -> None:
    usage = chunk.usage
    if usage is None:
        return
    if (
        usage.prompt_eval_count + _OUTPUT_TOKENS + _FORMATTING_RESERVE_TOKENS
        > _MODEL_CONTEXT_TOKENS
    ):
        raise GenerationServiceError(
            "generation prompt exceeded the configured model context budget"
        )
