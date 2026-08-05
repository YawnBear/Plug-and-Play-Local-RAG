import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.session import DatabaseManager, DatabaseReadiness
from app.main import create_app
from app.services.object_storage import BucketReadiness
from app.services.ollama_generation import OllamaModelReadiness
from app.services.readiness import ApplicationReadiness, ReadinessService


class _Database:
    def __init__(self, state: DatabaseReadiness | None = None) -> None:
        self.state = state or DatabaseReadiness(True, True, True, "ready")

    async def readiness(self) -> DatabaseReadiness:
        return self.state


class _Generator:
    def __init__(self, state: OllamaModelReadiness) -> None:
        self.state = state
        self.required_models: list[str] = []

    async def readiness(self, required_models: list[str]) -> OllamaModelReadiness:
        self.required_models = required_models
        return self.state


class _ObjectStore:
    def __init__(self, state: BucketReadiness | None = None) -> None:
        self.state = state or BucketReadiness(True, True, "ready")

    async def bucket_readiness(self) -> BucketReadiness:
        return self.state


def _settings(tmp_path: Path) -> Settings:
    executable = tmp_path / "ocr" / "python.exe"
    executable.parent.mkdir()
    executable.touch()
    return Settings(data_root=tmp_path / "data", ocr_python_executable=executable)


def test_readiness_healthy_with_lazy_reranker_is_ready(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    generator = _Generator(
        OllamaModelReadiness(
            True,
            frozenset({settings.generation_model, settings.embedding_model}),
            "ready",
        )
    )
    service = ReadinessService(
        _Database(), generator, SimpleNamespace(loaded=False), _ObjectStore(), settings
    )

    state = asyncio.run(service.check())

    assert state.ready is True
    assert state.reranker_loaded is False
    assert "informational" in state.detail
    assert generator.required_models == [
        settings.generation_model,
        settings.embedding_model,
    ]


def test_readiness_missing_ollama_is_actionable_and_not_ready(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    generator = _Generator(
        OllamaModelReadiness(
            False,
            frozenset(),
            "Ollama is unreachable; start Ollama and verify OLLAMA_BASE_URL",
        )
    )
    service = ReadinessService(
        _Database(), generator, SimpleNamespace(loaded=False), _ObjectStore(), settings
    )

    state = asyncio.run(service.check())

    assert state.ready is False
    assert state.ollama is False
    assert state.generation_model is False
    assert state.embedding_model is False
    assert "start Ollama" in state.detail


def test_readiness_missing_object_bucket_is_actionable_and_not_ready(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    generator = _Generator(
        OllamaModelReadiness(
            True,
            frozenset({settings.generation_model, settings.embedding_model}),
            "ready",
        )
    )
    store = _ObjectStore(
        BucketReadiness(
            endpoint=True,
            bucket=False,
            detail="object storage head bucket failed (NoSuchBucket)",
        )
    )

    state = asyncio.run(
        ReadinessService(
            _Database(), generator, SimpleNamespace(loaded=False), store, settings
        ).check()
    )

    assert state.ready is False
    assert state.object_storage_endpoint is True
    assert state.object_storage_bucket is False
    assert "NoSuchBucket" in state.detail


def test_readiness_reports_each_missing_model_without_loading_reranker(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    generator = _Generator(
        OllamaModelReadiness(
            True,
            frozenset({settings.generation_model}),
            f"required Ollama model(s) missing; run: "
            f"ollama pull {settings.embedding_model}",
        )
    )
    reranker = SimpleNamespace(loaded=False)
    service = ReadinessService(
        _Database(), generator, reranker, _ObjectStore(), settings
    )

    state = asyncio.run(service.check())

    assert state.ready is False
    assert state.ollama is True
    assert state.generation_model is True
    assert state.embedding_model is False
    assert reranker.loaded is False
    assert settings.embedding_model in state.detail


def test_readiness_reports_loaded_reranker_without_changing_aggregate(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    generator = _Generator(
        OllamaModelReadiness(
            True,
            frozenset({settings.generation_model, settings.embedding_model}),
            "ready",
        )
    )
    state = asyncio.run(
        ReadinessService(
            _Database(),
            generator,
            SimpleNamespace(loaded=True),
            _ObjectStore(),
            settings,
        ).check()
    )
    assert state.ready is True
    assert state.reranker_loaded is True
    assert "reranker loaded" in state.detail


class _Readiness:
    def __init__(self, state: ApplicationReadiness) -> None:
        self.state = state

    async def check(self) -> ApplicationReadiness:
        return self.state


def test_ready_route_uses_503_only_for_failed_aggregate() -> None:
    state = ApplicationReadiness(
        database=True,
        vector_extension=True,
        migration_current=True,
        object_storage_endpoint=True,
        object_storage_bucket=True,
        ollama=False,
        generation_model=False,
        embedding_model=False,
        ocr_configured=True,
        reranker_loaded=False,
        detail="Ollama unavailable",
    )
    app = create_app(Settings(), SimpleNamespace(readiness=_Readiness(state)))

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["ollama"] is False
    assert response.json()["reranker_loaded"] is False


def test_database_readiness_timeout_is_short_and_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = object.__new__(DatabaseManager)
    manager.readiness_timeout_seconds = 0.01

    async def never_finishes() -> tuple[object, object]:
        await asyncio.Event().wait()
        return True, "unreachable"

    monkeypatch.setattr(manager, "_readiness_query", never_finishes)

    state = asyncio.run(manager.readiness())

    assert state.ready is False
    assert "timed out" in state.message
    assert "DATABASE_URL" in state.message


def test_database_readiness_fails_closed_on_wrong_runtime_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = object.__new__(DatabaseManager)
    manager.readiness_timeout_seconds = 1
    manager.expected_role = "rag_api"

    async def wrong_identity() -> tuple[object, object, object, object, object]:
        return "0003_v6_ingestion_version_guard", True, False, True, False

    monkeypatch.setattr(manager, "_readiness_query", wrong_identity)

    state = asyncio.run(manager.readiness())

    assert state.ready is False
    assert state.identity_valid is False
    assert "dedicated rag_api database credential" in state.message
