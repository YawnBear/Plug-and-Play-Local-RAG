import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.services.ollama_generation import (
    GenerationChunk,
    GenerationServiceError,
    GenerationUsage,
    OllamaGenerationClient,
)


class _FailingResponseStream(httpx.AsyncByteStream):
    def __init__(
        self,
        request: httpx.Request,
        error_type: type[httpx.RequestError],
    ) -> None:
        self._request = request
        self._error_type = error_type

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"response":"partial","done":false}\n'
        raise self._error_type("stream failed", request=self._request)


def test_generation_preflight_and_stream_use_fixed_model_and_context() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model": "qwen3:8b"})
        return httpx.Response(
            200,
            content=(
                b'{"response":"Grounded ","done":false}\n'
                b'{"response":"[S1]","done":true,"done_reason":"stop"}\n'
            ),
        )

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test",
        "qwen3:8b",
        context_size=8192,
        output_tokens=3072,
        client=http_client,
    )

    async def exercise() -> list[GenerationChunk]:
        await client.check_available()
        tokens = [token async for token in client.stream("prompt")]
        await http_client.aclose()
        return tokens

    assert asyncio.run(exercise()) == [
        GenerationChunk("answer", "Grounded "),
        GenerationChunk("answer", "[S1]"),
        GenerationChunk("done", done_reason="stop"),
    ]
    assert json.loads(requests[0].content) == {"model": "qwen3:8b"}
    assert json.loads(requests[1].content) == {
        "model": "qwen3:8b",
        "prompt": "prompt",
        "stream": True,
        "think": True,
        "options": {"num_ctx": 8192, "num_predict": 3072, "temperature": 0.2},
    }


def test_generation_preflight_cache_is_bounded_and_deduplicates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"model": "qwen3:8b"}, request=request)

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test",
        "qwen3:8b",
        availability_cache_seconds=5.0,
        client=http_client,
    )

    async def exercise() -> None:
        await asyncio.gather(*(client.check_available() for _ in range(4)))
        await client.check_available()
        await http_client.aclose()

    asyncio.run(exercise())
    assert len(requests) == 1


def test_generation_keeps_ollama_thinking_separate_from_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                b'{"thinking":"unverified","response":"","done":false}\n'
                b'{"thinking":"","response":"answer [S1]","done":true,'
                b'"done_reason":"stop"}\n'
            ),
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test", "qwen3:8b", client=http_client
    )

    async def exercise() -> list[GenerationChunk]:
        chunks = [chunk async for chunk in client.stream("prompt")]
        await http_client.aclose()
        return chunks

    assert asyncio.run(exercise()) == [
        GenerationChunk("thinking", "unverified"),
        GenerationChunk("answer", "answer [S1]"),
        GenerationChunk("done", done_reason="stop"),
    ]


def test_generation_preserves_actual_ollama_token_and_timing_counters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                b'{"response":"answer","done":true,"done_reason":"stop",'
                b'"prompt_eval_count":7,"eval_count":2,"total_duration":100,'
                b'"load_duration":10,"prompt_eval_duration":20,'
                b'"eval_duration":30}\n'
            ),
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test", "qwen3:8b", client=http_client
    )

    async def exercise() -> list[GenerationChunk]:
        chunks = [chunk async for chunk in client.stream("two tokens")]
        await http_client.aclose()
        return chunks

    assert asyncio.run(exercise())[-1] == GenerationChunk(
        "done",
        done_reason="stop",
        usage=GenerationUsage(7, 2, 100, 10, 20, 30, 2),
    )


def test_generation_rejects_partial_or_invalid_usage_counters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                b'{"response":"answer","done":true,"done_reason":"stop",'
                b'"prompt_eval_count":7}\n'
            ),
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test", "qwen3:8b", client=http_client
    )

    async def exercise() -> None:
        with pytest.raises(GenerationServiceError, match="usage counters"):
            async for _chunk in client.stream("prompt"):
                pass
        await http_client.aclose()

    asyncio.run(exercise())


def test_generation_surfaces_length_stop_and_disables_thinking_on_continuation(
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=b'{"response":"partial","done":true,"done_reason":"length"}\n',
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test", "qwen3:8b", client=http_client
    )

    async def exercise() -> list[GenerationChunk]:
        chunks = [chunk async for chunk in client.stream("continue", think=False)]
        await http_client.aclose()
        return chunks

    assert asyncio.run(exercise()) == [
        GenerationChunk("answer", "partial"),
        GenerationChunk("done", done_reason="length"),
    ]
    assert json.loads(requests[0].content)["think"] is False


def test_generation_preflight_reports_missing_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found", request=request)

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test", "qwen3:8b", client=http_client
    )

    async def exercise() -> None:
        with pytest.raises(GenerationServiceError, match="qwen3:8b"):
            await client.check_available()
        await http_client.aclose()

    asyncio.run(exercise())


def test_generation_rejects_malformed_stream_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json\n", request=request)

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test", "qwen3:8b", client=http_client
    )

    async def exercise() -> None:
        with pytest.raises(GenerationServiceError, match="malformed"):
            async for _token in client.stream("prompt"):
                pass
        await http_client.aclose()

    asyncio.run(exercise())


def test_generation_requires_terminal_done_frame_after_partial_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"response":"partial","done":false}\n',
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test", "qwen3:8b", client=http_client
    )

    async def exercise() -> None:
        tokens: list[GenerationChunk] = []
        with pytest.raises(GenerationServiceError, match="terminal done"):
            async for token in client.stream("prompt"):
                tokens.append(token)
        assert tokens == [GenerationChunk("answer", "partial")]
        await http_client.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.RemoteProtocolError])
def test_generation_maps_midstream_request_errors(
    error_type: type[httpx.RequestError],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_FailingResponseStream(request, error_type),
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test", "qwen3:8b", client=http_client
    )

    async def exercise() -> None:
        tokens: list[GenerationChunk] = []
        with pytest.raises(GenerationServiceError, match="transport failed") as caught:
            async for token in client.stream("prompt"):
                tokens.append(token)
        assert tokens == [GenerationChunk("answer", "partial")]
        assert isinstance(caught.value.__cause__, error_type)
        await http_client.aclose()

    asyncio.run(exercise())


def test_generation_does_not_wrap_explicit_service_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"error":"model runner stopped","done":true}\n',
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test", "qwen3:8b", client=http_client
    )

    async def exercise() -> None:
        with pytest.raises(GenerationServiceError) as caught:
            async for _token in client.stream("prompt"):
                pass
        assert str(caught.value) == "model runner stopped"
        assert caught.value.__cause__ is None
        await http_client.aclose()

    asyncio.run(exercise())


def test_ollama_readiness_handles_broad_request_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset", request=request)

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    client = OllamaGenerationClient(
        "http://ollama.test", "qwen3:8b", client=http_client
    )

    async def exercise() -> None:
        state = await client.readiness(["qwen3:8b"])
        assert state.reachable is False
        assert state.available_models == frozenset()
        assert "OLLAMA_BASE_URL" in state.detail
        await http_client.aclose()

    asyncio.run(exercise())
