import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.routes.query as query_routes
from app.config import Settings
from app.main import create_app
from app.security.actor import ActorContext, ActorRole
from app.services.ollama_generation import GenerationServiceError
from app.services.rag import PreparedQuery

_ACTOR = ActorContext(uuid.uuid4(), ActorRole.MEMBER, 1, 1, uuid.uuid4())


@pytest.fixture(autouse=True)
def _authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_authenticated_request(request: object, *, mutation: bool = False):
        yield SimpleNamespace(actor=_ACTOR, session=object())

    monkeypatch.setattr(
        query_routes, "authenticated_request", fake_authenticated_request
    )


class _Rag:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.prepared_payload: object | None = None

    async def embed_query(self, payload: object) -> list[float]:
        self.prepared_payload = payload
        if self.error is not None:
            raise self.error
        return [0.0] * 1024

    async def retrieve_candidates(
        self, actor: object, session: object, payload: object, vector: list[float]
    ) -> tuple[object, ...]:
        return ()

    async def rerank_candidates(
        self, payload: object, candidates: object
    ) -> tuple[object, ...]:
        return ()

    async def prepare_sources(
        self,
        actor: ActorContext,
        session: object,
        payload: object,
        ranked: object,
    ) -> PreparedQuery:
        return PreparedQuery(prompt=None, sources=(), actor=actor)

    async def check_generation_available(
        self, prepared: PreparedQuery
    ) -> PreparedQuery:
        return prepared

    async def monitor(
        self, actor: object, session: object, prepared: PreparedQuery
    ) -> None:
        return None

    async def monitor_documents(
        self,
        actor: object,
        session: object,
        document_ids: tuple[uuid.UUID, ...],
    ) -> None:
        return None

    async def stream(
        self, prepared: PreparedQuery, *, monitor: object
    ) -> AsyncIterator[str]:
        yield 'event: sources\ndata: {"sources":[]}\n\n'
        yield (
            'event: final\ndata: {"answer":"declined",'
            '"insufficient_context":true,"citations":[]}\n\n'
        )


def _client(rag: _Rag) -> TestClient:
    return TestClient(create_app(Settings(), SimpleNamespace(rag=rag)))


def test_query_stream_requires_sse_accept_header() -> None:
    response = _client(_Rag()).post("/api/query/stream", json={"question": "question"})
    assert response.status_code == 406


def test_query_stream_rejects_whitespace_and_out_of_bounds() -> None:
    client = _client(_Rag())
    headers = {"Accept": "text/event-stream"}

    assert (
        client.post(
            "/api/query/stream", json={"question": "   "}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/query/stream",
            json={"question": "q", "retrieve_k": 21},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/query/stream",
            json={"question": "q", "context_k": 4},
            headers=headers,
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "question",
    [
        "q" * 2001,
        "question\u0000with a control character",
        "question\u200bwith hidden formatting",
    ],
)
def test_query_stream_rejects_unsafe_question_text(question: str) -> None:
    response = _client(_Rag()).post(
        "/api/query/stream",
        json={"question": question},
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 422


def test_query_stream_accepts_and_normalizes_exact_character_limit() -> None:
    rag = _Rag()
    response = _client(rag).post(
        "/api/query/stream",
        json={"question": "e\u0301" * 2000},
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert rag.prepared_payload is not None
    assert rag.prepared_payload.question == "\u00e9" * 2000


def test_query_stream_maps_prestream_dependency_failure_to_503() -> None:
    rag = _Rag()
    rag.error = GenerationServiceError("required model is unavailable")
    response = _client(rag).post(
        "/api/query/stream",
        json={"question": "question"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "required model is unavailable"


def test_query_stream_returns_sse_events_and_headers() -> None:
    response = _client(_Rag()).post(
        "/api/query/stream",
        json={"question": "question"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert "event: sources" in response.text
    assert "event: final" in response.text


@pytest.mark.parametrize("stage", ["embed", "rerank", "model"])
def test_stalled_legacy_query_stage_is_cancelled_on_subsecond_revocation(
    stage: str,
) -> None:
    class StalledRag(_Rag):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = False
            self.document_id = uuid.uuid4()

        async def _stall(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            raise AssertionError("unreachable")

        async def embed_query(self, payload: object) -> list[float]:
            if stage == "embed":
                return await self._stall()
            return [0.0] * 1024

        async def retrieve_candidates(
            self,
            actor: object,
            session: object,
            payload: object,
            vector: list[float],
        ) -> tuple[object, ...]:
            return (
                SimpleNamespace(chunk=SimpleNamespace(document_id=self.document_id)),
            )

        async def rerank_candidates(
            self, payload: object, candidates: object
        ) -> tuple[object, ...]:
            if stage == "rerank":
                return await self._stall()
            return ()

        async def prepare_sources(
            self,
            actor: ActorContext,
            session: object,
            payload: object,
            ranked: object,
        ) -> PreparedQuery:
            return PreparedQuery(prompt="prompt", sources=(object(),), actor=actor)

        async def check_generation_available(
            self, prepared: PreparedQuery
        ) -> PreparedQuery:
            if stage == "model":
                return await self._stall()
            return prepared

        async def monitor(
            self,
            actor: object,
            session: object,
            prepared: PreparedQuery,
        ) -> None:
            raise query_routes.RagAccessRevoked("query access was revoked")

        async def monitor_documents(
            self,
            actor: object,
            session: object,
            document_ids: tuple[uuid.UUID, ...],
        ) -> None:
            assert document_ids == (self.document_id,)
            raise query_routes.RagAccessRevoked("query access was revoked")

    rag = StalledRag()
    started = time.monotonic()
    response = _client(rag).post(
        "/api/query/stream",
        json={"question": "question"},
        headers={"Accept": "text/event-stream"},
    )
    assert time.monotonic() - started < 1.0
    assert response.status_code == 200
    assert '"code":"access_revoked"' in response.text
    assert rag.cancelled is True
