import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.db.repositories import RetrievedChunk


class RerankerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RerankedChunk:
    candidate: RetrievedChunk
    score: float


@dataclass(frozen=True, slots=True)
class RerankerTokenDiagnostics:
    pair_count: int
    truncated_pair_count: int
    maximum_pair_tokens: int
    model_limit: int = 1024


class BgeReranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3") -> None:
        self.model_name = model_name
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()
        self._last_token_diagnostics: RerankerTokenDiagnostics | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def last_token_diagnostics(self) -> RerankerTokenDiagnostics | None:
        return self._last_token_diagnostics

    async def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        limit: int,
    ) -> list[RerankedChunk]:
        if not 5 <= limit <= 8:
            raise ValueError("reranker limit must be between 5 and 8")
        if not candidates:
            return []
        await self._ensure_loaded()
        try:
            scores = await asyncio.to_thread(
                self._score_sync,
                query,
                [candidate.chunk.text for candidate in candidates],
            )
        except Exception as exc:
            raise RerankerError(f"reranker inference failed: {exc}") from exc
        if len(scores) != len(candidates):
            raise RerankerError("reranker returned an unexpected number of scores")
        ranked = [
            RerankedChunk(candidate, float(score))
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:limit]

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        await self._ensure_loaded()
        try:
            return await asyncio.to_thread(self._score_sync, query, list(passages))
        except Exception as exc:
            raise RerankerError(f"reranker inference failed: {exc}") from exc

    async def _ensure_loaded(self) -> None:
        if self.loaded:
            return
        async with self._load_lock:
            if self.loaded:
                return
            try:
                tokenizer, model = await asyncio.to_thread(self._load_sync)
            except Exception as exc:
                raise RerankerError(
                    f"unable to load CPU reranker {self.model_name!r}: {exc}"
                ) from exc
            self._tokenizer = tokenizer
            self._model = model

    def _load_sync(self) -> tuple[Any, Any]:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        model = AutoModelForSequenceClassification.from_pretrained(
            self.model_name, dtype=torch.float32
        )
        model.to("cpu")
        model.float()
        model.eval()
        return tokenizer, model

    def _score_sync(self, query: str, passages: list[str]) -> list[float]:
        import torch

        if self._tokenizer is None or self._model is None:
            raise RuntimeError("reranker is not loaded")
        untruncated = self._tokenizer(
            [[query, passage] for passage in passages],
            padding=False,
            truncation=False,
            add_special_tokens=True,
        )
        input_ids = untruncated.get("input_ids")
        if not isinstance(input_ids, list) or any(
            not isinstance(value, list) for value in input_ids
        ):
            raise RuntimeError("reranker tokenizer returned invalid token lengths")
        lengths = [len(value) for value in input_ids]
        self._last_token_diagnostics = RerankerTokenDiagnostics(
            pair_count=len(lengths),
            truncated_pair_count=sum(length > 1024 for length in lengths),
            maximum_pair_tokens=max(lengths, default=0),
        )
        inputs = self._tokenizer(
            [[query, passage] for passage in passages],
            padding=True,
            truncation=True,
            max_length=1024,
            return_tensors="pt",
        )
        inputs = {name: tensor.to("cpu") for name, tensor in inputs.items()}
        with torch.no_grad():
            logits = self._model(**inputs).logits.view(-1).float().cpu()
        return [float(score) for score in logits.tolist()]
