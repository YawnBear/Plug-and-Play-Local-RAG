import asyncio
import os

import pytest

from app.services.ollama_embeddings import OllamaEmbeddingClient

pytestmark = pytest.mark.integration


def test_real_qwen_embedding_adapter() -> None:
    if os.environ.get("RUN_OLLAMA_INTEGRATION") != "1":
        pytest.skip("RUN_OLLAMA_INTEGRATION is not enabled")
    embedder = OllamaEmbeddingClient(
        os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        "qwen3-embedding:0.6b",
    )

    async def exercise() -> None:
        vectors = await embedder.embed(["controlled Phase 3 embedding check"])
        assert len(vectors) == 1
        assert len(vectors[0]) == 1024
        await embedder.close()

    asyncio.run(exercise(), loop_factory=asyncio.SelectorEventLoop)
