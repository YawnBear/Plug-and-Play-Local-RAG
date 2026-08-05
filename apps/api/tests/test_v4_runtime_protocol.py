import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import coordinator_server, ocr_service_server, production_server
from app.runtime.coordinator import create_coordinator_app
from app.runtime.coordinator_client import (
    CoordinatorClient,
    CoordinatorEmbeddingClient,
    CoordinatorGenerationClient,
)
from app.runtime.limits import Stage
from app.runtime.network import require_loopback_host
from app.runtime.ocr_service import create_ocr_service_app
from app.runtime.ocr_workspace import OcrWorkspaceManager
from app.runtime.protocol import MAXIMUM_PROTOCOL_BODY_BYTES
from app.services.ollama_embeddings import EmbeddingServiceError
from app.services.ollama_generation import GenerationChunk
from app.services.parsing.types import OcrMode

TOKEN = "a" * 32
AUTHORIZATION = {"Authorization": f"Bearer {TOKEN}"}


class CoordinatorAdapter:
    calls: list[tuple[Stage, list[str]]]

    def __init__(self) -> None:
        self.calls = []

    async def execute(
        self,
        stage: Stage,
        inputs: Sequence[str],
        cancellation: asyncio.Event,
    ) -> list[str | float]:
        self.calls.append((stage, list(inputs)))
        return [",".join(["1"] * 1024)]

    async def stream(
        self,
        prompt: str,
        cancellation: asyncio.Event,
        *,
        think: bool = True,
    ) -> AsyncIterator[GenerationChunk]:
        yield GenerationChunk("answer", "ok")
        yield GenerationChunk("done", done_reason="stop")


class OcrAdapter:
    workspace: Path | None = None

    async def process(
        self,
        *,
        job_id: str,
        workspace: str,
        pages: Sequence[int],
        mode: OcrMode,
        cancellation: asyncio.Event,
    ) -> Sequence[int]:
        assert mode is OcrMode.FULL_PAGE
        self.workspace = Path(workspace)
        assert self.workspace.is_dir()
        return pages


def test_coordinator_protocol_is_authenticated_strict_and_bounded() -> None:
    adapter = CoordinatorAdapter()
    app = create_coordinator_app(service_token=TOKEN, adapter=adapter)
    payload = {
        "request_id": "request-1",
        "priority": "interactive",
        "texts": ["hello"],
    }

    with TestClient(app) as client:
        assert client.get("/health").status_code == 401
        assert client.post("/embed", json=payload).status_code == 401

        response = client.post("/embed", json=payload, headers=AUTHORIZATION)
        assert response.status_code == 200
        assert response.json() == {
            "request_id": "request-1",
            "embeddings": [[1.0] * 1024],
        }

        extra = dict(payload, request_id="request-2", unexpected=True)
        assert (
            client.post("/embed", json=extra, headers=AUTHORIZATION).status_code == 422
        )

        oversized_headers = {
            **AUTHORIZATION,
            "Content-Type": "application/json",
            "Content-Length": str(MAXIMUM_PROTOCOL_BODY_BYTES + 1),
        }
        assert (
            client.post("/embed", content=b"{}", headers=oversized_headers).status_code
            == 413
        )

    assert adapter.calls == [(Stage.EMBEDDING, ["hello"])]


def test_coordinator_exposes_typed_stage_protocols() -> None:
    class TypedAdapter:
        async def execute(
            self,
            stage: Stage,
            inputs: Sequence[str],
            cancellation: asyncio.Event,
        ) -> list[str | float]:
            if stage is Stage.EMBEDDING:
                return [",".join(["1"] * 1024)]
            if stage is Stage.RERANK:
                return [0.25]
            raise AssertionError("generation must use stream")

        async def stream(
            self,
            prompt: str,
            cancellation: asyncio.Event,
            *,
            think: bool = True,
        ) -> AsyncIterator[GenerationChunk]:
            yield GenerationChunk("thinking", "reason")
            yield GenerationChunk("answer", "answer")
            yield GenerationChunk("done", done_reason="stop")

    app = create_coordinator_app(service_token=TOKEN, adapter=TypedAdapter())
    with TestClient(app) as client:
        embedded = client.post(
            "/embed",
            json={
                "request_id": "embed-1",
                "priority": "interactive",
                "texts": ["hello"],
            },
            headers=AUTHORIZATION,
        )
        reranked = client.post(
            "/rerank",
            json={
                "request_id": "rerank-1",
                "priority": "interactive",
                "query": "query",
                "passages": ["passage"],
            },
            headers=AUTHORIZATION,
        )
        generated = client.post(
            "/generate/stream",
            json={
                "request_id": "generate-1",
                "priority": "interactive",
                "prompt": "prompt",
            },
            headers=AUTHORIZATION,
        )

    assert embedded.json() == {
        "request_id": "embed-1",
        "embeddings": [[1.0] * 1024],
    }
    assert reranked.json() == {"request_id": "rerank-1", "scores": [0.25]}
    assert generated.headers["content-type"].startswith("application/x-ndjson")
    assert (
        '{"request_id": "generate-1", "type": "thinking", "text": "reason"}'
        in generated.text
    )
    assert (
        '{"request_id": "generate-1", "type": "answer", "text": "answer"}'
        in generated.text
    )
    assert generated.text.endswith(
        '{"request_id": "generate-1", "done": true, "done_reason": "stop", '
        '"usage": null}\n'
    )


def test_coordinator_generation_client_validates_and_preserves_chunk_types() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        request_id = json.loads(request.content)["request_id"]
        return httpx.Response(
            200,
            content=(
                json.dumps(
                    {
                        "request_id": request_id,
                        "type": "thinking",
                        "text": "reason",
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "request_id": request_id,
                        "type": "answer",
                        "text": "answer",
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "request_id": request_id,
                        "done": True,
                        "done_reason": "stop",
                        "usage": None,
                    }
                )
                + "\n"
            ).encode(),
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://coordinator.test",
        transport=httpx.MockTransport(handler),
    )
    coordinator = CoordinatorClient(
        "http://coordinator.test",
        TOKEN,
        client=http_client,
    )
    generation = CoordinatorGenerationClient(
        coordinator,
        generation_model="qwen3:8b",
        embedding_model="qwen3-embedding:0.6b",
    )

    async def exercise() -> list[GenerationChunk]:
        chunks = [chunk async for chunk in generation.stream("prompt")]
        await http_client.aclose()
        return chunks

    assert asyncio.run(exercise()) == [
        GenerationChunk("thinking", "reason"),
        GenerationChunk("answer", "answer"),
        GenerationChunk("done", done_reason="stop"),
    ]


def test_coordinator_embedding_client_rejects_response_identity_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "request_id": "wrong-request",
                "embeddings": [[1.0] * 1024],
            },
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://coordinator.test",
        transport=httpx.MockTransport(handler),
    )
    coordinator = CoordinatorClient(
        "http://coordinator.test",
        TOKEN,
        client=http_client,
    )
    embedding = CoordinatorEmbeddingClient(coordinator)

    async def exercise() -> None:
        with pytest.raises(EmbeddingServiceError):
            await embedding.embed(["text"])
        await http_client.aclose()

    asyncio.run(exercise())


def test_ocr_protocol_uses_and_cleans_restricted_job_workspace(
    tmp_path: Path,
) -> None:
    adapter = OcrAdapter()
    root = (tmp_path / "ocr-root").resolve()
    app = create_ocr_service_app(
        service_token=TOKEN,
        adapter=adapter,
        workspaces=OcrWorkspaceManager(root),
    )

    with TestClient(app) as client:
        content = b"%PDF-remote-ocr"
        uploaded = client.put(
            "/jobs/job-1/input",
            content=content,
            headers={
                **AUTHORIZATION,
                "Content-Type": "application/pdf",
                "Content-Length": str(len(content)),
                "X-Content-SHA256": hashlib.sha256(content).hexdigest(),
            },
        )
        assert uploaded.status_code == 201
        response = client.post(
            "/ocr",
            json={
                "request_id": "request-1",
                "job_id": "job-1",
                "pages": [1, 2],
                "mode": "full_page",
            },
            headers=AUTHORIZATION,
        )
        deleted = client.delete("/jobs/job-1", headers=AUTHORIZATION)

    assert response.status_code == 200
    assert response.json()["completed_pages"] == [1, 2]
    assert deleted.status_code == 204
    assert adapter.workspace is not None
    assert not adapter.workspace.exists()
    assert adapter.workspace.parent == root


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.2", "localhost"])
def test_runtime_bind_rejects_nonliteral_or_nonloopback_hosts(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        require_loopback_host(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_runtime_bind_accepts_literal_loopback_hosts(host: str) -> None:
    assert require_loopback_host(host) == host


def test_server_entrypoints_pass_only_validated_loopback_apps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def managed_run(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(coordinator_server, "run_managed_uvicorn", managed_run)
    monkeypatch.setattr(ocr_service_server, "run_managed_uvicorn", managed_run)

    coordinator_server.run(
        host="127.0.0.1",
        port=8765,
        service_token=TOKEN,
        adapter=CoordinatorAdapter(),
        ownership_path=(tmp_path / "coordinator.lock").resolve(),
    )
    ocr_service_server.run(
        host="::1",
        port=8766,
        service_token=TOKEN,
        workspace_root=(tmp_path / "ocr").resolve(),
        adapter=OcrAdapter(),
        ownership_path=(tmp_path / "ocr.lock").resolve(),
    )

    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8765
    assert calls[0]["service"] == "inference"
    assert calls[1]["host"] == "::1"
    assert calls[1]["port"] == 8766
    assert calls[1]["service"] == "ocr"


def test_production_api_requires_loopback_mutual_tls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = []
    for name in ("server.crt", "server.key", "client-ca.crt"):
        path = (tmp_path / name).resolve()
        path.write_text("fixture", encoding="utf-8")
        paths.append(path)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(production_server, "create_app", lambda: object())
    monkeypatch.setattr(
        production_server,
        "run_managed_uvicorn",
        lambda **kwargs: calls.append(kwargs),
    )

    production_server.run(
        host="127.0.0.1",
        port=8443,
        certificate=paths[0],
        private_key=paths[1],
        client_ca=paths[2],
    )

    assert calls[0]["certificate"] == paths[0]
    assert calls[0]["private_key"] == paths[1]
    assert calls[0]["client_ca"] == paths[2]
    assert calls[0]["forwarded_allow_ips"] == "127.0.0.1"
    assert calls[0]["proxy_headers"] is True
