from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Chat,
    ChatScope,
    ChatTurn,
    Chunk,
    Document,
    LibraryNode,
    TurnCitation,
    TurnSource,
)
from app.db.repositories import RetrievedChunk
from app.domain import DocumentState
from app.security.actor import ActorContext
from app.services.answer_protocol import is_explicit_insufficient_context
from app.services.chunking import count_tokens
from app.services.library import (
    LibraryCorruption,
    acquire_library_lock,
    locations_for_documents_in_session,
)
from app.services.ollama_generation import GenerationChunk, OllamaGenerationClient
from app.services.reranker import BgeReranker, RerankedChunk
from app.services.retrieval import RetrievalService

MAX_CHAT_SCOPE_NODES = 256
MAX_HISTORY_TOKENS = 1536
MAX_COMBINED_TOKENS = 6144
MODEL_CONTEXT_TOKENS = 16_384
OUTPUT_TOKENS = 3_072
PROMPT_FORMATTING_RESERVE_TOKENS = 256
INSUFFICIENT_CONTEXT_ANSWER = (
    "I don't have sufficient context in the selected documents to answer that question."
)
RESTART_ERROR = "generation interrupted by application restart"
MAX_VISIBLE_REASONING_CODEPOINTS = 20_000
_SOURCE_LABEL = re.compile(r"\[S(\d+)\]")
_SOURCE_LIKE = re.compile(r"\[S[^\]\r\n]*\]")
_LOGGER = logging.getLogger(__name__)
_TITLE_TIMEOUT_SECONDS = 5.0
_TITLE_PROMPT_PREFIX = (
    "Write a concise title for the user's question. Return only the title: "
    "one line, at most 60 Unicode characters, with no quotes, markdown, or "
    "'Title:' prefix.\n\nUser question:\n"
)


class ChatError(RuntimeError):
    pass


class ChatNotFound(ChatError):
    pass


class ChatConflict(ChatError):
    pass


class ChatAccessRevoked(ChatConflict):
    pass


class ChatValidation(ValueError):
    pass


class ChatPreparationError(ChatError):
    pass


TransitionCallback = Callable[["BeginTurn", str, str], Awaitable[None]]
MonitorCallback = Callable[["PreparedChatTurn"], Awaitable[None]]
FinalizeCallback = Callable[
    [
        "PreparedChatTurn",
        str,
        bool,
        Sequence["ChatSourceSnapshot"],
    ],
    Awaitable[tuple["ChatSourceSnapshot", ...]],
]


@dataclass(frozen=True, slots=True)
class HistoryPair:
    question: str
    answer: str


@dataclass(frozen=True, slots=True)
class BeginTurn:
    actor: ActorContext
    chat_id: uuid.UUID
    turn_id: uuid.UUID
    token: uuid.UUID | None
    question: str
    attempt: int
    document_ids: tuple[uuid.UUID, ...]
    history: tuple[HistoryPair, ...]
    retrieval_query: str
    already_complete: bool = False
    continuation_answer: str | None = None


@dataclass(frozen=True, slots=True)
class ChatSourceSnapshot:
    rank: int
    label: str
    document_id: uuid.UUID | None
    chunk_id: uuid.UUID | None
    document_id_snapshot: uuid.UUID
    chunk_id_snapshot: uuid.UUID
    original_filename: str
    display_name: str
    logical_path: str
    page_start: int
    page_end: int
    section: str | None
    source_sha256: str
    text_sha256: str
    retrieval_distance: float
    rerank_score: float
    text: str
    token_count: int

    def payload(self) -> dict[str, object]:
        return {
            "label": self.label,
            "rank": self.rank,
            "document_id": str(self.document_id) if self.document_id else None,
            "chunk_id": str(self.chunk_id) if self.chunk_id else None,
            "document_id_snapshot": str(self.document_id_snapshot),
            "chunk_id_snapshot": str(self.chunk_id_snapshot),
            "filename": self.original_filename,
            "display_name": self.display_name,
            "logical_path": self.logical_path,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section": self.section,
            "source_available": self.document_id is not None,
        }


@dataclass(frozen=True, slots=True)
class PreparedChatTurn:
    actor: ActorContext
    chat_id: uuid.UUID
    turn_id: uuid.UUID
    token: uuid.UUID | None
    prompt: str | None
    sources: tuple[ChatSourceSnapshot, ...]
    already_complete: bool = False
    access_revoked: bool = False
    continuation_answer: str | None = None
    citation_base_prompt: str | None = None


def normalize_chat_title(value: str) -> str:
    title = unicodedata.normalize("NFC", value.strip())
    if not 1 <= len(title) <= 255:
        raise ChatValidation("title must contain 1-255 characters")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in title):
        raise ChatValidation("title contains a forbidden Unicode character")
    return title


def normalize_question(value: str) -> str:
    question = unicodedata.normalize("NFC", value.strip())
    if not 1 <= len(question) <= 2000:
        raise ChatValidation("question must contain 1-2000 characters")
    if any(
        unicodedata.category(char) in {"Cf", "Cs"}
        or (unicodedata.category(char) == "Cc" and char not in {"\n", "\r", "\t"})
        for char in question
    ):
        raise ChatValidation("question contains a forbidden Unicode character")
    return question


class ChatService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        retrieval: RetrievalService,
        reranker: BgeReranker,
        generator: OllamaGenerationClient,
    ) -> None:
        self._session_factory = session_factory
        self._retrieval = retrieval
        self._reranker = reranker
        self._generator = generator

    async def create(
        self, actor: ActorContext, session: AsyncSession, title: str | None
    ) -> Chat:
        resolved = normalize_chat_title(title) if title is not None else "New chat"
        chat_id = await session.scalar(
            text("SELECT v4_create_chat(:title, 'all_ready')"),
            {"title": resolved},
        )
        chat = await session.get(Chat, chat_id)
        if chat is None:
            raise RuntimeError("created chat is not visible")
        return chat

    async def list(self, actor: ActorContext, session: AsyncSession) -> list[Chat]:
        rows = (
            await session.execute(
                text(
                    "SELECT chat_id FROM v4_list_chats() "
                    "ORDER BY updated_at DESC, chat_id DESC"
                )
            )
        ).scalars()
        ids = list(rows)
        chats = list(await session.scalars(select(Chat).where(Chat.id.in_(ids))))
        by_id = {chat.id: chat for chat in chats}
        return [by_id[chat_id] for chat_id in ids if chat_id in by_id]

    async def rename(
        self,
        actor: ActorContext,
        session: AsyncSession,
        chat_id: uuid.UUID,
        title: str,
    ) -> Chat:
        resolved = normalize_chat_title(title)
        changed = await session.scalar(
            text("SELECT v4_rename_chat(:chat_id, :title)"),
            {"chat_id": chat_id, "title": resolved},
        )
        if not changed:
            raise ChatNotFound("chat not found")
        chat = await session.get(Chat, chat_id)
        if chat is None:
            raise ChatNotFound("chat not found")
        return chat

    async def delete(
        self, actor: ActorContext, session: AsyncSession, chat_id: uuid.UUID
    ) -> None:
        if await self._has_generating(session, actor, chat_id):
            raise ChatConflict("chat has an active generation")
        deleted = await session.scalar(
            text("SELECT v4_delete_chat(:chat_id)"), {"chat_id": chat_id}
        )
        if not deleted:
            raise ChatNotFound("chat not found")

    async def save_scope(
        self,
        actor: ActorContext,
        session: AsyncSession,
        chat_id: uuid.UUID,
        mode: str,
        node_ids: list[uuid.UUID],
    ) -> tuple[Chat, tuple[uuid.UUID, ...]]:
        if mode not in {"all_ready", "selected"}:
            raise ChatValidation("scope mode must be all_ready or selected")
        if len(node_ids) != len(set(node_ids)):
            raise ChatValidation("scope node IDs must be distinct")
        if mode == "all_ready" and node_ids:
            raise ChatValidation("all_ready scope must not include node IDs")
        if mode == "selected" and not 1 <= len(node_ids) <= MAX_CHAT_SCOPE_NODES:
            raise ChatValidation("selected scope requires 1-256 node IDs")
        await acquire_library_lock(session)
        chat = await self._owned_chat(session, actor, chat_id)
        if await self._has_generating(session, actor, chat_id):
            raise ChatConflict("chat has an active generation")
        canonical = (
            await self._canonical_scope(session, actor, node_ids)
            if mode == "selected"
            else ()
        )
        await session.scalar(
            text("SELECT v4_replace_chat_scope(:chat_id, :node_ids)"),
            {"chat_id": chat_id, "node_ids": list(canonical)},
        )
        await session.refresh(chat)
        return chat, canonical

    async def get(
        self,
        actor: ActorContext,
        session: AsyncSession,
        chat_id: uuid.UUID,
        *,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, object]:
        if page < 1:
            raise ChatValidation("page must be positive")
        if not 1 <= limit <= 100:
            raise ChatValidation("limit must be between 1 and 100")
        async with session.begin_nested():
            chat = await session.scalar(
                select(Chat).where(
                    Chat.id == chat_id,
                    Chat.owner_user_id == actor.user_id,
                    func.v4_current_actor_id() == actor.user_id,
                )
            )
            if chat is None:
                raise ChatNotFound("chat not found")
            scope_ids = list(
                await session.scalars(
                    select(ChatScope.node_id)
                    .where(ChatScope.chat_id == chat_id)
                    .order_by(ChatScope.node_id)
                )
            )
            total = int(
                await session.scalar(
                    text("SELECT count(*) FROM v4_chat_history WHERE chat_id=:chat_id"),
                    {"chat_id": chat_id},
                )
                or 0
            )
            turn_rows = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM v4_chat_history "
                            "WHERE chat_id=:chat_id ORDER BY ordinal "
                            "OFFSET :offset LIMIT :limit"
                        ),
                        {
                            "chat_id": chat_id,
                            "offset": (page - 1) * limit,
                            "limit": limit,
                        },
                    )
                )
                .mappings()
                .all()
            )
            redacted_by_turn = {row["id"]: bool(row["redacted"]) for row in turn_rows}
            turns = [
                ChatTurn(
                    **{key: value for key, value in row.items() if key != "redacted"}
                )
                for row in turn_rows
            ]
            turn_ids = [turn.id for turn in turns]
            sources = (
                [
                    TurnSource(**dict(row))
                    for row in (
                        (
                            await session.execute(
                                text(
                                    "SELECT * FROM v4_authorized_turn_sources "
                                    "WHERE turn_id = ANY(:turn_ids) "
                                    "ORDER BY turn_id, rank"
                                ),
                                {"turn_ids": turn_ids},
                            )
                        )
                        .mappings()
                        .all()
                    )
                ]
                if turn_ids
                else []
            )
            citations = (
                [
                    TurnCitation(**dict(row))
                    for row in (
                        (
                            await session.execute(
                                text(
                                    "SELECT * FROM v4_authorized_turn_citations "
                                    "WHERE turn_id = ANY(:turn_ids) "
                                    "ORDER BY turn_id, ordinal"
                                ),
                                {"turn_ids": turn_ids},
                            )
                        )
                        .mappings()
                        .all()
                    )
                ]
                if turn_ids
                else []
            )
            sources_by_turn: dict[uuid.UUID, list[TurnSource]] = {}
            citations_by_turn: dict[uuid.UUID, list[TurnCitation]] = {}
            for source in sources:
                sources_by_turn.setdefault(source.turn_id, []).append(source)
            for citation in citations:
                citations_by_turn.setdefault(citation.turn_id, []).append(citation)
            turn_payloads = []
            for original_turn in turns:
                authorized_sources = sources_by_turn.get(original_turn.id, [])
                redacted = redacted_by_turn.get(original_turn.id, True)
                turn = copy.copy(original_turn)
                if redacted:
                    turn.final_answer = None
                    turn.partial_answer = None
                    authorized_sources = []
                    citation_ranks: list[int] = []
                else:
                    citation_ranks = [
                        item.source_rank
                        for item in citations_by_turn.get(original_turn.id, [])
                    ]
                turn_payloads.append(
                    {
                        "turn": turn,
                        "sources": authorized_sources,
                        "citation_ranks": citation_ranks,
                    }
                )
            return {
                "chat": chat,
                "scope_ids": scope_ids,
                "turns": turn_payloads,
                "page": page,
                "limit": limit,
                "total": total,
            }

    async def prepare_message(
        self,
        actor: ActorContext,
        session: AsyncSession,
        chat_id: uuid.UUID,
        question: str,
        auto_title: str = "New chat",
    ) -> BeginTurn:
        return await self._begin_new(
            actor,
            session,
            chat_id,
            normalize_question(question),
            normalize_chat_title(auto_title),
        )

    async def should_generate_title(
        self,
        actor: ActorContext,
        session: AsyncSession,
        chat_id: uuid.UUID,
    ) -> bool:
        row = await session.execute(
            select(Chat.next_turn_ordinal, Chat.title_is_manual).where(
                Chat.id == chat_id,
                Chat.owner_user_id == actor.user_id,
                func.v4_current_actor_id() == actor.user_id,
            )
        )
        values = row.one_or_none()
        if values is None:
            raise ChatNotFound("chat not found")
        return values.next_turn_ordinal == 1 and not values.title_is_manual

    async def generate_first_title(self, question: str) -> str:
        normalized = normalize_question(question)
        prompt = f"{_TITLE_PROMPT_PREFIX}{normalized}"
        stream = self._generator.stream(prompt, think=False)
        chunks: list[str] = []
        terminal = False

        async def consume() -> None:
            nonlocal terminal
            try:
                async for chunk in stream:
                    if chunk.type == "thinking":
                        raise ChatValidation("title generation returned reasoning")
                    if chunk.type == "answer":
                        chunks.append(chunk.text)
                        if len("".join(chunks)) > 256:
                            raise ChatValidation("generated title is too long")
                    elif chunk.done_reason == "stop":
                        _validate_generation_usage(chunk, prompt)
                        terminal = True
                    else:
                        raise ChatValidation(
                            "title generation did not stop normally"
                        )
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()

        task = asyncio.create_task(consume())
        try:
            done, _ = await asyncio.wait(
                {task}, timeout=_TITLE_TIMEOUT_SECONDS
            )
            if not done:
                task.cancel()
                task.add_done_callback(_consume_background_task_result)
                _LOGGER.warning("automatic chat title generation timed out")
                return "New chat"
            await task
            if not terminal:
                raise ChatValidation(
                    "title generation ended without a stop frame"
                )
            return _normalize_generated_title("".join(chunks))
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
                task.add_done_callback(_consume_background_task_result)
            raise
        except Exception as exc:
            _LOGGER.warning("automatic chat title generation failed: %s", exc)
            return "New chat"

    async def prepare_retry(
        self,
        actor: ActorContext,
        session: AsyncSession,
        chat_id: uuid.UUID,
        turn_id: uuid.UUID,
    ) -> BeginTurn:
        return await self._begin_retry(actor, session, chat_id, turn_id)

    async def embed_retrieval_query(self, begin: BeginTurn) -> list[float] | None:
        if begin.already_complete or begin.continuation_answer is not None:
            return None
        return await self._retrieval.embed_query(begin.retrieval_query)

    async def retrieve_candidates(
        self,
        actor: ActorContext,
        session: AsyncSession,
        begin: BeginTurn,
        query_vector: list[float],
    ) -> Sequence[RetrievedChunk]:
        if actor != begin.actor:
            raise ChatNotFound("chat not found")
        return await self._retrieval.retrieve_vector(
            actor,
            session,
            query_vector,
            limit=20,
            document_ids=begin.document_ids,
        )

    async def rerank_candidates(
        self,
        begin: BeginTurn,
        candidates: Sequence[RetrievedChunk],
    ) -> Sequence[RerankedChunk]:
        return await self._reranker.rerank(begin.retrieval_query, candidates, limit=6)

    async def snapshot_sources(
        self,
        actor: ActorContext,
        session: AsyncSession,
        begin: BeginTurn,
        ranked: Sequence[RerankedChunk],
    ) -> PreparedChatTurn:
        if actor != begin.actor:
            raise ChatNotFound("chat not found")
        if begin.already_complete:
            return PreparedChatTurn(
                begin.actor,
                begin.chat_id,
                begin.turn_id,
                None,
                None,
                (),
                True,
            )
        if begin.continuation_answer is not None:
            await acquire_library_lock(session)
            await self._owned_chat(session, actor, begin.chat_id)
            turn = (
                await session.execute(
                    text(
                        "SELECT status, generation_token FROM v4_chat_history "
                        "WHERE chat_id=:chat_id AND id=:turn_id"
                    ),
                    {"chat_id": begin.chat_id, "turn_id": begin.turn_id},
                )
            ).one_or_none()
            if (
                turn is None
                or turn.status != "generating"
                or turn.generation_token != begin.token
            ):
                raise ChatConflict("turn generation token is stale")
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM v4_authorized_turn_sources "
                            "WHERE turn_id=:turn_id ORDER BY rank"
                        ),
                        {"turn_id": begin.turn_id},
                    )
                )
                .mappings()
                .all()
            )
            sources = tuple(
                _snapshot_from_row(TurnSource(**dict(row))) for row in rows
            )
            if not sources:
                raise ChatAccessRevoked("source access was revoked")
            selected, base_prompt = _budget_prompt(
                begin.question, begin.history, sources
            )
            if len(selected) != len(sources) or base_prompt is None:
                raise RuntimeError(
                    "continuation source snapshot exceeded prompt budget"
                )
            return PreparedChatTurn(
                begin.actor,
                begin.chat_id,
                begin.turn_id,
                begin.token,
                _continuation_prompt(base_prompt, begin.continuation_answer),
                sources,
                continuation_answer=begin.continuation_answer,
                citation_base_prompt=base_prompt,
            )
        snapshots, prompt = await self._snapshot_sources(
            actor, begin, session, ranked, begin.history
        )
        return PreparedChatTurn(
            begin.actor,
            begin.chat_id,
            begin.turn_id,
            begin.token if snapshots else None,
            prompt,
            snapshots,
            not snapshots,
            citation_base_prompt=prompt,
        )

    async def check_generation_available(
        self, prepared: PreparedChatTurn
    ) -> PreparedChatTurn:
        if not prepared.already_complete and prepared.prompt is not None:
            await self._generator.check_available()
        return prepared

    async def _monitored_generation(
        self,
        prepared: PreparedChatTurn,
        prompt: str,
        *,
        think: bool,
        monitor: MonitorCallback,
    ) -> AsyncIterator[GenerationChunk]:
        chunk_iterator = self._generator.stream(prompt, think=think).__aiter__()
        next_chunk: asyncio.Task[GenerationChunk] | None = None
        last_monitor = time.monotonic()
        try:
            while True:
                if next_chunk is None:
                    next_chunk = asyncio.create_task(anext(chunk_iterator))
                timeout = max(0.0, 1.0 - (time.monotonic() - last_monitor))
                done, _ = await asyncio.wait({next_chunk}, timeout=timeout)
                if not done:
                    await monitor(prepared)
                    last_monitor = time.monotonic()
                    continue
                try:
                    chunk = next_chunk.result()
                except StopAsyncIteration:
                    next_chunk = None
                    return
                next_chunk = None
                if time.monotonic() - last_monitor >= 1.0:
                    await monitor(prepared)
                    last_monitor = time.monotonic()
                yield chunk
        finally:
            if next_chunk is not None and not next_chunk.done():
                next_chunk.cancel()
                await asyncio.gather(next_chunk, return_exceptions=True)
            close = getattr(chunk_iterator, "aclose", None)
            if close is not None:
                await close()

    async def stream(
        self,
        prepared: PreparedChatTurn,
        *,
        finalize: FinalizeCallback,
        transition: TransitionCallback,
        monitor: MonitorCallback,
        sequence_start: int = 1,
    ) -> AsyncIterator[str]:
        sequence = sequence_start

        def event(name: str, payload: dict[str, object]) -> str:
            nonlocal sequence
            result = chat_stream_event(
                name,
                prepared.chat_id,
                prepared.turn_id,
                sequence,
                payload,
            )
            sequence += 1
            return result

        if prepared.access_revoked:
            yield event(
                "error",
                {
                    "code": "access_revoked",
                    "message": "access to a source was revoked",
                },
            )
            return
        if prepared.already_complete or prepared.prompt is None:
            yield event(
                "sources",
                {"sources": [source.payload() for source in prepared.sources]},
            )
            yield event("status", {"phase": "validating_citations"})
            yield event(
                "final",
                {
                    "answer": INSUFFICIENT_CONTEXT_ANSWER,
                    "insufficient_context": True,
                    "citations": [],
                },
            )
            return

        parts: list[str] = (
            [prepared.continuation_answer]
            if prepared.continuation_answer is not None
            else []
        )
        visible_reasoning = 0
        reasoning_truncated = False
        answer_started = False
        automatic_continuation_available = prepared.continuation_answer is None
        begin = BeginTurn(
            prepared.actor,
            prepared.chat_id,
            prepared.turn_id,
            prepared.token,
            "",
            0,
            (),
            (),
            "",
        )
        try:
            await monitor(prepared)
            yield event(
                "sources",
                {"sources": [source.payload() for source in prepared.sources]},
            )
            yield event("status", {"phase": "reasoning"})
            yield event("reasoning_start", {})
            if prepared.continuation_answer is not None:
                answer_started = True
                yield event("reasoning_end", {"truncated": False})
                yield event("status", {"phase": "streaming_answer"})
                yield event("token", {"text": prepared.continuation_answer})
                yield event("status", {"phase": "continuing_answer"})

            generation_prompt = prepared.prompt
            generation_thinks = prepared.continuation_answer is None
            while True:
                stop_reason: str | None = None
                async for chunk in self._monitored_generation(
                    prepared,
                    generation_prompt,
                    think=generation_thinks,
                    monitor=monitor,
                ):
                    if chunk.type == "done":
                        _validate_generation_usage(chunk, generation_prompt)
                        stop_reason = chunk.done_reason
                        break
                    if chunk.type == "thinking":
                        if answer_started or not generation_thinks:
                            raise RuntimeError(
                                "generation returned thinking after answer text"
                            )
                        remaining = (
                            MAX_VISIBLE_REASONING_CODEPOINTS - visible_reasoning
                        )
                        if remaining > 0:
                            visible_text = chunk.text[:remaining]
                            visible_reasoning += len(visible_text)
                            if visible_text:
                                yield event(
                                    "reasoning_delta", {"text": visible_text}
                                )
                        if len(chunk.text) > remaining:
                            reasoning_truncated = True
                        continue
                    if chunk.type != "answer":
                        raise RuntimeError(
                            "generation returned an invalid chunk type"
                        )
                    if not answer_started:
                        answer_started = True
                        yield event(
                            "reasoning_end",
                            {"truncated": reasoning_truncated},
                        )
                        yield event("status", {"phase": "streaming_answer"})
                    parts.append(chunk.text)
                    yield event("token", {"text": chunk.text})
                if stop_reason is None:
                    raise RuntimeError(
                        "generation ended without a terminal stop reason"
                    )
                if stop_reason == "stop":
                    break
                if stop_reason != "length":
                    raise RuntimeError("generation returned an invalid stop reason")
                if not answer_started:
                    answer_started = True
                    yield event(
                        "reasoning_end", {"truncated": reasoning_truncated}
                    )
                    yield event("status", {"phase": "streaming_answer"})
                if automatic_continuation_available:
                    automatic_continuation_available = False
                    yield event("status", {"phase": "continuing_answer"})
                    generation_prompt = _continuation_prompt(
                        prepared.prompt, "".join(parts)
                    )
                    generation_thinks = False
                    continue
                answer = "".join(parts).strip()
                if not answer:
                    raise RuntimeError(
                        "model exhausted the generation limit without an answer"
                    )
                await transition(
                    begin,
                    "length_limited",
                    answer,
                )
                yield event(
                    "error",
                    {
                        "code": "generation_limit",
                        "message": (
                            "The response reached its generation limit twice. "
                            "The partial answer is unverified; continue the response."
                        ),
                    },
                )
                return
            answer = "".join(parts).strip()
            cited = _validated_source_ranks(answer, prepared.sources)
            yield event("status", {"phase": "validating_citations"})
            if (
                cited is None
                and answer
                and not is_explicit_insufficient_context(answer)
            ):
                yield event("status", {"phase": "repairing_citations"})
                yield event("answer_reset", {})
                repaired_parts: list[str] = []
                repair_stop_reason: str | None = None
                repair_prompt = _citation_repair_prompt(
                    prepared.citation_base_prompt or prepared.prompt,
                    answer,
                )
                async for chunk in self._monitored_generation(
                    prepared,
                    repair_prompt,
                    think=False,
                    monitor=monitor,
                ):
                    if chunk.type == "done":
                        _validate_generation_usage(chunk, repair_prompt)
                        repair_stop_reason = chunk.done_reason
                        break
                    if chunk.type != "answer":
                        raise RuntimeError(
                            "citation repair returned an invalid chunk type"
                        )
                    repaired_parts.append(chunk.text)
                    yield event("token", {"text": chunk.text})
                repaired_answer = "".join(repaired_parts).strip()
                repaired_cited = (
                    _validated_source_ranks(repaired_answer, prepared.sources)
                    if repair_stop_reason == "stop"
                    else None
                )
                if repaired_cited is None:
                    await transition(
                        begin,
                        "citation_failed",
                        answer,
                    )
                    yield event(
                        "error",
                        {
                            "code": "citation_validation_failed",
                            "message": (
                                "The answer could not be citation-verified. "
                                "The draft was preserved for retry."
                            ),
                        },
                    )
                    return
                answer = repaired_answer
                cited = repaired_cited
                yield event("status", {"phase": "validating_citations"})
            insufficient = is_explicit_insufficient_context(answer)
            final_answer = INSUFFICIENT_CONTEXT_ANSWER if insufficient else answer
            citation_sources = () if insufficient else cited
            if not insufficient and citation_sources is None:
                raise RuntimeError("citation validation returned no sources")
            citation_sources = await finalize(
                prepared,
                final_answer,
                insufficient,
                citation_sources or (),
            )
        except asyncio.CancelledError:
            await self._shield_transition(
                transition, begin, "interrupted", "generation cancelled"
            )
            raise
        except GeneratorExit:
            await self._shield_transition(
                transition, begin, "interrupted", "generation cancelled"
            )
            raise
        except ChatAccessRevoked:
            await self._shield_transition(
                transition, begin, "access_revoked", "access_revoked"
            )
            yield event(
                "error",
                {
                    "code": "access_revoked",
                    "message": "access to a source was revoked",
                },
            )
            return
        except Exception as exc:
            message = _bounded_error(exc)
            await self._shield_transition(transition, begin, "failed", message)
            yield event(
                "error",
                {
                    "code": "generation_failed",
                    "message": message,
                },
            )
            return
        yield event(
            "final",
            {
                "answer": final_answer,
                "insufficient_context": insufficient,
                "citations": [source.payload() for source in citation_sources],
            },
        )

    async def interrupt_prepared(
        self,
        prepared: PreparedChatTurn,
        *,
        transition: TransitionCallback,
    ) -> None:
        if (
            prepared.already_complete
            or prepared.access_revoked
            or prepared.token is None
        ):
            return
        await self._shield_transition(
            transition,
            BeginTurn(
                prepared.actor,
                prepared.chat_id,
                prepared.turn_id,
                prepared.token,
                "",
                0,
                (),
                (),
                "",
            ),
            "interrupted",
            "generation cancelled",
        )

    async def _begin_new(
        self,
        actor: ActorContext,
        session: AsyncSession,
        chat_id: uuid.UUID,
        question: str,
        auto_title: str,
    ) -> BeginTurn:
        token = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "SELECT * FROM v4_begin_turn("
                    ":chat_id, :question, :generation_token, :auto_title)"
                ),
                {
                    "chat_id": chat_id,
                    "question": question,
                    "generation_token": token,
                    "auto_title": auto_title,
                },
            )
        ).one()
        chat = await self._owned_chat(session, actor, chat_id)
        document_ids = await self._resolve_scope(session, actor, chat)
        history = await self._completed_history(session, actor, chat.id)
        previous = history[-1].question if history else None
        already_complete = not document_ids
        if already_complete:
            finalized = await session.scalar(
                text(
                    "SELECT v4_finalize_turn("
                    ":turn_id, :generation_token, :answer, true, :source_ranks)"
                ),
                {
                    "turn_id": row.turn_id,
                    "generation_token": token,
                    "answer": INSUFFICIENT_CONTEXT_ANSWER,
                    "source_ranks": [],
                },
            )
            if not finalized:
                raise ChatConflict("turn generation token is stale")
        return BeginTurn(
            actor,
            chat.id,
            row.turn_id,
            None if already_complete else token,
            question,
            1,
            document_ids,
            history,
            _retrieval_query(question, previous),
            already_complete,
        )

    async def _begin_retry(
        self,
        actor: ActorContext,
        session: AsyncSession,
        chat_id: uuid.UUID,
        turn_id: uuid.UUID,
    ) -> BeginTurn:
        token = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "SELECT * FROM v4_retry_turn(:chat_id, :turn_id, :generation_token)"
                ),
                {
                    "chat_id": chat_id,
                    "turn_id": turn_id,
                    "generation_token": token,
                },
            )
        ).one()
        if row.result_status == "not_found":
            raise ChatNotFound("turn not found")
        if row.result_status in {"not_latest", "not_retryable"}:
            raise ChatConflict(
                "only the latest failed, interrupted, length-limited, or "
                "citation-failed turn can retry"
            )
        if row.result_status != "retried":
            raise RuntimeError("turn retry returned an invalid outcome")
        chat = await self._owned_chat(session, actor, chat_id)
        document_ids = await self._resolve_scope(session, actor, chat)
        history = await self._completed_history(session, actor, chat.id)
        previous = history[-1].question if history else None
        continuation_answer = row.partial_answer
        already_complete = not document_ids and continuation_answer is None
        if already_complete:
            finalized = await session.scalar(
                text(
                    "SELECT v4_finalize_turn("
                    ":turn_id, :generation_token, :answer, true, :source_ranks)"
                ),
                {
                    "turn_id": row.turn_id,
                    "generation_token": token,
                    "answer": INSUFFICIENT_CONTEXT_ANSWER,
                    "source_ranks": [],
                },
            )
            if not finalized:
                raise ChatConflict("turn generation token is stale")
        return BeginTurn(
            actor,
            chat.id,
            row.turn_id,
            None if already_complete else token,
            row.question,
            row.attempt,
            document_ids,
            history,
            _retrieval_query(row.question, previous),
            already_complete,
            continuation_answer,
        )

    async def _snapshot_sources(
        self,
        actor: ActorContext,
        begin: BeginTurn,
        session: AsyncSession,
        ranked: Sequence[RerankedChunk],
        history: Sequence[HistoryPair],
    ) -> tuple[tuple[ChatSourceSnapshot, ...], str | None]:
        await acquire_library_lock(session)
        await self._owned_chat(session, actor, begin.chat_id)
        turn = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM v4_chat_history "
                        "WHERE chat_id=:chat_id AND id=:turn_id"
                    ),
                    {"chat_id": begin.chat_id, "turn_id": begin.turn_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            turn is None
            or turn["status"] != "generating"
            or turn["generation_token"] != begin.token
        ):
            raise ChatConflict("turn generation token is stale")
        chunk_ids = [item.candidate.chunk.id for item in ranked]
        retained = {
            chunk.id: (chunk, document)
            for chunk, document in (
                await session.execute(
                    select(Chunk, Document)
                    .join(Document, Document.id == Chunk.document_id)
                    .where(
                        Chunk.id.in_(chunk_ids),
                        Document.id.in_(begin.document_ids),
                        Document.state == DocumentState.READY.value,
                    )
                )
            ).all()
        }
        document_ids = list(
            dict.fromkeys(document.id for _chunk, document in retained.values())
        )
        locations = await locations_for_documents_in_session(
            session, actor, document_ids
        )
        drafts: list[ChatSourceSnapshot] = []
        for item in ranked:
            current = retained.get(item.candidate.chunk.id)
            if current is None:
                continue
            chunk, document = current
            location = locations.get(document.id)
            if location is None:
                raise LibraryCorruption("ready document has no canonical location")
            rank = len(drafts) + 1
            drafts.append(
                ChatSourceSnapshot(
                    rank,
                    f"S{rank}",
                    document.id,
                    chunk.id,
                    document.id,
                    chunk.id,
                    chunk.filename,
                    location.display_name,
                    location.logical_path,
                    chunk.page_start,
                    chunk.page_end,
                    chunk.section,
                    chunk.source_sha256,
                    chunk.text_sha256,
                    float(item.candidate.distance),
                    float(item.score),
                    chunk.text,
                    chunk.token_count,
                )
            )
        selected, prompt = _budget_prompt(begin.question, history, drafts)
        if not selected:
            finalized = await session.scalar(
                text(
                    "SELECT v4_finalize_turn("
                    ":turn_id, :generation_token, :answer, true, :source_ranks)"
                ),
                {
                    "turn_id": begin.turn_id,
                    "generation_token": begin.token,
                    "answer": INSUFFICIENT_CONTEXT_ANSWER,
                    "source_ranks": [],
                },
            )
            if not finalized:
                raise ChatConflict("turn generation token is stale")
            return (), None
        source_payload = [
            {
                "rank": source.rank,
                "chunk_id": str(source.chunk_id),
                "retrieval_distance": source.retrieval_distance,
                "rerank_score": source.rerank_score,
            }
            for source in selected
        ]
        stored = await session.scalar(
            text(
                "SELECT v4_store_turn_sources("
                ":turn_id, :generation_token, CAST(:sources AS jsonb))"
            ),
            {
                "turn_id": begin.turn_id,
                "generation_token": begin.token,
                "sources": json.dumps(source_payload),
            },
        )
        if stored != len(selected):
            raise ChatConflict("turn source snapshot was incomplete")
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM v4_authorized_turn_sources "
                        "WHERE turn_id=:turn_id ORDER BY rank"
                    ),
                    {"turn_id": begin.turn_id},
                )
            )
            .mappings()
            .all()
        )
        authoritative = tuple(
            _snapshot_from_row(TurnSource(**dict(row))) for row in rows
        )
        if len(authoritative) != len(selected):
            raise ChatAccessRevoked("source access was revoked")
        authoritative, prompt = _budget_prompt(begin.question, history, authoritative)
        if len(authoritative) != len(selected) or prompt is None:
            raise RuntimeError("authoritative source snapshot exceeded prompt budget")
        return authoritative, prompt

    async def complete(
        self,
        actor: ActorContext,
        session: AsyncSession,
        prepared: PreparedChatTurn,
        answer: str,
        insufficient: bool,
        citations: Sequence[ChatSourceSnapshot],
    ) -> tuple[ChatSourceSnapshot, ...]:
        if actor != prepared.actor:
            raise ChatNotFound("chat not found")
        await acquire_library_lock(session)
        await self.monitor(actor, session, prepared)
        finalized = await session.scalar(
            text(
                "SELECT v4_finalize_turn("
                ":turn_id, :generation_token, :answer, :insufficient, "
                ":source_ranks)"
            ),
            {
                "turn_id": prepared.turn_id,
                "generation_token": prepared.token,
                "answer": answer,
                "insufficient": insufficient,
                "source_ranks": [source.rank for source in citations],
            },
        )
        if not finalized:
            row = (
                await session.execute(
                    text(
                        "SELECT status FROM v4_chat_history "
                        "WHERE chat_id=:chat_id AND id=:turn_id"
                    ),
                    {
                        "chat_id": prepared.chat_id,
                        "turn_id": prepared.turn_id,
                    },
                )
            ).one_or_none()
            if row is not None and row.status == "access_revoked":
                raise ChatAccessRevoked("source access was revoked")
            raise ChatConflict("turn generation token is stale")
        if not citations:
            return ()
        source_rows = [
            TurnSource(**dict(row))
            for row in (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM v4_authorized_turn_sources "
                            "WHERE turn_id=:turn_id AND rank = ANY(:ranks)"
                        ),
                        {
                            "turn_id": prepared.turn_id,
                            "ranks": [source.rank for source in citations],
                        },
                    )
                )
                .mappings()
                .all()
            )
        ]
        sources_by_rank = {source.rank: source for source in source_rows}
        if set(sources_by_rank) != {source.rank for source in citations}:
            raise ChatAccessRevoked("source access was revoked")
        return tuple(
            _snapshot_from_row(sources_by_rank[source.rank]) for source in citations
        )

    @staticmethod
    async def _shield_transition(
        transition: TransitionCallback,
        begin: BeginTurn,
        status: str,
        message: str,
    ) -> None:
        task = asyncio.create_task(transition(begin, status, message))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task

    async def transition(
        self,
        actor: ActorContext,
        session: AsyncSession,
        begin: BeginTurn,
        status: str,
        message: str,
    ) -> None:
        if begin.token is None:
            return
        if actor != begin.actor:
            raise ChatNotFound("chat not found")
        if status == "access_revoked":
            changed = await session.scalar(
                text("SELECT v4_mark_turn_access_revoked(:turn_id, :generation_token)"),
                {
                    "turn_id": begin.turn_id,
                    "generation_token": begin.token,
                },
            )
            if not changed:
                return
            return
        if status == "interrupted":
            outcome = await session.scalar(
                text("SELECT v4_interrupt_turn(:chat_id, :turn_id, :generation_token)"),
                {
                    "chat_id": begin.chat_id,
                    "turn_id": begin.turn_id,
                    "generation_token": begin.token,
                },
            )
            if outcome not in {
                "interrupted",
                "already_interrupted",
                "stale",
                "not_found",
            }:
                raise RuntimeError("turn interruption returned an invalid outcome")
            return
        if status == "length_limited":
            outcome = await session.scalar(
                text(
                    "SELECT v4_mark_turn_length_limited("
                    ":chat_id, :turn_id, :generation_token, :partial_answer)"
                ),
                {
                    "chat_id": begin.chat_id,
                    "turn_id": begin.turn_id,
                    "generation_token": begin.token,
                    "partial_answer": message,
                },
            )
            if outcome not in {
                "length_limited",
                "already_limited",
                "stale",
                "not_found",
            }:
                raise RuntimeError(
                    "turn length-limit transition returned an invalid outcome"
                )
            return
        if status == "citation_failed":
            outcome = await session.scalar(
                text(
                    "SELECT v4_mark_turn_citation_failed("
                    ":chat_id, :turn_id, :generation_token, :partial_answer)"
                ),
                {
                    "chat_id": begin.chat_id,
                    "turn_id": begin.turn_id,
                    "generation_token": begin.token,
                    "partial_answer": message,
                },
            )
            if outcome not in {
                "citation_failed",
                "already_failed",
                "stale",
                "not_found",
            }:
                raise RuntimeError(
                    "turn citation-failure transition returned an invalid outcome"
                )
            return
        if status != "failed":
            raise ValueError("unsupported turn transition")
        await session.scalar(
            text("SELECT v4_fail_turn(:turn_id, :generation_token, :error)"),
            {
                "turn_id": begin.turn_id,
                "generation_token": begin.token,
                "error": message[:500] or "generation failed",
            },
        )

    async def monitor(
        self,
        actor: ActorContext,
        session: AsyncSession,
        prepared: PreparedChatTurn,
    ) -> None:
        if actor != prepared.actor:
            raise ChatNotFound("chat not found")
        chat_id = await session.scalar(
            select(Chat.id).where(
                Chat.id == prepared.chat_id,
                Chat.owner_user_id == actor.user_id,
                func.v4_current_actor_id() == actor.user_id,
            )
        )
        if chat_id is None:
            raise ChatNotFound("chat not found")
        turn = await session.scalar(
            text(
                "SELECT id FROM v4_chat_history "
                "WHERE chat_id=:chat_id AND id=:turn_id "
                "AND status='generating'"
            ),
            {"chat_id": prepared.chat_id, "turn_id": prepared.turn_id},
        )
        if turn is None:
            raise ChatConflict("turn generation token is stale")
        document_ids = tuple(
            source.document_id
            for source in prepared.sources
            if source.document_id is not None
        )
        if document_ids:
            readable = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(
                        Document.id.in_(document_ids),
                        func.v4_can_read_document(Document.id),
                    )
                )
                or 0
            )
            if readable != len(set(document_ids)):
                raise ChatAccessRevoked("source access was revoked")

    async def _owned_chat(
        self, session: AsyncSession, actor: ActorContext, chat_id: uuid.UUID
    ) -> Chat:
        chat = await session.scalar(
            select(Chat).where(
                Chat.id == chat_id,
                Chat.owner_user_id == actor.user_id,
                func.v4_current_actor_id() == actor.user_id,
            )
        )
        if chat is None:
            raise ChatNotFound("chat not found")
        return chat

    async def _has_generating(
        self, session: AsyncSession, actor: ActorContext, chat_id: uuid.UUID
    ) -> bool:
        return (
            await session.scalar(
                text(
                    "SELECT id FROM v4_chat_history "
                    "WHERE chat_id=:chat_id AND status='generating' LIMIT 1"
                ),
                {"chat_id": chat_id},
            )
            is not None
        )

    async def _canonical_scope(
        self,
        session: AsyncSession,
        actor: ActorContext,
        node_ids: list[uuid.UUID],
    ) -> tuple[uuid.UUID, ...]:
        nodes = list(
            await session.scalars(
                select(LibraryNode).where(
                    func.v4_current_actor_id() == actor.user_id,
                    func.v4_can_view_library_node(LibraryNode.id),
                )
            )
        )
        records = {node.id: node for node in nodes}
        missing = set(node_ids) - records.keys()
        if missing:
            raise ChatNotFound("scope node not found")
        selected = set(node_ids)
        canonical = []
        for node_id in sorted(selected, key=str):
            parent_id = records[node_id].parent_id
            redundant = False
            seen: set[uuid.UUID] = set()
            while parent_id is not None:
                if parent_id in seen:
                    raise LibraryCorruption("library scope ancestry is cyclic")
                seen.add(parent_id)
                parent = records.get(parent_id)
                if parent is None:
                    raise LibraryCorruption("library scope node has no parent")
                if parent_id in selected and parent.kind == "folder":
                    redundant = True
                    break
                parent_id = parent.parent_id
            if not redundant:
                canonical.append(node_id)
        return tuple(canonical)

    async def _resolve_scope(
        self, session: AsyncSession, actor: ActorContext, chat: Chat
    ) -> tuple[uuid.UUID, ...]:
        ready_ids = set(
            await session.scalars(
                select(Document.id)
                .where(Document.state == DocumentState.READY.value)
                .where(
                    func.v4_current_actor_id() == actor.user_id,
                    func.v4_can_read_document(Document.id),
                )
            )
        )
        nodes = list(
            await session.scalars(
                select(LibraryNode).where(
                    func.v4_current_actor_id() == actor.user_id,
                    func.v4_can_view_library_node(LibraryNode.id),
                )
            )
        )
        if chat.scope_mode == "all_ready":
            return tuple(
                sorted(
                    (
                        node.document_id
                        for node in nodes
                        if node.kind == "file" and node.document_id in ready_ids
                    ),
                    key=str,
                )
            )
        selected = set(
            await session.scalars(
                select(ChatScope.node_id).where(ChatScope.chat_id == chat.id)
            )
        )
        records = {node.id: node for node in nodes}
        resolved: set[uuid.UUID] = set()
        for node in nodes:
            if node.kind != "file" or node.document_id not in ready_ids:
                continue
            current: LibraryNode | None = node
            seen: set[uuid.UUID] = set()
            while current is not None:
                if current.id in selected:
                    resolved.add(node.document_id)
                    break
                if current.id in seen:
                    raise LibraryCorruption("library scope ancestry is cyclic")
                seen.add(current.id)
                current = records.get(current.parent_id) if current.parent_id else None
        return tuple(sorted(resolved, key=str))

    async def _completed_history(
        self, session: AsyncSession, actor: ActorContext, chat_id: uuid.UUID
    ) -> tuple[HistoryPair, ...]:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT question, final_answer FROM v4_chat_history "
                        "WHERE chat_id=:chat_id AND status='complete' "
                        "AND NOT redacted ORDER BY ordinal"
                    ),
                    {"chat_id": chat_id},
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            HistoryPair(str(row["question"]), str(row["final_answer"]))
            for row in rows
            if row["final_answer"]
        )


def _retrieval_query(question: str, previous: str | None) -> str:
    if previous is None:
        return question
    return f"Current question:\n{question}\n\nPrevious completed question:\n{previous}"


def _select_history(
    history: Sequence[HistoryPair], budget: int = MAX_HISTORY_TOKENS
) -> str:
    selected: list[str] = []
    for pair in reversed(history):
        block = f"User: {pair.question}\nAssistant: {pair.answer}"
        candidate = [block, *selected]
        if count_tokens("\n\n".join(candidate)) <= budget:
            selected.append(block)
    selected.reverse()
    return "\n\n".join(selected)


def _source_block(source: ChatSourceSnapshot) -> str:
    page = (
        str(source.page_start)
        if source.page_start == source.page_end
        else f"{source.page_start}-{source.page_end}"
    )
    return (
        f"[{source.label}]\nFilename: {source.original_filename}\n"
        f"Current display name: {source.display_name}\n"
        f"Logical path: {source.logical_path}\nPage: {page}\n"
        f"Section: {source.section or 'Not specified'}\nContent:\n{source.text}"
    )


def _normalize_generated_title(value: str) -> str:
    title = value.strip()
    markdown_prefix = re.compile(
        r"^(?:#{1,6}\s|>\s?|[-*+]\s+|\d+[.)]\s+|(?:```|~~~))"
    )
    markdown_marker = title.startswith(
        ("**", "__", "_", "~~", "```", "~~~")
    ) or title.endswith(("**", "__", "_", "~~", "```", "~~~"))
    markdown_link = re.search(r"!?\[[^\]\r\n]*\]\([^)\r\n]+\)", title)
    wrapped_markdown = markdown_marker
    if (
        "\n" in title
        or "\r" in title
        or title.startswith(("`", "'", '"', "“", "‘"))
        or title.endswith(("`", "'", '"', "”", "’"))
        or title.casefold().startswith("title:")
        or markdown_prefix.match(title) is not None
        or markdown_link is not None
        or wrapped_markdown
        or len(title) > 60
    ):
        raise ChatValidation("generated title has an invalid format")
    return normalize_chat_title(title)


def _consume_background_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except BaseException as exc:
        _LOGGER.debug(
            "automatic chat title cleanup completed after the deadline: %s",
            exc,
        )


def _render_prompt(question: str, history: str, sources: str) -> str:
    history_value = history or "No completed conversation history."
    return (
        "Answer the current question using only the labeled source blocks.\n"
        "Conversation history is reference context only and is not factual evidence.\n"
        "Every factual claim must cite [S1], [S2], etc.\n"
        "If sources are insufficient, reply exactly: INSUFFICIENT_CONTEXT\n\n"
        f"Conversation history:\n{history_value}\n\n"
        f"Current question:\n{question}\n\nSources:\n{sources}"
    )


def _continuation_prompt(base_prompt: str, partial_answer: str) -> str:
    prompt = (
        f"{base_prompt}\n\n"
        "The answer below stopped only because the generation limit was reached. "
        "Continue exactly where it stopped. Return only the missing continuation. "
        "Do not repeat, restart, summarize, or revise existing text. Preserve its "
        "Markdown structure and finish every citation and table row.\n\n"
        "<partial_answer>\n"
        f"{partial_answer}\n"
        "</partial_answer>"
    )
    if (
        count_tokens(prompt)
        + OUTPUT_TOKENS
        + PROMPT_FORMATTING_RESERVE_TOKENS
        > MODEL_CONTEXT_TOKENS
    ):
        raise RuntimeError("continuation prompt exceeds the model context budget")
    return prompt


def _citation_repair_prompt(base_prompt: str, draft_answer: str) -> str:
    prompt = (
        f"{base_prompt}\n\n"
        "The draft answer below contains useful content but failed citation "
        "validation. Rewrite it without adding facts. Preserve its meaning and "
        "Markdown structure, including exactly the requested table columns. "
        "Remove any unsupported claim. Add a valid source citation such as [S1] "
        "to every factual table row and factual sentence. Use only source labels "
        "that appear in the labeled source blocks above. Return only the repaired "
        "answer, with no commentary about the repair.\n\n"
        "<draft_answer>\n"
        f"{draft_answer}\n"
        "</draft_answer>"
    )
    if (
        count_tokens(prompt)
        + OUTPUT_TOKENS
        + PROMPT_FORMATTING_RESERVE_TOKENS
        > MODEL_CONTEXT_TOKENS
    ):
        raise RuntimeError("citation repair prompt exceeds the model context budget")
    return prompt


def _budget_prompt(
    question: str,
    history: Sequence[HistoryPair],
    sources: Sequence[ChatSourceSnapshot],
) -> tuple[tuple[ChatSourceSnapshot, ...], str | None]:
    shell = _render_prompt(question, "", "")
    overhead = count_tokens(shell)
    combined = min(
        MAX_COMBINED_TOKENS,
        max(
            0,
            MODEL_CONTEXT_TOKENS
            - OUTPUT_TOKENS
            - PROMPT_FORMATTING_RESERVE_TOKENS
            - overhead,
        ),
    )
    history_text = _select_history(history, min(MAX_HISTORY_TOKENS, combined))
    history_tokens = count_tokens(history_text) if history_text else 0
    source_budget = combined - history_tokens
    selected: list[ChatSourceSnapshot] = []
    for source in sources:
        relabeled = ChatSourceSnapshot(
            len(selected) + 1,
            f"S{len(selected) + 1}",
            source.document_id,
            source.chunk_id,
            source.document_id_snapshot,
            source.chunk_id_snapshot,
            source.original_filename,
            source.display_name,
            source.logical_path,
            source.page_start,
            source.page_end,
            source.section,
            source.source_sha256,
            source.text_sha256,
            source.retrieval_distance,
            source.rerank_score,
            source.text,
            source.token_count,
        )
        candidate = [*selected, relabeled]
        source_text = "\n\n".join(_source_block(item) for item in candidate)
        if count_tokens(source_text) > source_budget:
            break
        candidate_prompt = _render_prompt(question, history_text, source_text)
        if (
            count_tokens(candidate_prompt)
            + OUTPUT_TOKENS
            + PROMPT_FORMATTING_RESERVE_TOKENS
            > MODEL_CONTEXT_TOKENS
        ):
            break
        selected.append(relabeled)
    if not selected:
        return (), None
    blocks = "\n\n".join(_source_block(source) for source in selected)
    prompt = _render_prompt(question, history_text, blocks)
    if (
        count_tokens(prompt)
        + OUTPUT_TOKENS
        + PROMPT_FORMATTING_RESERVE_TOKENS
        > MODEL_CONTEXT_TOKENS
    ):
        raise RuntimeError("chat prompt exceeds the model context budget")
    if history_tokens > MAX_HISTORY_TOKENS:
        raise RuntimeError("chat history exceeds its token budget")
    if history_tokens + count_tokens(blocks) > MAX_COMBINED_TOKENS:
        raise RuntimeError("chat context exceeds its combined token budget")
    return tuple(selected), prompt


def _validate_generation_usage(chunk: GenerationChunk, prompt: str) -> None:
    usage = chunk.usage
    if usage is None:
        return
    estimate = count_tokens(prompt)
    _LOGGER.info(
        "generation token budget estimate=%d actual=%d output=%d reserve=%d",
        estimate,
        usage.prompt_eval_count,
        OUTPUT_TOKENS,
        PROMPT_FORMATTING_RESERVE_TOKENS,
    )
    if (
        usage.prompt_eval_count
        + OUTPUT_TOKENS
        + PROMPT_FORMATTING_RESERVE_TOKENS
        > MODEL_CONTEXT_TOKENS
    ):
        raise RuntimeError(
            "generation prompt exceeded the configured model context budget"
        )


def _validated_source_ranks(
    answer: str, sources: Sequence[ChatSourceSnapshot]
) -> tuple[ChatSourceSnapshot, ...] | None:
    if not answer or is_explicit_insufficient_context(answer):
        return None
    supplied = {source.label: source for source in sources}
    valid_tokens = {f"[{label}]" for label in supplied}
    if any(token not in valid_tokens for token in _SOURCE_LIKE.findall(answer)):
        return None
    labels = [f"S{number}" for number in _SOURCE_LABEL.findall(answer)]
    if not labels or any(label not in supplied for label in labels):
        return None
    return tuple(supplied[label] for label in dict.fromkeys(labels))


def _snapshot_from_row(source: TurnSource) -> ChatSourceSnapshot:
    return ChatSourceSnapshot(
        rank=source.rank,
        label=source.label,
        document_id=source.document_id,
        chunk_id=source.chunk_id,
        document_id_snapshot=source.document_id_snapshot,
        chunk_id_snapshot=source.chunk_id_snapshot,
        original_filename=source.original_filename,
        display_name=source.display_name,
        logical_path=source.logical_path,
        page_start=source.page_start,
        page_end=source.page_end,
        section=source.section,
        source_sha256=source.source_sha256,
        text_sha256=source.text_sha256,
        retrieval_distance=source.retrieval_distance,
        rerank_score=source.rerank_score,
        text=source.snapshot_text,
        token_count=source.token_count,
    )


def _bounded_error(exc: Exception) -> str:
    return (str(exc).strip() or exc.__class__.__name__)[:500]


def chat_stream_event(
    name: str,
    chat_id: uuid.UUID,
    turn_id: uuid.UUID,
    sequence: int,
    payload: dict[str, object],
) -> str:
    if sequence < 1:
        raise ValueError("stream event sequence must start at 1")
    body = {
        "chat_id": str(chat_id),
        "turn_id": str(turn_id),
        "seq": sequence,
        **payload,
    }
    return f"event: {name}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
