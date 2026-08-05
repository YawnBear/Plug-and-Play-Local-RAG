import asyncio
import threading
import uuid
from types import SimpleNamespace

import pytest
import torch

from app.db.repositories import RetrievedChunk
from app.services.reranker import BgeReranker


def _candidate(text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=SimpleNamespace(id=uuid.uuid4(), text=text),
        distance=0.1,
    )


class _DeterministicReranker(BgeReranker):
    def __init__(self) -> None:
        super().__init__("test-model")
        self.load_count = 0
        self.inference_thread: int | None = None

    def _load_sync(self) -> tuple[object, object]:
        self.load_count += 1
        return object(), object()

    def _score_sync(self, query: str, passages: list[str]) -> list[float]:
        self.inference_thread = threading.get_ident()
        return [float(len(passage)) for passage in passages]


def test_reranker_loads_once_off_loop_and_sorts_scores() -> None:
    reranker = _DeterministicReranker()
    candidates = [_candidate("short"), _candidate("a much longer passage")]
    loop_thread = threading.get_ident()

    async def exercise() -> None:
        first = await reranker.rerank("question", candidates, limit=6)
        second = await reranker.rerank("question", candidates, limit=6)
        assert first[0].candidate.chunk.text == "a much longer passage"
        assert second[0].score >= second[1].score

    asyncio.run(exercise())

    assert reranker.load_count == 1
    assert reranker.inference_thread is not None
    assert reranker.inference_thread != loop_thread


@pytest.mark.parametrize("limit", [4, 9])
def test_reranker_enforces_context_bounds(limit: int) -> None:
    async def exercise() -> None:
        with pytest.raises(ValueError, match="between 5 and 8"):
            await _DeterministicReranker().rerank(
                "question", [_candidate("passage")], limit=limit
            )

    asyncio.run(exercise())


def test_reranker_measures_every_pair_before_model_truncation() -> None:
    class _Tokenizer:
        calls: list[dict[str, object]]

        def __init__(self) -> None:
            self.calls = []

        def __call__(self, pairs, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("truncation") is False:
                return {"input_ids": [[1] * 100, [1] * 1025]}
            return {
                "input_ids": torch.tensor([[1, 2], [3, 4]]),
                "attention_mask": torch.tensor([[1, 1], [1, 1]]),
            }

    class _Model:
        def __call__(self, **inputs):
            assert inputs["input_ids"].shape == (2, 2)
            return SimpleNamespace(logits=torch.tensor([[0.1], [0.2]]))

    tokenizer = _Tokenizer()
    reranker = BgeReranker("test-model")
    reranker._tokenizer = tokenizer
    reranker._model = _Model()

    scores = reranker._score_sync("query", ["short", "long"])

    assert scores == pytest.approx([0.1, 0.2])
    assert reranker.last_token_diagnostics is not None
    assert reranker.last_token_diagnostics.pair_count == 2
    assert reranker.last_token_diagnostics.truncated_pair_count == 1
    assert reranker.last_token_diagnostics.maximum_pair_tokens == 1025
    assert tokenizer.calls[0]["truncation"] is False
    assert tokenizer.calls[1]["truncation"] is True
    assert tokenizer.calls[1]["max_length"] == 1024
