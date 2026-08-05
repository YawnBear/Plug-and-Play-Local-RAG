import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.config import Settings
from app.db.session import DatabaseReadiness
from app.lifespan import build_lifespan


class _Database:
    disposed = False

    async def readiness(self) -> DatabaseReadiness:
        return DatabaseReadiness(True, True, True, "ready")

    async def startup_readiness(self) -> DatabaseReadiness:
        return DatabaseReadiness(True, True, True, "ready")

    async def dispose(self) -> None:
        self.disposed = True


class _Worker:
    started = False
    stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _Embedder:
    closed = False

    async def close(self) -> None:
        self.closed = True


class _Generator:
    closed = False

    async def close(self) -> None:
        self.closed = True


def test_lifespan_closes_worker_embedder_and_database(tmp_path: Path) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()
    settings = Settings(data_root=tmp_path / "data", ocr_python_executable=executable)
    container = SimpleNamespace(
        database=_Database(),
        worker=_Worker(),
        embedder=_Embedder(),
        generator=_Generator(),
    )
    app = FastAPI(lifespan=build_lifespan(settings, container))

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            assert not container.worker.started
        assert not container.worker.stopped
        assert container.embedder.closed
        assert container.generator.closed
        assert container.database.disposed

    asyncio.run(exercise())


def test_lifespan_uses_schema_startup_gate_not_legacy_data_gate(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()
    settings = Settings(data_root=tmp_path / "data", ocr_python_executable=executable)

    class LegacyDatabase(_Database):
        async def readiness(self) -> DatabaseReadiness:
            return DatabaseReadiness(
                True, True, False, "migrate-originals-to-object-store"
            )

    container = SimpleNamespace(
        database=LegacyDatabase(),
        worker=_Worker(),
        embedder=_Embedder(),
        generator=_Generator(),
    )
    app = FastAPI(lifespan=build_lifespan(settings, container))

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            assert not container.worker.started

    asyncio.run(exercise())


def test_lifespan_repairs_interrupted_chat_turns_before_workers_start(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()
    settings = Settings(data_root=tmp_path / "data", ocr_python_executable=executable)
    events: list[str] = []

    class Chats:
        async def repair_interrupted(self) -> int:
            events.append("repair")
            return 1

    class Worker(_Worker):
        async def start(self) -> None:
            events.append("worker")
            await super().start()

    container = SimpleNamespace(
        database=_Database(),
        worker=Worker(),
        chats=Chats(),
        embedder=_Embedder(),
        generator=_Generator(),
    )
    app = FastAPI(lifespan=build_lifespan(settings, container))

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            assert events == []

    asyncio.run(exercise())


def test_lifespan_aborts_and_closes_resources_when_chat_repair_fails(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()
    settings = Settings(data_root=tmp_path / "data", ocr_python_executable=executable)

    class Chats:
        async def repair_interrupted(self) -> int:
            raise RuntimeError("repair failed")

    container = SimpleNamespace(
        database=_Database(),
        worker=_Worker(),
        chats=Chats(),
        embedder=_Embedder(),
        generator=_Generator(),
    )
    app = FastAPI(lifespan=build_lifespan(settings, container))

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            pass
        assert not container.worker.started
        assert container.embedder.closed
        assert container.generator.closed
        assert container.database.disposed

    asyncio.run(exercise())


def test_lifespan_cleans_every_attempted_resource_when_later_start_fails(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()
    settings = Settings(data_root=tmp_path / "data", ocr_python_executable=executable)
    events: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        async def close(self) -> None:
            events.append(f"{self.name}.close")

    class Database(_Database):
        async def dispose(self) -> None:
            events.append("database.dispose")

    class Worker:
        async def start(self) -> None:
            events.append("worker.start")

        async def stop(self) -> None:
            events.append("worker.stop")

    class DeletionWorker:
        async def start(self) -> None:
            events.append("deletion.start")
            raise RuntimeError("deletion start failed")

        async def stop(self) -> None:
            events.append("deletion.stop")

    container = SimpleNamespace(
        database=Database(),
        worker=Worker(),
        deletion_worker=DeletionWorker(),
        embedder=Resource("embedder"),
        generator=Resource("generator"),
    )
    app = FastAPI(lifespan=build_lifespan(settings, container))

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            pass
        assert events == [
            "embedder.close",
            "generator.close",
            "database.dispose",
        ]

    asyncio.run(exercise())


def test_lifespan_continues_shutdown_after_stop_failures(tmp_path: Path) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()
    settings = Settings(data_root=tmp_path / "data", ocr_python_executable=executable)
    events: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        async def close(self) -> None:
            events.append(f"{self.name}.close")

    class Database(_Database):
        async def dispose(self) -> None:
            events.append("database.dispose")

    class Worker:
        async def start(self) -> None:
            events.append("worker.start")

        async def stop(self) -> None:
            events.append("worker.stop")
            raise RuntimeError("worker stop failed")

    class DeletionWorker:
        async def start(self) -> None:
            events.append("deletion.start")

        async def stop(self) -> None:
            events.append("deletion.stop")
            raise RuntimeError("deletion stop failed")

    container = SimpleNamespace(
        database=Database(),
        worker=Worker(),
        deletion_worker=DeletionWorker(),
        embedder=Resource("embedder"),
        generator=Resource("generator"),
    )
    app = FastAPI(lifespan=build_lifespan(settings, container))

    async def exercise() -> None:
        try:
            async with app.router.lifespan_context(app):
                pass
        except RuntimeError:
            raise AssertionError("API lifespan must not touch worker processes")
        assert events == [
            "embedder.close",
            "generator.close",
            "database.dispose",
        ]

    asyncio.run(exercise())


def test_only_personal_profile_starts_the_setup_surface_before_bootstrap(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()

    class BootstrapDatabase(_Database):
        async def startup_readiness(self) -> DatabaseReadiness:
            return DatabaseReadiness(
                True,
                True,
                True,
                "first administrator bootstrap is required",
                bootstrap_required=True,
                catalog_integrity=True,
                identity_valid=True,
            )

    def container():
        return SimpleNamespace(
            database=BootstrapDatabase(),
            embedder=_Embedder(),
            generator=_Generator(),
        )

    personal_settings = Settings(
        product_profile="personal",
        canonical_origin="http://127.0.0.1:3000",
        canonical_host="127.0.0.1",
        cors_origins=[],
        data_root=tmp_path / "personal-data",
        ocr_python_executable=executable,
    )
    personal_container = container()
    personal_app = FastAPI(
        lifespan=build_lifespan(personal_settings, personal_container)
    )

    async def personal_exercise() -> None:
        async with personal_app.router.lifespan_context(personal_app):
            pass

    asyncio.run(personal_exercise())

    team_settings = Settings(
        data_root=tmp_path / "team-data",
        ocr_python_executable=executable,
    )
    team_container = container()
    team_app = FastAPI(lifespan=build_lifespan(team_settings, team_container))

    async def team_exercise() -> None:
        async with team_app.router.lifespan_context(team_app):
            pass

    with pytest.raises(RuntimeError, match="bootstrap is required"):
        asyncio.run(team_exercise())
