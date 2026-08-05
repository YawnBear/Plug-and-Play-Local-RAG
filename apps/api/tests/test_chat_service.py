import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.models import Chat, LibraryNode
from app.security.actor import ActorContext, ActorRole
from app.services import chats as chats_module
from app.services.chats import (
    INSUFFICIENT_CONTEXT_ANSWER,
    MAX_COMBINED_TOKENS,
    MAX_HISTORY_TOKENS,
    MODEL_CONTEXT_TOKENS,
    OUTPUT_TOKENS,
    BeginTurn,
    ChatAccessRevoked,
    ChatService,
    ChatSourceSnapshot,
    ChatValidation,
    HistoryPair,
    PreparedChatTurn,
    _budget_prompt,
    _normalize_generated_title,
    _retrieval_query,
    _select_history,
    normalize_chat_title,
    normalize_question,
)
from app.services.chunking import count_tokens
from app.services.ollama_generation import (
    GenerationChunk,
    GenerationServiceError,
    GenerationUsage,
)


def _actor() -> ActorContext:
    return ActorContext(uuid.uuid4(), ActorRole.MEMBER, 1, 1, uuid.uuid4())


def test_actual_ollama_prompt_count_enforces_context_and_formatting_reserve() -> None:
    usage = GenerationUsage(
        prompt_eval_count=MODEL_CONTEXT_TOKENS - OUTPUT_TOKENS,
        eval_count=1,
        total_duration_ns=1,
        load_duration_ns=0,
        prompt_eval_duration_ns=1,
        eval_duration_ns=1,
        estimated_prompt_tokens=1,
    )

    with pytest.raises(RuntimeError, match="model context budget"):
        chats_module._validate_generation_usage(
            GenerationChunk("done", done_reason="stop", usage=usage),
            "small lexical estimate",
        )


def _source(rank: int, text: str = "grounded fact") -> ChatSourceSnapshot:
    document_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    return ChatSourceSnapshot(
        rank=rank,
        label=f"S{rank}",
        document_id=document_id,
        chunk_id=chunk_id,
        document_id_snapshot=document_id,
        chunk_id_snapshot=chunk_id,
        original_filename="report.pdf",
        display_name="Report",
        logical_path="/Research/Report",
        page_start=1,
        page_end=1,
        section=None,
        source_sha256="a" * 64,
        text_sha256="b" * 64,
        retrieval_distance=0.1,
        rerank_score=0.9,
        text=text,
        token_count=max(1, count_tokens(text)),
    )


def _events(items: Sequence[str]) -> list[tuple[str, dict[str, object]]]:
    parsed = []
    for item in items:
        lines = item.strip().splitlines()
        parsed.append((lines[0].removeprefix("event: "), json.loads(lines[1][6:])))
    return parsed


def test_chat_text_normalization_contract() -> None:
    assert normalize_chat_title("  Cafe\u0301  ") == "Caf\u00e9"
    assert normalize_question("  first line\nsecond line  ") == (
        "first line\nsecond line"
    )
    assert normalize_question("one\ttwo\r\nthree") == "one\ttwo\r\nthree"
    with pytest.raises(ChatValidation):
        normalize_chat_title("hidden\u200bformat")
    with pytest.raises(ChatValidation):
        normalize_question(" " * 10)


@pytest.mark.parametrize("forbidden", ["\x00", "\x01", "\u200b", "\ud800"])
def test_question_rejects_database_invalid_unicode(forbidden: str) -> None:
    with pytest.raises(ChatValidation, match="forbidden Unicode"):
        normalize_question(f"before{forbidden}after")


def test_followup_retrieval_query_uses_only_previous_completed_question() -> None:
    assert _retrieval_query("current", None) == "current"
    assert _retrieval_query("current", "previous") == (
        "Current question:\ncurrent\n\nPrevious completed question:\nprevious"
    )


def test_generated_title_validation_is_strict_and_unicode_bounded() -> None:
    assert _normalize_generated_title("  Café overview  ") == "Café overview"
    for invalid in (
        "Title: Overview",
        '"Overview"',
        "```Overview```",
        "First\nSecond",
        "界" * 61,
    ):
        with pytest.raises(ChatValidation):
            _normalize_generated_title(invalid)


def test_history_selection_uses_whole_pairs_and_returns_chronological_order() -> None:
    history = (
        HistoryPair("old", "old answer"),
        HistoryPair("middle", " ".join(["token"] * 20)),
        HistoryPair("new", "new answer"),
    )
    selected = _select_history(history, budget=16)

    assert "User: old" in selected
    assert "User: middle" not in selected
    assert selected.index("User: old") < selected.index("User: new")
    assert count_tokens(selected) <= 16


def test_prompt_budget_keeps_an_ordered_source_prefix() -> None:
    history = tuple(
        HistoryPair(f"question {index}", " ".join(["answer"] * 80))
        for index in range(30)
    )
    sources = tuple(
        _source(index, " ".join(["evidence"] * 900)) for index in range(1, 9)
    )

    selected, prompt = _budget_prompt("?" * 2000, history, sources)

    assert prompt is not None
    assert selected
    assert [source.rank for source in selected] == list(range(1, len(selected) + 1))
    assert count_tokens(prompt) + OUTPUT_TOKENS <= MODEL_CONTEXT_TOKENS
    history_text = _select_history(history, MAX_HISTORY_TOKENS)
    assert count_tokens(history_text) <= MAX_HISTORY_TOKENS
    source_text = "\n\n".join(source.text for source in selected)
    assert count_tokens(history_text) + count_tokens(source_text) <= (
        MAX_COMBINED_TOKENS
    )


class _Generator:
    def __init__(
        self,
        tokens: Sequence[str | GenerationChunk],
        error: Exception | None = None,
    ) -> None:
        self.tokens = tokens
        self.error = error
        self.availability_calls = 0
        self.prompts: list[tuple[str, bool]] = []

    async def check_available(self) -> None:
        self.availability_calls += 1

    async def stream(
        self, prompt: str, *, think: bool = True
    ) -> AsyncIterator[GenerationChunk]:
        self.prompts.append((prompt, think))
        for token in self.tokens:
            yield (
                token
                if isinstance(token, GenerationChunk)
                else GenerationChunk("answer", token)
            )
        if self.error is not None:
            raise self.error
        yield GenerationChunk("done", done_reason="stop")


def test_first_title_uses_non_thinking_stream_and_normalized_question() -> None:
    async def exercise() -> None:
        generator = _Generator(["Concise title"])
        service = ChatService(None, None, None, generator)
        title = await service.generate_first_title("  Cafe\u0301 facts  ")
        assert title == "Concise title"
        prompt, think = generator.prompts[0]
        assert think is False
        assert prompt.endswith("Caf\u00e9 facts")
        assert "Concise title" not in prompt

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "chunks",
    [
        [GenerationChunk("thinking", "reasoning")],
        [GenerationChunk("answer", '"Quoted"')],
        [GenerationChunk("answer", "# Heading")],
        [GenerationChunk("answer", "**Bold**")],
        [GenerationChunk("answer", "__Bold__")],
        [GenerationChunk("answer", "_Italic_")],
        [GenerationChunk("answer", "~~Strike~~")],
        [GenerationChunk("answer", "“Smart quote”")],
        [GenerationChunk("answer", "> Quoted")],
        [GenerationChunk("answer", "- List item")],
        [GenerationChunk("answer", "```Fenced```")],
        [GenerationChunk("answer", "[Overview](https://example.test)")],
        [GenerationChunk("answer", "![Overview](https://example.test/image.png)")],
        [GenerationChunk("answer", "First\nSecond")],
        [GenerationChunk("answer", "x" * 61)],
        [GenerationChunk("done", done_reason="length")],
    ],
)
def test_invalid_first_title_is_nonfatal(chunks: Sequence[GenerationChunk]) -> None:
    async def exercise() -> None:
        service = ChatService(None, None, None, _Generator(chunks))
        assert await service.generate_first_title("question") == "New chat"

    asyncio.run(exercise())


def test_first_title_generator_failure_is_nonfatal() -> None:
    async def exercise() -> None:
        service = ChatService(
            None, None, None, _Generator([], GenerationServiceError("offline"))
        )
        assert await service.generate_first_title("question") == "New chat"

    asyncio.run(exercise())


def test_first_title_timeout_includes_stalled_cancellation_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StalledCleanupGenerator:
        def stream(self, prompt: str, *, think: bool = True):
            class Stream:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    try:
                        await asyncio.sleep(60)
                    except asyncio.CancelledError:
                        # Production coordinator cancellation performs another
                        # request and can itself become unresponsive.
                        await asyncio.sleep(60)
                    raise StopAsyncIteration

                async def aclose(self):
                    await asyncio.sleep(60)

            return Stream()

    async def exercise() -> None:
        monkeypatch.setattr(chats_module, "_TITLE_TIMEOUT_SECONDS", 0.02)
        service = ChatService(None, None, None, StalledCleanupGenerator())
        started = asyncio.get_running_loop().time()
        assert await service.generate_first_title("question") == "New chat"
        assert asyncio.get_running_loop().time() - started < 0.2

    asyncio.run(exercise())


def test_first_title_parent_cancellation_is_not_swallowed() -> None:
    class CancellableGenerator:
        def stream(self, prompt: str, *, think: bool = True):
            class Stream:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    await asyncio.sleep(60)
                    raise StopAsyncIteration

                async def aclose(self):
                    await asyncio.sleep(60)

            return Stream()

    async def exercise() -> None:
        service = ChatService(None, None, None, CancellableGenerator())
        task = asyncio.create_task(service.generate_first_title("question"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.5)

    asyncio.run(exercise())


class _StreamService(ChatService):
    def __init__(self, generator: _Generator) -> None:
        super().__init__(None, None, None, generator)
        self.completed: tuple[str, bool, Sequence[ChatSourceSnapshot]] | None = None
        self.transitioned: tuple[str, str] | None = None
        self.persisted_citations: tuple[ChatSourceSnapshot, ...] | None = None

    async def finalize(
        self,
        prepared: PreparedChatTurn,
        answer: str,
        insufficient: bool,
        citations: Sequence[ChatSourceSnapshot],
    ) -> tuple[ChatSourceSnapshot, ...]:
        self.completed = (answer, insufficient, citations)
        if self.persisted_citations is not None:
            return self.persisted_citations
        return tuple(citations)

    async def transition(self, begin: BeginTurn, status: str, message: str) -> None:
        self.transitioned = (status, message)

    async def monitor(self, prepared: PreparedChatTurn) -> None:
        return None

    def events(self, prepared: PreparedChatTurn) -> AsyncIterator[str]:
        return self.stream(
            prepared,
            finalize=self.finalize,
            transition=self.transition,
            monitor=self.monitor,
        )


def test_stream_commits_validated_final_before_final_event() -> None:
    async def exercise() -> None:
        source = _source(1)
        service = _StreamService(_Generator(["Grounded ", "answer [S1]"]))
        prepared = PreparedChatTurn(
            _actor(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "prompt", (source,)
        )
        raw = []
        async for event in service.events(prepared):
            if "event: final" in event:
                assert service.completed is not None
            raw.append(event)
        events = _events(raw)
        assert [name for name, _payload in events] == [
            "sources",
            "status",
            "reasoning_start",
            "reasoning_end",
            "status",
            "token",
            "token",
            "status",
            "final",
        ]
        assert [payload["seq"] for _name, payload in events] == list(range(1, 10))
        assert events[1][1]["phase"] == "reasoning"
        assert events[4][1]["phase"] == "streaming_answer"
        assert events[7][1]["phase"] == "validating_citations"
        assert events[-1][1]["insufficient_context"] is False
        assert service.completed == ("Grounded answer [S1]", False, (source,))

    asyncio.run(exercise())


def test_length_stop_automatically_continues_once_without_more_thinking() -> None:
    class _LengthThenStopGenerator(_Generator):
        def __init__(self) -> None:
            super().__init__([])
            self.calls: list[tuple[str, bool]] = []

        async def stream(
            self, prompt: str, *, think: bool = True
        ) -> AsyncIterator[GenerationChunk]:
            self.calls.append((prompt, think))
            if len(self.calls) == 1:
                yield GenerationChunk("thinking", "reason")
                yield GenerationChunk("answer", "Grounded ")
                yield GenerationChunk("done", done_reason="length")
                return
            yield GenerationChunk("answer", "answer [S1]")
            yield GenerationChunk("done", done_reason="stop")

    async def exercise() -> None:
        source = _source(1)
        generator = _LengthThenStopGenerator()
        service = _StreamService(generator)
        prepared = PreparedChatTurn(
            _actor(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "prompt", (source,)
        )

        events = _events([event async for event in service.events(prepared)])

        assert [think for _prompt, think in generator.calls] == [True, False]
        assert generator.calls[1][0] != "prompt"
        assert any(
            name == "status" and payload["phase"] == "continuing_answer"
            for name, payload in events
        )
        assert service.completed == ("Grounded answer [S1]", False, (source,))
        assert events[-1][0] == "final"

    asyncio.run(exercise())


def test_second_length_stop_persists_unverified_partial_without_final() -> None:
    class _AlwaysLimitedGenerator(_Generator):
        async def stream(
            self, prompt: str, *, think: bool = True
        ) -> AsyncIterator[GenerationChunk]:
            yield GenerationChunk("answer", "Grounded [S1] ")
            yield GenerationChunk("done", done_reason="length")

    async def exercise() -> None:
        source = _source(1)
        service = _StreamService(_AlwaysLimitedGenerator([]))
        prepared = PreparedChatTurn(
            _actor(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "prompt", (source,)
        )

        events = _events([event async for event in service.events(prepared)])

        assert service.completed is None
        assert service.transitioned == (
            "length_limited",
            "Grounded [S1] Grounded [S1]",
        )
        assert events[-1][0] == "error"
        assert events[-1][1]["code"] == "generation_limit"
        assert all(name != "final" for name, _payload in events)

    asyncio.run(exercise())


def test_manual_continuation_reuses_partial_and_finishes_without_thinking() -> None:
    class _ContinuationGenerator(_Generator):
        def __init__(self) -> None:
            super().__init__([])
            self.think_values: list[bool] = []

        async def stream(
            self, prompt: str, *, think: bool = True
        ) -> AsyncIterator[GenerationChunk]:
            self.think_values.append(think)
            yield GenerationChunk("answer", "answer [S1]")
            yield GenerationChunk("done", done_reason="stop")

    async def exercise() -> None:
        source = _source(1)
        generator = _ContinuationGenerator()
        service = _StreamService(generator)
        prepared = PreparedChatTurn(
            _actor(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "continuation prompt",
            (source,),
            continuation_answer="Grounded ",
        )

        events = _events([event async for event in service.events(prepared)])

        assert generator.think_values == [False]
        assert service.completed == ("Grounded answer [S1]", False, (source,))
        assert [payload["text"] for name, payload in events if name == "token"] == [
            "Grounded ",
            "answer [S1]",
        ]

    asyncio.run(exercise())


def test_missing_citation_is_repaired_and_committed() -> None:
    class _RepairGenerator(_Generator):
        def __init__(self) -> None:
            super().__init__([])
            self.calls: list[tuple[str, bool]] = []

        async def stream(
            self, prompt: str, *, think: bool = True
        ) -> AsyncIterator[GenerationChunk]:
            self.calls.append((prompt, think))
            answer = "Useful draft" if len(self.calls) == 1 else "Useful draft [S1]"
            yield GenerationChunk("answer", answer)
            yield GenerationChunk("done", done_reason="stop")

    async def exercise() -> None:
        source = _source(1)
        generator = _RepairGenerator()
        service = _StreamService(generator)
        prepared = PreparedChatTurn(
            _actor(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "prompt", (source,)
        )
        events = _events([event async for event in service.events(prepared)])
        assert [think for _prompt, think in generator.calls] == [True, False]
        assert generator.calls[1][0] != "prompt"
        assert service.completed == ("Useful draft [S1]", False, (source,))
        assert any(name == "answer_reset" for name, _payload in events)
        assert [
            payload["phase"]
            for name, payload in events
            if name == "status"
        ][-3:] == [
            "validating_citations",
            "repairing_citations",
            "validating_citations",
        ]
        assert events[-1][0] == "final"
        assert events[-1][1]["citations"]

    asyncio.run(exercise())


def test_failed_citation_repair_preserves_unverified_draft_without_final() -> None:
    class _FailedRepairGenerator(_Generator):
        def __init__(self) -> None:
            super().__init__([])
            self.calls = 0

        async def stream(
            self, prompt: str, *, think: bool = True
        ) -> AsyncIterator[GenerationChunk]:
            self.calls += 1
            answer = "Useful original draft" if self.calls == 1 else "Unsupported [S9]"
            yield GenerationChunk("answer", answer)
            yield GenerationChunk("done", done_reason="stop")

    async def exercise() -> None:
        source = _source(1)
        service = _StreamService(_FailedRepairGenerator())
        prepared = PreparedChatTurn(
            _actor(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "prompt", (source,)
        )

        events = _events([event async for event in service.events(prepared)])

        assert service.completed is None
        assert service.transitioned == (
            "citation_failed",
            "Useful original draft",
        )
        assert events[-1][0] == "error"
        assert events[-1][1]["code"] == "citation_validation_failed"
        assert all(name != "final" for name, _payload in events)

    asyncio.run(exercise())


def test_explicit_insufficient_context_sentinel_does_not_run_repair() -> None:
    async def exercise() -> None:
        source = _source(1)
        generator = _Generator(["INSUFFICIENT_CONTEXT"])
        service = _StreamService(generator)
        prepared = PreparedChatTurn(
            _actor(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "prompt", (source,)
        )

        events = _events([event async for event in service.events(prepared)])

        assert service.completed == (INSUFFICIENT_CONTEXT_ANSWER, True, ())
        assert all(name != "answer_reset" for name, _payload in events)
        assert events[-1][1]["insufficient_context"] is True

    asyncio.run(exercise())


def test_terminal_insufficient_context_discards_explanatory_citations() -> None:
    async def exercise() -> None:
        source = _source(1)
        generator = _Generator(
            [
                "The sources do not establish the requested value [S1].\n\n",
                "INSUFFICIENT_CONTEXT",
            ]
        )
        service = _StreamService(generator)
        prepared = PreparedChatTurn(
            _actor(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "prompt", (source,)
        )

        events = _events([event async for event in service.events(prepared)])

        assert service.completed == (INSUFFICIENT_CONTEXT_ANSWER, True, ())
        assert all(name != "answer_reset" for name, _payload in events)
        assert events[-1][1]["insufficient_context"] is True
        assert events[-1][1]["citations"] == []

    asyncio.run(exercise())


def test_already_complete_stream_emits_no_context_tail_without_regeneration() -> None:
    async def exercise() -> None:
        service = _StreamService(
            _Generator([], AssertionError("complete turn must not regenerate"))
        )
        prepared = PreparedChatTurn(
            _actor(), uuid.uuid4(), uuid.uuid4(), None, None, (), True
        )

        events = _events(
            [
                event
                async for event in service.stream(
                    prepared,
                    finalize=service.finalize,
                    transition=service.transition,
                    monitor=service.monitor,
                    sequence_start=4,
                )
            ]
        )

        assert [name for name, _payload in events] == [
            "sources",
            "status",
            "final",
        ]
        assert [payload["seq"] for _name, payload in events] == [4, 5, 6]
        assert events[1][1]["phase"] == "validating_citations"
        assert events[-1][1] == {
            "chat_id": str(prepared.chat_id),
            "turn_id": str(prepared.turn_id),
            "seq": 6,
            "answer": INSUFFICIENT_CONTEXT_ANSWER,
            "insufficient_context": True,
            "citations": [],
        }
        assert service.completed is None
        assert service.transitioned is None

    asyncio.run(exercise())


def test_preparation_revocation_emits_only_access_revoked_error() -> None:
    async def exercise() -> None:
        service = _StreamService(_Generator([]))
        prepared = PreparedChatTurn(
            _actor(),
            uuid.uuid4(),
            uuid.uuid4(),
            None,
            None,
            (),
            access_revoked=True,
        )
        events = _events([event async for event in service.events(prepared)])
        assert [name for name, _payload in events] == ["error"]
        assert events[0][1]["code"] == "access_revoked"
        assert service.transitioned is None
        assert service.completed is None

    asyncio.run(exercise())


def test_final_citations_use_persisted_availability_after_delete_race() -> None:
    async def exercise() -> None:
        source = _source(1)
        service = _StreamService(_Generator(["Grounded [S1]"]))
        service.persisted_citations = (
            replace(source, document_id=None, chunk_id=None),
        )
        prepared = PreparedChatTurn(
            _actor(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "prompt", (source,)
        )

        events = _events([event async for event in service.events(prepared)])

        initial_source = events[0][1]["sources"][0]
        final_source = events[-1][1]["citations"][0]
        assert initial_source["source_available"] is True
        assert final_source["document_id"] is None
        assert final_source["chunk_id"] is None
        assert final_source["source_available"] is False
        assert final_source["document_id_snapshot"] == str(source.document_id_snapshot)

    asyncio.run(exercise())


def test_midstream_failure_transitions_before_error_and_never_emits_final() -> None:
    async def exercise() -> None:
        service = _StreamService(
            _Generator(["draft"], GenerationServiceError("model disconnected"))
        )
        prepared = PreparedChatTurn(
            _actor(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "prompt",
            (_source(1),),
        )
        events = _events([event async for event in service.events(prepared)])
        assert service.transitioned == ("failed", "model disconnected")
        assert [name for name, _payload in events] == [
            "sources",
            "status",
            "reasoning_start",
            "reasoning_end",
            "status",
            "token",
            "error",
        ]
        assert service.completed is None

    asyncio.run(exercise())


def test_reasoning_is_typed_bounded_and_never_persisted() -> None:
    async def exercise() -> None:
        source = _source(1)
        service = _StreamService(
            _Generator(
                [
                    GenerationChunk("thinking", "r" * 19_999),
                    GenerationChunk("thinking", "xyz"),
                    GenerationChunk("answer", "Grounded [S1]"),
                ]
            )
        )
        prepared = PreparedChatTurn(
            _actor(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "prompt", (source,)
        )

        events = _events([event async for event in service.events(prepared)])

        deltas = [
            payload["text"] for name, payload in events if name == "reasoning_delta"
        ]
        assert sum(len(text) for text in deltas) == 20_000
        reasoning_end = next(
            payload for name, payload in events if name == "reasoning_end"
        )
        assert reasoning_end["truncated"] is True
        assert service.completed == ("Grounded [S1]", False, (source,))
        assert all("r" * 100 not in str(value) for value in service.completed)

    asyncio.run(exercise())


def test_thinking_after_answer_is_rejected_without_finalizing() -> None:
    async def exercise() -> None:
        service = _StreamService(
            _Generator(
                [
                    GenerationChunk("answer", "draft"),
                    GenerationChunk("thinking", "late reason"),
                ]
            )
        )
        prepared = PreparedChatTurn(
            _actor(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "prompt",
            (_source(1),),
        )

        events = _events([event async for event in service.events(prepared)])

        assert events[-1][0] == "error"
        assert events[-1][1]["code"] == "generation_failed"
        assert "thinking after answer" in str(events[-1][1]["message"])
        assert service.completed is None

    asyncio.run(exercise())


def test_finalization_revocation_emits_access_revoked_and_clears_draft() -> None:
    async def exercise() -> None:
        service = _StreamService(_Generator(["Grounded [S1]"]))
        prepared = PreparedChatTurn(
            _actor(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "prompt",
            (_source(1),),
        )

        async def revoked_finalize(*args: object) -> tuple[ChatSourceSnapshot, ...]:
            raise ChatAccessRevoked

        events = _events(
            [
                event
                async for event in service.stream(
                    prepared,
                    finalize=revoked_finalize,
                    transition=service.transition,
                    monitor=service.monitor,
                )
            ]
        )

        assert events[-1][0] == "error"
        assert events[-1][1]["code"] == "access_revoked"
        assert service.transitioned == ("access_revoked", "access_revoked")

    asyncio.run(exercise())


def test_disconnect_after_sources_marks_turn_interrupted() -> None:
    async def exercise() -> None:
        service = _StreamService(_Generator(["draft [S1]"]))
        prepared = PreparedChatTurn(
            _actor(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "prompt",
            (_source(1),),
        )
        stream = service.events(prepared)
        assert "event: sources" in await anext(stream)
        await stream.aclose()
        assert service.transitioned == ("interrupted", "generation cancelled")
        assert service.completed is None

    asyncio.run(exercise())


class _EmptyRetrieval:
    async def embed_query(self, query: str) -> list[float]:
        return [0.0] * 1024

    async def retrieve_vector(self, *args: object, **kwargs: object) -> list[object]:
        return []


class _EmptyReranker:
    async def rerank(self, *args: object, **kwargs: object) -> list[object]:
        return []


class _NoSourceService(ChatService):
    async def _snapshot_sources(
        self,
        actor: ActorContext,
        begin: BeginTurn,
        session: object,
        ranked: Sequence[object],
        history: Sequence[HistoryPair],
    ) -> tuple[tuple[ChatSourceSnapshot, ...], str | None]:
        return (), None


def test_no_retrieval_sources_complete_without_generator_availability_call() -> None:
    async def exercise() -> None:
        generator = _Generator([])
        service = _NoSourceService(
            None,
            _EmptyRetrieval(),
            _EmptyReranker(),
            generator,
        )
        begin = BeginTurn(
            _actor(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "question",
            1,
            (uuid.uuid4(),),
            (),
            "question",
        )
        actor = _actor()
        begin = replace(begin, actor=actor)
        prepared = await service.snapshot_sources(actor, object(), begin, [])
        assert prepared.already_complete
        assert generator.availability_calls == 0

    asyncio.run(exercise())


def test_embedding_failure_does_not_open_or_transition_database_state() -> None:
    class BrokenRetrieval:
        async def embed_query(self, query: str) -> list[float]:
            raise RuntimeError("database unavailable")

    async def exercise() -> None:
        generator = _Generator([])
        service = _NoSourceService(
            None,
            BrokenRetrieval(),
            _EmptyReranker(),
            generator,
        )
        begin = BeginTurn(
            _actor(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "question",
            1,
            (uuid.uuid4(),),
            (),
            "question",
        )
        with pytest.raises(RuntimeError, match="database unavailable"):
            await service.embed_retrieval_query(begin)

    asyncio.run(exercise())


def test_turn_mutations_use_only_controlled_v4_functions_and_views() -> None:
    source = (Path(__file__).parents[1] / "app" / "services" / "chats.py").read_text(
        encoding="utf-8"
    )
    for function_name in (
        "v4_begin_turn",
        "v4_retry_turn",
        "v4_store_turn_sources",
        "v4_finalize_turn",
        "v4_interrupt_turn",
        "v4_fail_turn",
        "v4_mark_turn_citation_failed",
        "v4_mark_turn_access_revoked",
    ):
        assert function_name in source
    assert "SELECT * FROM v4_chat_history" in source
    assert "SELECT * FROM v4_authorized_turn_sources" in source
    assert "session.add(turn)" not in source
    assert "session.add_all(" not in source
    assert "update(ChatTurn)" not in source
    assert "delete(TurnSource)" not in source
    assert "delete(TurnCitation)" not in source


class _TransitionSession:
    def __init__(self, turn: object) -> None:
        self.turn = turn

    async def __aenter__(self) -> "_TransitionSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> "_TransitionSession":
        return self

    async def scalar(
        self, statement: object, parameters: dict[str, object] | None = None
    ) -> object:
        assert "v4_interrupt_turn" in str(statement)
        assert parameters is not None
        if self.turn.status == "interrupted":
            return "already_interrupted"
        if (
            self.turn.status != "generating"
            or self.turn.generation_token != parameters["generation_token"]
        ):
            return "stale"
        self.turn.status = "interrupted"
        self.turn.generation_token = None
        self.turn.final_answer = None
        self.turn.insufficient_context = False
        self.turn.error = "generation cancelled"
        self.turn.completed_at = None
        return "interrupted"


class _TransitionFactory:
    def __init__(self, turn: object) -> None:
        self.session = _TransitionSession(turn)
        self.calls = 0

    def __call__(self) -> _TransitionSession:
        self.calls += 1
        return self.session


class _TransitionService(ChatService):
    async def _owned_chat(
        self, session: object, actor: ActorContext, chat_id: uuid.UUID
    ) -> Chat:
        return Chat(id=chat_id)


def test_interrupt_prepared_is_token_fenced_and_idempotent() -> None:
    async def exercise() -> None:
        chat_id = uuid.uuid4()
        turn_id = uuid.uuid4()
        active_token = uuid.uuid4()
        turn = SimpleNamespace(
            id=turn_id,
            status="generating",
            generation_token=active_token,
            final_answer=None,
            insufficient_context=False,
            error=None,
            completed_at=None,
            attempt=2,
        )
        factory = _TransitionFactory(turn)
        service = _TransitionService(factory, None, None, None)

        stale = PreparedChatTurn(
            _actor(), chat_id, turn_id, uuid.uuid4(), "prompt", (_source(1),)
        )

        async def transition(begin: BeginTurn, status: str, message: str) -> None:
            await service.transition(
                begin.actor, factory.session, begin, status, message
            )

        await service.interrupt_prepared(stale, transition=transition)
        assert turn.status == "generating"
        assert turn.generation_token == active_token
        assert turn.attempt == 2

        active = replace(stale, token=active_token)
        await service.interrupt_prepared(active, transition=transition)
        assert turn.status == "interrupted"
        assert turn.generation_token is None
        assert turn.error == "generation cancelled"
        assert turn.attempt == 2

        await service.interrupt_prepared(active, transition=transition)
        assert turn.status == "interrupted"
        assert turn.error == "generation cancelled"
        assert turn.attempt == 2

    asyncio.run(exercise())


@pytest.mark.parametrize("status", ["complete", "failed", "interrupted"])
def test_interrupt_prepared_does_not_overwrite_terminal_turn(status: str) -> None:
    async def exercise() -> None:
        chat_id = uuid.uuid4()
        turn_id = uuid.uuid4()
        stale_token = uuid.uuid4()
        turn = SimpleNamespace(
            id=turn_id,
            status=status,
            generation_token=None,
            final_answer="kept answer" if status == "complete" else None,
            insufficient_context=False,
            error=None if status == "complete" else "kept error",
            completed_at=object() if status == "complete" else None,
            attempt=3,
        )
        factory = _TransitionFactory(turn)
        service = _TransitionService(factory, None, None, None)
        prepared = PreparedChatTurn(
            _actor(), chat_id, turn_id, stale_token, "prompt", (_source(1),)
        )

        snapshot = vars(turn).copy()

        async def transition(begin: BeginTurn, next_status: str, message: str) -> None:
            await service.transition(
                begin.actor,
                factory.session,
                begin,
                next_status,
                message,
            )

        await service.interrupt_prepared(prepared, transition=transition)
        assert vars(turn) == snapshot

    asyncio.run(exercise())


def test_interrupt_prepared_noops_for_already_complete_preparation() -> None:
    async def exercise() -> None:
        factory = _TransitionFactory(object())
        service = _TransitionService(factory, None, None, None)
        prepared = PreparedChatTurn(
            _actor(), uuid.uuid4(), uuid.uuid4(), None, None, (), True
        )

        async def transition(begin: BeginTurn, status: str, message: str) -> None:
            raise AssertionError("complete preparation must not transition")

        await service.interrupt_prepared(prepared, transition=transition)
        assert factory.calls == 0

    asyncio.run(exercise())


class _QueuedScalarsSession:
    def __init__(self, *results: object) -> None:
        self.results = list(results)

    async def scalars(self, statement: object) -> object:
        return self.results.pop(0)


def _node(
    kind: str,
    *,
    parent_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> LibraryNode:
    node_id = uuid.uuid4()
    return LibraryNode(
        id=node_id,
        parent_id=parent_id,
        kind=kind,
        name=f"{kind}-{node_id}",
        name_key=f"{kind}-{node_id}",
        document_id=document_id,
    )


def test_selected_scope_canonicalizes_redundant_descendants() -> None:
    async def exercise() -> None:
        root = _node("folder")
        child = _node("folder", parent_id=root.id)
        file_node = _node("file", parent_id=child.id, document_id=uuid.uuid4())
        session = _QueuedScalarsSession([root, child, file_node])
        service = ChatService(None, None, None, None)

        canonical = await service._canonical_scope(
            session, _actor(), [file_node.id, child.id, root.id]
        )
        assert canonical == (root.id,)

    asyncio.run(exercise())


def test_selected_folder_scope_resolves_current_ready_descendants() -> None:
    async def resolve(in_scope: bool) -> tuple[uuid.UUID, ...]:
        root = _node("folder")
        selected = LibraryNode(
            id=selected_id,
            parent_id=root.id,
            kind="folder",
            name="selected",
            name_key="selected",
            document_id=None,
        )
        file_node = _node(
            "file",
            parent_id=selected.id if in_scope else root.id,
            document_id=document_id,
        )
        session = _QueuedScalarsSession(
            [document_id], [root, selected, file_node], [selected_id]
        )
        chat = Chat(id=uuid.uuid4(), scope_mode="selected")
        service = ChatService(None, None, None, None)
        return await service._resolve_scope(session, _actor(), chat)

    selected_id = uuid.uuid4()
    document_id = uuid.uuid4()
    assert asyncio.run(resolve(True)) == (document_id,)
    assert asyncio.run(resolve(False)) == ()
