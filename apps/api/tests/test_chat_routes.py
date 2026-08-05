from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.routes.chats as chat_routes
from app.config import Settings
from app.db.models import Chat, ChatTurn, TurnSource
from app.main import create_app
from app.security.actor import ActorContext, ActorRole
from app.services.chats import (
    BeginTurn,
    ChatAccessRevoked,
    ChatConflict,
    ChatNotFound,
    ChatPreparationError,
    ChatValidation,
    PreparedChatTurn,
    chat_stream_event,
)

_ACTOR = ActorContext(uuid.uuid4(), ActorRole.MEMBER, 1, 1, uuid.uuid4())


@pytest.fixture(autouse=True)
def _authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_authenticated_request(request: object, *, mutation: bool = False):
        yield SimpleNamespace(actor=_ACTOR, session=object())

    monkeypatch.setattr(
        chat_routes, "authenticated_request", fake_authenticated_request
    )


def _chat(title: str = "New chat") -> Chat:
    now = datetime.now(UTC)
    return Chat(
        id=uuid.uuid4(),
        title=title,
        title_is_manual=title != "New chat",
        scope_mode="all_ready",
        scope_version=1,
        next_turn_ordinal=1,
        created_at=now,
        updated_at=now,
    )


class _Chats:
    def __init__(self) -> None:
        self.chat = _chat()
        self.error: Exception | None = None
        self.question: str | None = None
        self.deleted = False

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    async def list(self, actor: object, session: object) -> list[Chat]:
        return [self.chat]

    async def create(self, actor: object, session: object, title: str | None) -> Chat:
        self._raise()
        self.chat = _chat(title or "New chat")
        return self.chat

    async def get(
        self,
        actor: object,
        session: object,
        chat_id: uuid.UUID,
        *,
        page: int,
        limit: int,
    ) -> dict[str, object]:
        self._raise()
        return {
            "chat": self.chat,
            "scope_ids": [],
            "turns": [],
            "page": page,
            "limit": limit,
            "total": 0,
        }

    async def rename(
        self, actor: object, session: object, chat_id: uuid.UUID, title: str
    ) -> Chat:
        self._raise()
        self.chat.title = title
        self.chat.title_is_manual = True
        return self.chat

    async def delete(self, actor: object, session: object, chat_id: uuid.UUID) -> None:
        self._raise()
        self.deleted = True

    async def save_scope(
        self,
        actor: object,
        session: object,
        chat_id: uuid.UUID,
        mode: str,
        node_ids: list[uuid.UUID],
    ) -> tuple[Chat, tuple[uuid.UUID, ...]]:
        self._raise()
        self.chat.scope_mode = mode
        self.chat.scope_version += 1
        return self.chat, tuple(node_ids)

    async def prepare_message(
        self,
        actor: ActorContext,
        session: object,
        chat_id: uuid.UUID,
        question: str,
        auto_title: str = "New chat",
    ) -> BeginTurn:
        self._raise()
        self.question = question
        return BeginTurn(
            actor,
            chat_id,
            uuid.uuid4(),
            None,
            question,
            1,
            (),
            (),
            question,
            True,
        )

    async def should_generate_title(
        self, actor: ActorContext, session: object, chat_id: uuid.UUID
    ) -> bool:
        return False

    async def generate_first_title(self, question: str) -> str:
        return "New chat"

    async def prepare_retry(
        self,
        actor: ActorContext,
        session: object,
        chat_id: uuid.UUID,
        turn_id: uuid.UUID,
    ) -> BeginTurn:
        self._raise()
        return BeginTurn(
            actor, chat_id, turn_id, None, "retry", 2, (), (), "retry", True
        )

    async def embed_retrieval_query(self, begin: BeginTurn) -> None:
        return None

    async def rerank_candidates(
        self, begin: BeginTurn, candidates: object
    ) -> tuple[object, ...]:
        return ()

    async def snapshot_sources(
        self,
        actor: ActorContext,
        session: object,
        begin: BeginTurn,
        ranked: object,
    ) -> PreparedChatTurn:
        return PreparedChatTurn(
            actor, begin.chat_id, begin.turn_id, None, None, (), True
        )

    async def check_generation_available(
        self, prepared: PreparedChatTurn
    ) -> PreparedChatTurn:
        return prepared

    async def monitor(
        self, actor: object, session: object, prepared: PreparedChatTurn
    ) -> None:
        return None

    async def complete(self, *args: object) -> tuple[object, ...]:
        return ()

    async def transition(self, *args: object) -> None:
        return None

    async def interrupt_prepared(self, *args: object, **kwargs: object) -> None:
        return None

    async def stream(
        self, prepared: PreparedChatTurn, **kwargs: object
    ) -> AsyncIterator[str]:
        ids = {
            "chat_id": str(prepared.chat_id),
            "turn_id": str(prepared.turn_id),
        }
        yield f"event: sources\ndata: {json.dumps({**ids, 'sources': []})}\n\n"
        final = {
            **ids,
            "answer": "declined",
            "insufficient_context": True,
            "citations": [],
        }
        yield (f"event: final\ndata: {json.dumps(final)}\n\n")


def _client(chats: _Chats) -> TestClient:
    return TestClient(create_app(Settings(), SimpleNamespace(chats=chats)))


def test_chat_crud_and_scope_contracts() -> None:
    chats = _Chats()
    client = _client(chats)

    created = client.post("/api/chats", json={"title": "Manual"})
    assert created.status_code == 201
    assert created.json()["title_is_manual"] is True
    chat_id = created.json()["chat_id"]

    listed = client.get("/api/chats")
    assert listed.status_code == 200
    assert listed.json()[0]["chat_id"] == chat_id

    renamed = client.patch(f"/api/chats/{chat_id}", json={"title": "Renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed"

    node_id = uuid.uuid4()
    scoped = client.put(
        f"/api/chats/{chat_id}/scope",
        json={"mode": "selected", "node_ids": [str(node_id)]},
    )
    assert scoped.status_code == 200
    assert scoped.json()["scope_node_ids"] == [str(node_id)]

    detail = client.get(f"/api/chats/{chat_id}")
    assert detail.status_code == 200
    assert detail.json()["turns"] == []

    deleted = client.delete(f"/api/chats/{chat_id}")
    assert deleted.status_code == 204
    assert chats.deleted


def test_citation_evidence_is_narrow_no_store_and_opaque(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    document_id = uuid.uuid4()
    requested: list[dict[str, object]] = []
    row: dict[str, object] | None = {
        "label": "S1",
        "rank": 1,
        "document_id": document_id,
        "display_name": "Policy.pdf",
        "logical_path": "/Policy.pdf",
        "page_start": 2,
        "page_end": 2,
        "section": "Policy",
        "parse_method": "direct",
        "snapshot_text": "Complete cited chunk.",
        "highlight_anchor": {
            "version": 1,
            "normalization": "citation-highlight-v1",
            "pages": [
                {
                    "page": 2,
                    "kind": "text_quote",
                    "selector": {
                        "exact": "Complete cited chunk.",
                        "prefix": "",
                        "suffix": "",
                        "sha256": hashlib.sha256(
                            b"Complete cited chunk."
                        ).hexdigest(),
                    },
                }
            ],
        },
        "source_sha256": "b" * 64,
        "text_sha256": "c" * 64,
    }

    class _Result:
        def mappings(self) -> _Result:
            return self

        def one_or_none(self) -> dict[str, object] | None:
            return row

    class _Session:
        async def execute(
            self, statement: object, parameters: dict[str, object]
        ) -> _Result:
            assert "v5_citation_evidence" in str(statement)
            requested.append(parameters)
            return _Result()

    @asynccontextmanager
    async def evidence_request(request: object, *, mutation: bool = False):
        assert mutation is False
        yield SimpleNamespace(actor=_ACTOR, session=_Session())

    monkeypatch.setattr(chat_routes, "authenticated_request", evidence_request)
    client = _client(_Chats())
    response = client.get(
        f"/api/chats/{chat_id}/turns/{turn_id}/citations/S1/evidence"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["snapshot_text"] == "Complete cited chunk."
    assert requested == [
        {"chat_id": chat_id, "turn_id": turn_id, "source_rank": 1}
    ]

    row = None
    unavailable = client.get(
        f"/api/chats/{chat_id}/turns/{turn_id}/citations/S1/evidence"
    )
    assert unavailable.status_code == 404
    assert unavailable.headers["cache-control"] == "private, no-store"
    assert "citation evidence not found" in unavailable.text


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (ChatValidation("bad input"), 422),
        (ChatNotFound("missing"), 404),
        (ChatConflict("busy"), 409),
    ],
)
def test_chat_routes_map_domain_errors(error: Exception, status_code: int) -> None:
    chats = _Chats()
    chats.error = error
    response = _client(chats).patch(
        f"/api/chats/{uuid.uuid4()}", json={"title": "title"}
    )
    assert response.status_code == status_code


def test_chat_stream_requires_accept_and_maps_prestream_failure() -> None:
    chats = _Chats()
    client = _client(chats)
    chat_id = uuid.uuid4()

    assert (
        client.post(
            f"/api/chats/{chat_id}/messages/stream",
            json={"question": "question"},
        ).status_code
        == 406
    )

    chats.error = ChatPreparationError("dependency unavailable")
    response = client.post(
        f"/api/chats/{chat_id}/messages/stream",
        headers={"Accept": "text/event-stream"},
        json={"question": "question"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "dependency unavailable"


def test_chat_message_and_retry_stream_sse_contract() -> None:
    chats = _Chats()
    client = _client(chats)
    chat_id = uuid.uuid4()
    headers = {"Accept": "text/event-stream"}

    response = client.post(
        f"/api/chats/{chat_id}/messages/stream",
        headers=headers,
        json={"question": "question"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert "event: sources" in response.text
    assert f'"chat_id": "{chat_id}"' in response.text
    assert "event: final" in response.text

    turn_id = uuid.uuid4()
    retry = client.post(
        f"/api/chats/{chat_id}/turns/{turn_id}/retry/stream", headers=headers
    )
    assert retry.status_code == 200
    assert f'"turn_id": "{turn_id}"' in retry.text


def test_first_message_title_preflight_closes_read_transaction_before_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class TitleChats(_Chats):
        async def should_generate_title(
            self, actor: ActorContext, session: object, chat_id: uuid.UUID
        ) -> bool:
            events.append("preflight")
            return True

        async def generate_first_title(self, question: str) -> str:
            assert question == "What changed?"
            events.append("generate")
            return "Changed behavior"

        async def prepare_message(
            self,
            actor: ActorContext,
            session: object,
            chat_id: uuid.UUID,
            question: str,
            auto_title: str = "New chat",
        ) -> BeginTurn:
            events.append(f"prepare:{auto_title}")
            return await super().prepare_message(
                actor, session, chat_id, question, auto_title
            )

    @asynccontextmanager
    async def request_auth(request: object, *, mutation: bool = False):
        events.append("mutation-enter" if mutation else "read-enter")
        try:
            yield SimpleNamespace(actor=_ACTOR, session=object())
        finally:
            events.append("mutation-exit" if mutation else "read-exit")

    monkeypatch.setattr(chat_routes, "authenticated_request", request_auth)
    response = _client(TitleChats()).post(
        f"/api/chats/{uuid.uuid4()}/messages/stream",
        headers={"Accept": "text/event-stream"},
        json={"question": "What changed?"},
    )
    assert response.status_code == 200
    assert events[:6] == [
        "mutation-enter",
        "preflight",
        "mutation-exit",
        "generate",
        "mutation-enter",
        "prepare:Changed behavior",
    ]


@pytest.mark.parametrize("operation", ["message", "retry"])
def test_already_complete_begin_emits_full_no_context_lifecycle(
    operation: str,
) -> None:
    class AlreadyCompleteChats(_Chats):
        def __init__(self) -> None:
            super().__init__()
            self.sequence_starts: list[int] = []

        async def embed_retrieval_query(self, begin: BeginTurn) -> None:
            raise AssertionError("an already-complete turn must not be regenerated")

        async def rerank_candidates(
            self, begin: BeginTurn, candidates: object
        ) -> tuple[object, ...]:
            raise AssertionError("an already-complete turn must not be reranked")

        async def snapshot_sources(
            self,
            actor: ActorContext,
            session: object,
            begin: BeginTurn,
            ranked: object,
        ) -> PreparedChatTurn:
            raise AssertionError("an already-complete turn must not be persisted again")

        async def stream(
            self, prepared: PreparedChatTurn, **kwargs: object
        ) -> AsyncIterator[str]:
            assert prepared.already_complete is True
            sequence = kwargs["sequence_start"]
            assert isinstance(sequence, int)
            self.sequence_starts.append(sequence)
            yield chat_stream_event(
                "sources",
                prepared.chat_id,
                prepared.turn_id,
                sequence,
                {"sources": []},
            )
            yield chat_stream_event(
                "status",
                prepared.chat_id,
                prepared.turn_id,
                sequence + 1,
                {"phase": "validating_citations"},
            )
            yield chat_stream_event(
                "final",
                prepared.chat_id,
                prepared.turn_id,
                sequence + 2,
                {
                    "answer": "declined",
                    "insufficient_context": True,
                    "citations": [],
                },
            )

    chats = AlreadyCompleteChats()
    chat_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    client = _client(chats)
    headers = {"Accept": "text/event-stream"}
    response = (
        client.post(
            f"/api/chats/{chat_id}/messages/stream",
            headers=headers,
            json={"question": "question"},
        )
        if operation == "message"
        else client.post(
            f"/api/chats/{chat_id}/turns/{turn_id}/retry/stream",
            headers=headers,
        )
    )

    frames = [block.splitlines() for block in response.text.strip().split("\n\n")]
    names = [frame[0].removeprefix("event: ") for frame in frames]
    payloads = [json.loads(frame[1].removeprefix("data: ")) for frame in frames]

    assert response.status_code == 200
    assert names == ["status", "status", "status", "sources", "status", "final"]
    assert [payload["seq"] for payload in payloads] == list(range(1, 7))
    assert [payload.get("phase") for payload in payloads] == [
        "retrieving",
        "reranking",
        "preparing_answer",
        None,
        "validating_citations",
        None,
    ]
    assert chats.sequence_starts == [4]
    if operation == "retry":
        assert {payload["turn_id"] for payload in payloads} == {str(turn_id)}


def test_preparation_runs_inside_stream_with_exact_ordered_statuses() -> None:
    class ActiveChats(_Chats):
        async def prepare_message(
            self,
            actor: ActorContext,
            session: object,
            chat_id: uuid.UUID,
            question: str,
            auto_title: str = "New chat",
        ) -> BeginTurn:
            return BeginTurn(
                actor,
                chat_id,
                uuid.uuid4(),
                uuid.uuid4(),
                question,
                1,
                (),
                (),
                question,
            )

        async def embed_retrieval_query(self, begin: BeginTurn) -> list[float]:
            return [0.0]

        async def retrieve_candidates(
            self,
            actor: ActorContext,
            session: object,
            begin: BeginTurn,
            query_vector: list[float],
        ) -> tuple[object, ...]:
            return ()

        async def snapshot_sources(
            self,
            actor: ActorContext,
            session: object,
            begin: BeginTurn,
            ranked: object,
        ) -> PreparedChatTurn:
            return PreparedChatTurn(
                actor,
                begin.chat_id,
                begin.turn_id,
                None,
                None,
                (),
                True,
            )

        async def stream(
            self, prepared: PreparedChatTurn, **kwargs: object
        ) -> AsyncIterator[str]:
            sequence = kwargs["sequence_start"]
            assert isinstance(sequence, int)
            yield chat_stream_event(
                "sources",
                prepared.chat_id,
                prepared.turn_id,
                sequence,
                {"sources": []},
            )
            yield chat_stream_event(
                "status",
                prepared.chat_id,
                prepared.turn_id,
                sequence + 1,
                {"phase": "validating_citations"},
            )
            yield chat_stream_event(
                "final",
                prepared.chat_id,
                prepared.turn_id,
                sequence + 2,
                {
                    "answer": "declined",
                    "insufficient_context": True,
                    "citations": [],
                },
            )

    response = _client(ActiveChats()).post(
        f"/api/chats/{uuid.uuid4()}/messages/stream",
        headers={"Accept": "text/event-stream"},
        json={"question": "question"},
    )
    frames = [block.splitlines() for block in response.text.strip().split("\n\n")]
    names = [frame[0].removeprefix("event: ") for frame in frames]
    payloads = [json.loads(frame[1].removeprefix("data: ")) for frame in frames]

    assert names == ["status", "status", "status", "sources", "status", "final"]
    assert [payload["seq"] for payload in payloads] == list(range(1, 7))
    assert [payload.get("phase") for payload in payloads] == [
        "retrieving",
        "reranking",
        "preparing_answer",
        None,
        "validating_citations",
        None,
    ]


def test_post_begin_preparation_failure_is_streamed_and_token_fenced() -> None:
    class FailingChats(_Chats):
        def __init__(self) -> None:
            super().__init__()
            self.transitioned: tuple[BeginTurn, str, str] | None = None

        async def prepare_message(
            self,
            actor: ActorContext,
            session: object,
            chat_id: uuid.UUID,
            question: str,
            auto_title: str = "New chat",
        ) -> BeginTurn:
            return BeginTurn(
                actor,
                chat_id,
                uuid.uuid4(),
                uuid.uuid4(),
                question,
                1,
                (),
                (),
                question,
            )

        async def embed_retrieval_query(self, begin: BeginTurn) -> None:
            raise RuntimeError("embedding unavailable")

        async def transition(
            self,
            actor: ActorContext,
            session: object,
            begin: BeginTurn,
            state: str,
            message: str,
        ) -> None:
            self.transitioned = (begin, state, message)

    chats = FailingChats()
    response = _client(chats).post(
        f"/api/chats/{uuid.uuid4()}/messages/stream",
        headers={"Accept": "text/event-stream"},
        json={"question": "question"},
    )

    assert response.status_code == 200
    assert "event: status" in response.text
    assert '"phase": "retrieving"' in response.text
    assert "event: error" in response.text
    assert '"seq": 2' in response.text
    assert '"code": "generation_failed"' in response.text
    assert chats.transitioned is not None
    begin, state, message = chats.transitioned
    assert begin.token is not None
    assert state == "failed"
    assert message == "embedding unavailable"


@pytest.mark.parametrize("stage", ["embed", "rerank"])
def test_stalled_chat_preparation_is_cancelled_on_subsecond_revocation(
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TrustedSession:
        def __init__(self) -> None:
            self.status = "generating"
            self.calls: list[str] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def begin(self):
            return self

        async def scalar(self, statement, parameters):
            self.calls.append(str(statement))
            assert self.status == "generating"
            self.status = "access_revoked"
            return "updated"

    class StalledChats(_Chats):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = False
            self.transitioned: tuple[str, str] | None = None

        async def prepare_message(
            self,
            actor: ActorContext,
            session: object,
            chat_id: uuid.UUID,
            question: str,
            auto_title: str = "New chat",
        ) -> BeginTurn:
            return BeginTurn(
                actor,
                chat_id,
                uuid.uuid4(),
                uuid.uuid4(),
                question,
                1,
                (),
                (),
                question,
            )

        async def _stall(self) -> list[float]:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            raise AssertionError("unreachable")

        async def embed_retrieval_query(self, begin: BeginTurn) -> list[float] | None:
            if stage == "embed":
                return await self._stall()
            return [0.0]

        async def retrieve_candidates(
            self,
            actor: ActorContext,
            session: object,
            begin: BeginTurn,
            query_vector: list[float],
        ) -> list[object]:
            return []

        async def rerank_candidates(
            self,
            begin: BeginTurn,
            candidates: list[object],
        ) -> list[object]:
            if stage == "rerank":
                return await self._stall()
            return []

        async def monitor(
            self,
            actor: ActorContext,
            session: object,
            prepared: PreparedChatTurn,
        ) -> None:
            raise ChatAccessRevoked("chat access was revoked")

        async def transition(
            self,
            actor: ActorContext,
            session: object,
            begin: BeginTurn,
            state: str,
            message: str,
        ) -> None:
            self.transitioned = (state, message)

    chats = StalledChats()
    trusted = TrustedSession()
    authentication_calls = 0

    @asynccontextmanager
    async def request_auth(request: object, *, mutation: bool = False):
        nonlocal authentication_calls
        authentication_calls += 1
        yield SimpleNamespace(actor=_ACTOR, session=object())

    monkeypatch.setattr(chat_routes, "authenticated_request", request_auth)
    app = create_app(
        Settings(),
        SimpleNamespace(
            chats=chats,
            database=SimpleNamespace(session_factory=lambda: trusted),
        ),
    )
    started = time.monotonic()
    response = TestClient(app).post(
        f"/api/chats/{uuid.uuid4()}/messages/stream",
        headers={"Accept": "text/event-stream"},
        json={"question": "question"},
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 1.5
    assert chats.cancelled is True
    assert '"code": "access_revoked"' in response.text
    assert trusted.status == "access_revoked"
    assert any("v4_mark_turn_access_revoked_trusted" in call for call in trusted.calls)
    assert authentication_calls >= 2
    assert chats.transitioned is None


def test_stream_revocation_uses_plain_trusted_transaction_without_reauthentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TrustedSession:
        def __init__(self) -> None:
            self.status = "generating"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def begin(self):
            return self

        async def scalar(self, statement, parameters):
            assert "v4_mark_turn_access_revoked_trusted" in str(statement)
            self.status = "access_revoked"
            return "updated"

    class RevokedStreamChats(_Chats):
        async def prepare_message(
            self,
            actor: ActorContext,
            session: object,
            chat_id: uuid.UUID,
            question: str,
            auto_title: str = "New chat",
        ) -> BeginTurn:
            return BeginTurn(
                actor,
                chat_id,
                uuid.uuid4(),
                uuid.uuid4(),
                question,
                1,
                (),
                (),
                question,
            )

        async def snapshot_sources(
            self,
            actor: ActorContext,
            session: object,
            begin: BeginTurn,
            ranked: object,
        ) -> PreparedChatTurn:
            return PreparedChatTurn(
                actor,
                begin.chat_id,
                begin.turn_id,
                begin.token,
                "prompt",
                (),
            )

        async def stream(
            self, prepared: PreparedChatTurn, **kwargs: object
        ) -> AsyncIterator[str]:
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
            await kwargs["transition"](begin, "access_revoked", "access_revoked")
            yield (
                'event: error\ndata: {"code":"access_revoked",'
                '"message":"access to a source was revoked"}\n\n'
            )

    trusted = TrustedSession()
    authentication_calls = 0

    @asynccontextmanager
    async def request_auth(request: object, *, mutation: bool = False):
        nonlocal authentication_calls
        authentication_calls += 1
        yield SimpleNamespace(actor=_ACTOR, session=object())

    monkeypatch.setattr(chat_routes, "authenticated_request", request_auth)
    chats = RevokedStreamChats()
    app = create_app(
        Settings(),
        SimpleNamespace(
            chats=chats,
            database=SimpleNamespace(session_factory=lambda: trusted),
        ),
    )
    response = TestClient(app).post(
        f"/api/chats/{uuid.uuid4()}/messages/stream",
        json={"question": "question"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert '"code":"access_revoked"' in response.text
    assert trusted.status == "access_revoked"
    assert authentication_calls == 3


def test_chat_detail_preserves_deleted_source_snapshot() -> None:
    chats = _Chats()
    now = datetime.now(UTC)
    turn_id = uuid.uuid4()
    document_snapshot = uuid.uuid4()
    chunk_snapshot = uuid.uuid4()
    turn = ChatTurn(
        id=turn_id,
        chat_id=chats.chat.id,
        ordinal=1,
        question="question",
        status="complete",
        attempt=1,
        scope_version=1,
        generation_token=None,
        final_answer="answer [S1]",
        insufficient_context=False,
        error=None,
        completed_at=now,
        created_at=now,
        updated_at=now,
    )
    source = TurnSource(
        turn_id=turn_id,
        rank=1,
        label="S1",
        document_id=None,
        chunk_id=None,
        document_id_snapshot=document_snapshot,
        chunk_id_snapshot=chunk_snapshot,
        original_filename="deleted.pdf",
        display_name="Deleted",
        logical_path="/Old/Deleted",
        page_start=2,
        page_end=2,
        section=None,
        source_sha256="a" * 64,
        text_sha256="b" * 64,
        retrieval_distance=0.1,
        rerank_score=0.9,
        snapshot_text="fact",
        token_count=1,
        created_at=now,
    )

    async def get(
        actor: object,
        session: object,
        chat_id: uuid.UUID,
        *,
        page: int,
        limit: int,
    ) -> dict[str, object]:
        return {
            "chat": chats.chat,
            "scope_ids": [],
            "turns": [{"turn": turn, "sources": [source], "citation_ranks": [1]}],
            "page": page,
            "limit": limit,
            "total": 1,
        }

    chats.get = get
    response = _client(chats).get(f"/api/chats/{chats.chat.id}")
    payload = response.json()["turns"][0]["sources"][0]
    assert payload["document_id"] is None
    assert payload["document_id_snapshot"] == str(document_snapshot)
    assert payload["logical_path"] == "/Old/Deleted"
    assert payload["source_available"] is False
    assert response.json()["turns"][0]["citations"] == [payload]
