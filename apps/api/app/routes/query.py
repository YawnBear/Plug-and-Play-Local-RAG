import asyncio
from collections.abc import AsyncIterator, Awaitable, Sequence
from typing import TypeVar
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.schemas.query import QueryRequest
from app.security.request_auth import authenticated_request
from app.services.ollama_embeddings import EmbeddingServiceError
from app.services.ollama_generation import GenerationServiceError
from app.services.rag import PreparedQuery, RagAccessRevoked
from app.services.reranker import RerankerError

router = APIRouter(prefix="/api/query", tags=["query"])
_Awaited = TypeVar("_Awaited")


def _streaming_response(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _access_revoked_event() -> AsyncIterator[str]:
    yield (
        'event: error\ndata: {"code":"access_revoked",'
        '"message":"access to a source was revoked"}\n\n'
    )


@router.post("/stream")
async def stream_query(
    request: Request,
    payload: QueryRequest,
    accept: str | None = Header(default=None),
) -> StreamingResponse:
    if accept is None or "text/event-stream" not in accept.lower():
        raise HTTPException(
            status.HTTP_406_NOT_ACCEPTABLE,
            "Accept must include text/event-stream",
        )
    try:
        rag = request.app.state.container.rag
        async with authenticated_request(request, mutation=True) as authenticated:
            actor = authenticated.actor

        async def monitor_access(
            target: PreparedQuery,
            document_ids: Sequence[UUID] = (),
        ) -> None:
            try:
                async with authenticated_request(request) as authenticated:
                    if document_ids:
                        await rag.monitor_documents(
                            authenticated.actor,
                            authenticated.session,
                            document_ids,
                        )
                    else:
                        await rag.monitor(
                            authenticated.actor,
                            authenticated.session,
                            target,
                        )
            except HTTPException as exc:
                if exc.status_code in {
                    status.HTTP_401_UNAUTHORIZED,
                    status.HTTP_403_FORBIDDEN,
                    status.HTTP_404_NOT_FOUND,
                }:
                    raise RagAccessRevoked("query access was revoked") from exc
                raise

        async def monitored(
            awaitable: Awaitable[_Awaited],
            target: PreparedQuery,
            document_ids: Sequence[UUID] = (),
        ) -> _Awaited:
            task = asyncio.create_task(awaitable)
            try:
                while True:
                    done, _ = await asyncio.wait({task}, timeout=0.5)
                    if done:
                        return task.result()
                    await monitor_access(target, document_ids)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        actor_only = PreparedQuery(None, (), actor=actor)
        query_vector = await monitored(rag.embed_query(payload), actor_only)
        async with authenticated_request(request) as authenticated:
            candidates = await rag.retrieve_candidates(
                authenticated.actor,
                authenticated.session,
                payload,
                query_vector,
            )
        candidate_document_ids = tuple(
            dict.fromkeys(candidate.chunk.document_id for candidate in candidates)
        )
        ranked = await monitored(
            rag.rerank_candidates(payload, candidates),
            actor_only,
            candidate_document_ids,
        )
        async with authenticated_request(request) as authenticated:
            prepared = await rag.prepare_sources(
                authenticated.actor,
                authenticated.session,
                payload,
                ranked,
            )
        if prepared.actor != actor:
            raise RagAccessRevoked("query actor changed")
        prepared = await monitored(
            rag.check_generation_available(prepared),
            prepared,
        )
    except (EmbeddingServiceError, RerankerError, GenerationServiceError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            str(exc),
        ) from exc
    except RagAccessRevoked:
        return _streaming_response(_access_revoked_event())

    async def monitor(target: PreparedQuery) -> None:
        await monitor_access(target)

    return _streaming_response(rag.stream(prepared, monitor=monitor))
