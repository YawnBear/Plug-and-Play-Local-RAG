import asyncio
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from typing import Protocol, TypeVar
from uuid import UUID

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect
from starlette.types import Receive, Scope, Send

from app.schemas.chats import (
    ChatCreateRequest,
    ChatDetailResponse,
    ChatMessageRequest,
    ChatRenameRequest,
    ChatScopeRequest,
    ChatScopeResponse,
    ChatSummaryResponse,
    ChatTurnResponse,
    CitationEvidenceResponse,
    HistoricalSourceResponse,
)
from app.security.request_auth import authenticated_request
from app.services.chats import (
    BeginTurn,
    ChatAccessRevoked,
    ChatConflict,
    ChatNotFound,
    ChatPreparationError,
    ChatSourceSnapshot,
    ChatValidation,
    PreparedChatTurn,
    chat_stream_event,
)

_Awaited = TypeVar("_Awaited")

router = APIRouter(prefix="/api/chats", tags=["chats"])


class _DisconnectAwareStreamingResponse(StreamingResponse):
    def __init__(
        self,
        content: AsyncIterable[str | bytes],
        *,
        on_interrupted: Callable[[], Awaitable[None]] | None = None,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
    ) -> None:
        super().__init__(content, status_code, headers, media_type, background)
        self._on_interrupted = on_interrupted

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        stream_task = asyncio.create_task(self.stream_response(send))
        disconnect_task = asyncio.create_task(self.listen_for_disconnect(receive))
        primary_error: BaseException | None = None
        translate_client_disconnect = False
        response_interrupted = False
        try:
            done, _pending = await asyncio.wait(
                {stream_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stream_task in done:
                try:
                    await stream_task
                except BaseException as exc:
                    primary_error = exc
                    translate_client_disconnect = isinstance(exc, OSError)
                if disconnect_task in done:
                    try:
                        await disconnect_task
                    except BaseException as exc:
                        if primary_error is None:
                            primary_error = exc
            else:
                response_interrupted = True
                try:
                    await disconnect_task
                except BaseException as exc:
                    primary_error = exc
                stream_task.cancel()
                stream_result = (
                    await asyncio.gather(stream_task, return_exceptions=True)
                )[0]
                if (
                    primary_error is None
                    and isinstance(stream_result, BaseException)
                    and not isinstance(stream_result, asyncio.CancelledError)
                ):
                    primary_error = stream_result
                    translate_client_disconnect = isinstance(stream_result, OSError)
        except BaseException as exc:
            primary_error = exc
        finally:
            for task in (stream_task, disconnect_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stream_task, disconnect_task, return_exceptions=True)
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                try:
                    await close()
                except BaseException as exc:
                    if primary_error is None:
                        primary_error = exc

        if (
            response_interrupted or primary_error is not None
        ) and self._on_interrupted is not None:
            try:
                await self._on_interrupted()
            except BaseException as exc:
                if primary_error is None:
                    primary_error = exc

        if primary_error is not None:
            if translate_client_disconnect:
                raise ClientDisconnect() from primary_error
            raise primary_error

        if self.background is not None:
            await self.background()


class ChatRecord(Protocol):
    id: UUID
    title: str
    title_is_manual: bool
    scope_mode: str
    scope_version: int
    created_at: object
    updated_at: object


def _summary(chat: ChatRecord) -> ChatSummaryResponse:
    return ChatSummaryResponse(
        chat_id=chat.id,
        title=chat.title,
        title_is_manual=chat.title_is_manual,
        scope_mode=chat.scope_mode,
        scope_version=chat.scope_version,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
    )


def _raise_chat_error(exc: Exception) -> None:
    if isinstance(exc, ChatValidation):
        raise HTTPException(422, str(exc)) from exc
    if isinstance(exc, ChatNotFound):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, ChatConflict):
        raise HTTPException(409, str(exc)) from exc
    raise exc


@router.get("", response_model=list[ChatSummaryResponse])
async def list_chats(request: Request) -> list[ChatSummaryResponse]:
    async with authenticated_request(request) as authenticated:
        chats = await request.app.state.container.chats.list(
            authenticated.actor, authenticated.session
        )
    return [_summary(chat) for chat in chats]


@router.post("", response_model=ChatSummaryResponse, status_code=201)
async def create_chat(
    request: Request, payload: ChatCreateRequest
) -> ChatSummaryResponse:
    try:
        async with authenticated_request(request, mutation=True) as authenticated:
            chat = await request.app.state.container.chats.create(
                authenticated.actor, authenticated.session, payload.title
            )
    except Exception as exc:
        _raise_chat_error(exc)
        raise
    return _summary(chat)


@router.get("/{chat_id}", response_model=ChatDetailResponse)
async def get_chat(
    request: Request,
    chat_id: UUID,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
) -> ChatDetailResponse:
    try:
        async with authenticated_request(request) as authenticated:
            detail = await request.app.state.container.chats.get(
                authenticated.actor,
                authenticated.session,
                chat_id,
                page=page,
                limit=limit,
            )
    except Exception as exc:
        _raise_chat_error(exc)
        raise
    chat = detail["chat"]
    turns = []
    for item in detail["turns"]:
        turn = item["turn"]
        sources = [
            HistoricalSourceResponse(
                label=source.label,
                rank=source.rank,
                document_id=source.document_id,
                chunk_id=source.chunk_id,
                document_id_snapshot=source.document_id_snapshot,
                chunk_id_snapshot=source.chunk_id_snapshot,
                filename=source.original_filename,
                display_name=source.display_name,
                logical_path=source.logical_path,
                page_start=source.page_start,
                page_end=source.page_end,
                section=source.section,
                source_sha256=source.source_sha256,
                text_sha256=source.text_sha256,
                retrieval_distance=source.retrieval_distance,
                rerank_score=source.rerank_score,
                source_available=source.document_id is not None,
            )
            for source in item["sources"]
        ]
        sources_by_rank = {source.rank: source for source in sources}
        citations = [
            sources_by_rank[rank]
            for rank in item["citation_ranks"]
            if rank in sources_by_rank
        ]
        turns.append(
            ChatTurnResponse(
                turn_id=turn.id,
                ordinal=turn.ordinal,
                question=turn.question,
                status=turn.status,
                attempt=turn.attempt,
                scope_version=turn.scope_version,
                final_answer=turn.final_answer,
                partial_answer=turn.partial_answer,
                insufficient_context=turn.insufficient_context,
                error=turn.error,
                sources=sources,
                citations=citations,
                citation_ranks=item["citation_ranks"],
                created_at=turn.created_at,
                updated_at=turn.updated_at,
                completed_at=turn.completed_at,
            )
        )
    base = _summary(chat)
    return ChatDetailResponse(
        **base.model_dump(),
        scope_node_ids=detail["scope_ids"],
        turns=turns,
        page=detail["page"],
        limit=detail["limit"],
        total=detail["total"],
    )


@router.get(
    "/{chat_id}/turns/{turn_id}/citations/{label}/evidence",
    response_model=CitationEvidenceResponse,
)
async def get_citation_evidence(
    request: Request,
    response: Response,
    chat_id: UUID,
    turn_id: UUID,
    label: str = Path(pattern=r"^S[1-8]$"),
) -> CitationEvidenceResponse:
    async with authenticated_request(request) as authenticated:
        row = (
            await authenticated.session.execute(
                text(
                    "SELECT * FROM v5_citation_evidence("
                    ":chat_id, :turn_id, :source_rank)"
                ),
                {
                    "chat_id": chat_id,
                    "turn_id": turn_id,
                    "source_rank": int(label[1:]),
                },
            )
        ).mappings().one_or_none()
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "citation evidence not found",
            headers={"Cache-Control": "private, no-store"},
        )
    response.headers["Cache-Control"] = "private, no-store"
    return CitationEvidenceResponse.model_validate(dict(row))


@router.patch("/{chat_id}", response_model=ChatSummaryResponse)
async def rename_chat(
    request: Request, chat_id: UUID, payload: ChatRenameRequest
) -> ChatSummaryResponse:
    try:
        async with authenticated_request(request, mutation=True) as authenticated:
            chat = await request.app.state.container.chats.rename(
                authenticated.actor,
                authenticated.session,
                chat_id,
                payload.title,
            )
    except Exception as exc:
        _raise_chat_error(exc)
        raise
    return _summary(chat)


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(request: Request, chat_id: UUID) -> None:
    try:
        async with authenticated_request(request, mutation=True) as authenticated:
            await request.app.state.container.chats.delete(
                authenticated.actor, authenticated.session, chat_id
            )
    except Exception as exc:
        _raise_chat_error(exc)


@router.put("/{chat_id}/scope", response_model=ChatScopeResponse)
async def save_chat_scope(
    request: Request, chat_id: UUID, payload: ChatScopeRequest
) -> ChatScopeResponse:
    try:
        async with authenticated_request(request, mutation=True) as authenticated:
            chat, scope_ids = await request.app.state.container.chats.save_scope(
                authenticated.actor,
                authenticated.session,
                chat_id,
                payload.mode,
                payload.node_ids,
            )
    except Exception as exc:
        _raise_chat_error(exc)
        raise
    return ChatScopeResponse(
        **_summary(chat).model_dump(), scope_node_ids=list(scope_ids)
    )


def _require_sse(accept: str | None) -> None:
    if accept is None or "text/event-stream" not in accept.lower():
        raise HTTPException(406, "Accept must include text/event-stream")


async def _mark_access_revoked_trusted(request: Request, begin: BeginTurn) -> None:
    if begin.token is None:
        return
    async with (
        request.app.state.container.database.session_factory() as session,
        session.begin(),
    ):
        outcome = await session.scalar(
            text(
                "SELECT v4_mark_turn_access_revoked_trusted("
                ":turn_id, :generation_token)"
            ),
            {
                "turn_id": begin.turn_id,
                "generation_token": begin.token,
            },
        )
    if outcome not in {"updated", "already_terminal", "stale", "not_found"}:
        raise RuntimeError("trusted access-revocation returned an invalid outcome")


async def _prepared_response(
    request: Request, prepare: Awaitable[BeginTurn]
) -> StreamingResponse:
    try:
        begin = await prepare
    except ChatPreparationError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        _raise_chat_error(exc)
        raise
    chats = request.app.state.container.chats

    async def transition(begin: BeginTurn, state: str, message: str) -> None:
        if state == "access_revoked":
            await _mark_access_revoked_trusted(request, begin)
            return
        async with authenticated_request(request, mutation=True) as authenticated:
            await chats.transition(
                authenticated.actor,
                authenticated.session,
                begin,
                state,
                message,
            )

    async def finalize(
        target: PreparedChatTurn,
        answer: str,
        insufficient: bool,
        citations: Sequence[ChatSourceSnapshot],
    ) -> tuple[ChatSourceSnapshot, ...]:
        async with authenticated_request(request, mutation=True) as authenticated:
            return await chats.complete(
                authenticated.actor,
                authenticated.session,
                target,
                answer,
                insufficient,
                citations,
            )

    async def monitor(target: PreparedChatTurn) -> None:
        try:
            async with authenticated_request(request) as authenticated:
                await chats.monitor(authenticated.actor, authenticated.session, target)
        except HTTPException as exc:
            if exc.status_code in {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            }:
                raise ChatAccessRevoked("chat access was revoked") from exc
            raise

    async def monitored(
        awaitable: Awaitable[_Awaited],
        target: PreparedChatTurn,
    ) -> _Awaited:
        task = asyncio.create_task(awaitable)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.5)
                if done:
                    return task.result()
                await monitor(target)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def stream() -> AsyncIterable[str]:
        sequence = 1

        def event(name: str, payload: dict[str, object]) -> str:
            nonlocal sequence
            result = chat_stream_event(
                name,
                begin.chat_id,
                begin.turn_id,
                sequence,
                payload,
            )
            sequence += 1
            return result

        pending = PreparedChatTurn(
            begin.actor,
            begin.chat_id,
            begin.turn_id,
            begin.token,
            None,
            (),
            begin.already_complete,
        )
        try:
            yield event("status", {"phase": "retrieving"})
            if begin.already_complete:
                yield event("status", {"phase": "reranking"})
                yield event("status", {"phase": "preparing_answer"})
                async for frame in chats.stream(
                    pending,
                    finalize=finalize,
                    transition=transition,
                    monitor=monitor,
                    sequence_start=sequence,
                ):
                    yield frame
                return
            query_vector = await monitored(
                chats.embed_retrieval_query(begin),
                pending,
            )
            candidates = ()
            if query_vector is not None:
                async with authenticated_request(request) as authenticated:
                    candidates = await chats.retrieve_candidates(
                        authenticated.actor,
                        authenticated.session,
                        begin,
                        query_vector,
                    )
            yield event("status", {"phase": "reranking"})
            ranked = await monitored(
                chats.rerank_candidates(begin, candidates),
                pending,
            )
            yield event("status", {"phase": "preparing_answer"})
            async with authenticated_request(request, mutation=True) as authenticated:
                prepared = await chats.snapshot_sources(
                    authenticated.actor,
                    authenticated.session,
                    begin,
                    ranked,
                )
            prepared = await monitored(
                chats.check_generation_available(prepared),
                prepared,
            )
            async for frame in chats.stream(
                prepared,
                finalize=finalize,
                transition=transition,
                monitor=monitor,
                sequence_start=sequence,
            ):
                yield frame
        except asyncio.CancelledError:
            await chats.interrupt_prepared(pending, transition=transition)
            raise
        except GeneratorExit:
            await chats.interrupt_prepared(pending, transition=transition)
            raise
        except ChatAccessRevoked:
            await _mark_access_revoked_trusted(request, begin)
            yield event(
                "error",
                {
                    "code": "access_revoked",
                    "message": "access to a source was revoked",
                },
            )
        except HTTPException as exc:
            if exc.status_code in {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            }:
                await _mark_access_revoked_trusted(request, begin)
                yield event(
                    "error",
                    {
                        "code": "access_revoked",
                        "message": "access to a source was revoked",
                    },
                )
                return
            message = (str(exc.detail).strip() or "generation failed")[:500]
            await transition(begin, "failed", message)
            yield event(
                "error",
                {
                    "code": "generation_failed",
                    "message": message,
                },
            )
        except Exception as exc:
            message = (str(exc).strip() or exc.__class__.__name__)[:500]
            await transition(begin, "failed", message)
            yield event(
                "error",
                {
                    "code": "generation_failed",
                    "message": message,
                },
            )

    return _DisconnectAwareStreamingResponse(
        stream(),
        on_interrupted=lambda: chats.interrupt_prepared(
            PreparedChatTurn(
                begin.actor,
                begin.chat_id,
                begin.turn_id,
                begin.token,
                None,
                (),
                begin.already_complete,
            ),
            transition=transition,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _prepare_turn(
    request: Request,
    chat_id: UUID,
    *,
    question: str | None = None,
    retry_turn_id: UUID | None = None,
) -> BeginTurn:
    chats = request.app.state.container.chats
    auto_title = "New chat"
    if retry_turn_id is None:
        async with authenticated_request(request, mutation=True) as authenticated:
            generate_title = await chats.should_generate_title(
                authenticated.actor,
                authenticated.session,
                chat_id,
            )
        if generate_title:
            auto_title = await chats.generate_first_title(question or "")
    async with authenticated_request(request, mutation=True) as authenticated:
        begin = (
            await chats.prepare_message(
                authenticated.actor,
                authenticated.session,
                chat_id,
                question or "",
                auto_title,
            )
            if retry_turn_id is None
            else await chats.prepare_retry(
                authenticated.actor,
                authenticated.session,
                chat_id,
                retry_turn_id,
            )
        )

    return begin


@router.post("/{chat_id}/messages/stream")
async def stream_chat_message(
    request: Request,
    chat_id: UUID,
    payload: ChatMessageRequest,
    accept: str | None = Header(default=None),
) -> StreamingResponse:
    _require_sse(accept)
    return await _prepared_response(
        request,
        _prepare_turn(request, chat_id, question=payload.question),
    )


@router.post("/{chat_id}/turns/{turn_id}/retry/stream")
async def retry_chat_turn(
    request: Request,
    chat_id: UUID,
    turn_id: UUID,
    accept: str | None = Header(default=None),
) -> StreamingResponse:
    _require_sse(accept)
    return await _prepared_response(
        request,
        _prepare_turn(request, chat_id, retry_turn_id=turn_id),
    )
