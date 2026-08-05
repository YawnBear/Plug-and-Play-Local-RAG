import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace

import pytest

from app.db.repositories import RetrievedChunk
from app.schemas.query import QueryRequest
from app.security.actor import ActorContext, ActorRole
from app.services.library import LibraryCorruption, LibraryLocation
from app.services.ollama_generation import GenerationChunk, GenerationServiceError
from app.services.rag import INSUFFICIENT_CONTEXT_ANSWER, PreparedQuery, RagService
from app.services.reranker import RerankedChunk


def _actor() -> ActorContext:
    return ActorContext(uuid.uuid4(), ActorRole.MEMBER, 1, 1, uuid.uuid4())


class _Session:
    pass


def _candidate(text: str = "The policy allows seven days.") -> RetrievedChunk:
    return RetrievedChunk(
        chunk=SimpleNamespace(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            filename="policy.pdf",
            page_start=2,
            page_end=3,
            section="Deadlines",
            text=text,
        ),
        distance=0.1,
    )


class _Retrieval:
    def __init__(self, candidates: Sequence[RetrievedChunk]) -> None:
        self.candidates = candidates
        self.calls: list[tuple[str, int, object]] = []
        self.active = False

    async def retrieve(
        self,
        actor: ActorContext,
        session: object,
        query: str,
        *,
        limit: int,
        document_ids: object,
    ) -> Sequence[RetrievedChunk]:
        self.active = True
        self.calls.append((query, limit, document_ids))
        self.active = False
        return self.candidates


class _Reranker:
    def __init__(self) -> None:
        self.limits: list[int] = []

    async def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        limit: int,
    ) -> list[RerankedChunk]:
        self.limits.append(limit)
        return [
            RerankedChunk(candidate=candidate, score=1.0)
            for candidate in candidates[:limit]
        ]


class _Generator:
    def __init__(
        self,
        tokens: Sequence[str] = ("Seven days ", "[S1]"),
        *,
        error: GenerationServiceError | None = None,
    ) -> None:
        self.tokens = tokens
        self.error = error
        self.check_count = 0
        self.prompts: list[str] = []
        self.database_active_during_stream: bool | None = None
        self.retrieval: _Retrieval | None = None

    async def check_available(self) -> None:
        self.check_count += 1

    async def stream(
        self, prompt: str, *, think: bool = True
    ) -> AsyncIterator[GenerationChunk]:
        self.prompts.append(prompt)
        if self.retrieval is not None:
            self.database_active_during_stream = self.retrieval.active
        if self.error is not None:
            raise self.error
        for token in self.tokens:
            yield GenerationChunk("answer", token)
        yield GenerationChunk("done", done_reason="stop")


class _Library:
    def __init__(self, *, omit: bool = False) -> None:
        self.omit = omit

    async def locations_for_documents(
        self,
        actor: ActorContext,
        session: object,
        document_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, LibraryLocation]:
        if self.omit:
            return {}
        return {
            document_id: LibraryLocation(
                uuid.uuid4(),
                None,
                "Current policy.pdf",
                "/Policies/Current policy.pdf",
                actor.user_id,
            )
            for document_id in document_ids
        }


def _parse_event(event: str) -> tuple[str, dict[str, object]]:
    lines = event.strip().splitlines()
    return lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))


async def _collect(service: RagService, prepared: PreparedQuery) -> list[str]:
    async def monitor(target: PreparedQuery) -> None:
        return None

    return [event async for event in service.stream(prepared, monitor=monitor)]


def test_query_defaults_retrieve_20_rerank_6_and_stream_valid_final() -> None:
    candidate = _candidate()
    retrieval = _Retrieval([candidate])
    reranker = _Reranker()
    generator = _Generator()
    generator.retrieval = retrieval
    service = RagService(retrieval, reranker, generator, _Library())

    async def exercise() -> list[str]:
        prepared = await service.prepare(
            _actor(), _Session(), QueryRequest(question="  deadline?  ")
        )
        return await _collect(service, prepared)

    events = asyncio.run(exercise())

    assert retrieval.calls == [("deadline?", 20, None)]
    assert reranker.limits == [6]
    assert generator.check_count == 1
    assert generator.database_active_during_stream is False
    assert [_parse_event(event)[0] for event in events] == [
        "sources",
        "token",
        "token",
        "final",
    ]
    final = _parse_event(events[-1])[1]
    assert final["answer"] == "Seven days [S1]"
    assert final["insufficient_context"] is False
    assert final["citations"] == [
        {
            "label": "S1",
            "chunk_id": str(candidate.chunk.id),
            "filename": "policy.pdf",
            "document_id": str(candidate.chunk.document_id),
            "display_name": "Current policy.pdf",
            "logical_path": "/Policies/Current policy.pdf",
            "page_start": 2,
            "page_end": 3,
        }
    ]
    assert "[S1]" in generator.prompts[0]
    assert "policy.pdf" in generator.prompts[0]
    assert "/Policies/Current policy.pdf" in generator.prompts[0]


def test_document_filter_and_context_limit_are_forwarded() -> None:
    document_id = uuid.uuid4()
    retrieval = _Retrieval([_candidate()])
    reranker = _Reranker()
    generator = _Generator()
    service = RagService(retrieval, reranker, generator, _Library())

    async def exercise() -> None:
        await service.prepare(
            _actor(),
            _Session(),
            QueryRequest(
                question="deadline?",
                document_ids=[document_id],
                retrieve_k=12,
                context_k=8,
            ),
        )

    asyncio.run(exercise())

    assert retrieval.calls == [("deadline?", 12, [document_id])]
    assert reranker.limits == [8]


def test_empty_retrieval_declines_without_loading_generation() -> None:
    retrieval = _Retrieval([])
    reranker = _Reranker()
    generator = _Generator()
    service = RagService(retrieval, reranker, generator, _Library())

    async def exercise() -> list[str]:
        prepared = await service.prepare(
            _actor(), _Session(), QueryRequest(question="unsupported")
        )
        return await _collect(service, prepared)

    events = asyncio.run(exercise())

    assert [_parse_event(event)[0] for event in events] == ["sources", "final"]
    final = _parse_event(events[-1])[1]
    assert final == {
        "answer": INSUFFICIENT_CONTEXT_ANSWER,
        "insufficient_context": True,
        "citations": [],
    }
    assert reranker.limits == []
    assert generator.check_count == 0


@pytest.mark.parametrize(
    "tokens",
    [
        ("An uncited claim",),
        ("A claim [S2]",),
        ("A claim [S1] plus malformed [Sfoo]",),
        ("INSUFFICIENT_CONTEXT",),
    ],
)
def test_invalid_or_missing_source_labels_produce_authoritative_decline(
    tokens: Sequence[str],
) -> None:
    retrieval = _Retrieval([_candidate()])
    service = RagService(retrieval, _Reranker(), _Generator(tokens), _Library())

    async def exercise() -> list[str]:
        prepared = await service.prepare(
            _actor(), _Session(), QueryRequest(question="question")
        )
        return await _collect(service, prepared)

    events = asyncio.run(exercise())
    final = _parse_event(events[-1])[1]
    assert final["answer"] == INSUFFICIENT_CONTEXT_ANSWER
    assert final["insufficient_context"] is True
    assert final["citations"] == []


def test_terminal_insufficient_context_discards_explanatory_citations() -> None:
    retrieval = _Retrieval([_candidate()])
    service = RagService(
        retrieval,
        _Reranker(),
        _Generator(
            (
                "The sources do not establish the requested value [S1].\n\n",
                "INSUFFICIENT_CONTEXT",
            )
        ),
        _Library(),
    )

    async def exercise() -> list[str]:
        prepared = await service.prepare(
            _actor(), _Session(), QueryRequest(question="unknown value")
        )
        return await _collect(service, prepared)

    events = asyncio.run(exercise())
    final = _parse_event(events[-1])[1]

    assert final == {
        "answer": INSUFFICIENT_CONTEXT_ANSWER,
        "insufficient_context": True,
        "citations": [],
    }


def test_streaming_generation_failure_emits_error_event() -> None:
    retrieval = _Retrieval([_candidate()])
    generator = _Generator(error=GenerationServiceError("Ollama stopped"))
    service = RagService(retrieval, _Reranker(), generator, _Library())

    async def exercise() -> list[str]:
        prepared = await service.prepare(
            _actor(), _Session(), QueryRequest(question="question")
        )
        return await _collect(service, prepared)

    events = asyncio.run(exercise())
    assert [_parse_event(event)[0] for event in events] == ["sources", "error"]
    assert _parse_event(events[-1])[1] == {
        "code": "generation_failed",
        "message": "Ollama stopped",
    }


def test_client_cancellation_propagates_after_database_work_is_released() -> None:
    retrieval = _Retrieval([_candidate()])

    class _CancelledGenerator(_Generator):
        async def stream(
            self, prompt: str, *, think: bool = True
        ) -> AsyncIterator[GenerationChunk]:
            assert retrieval.active is False
            raise asyncio.CancelledError
            yield GenerationChunk("answer", "unreachable")

    service = RagService(retrieval, _Reranker(), _CancelledGenerator(), _Library())

    async def exercise() -> None:
        prepared = await service.prepare(
            _actor(), _Session(), QueryRequest(question="question")
        )

        async def monitor(target: PreparedQuery) -> None:
            return None

        stream = service.stream(prepared, monitor=monitor)
        assert _parse_event(await anext(stream))[0] == "sources"
        with pytest.raises(asyncio.CancelledError):
            await anext(stream)

    asyncio.run(exercise())


def test_missing_canonical_location_is_explicit_corruption() -> None:
    service = RagService(
        _Retrieval([_candidate()]),
        _Reranker(),
        _Generator(),
        _Library(omit=True),
    )

    with pytest.raises(LibraryCorruption, match="canonical"):
        asyncio.run(
            service.prepare(_actor(), _Session(), QueryRequest(question="question"))
        )
