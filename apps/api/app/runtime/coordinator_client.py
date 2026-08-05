import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager

import httpx

from app.db.repositories import RetrievedChunk
from app.domain import validate_embedding
from app.services.ollama_embeddings import EmbeddingServiceError
from app.services.ollama_generation import (
    GenerationChunk,
    GenerationServiceError,
    GenerationUsage,
    OllamaModelReadiness,
)
from app.services.reranker import RerankedChunk, RerankerError


class CoordinatorClient:
    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout_seconds: float = 300,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if len(service_token) < 32:
            raise ValueError(
                "coordinator service token must contain at least 32 characters"
            )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {service_token}"},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self) -> None:
        response = await self._client.get("/health")
        response.raise_for_status()

    async def post(self, path: str, payload: dict[str, object]) -> httpx.Response:
        return await self._client.post(path, json=payload)

    def stream(
        self, path: str, payload: dict[str, object]
    ) -> AbstractAsyncContextManager[httpx.Response]:
        return self._client.stream("POST", path, json=payload)

    async def cancel(self, request_id: str) -> None:
        response = await self._client.post(f"/cancel/{request_id}", json={})
        response.raise_for_status()


class CoordinatorEmbeddingClient:
    def __init__(
        self,
        coordinator: CoordinatorClient,
        *,
        priority: str = "interactive",
    ) -> None:
        if priority not in {"interactive", "background"}:
            raise ValueError("embedding priority is invalid")
        self._coordinator = coordinator
        self._priority = priority

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        request_id = uuid.uuid4().hex
        try:
            response = await self._coordinator.post(
                "/embed",
                {
                    "request_id": request_id,
                    "priority": self._priority,
                    "texts": list(texts),
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("request_id") != request_id:
                raise ValueError("embedding request identifier mismatch")
            embeddings = payload["embeddings"]
            if not isinstance(embeddings, list) or len(embeddings) != len(texts):
                raise ValueError("unexpected embedding count")
            validated: list[list[float]] = []
            for vector in embeddings:
                if not isinstance(vector, list):
                    raise ValueError("malformed embedding")
                values = [float(value) for value in vector]
                validate_embedding(values)
                validated.append(values)
            return validated
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingServiceError(
                "inference coordinator embedding failed"
            ) from exc

    async def close(self) -> None:
        return None


class CoordinatorReranker:
    model_name = "BAAI/bge-reranker-v2-m3"

    def __init__(self, coordinator: CoordinatorClient) -> None:
        self._coordinator = coordinator

    @property
    def loaded(self) -> bool:
        return False

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
        try:
            response = await self._coordinator.post(
                "/rerank",
                {
                    "request_id": uuid.uuid4().hex,
                    "priority": "interactive",
                    "query": query,
                    "passages": [candidate.chunk.text for candidate in candidates],
                },
            )
            response.raise_for_status()
            scores = response.json()["scores"]
            if not isinstance(scores, list) or len(scores) != len(candidates):
                raise ValueError("unexpected reranker score count")
            ranked = [
                RerankedChunk(candidate, float(score))
                for candidate, score in zip(candidates, scores, strict=True)
            ]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RerankerError("inference coordinator rerank failed") from exc
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:limit]

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        try:
            response = await self._coordinator.post(
                "/rerank",
                {
                    "request_id": uuid.uuid4().hex,
                    "priority": "interactive",
                    "query": query,
                    "passages": list(passages),
                },
            )
            response.raise_for_status()
            scores = response.json()["scores"]
            if not isinstance(scores, list) or len(scores) != len(passages):
                raise ValueError("unexpected reranker score count")
            return [float(score) for score in scores]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RerankerError("inference coordinator rerank failed") from exc


class CoordinatorGenerationClient:
    def __init__(
        self,
        coordinator: CoordinatorClient,
        *,
        generation_model: str,
        embedding_model: str,
    ) -> None:
        self._coordinator = coordinator
        self._models = frozenset({generation_model, embedding_model})

    async def check_available(self) -> None:
        try:
            await self._coordinator.health()
        except httpx.HTTPError as exc:
            raise GenerationServiceError(
                "inference coordinator is unavailable"
            ) from exc

    async def readiness(self, required_models: Sequence[str]) -> OllamaModelReadiness:
        try:
            await self._coordinator.health()
        except httpx.HTTPError:
            return OllamaModelReadiness(
                False, frozenset(), "inference coordinator is unavailable"
            )
        available = self._models
        missing = set(required_models) - available
        return OllamaModelReadiness(
            True,
            available,
            "ready" if not missing else "inference coordinator model mismatch",
        )

    async def stream(
        self, prompt: str, *, think: bool = True
    ) -> AsyncIterator[GenerationChunk]:
        request_id = uuid.uuid4().hex
        terminal = False
        try:
            async with self._coordinator.stream(
                "/generate/stream",
                {
                    "request_id": request_id,
                    "priority": "interactive",
                    "prompt": prompt,
                    "think": think,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    frame = json.loads(line)
                    if not isinstance(frame, dict):
                        raise ValueError("malformed generation frame")
                    if frame.get("request_id") != request_id:
                        raise ValueError("generation request identifier mismatch")
                    if frame.get("done") is True:
                        if set(frame) != {
                            "request_id",
                            "done",
                            "done_reason",
                            "usage",
                        }:
                            raise ValueError("malformed generation terminal frame")
                        done_reason = frame.get("done_reason")
                        if done_reason not in {"stop", "length"}:
                            raise ValueError("malformed generation stop reason")
                        usage_payload = frame.get("usage")
                        usage = (
                            None
                            if usage_payload is None
                            else GenerationUsage(**usage_payload)
                        )
                        yield GenerationChunk(
                            "done",
                            done_reason=done_reason,
                            usage=usage,
                        )
                        terminal = True
                        break
                    if set(frame) != {"request_id", "type", "text"}:
                        raise ValueError("malformed generation chunk")
                    chunk_type = frame.get("type")
                    text = frame.get("text")
                    if chunk_type not in {"thinking", "answer"} or not isinstance(
                        text, str
                    ):
                        raise ValueError("malformed generation chunk")
                    yield GenerationChunk(chunk_type, text)
            if not terminal:
                raise ValueError("generation stream ended before terminal frame")
        except asyncio.CancelledError:
            try:
                await self._coordinator.cancel(request_id)
            finally:
                raise
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise GenerationServiceError(
                "inference coordinator generation failed"
            ) from exc

    async def close(self) -> None:
        return None
