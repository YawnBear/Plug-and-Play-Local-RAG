import asyncio
import json
import math

import httpx
import pytest

from app.services.ollama_embeddings import (
    EmbeddingServiceError,
    OllamaEmbeddingClient,
)


def _client(handler: object) -> tuple[OllamaEmbeddingClient, httpx.AsyncClient]:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:11434"
    )
    return OllamaEmbeddingClient(
        "http://127.0.0.1:11434", "qwen3-embedding:0.6b", client=http_client
    ), http_client


def test_embed_uses_ollama_contract_and_validates_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        assert json.loads(request.read()) == {
            "model": "qwen3-embedding:0.6b",
            "input": ["grounded text"],
            "truncate": False,
        }
        return httpx.Response(200, json={"embeddings": [[1.0, *([0.0] * 1023)]]})

    embedder, http_client = _client(handler)
    vectors = asyncio.run(embedder.embed(["grounded text"]))
    asyncio.run(http_client.aclose())

    assert len(vectors) == 1
    assert len(vectors[0]) == 1024


@pytest.mark.parametrize(
    "embedding",
    [[0.0] * 1024, [1.0], [math.inf, *([0.0] * 1023)]],
)
def test_embed_rejects_invalid_vectors(embedding: list[float]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps({"embeddings": [embedding]}).encode(),
            headers={"content-type": "application/json"},
        )

    embedder, http_client = _client(handler)
    with pytest.raises(EmbeddingServiceError):
        asyncio.run(embedder.embed(["text"]))
    asyncio.run(http_client.aclose())


def test_embed_reports_unavailable_ollama() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    embedder, http_client = _client(handler)
    with pytest.raises(EmbeddingServiceError, match="start Ollama"):
        asyncio.run(embedder.embed(["text"]))
    asyncio.run(http_client.aclose())


def test_embed_reports_missing_model_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found", request=request)

    embedder, http_client = _client(handler)
    with pytest.raises(EmbeddingServiceError, match="model not found"):
        asyncio.run(embedder.embed(["text"]))
    asyncio.run(http_client.aclose())


def test_embed_rejects_missing_result_identity_by_position() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": []}, request=request)

    embedder, http_client = _client(handler)
    with pytest.raises(EmbeddingServiceError, match="unexpected number"):
        asyncio.run(embedder.embed(["text"]))
    asyncio.run(http_client.aclose())
