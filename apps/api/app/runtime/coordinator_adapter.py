import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict

from app.runtime.limits import Stage
from app.services.ollama_embeddings import OllamaEmbeddingClient
from app.services.ollama_generation import GenerationChunk, OllamaGenerationClient
from app.services.reranker import BgeReranker


class ModelCoordinatorAdapter:
    """Owns all direct Ollama and CPU reranker access."""

    def __init__(
        self,
        embedder: OllamaEmbeddingClient,
        reranker: BgeReranker,
        generator: OllamaGenerationClient,
    ) -> None:
        self._embedder = embedder
        self._reranker = reranker
        self._generator = generator

    async def execute(
        self,
        stage: Stage,
        inputs: Sequence[str],
        cancellation: asyncio.Event,
    ) -> list[str | float]:
        if cancellation.is_set():
            raise asyncio.CancelledError
        if stage is Stage.EMBEDDING:
            embeddings = await self._embedder.embed(inputs)
            return [
                ",".join(format(value, ".17g") for value in vector)
                for vector in embeddings
            ]
        if stage is Stage.RERANK:
            return await self._reranker.score(inputs[0], inputs[1:])
        if stage is Stage.GENERATION:
            raise ValueError("generation must use the streaming coordinator protocol")
        raise ValueError(f"unsupported coordinator stage: {stage}")

    async def stream(
        self,
        prompt: str,
        cancellation: asyncio.Event,
        *,
        think: bool = True,
    ) -> AsyncIterator[GenerationChunk]:
        async for chunk in self._generator.stream(prompt, think=think):
            if cancellation.is_set():
                raise asyncio.CancelledError
            yield chunk

    async def close(self) -> None:
        await self._embedder.close()
        await self._generator.close()

    def diagnostics(self) -> dict[str, object]:
        reranker = self._reranker.last_token_diagnostics
        return {
            "reranker_tokens": None if reranker is None else asdict(reranker),
        }
