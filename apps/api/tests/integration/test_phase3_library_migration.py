import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.config import get_settings

pytestmark = pytest.mark.integration


def _run(coroutine: object) -> object:
    return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)


def test_isolated_populated_0002_upgrade_trigger_and_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("RUN_PHASE3_MIGRATION_E2E") != "1":
        pytest.skip("RUN_PHASE3_MIGRATION_E2E is not enabled")
    admin_url = os.environ.get("TEST_DATABASE_ADMIN_URL")
    if not admin_url:
        pytest.skip("TEST_DATABASE_ADMIN_URL is not configured")

    database_name = f"rag_phase3_{uuid.uuid4().hex}"
    target_url = make_url(admin_url).set(database=database_name)
    rendered_target_url = target_url.render_as_string(hide_password=False)

    async def admin(statement: str) -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    _run(admin(f'CREATE DATABASE "{database_name}"'))
    target_engine = None
    try:
        monkeypatch.setenv("DATABASE_URL", rendered_target_url)
        get_settings.cache_clear()
        resolved_url = get_settings().database_url
        assert make_url(resolved_url).database == database_name
        api_root = Path(__file__).parents[2]
        alembic = Config(str(api_root / "alembic.ini"))
        alembic.set_main_option("sqlalchemy.url", resolved_url)
        assert make_url(alembic.get_main_option("sqlalchemy.url")).database == (
            database_name
        )

        async def assert_isolated_target() -> None:
            engine = create_async_engine(resolved_url)
            try:
                async with engine.connect() as connection:
                    assert await connection.scalar(
                        text("SELECT current_database()")
                    ) == (database_name)
            finally:
                await engine.dispose()

        _run(assert_isolated_target())
        command.upgrade(alembic, "0002_object_storage_foundation")

        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        created = datetime(2026, 1, 1, tzinfo=UTC)

        async def seed() -> None:
            nonlocal target_engine
            target_engine = create_async_engine(rendered_target_url)
            async with target_engine.begin() as connection:
                for document_id, checksum, filename, timestamp in (
                    (first_id, "a" * 64, "Report.pdf", created),
                    (
                        second_id,
                        "b" * 64,
                        "REPORT.PDF",
                        created + timedelta(seconds=1),
                    ),
                ):
                    await connection.execute(
                        text(
                            "INSERT INTO documents ("
                            "id, sha256, original_filename, mime_type, byte_size, "
                            "content_path, object_key, state, stage, parser_version, "
                            "chunking_version, embedding_version, page_count, "
                            "chunk_count, created_at, updated_at"
                            ") VALUES ("
                            ":id, :sha256, :filename, 'application/pdf', 10, NULL, "
                            ":object_key, 'uploaded', 'uploaded', 'v1', 'v1', 'v1', "
                            "NULL, 0, :created_at, :created_at)"
                        ),
                        {
                            "id": document_id,
                            "sha256": checksum,
                            "filename": filename,
                            "object_key": f"originals/{checksum[:2]}/{checksum}.pdf",
                            "created_at": timestamp,
                        },
                    )

        _run(seed())
        assert target_engine is not None
        _run(target_engine.dispose())
        target_engine = None

        _run(assert_isolated_target())
        command.upgrade(alembic, "0003_library_foundation")

        async def verify_upgrade() -> None:
            engine = create_async_engine(rendered_target_url)
            try:
                async with engine.connect() as connection:
                    rows = (
                        await connection.execute(
                            text(
                                "SELECT id, document_id, name FROM library_nodes "
                                "ORDER BY created_at, document_id"
                            )
                        )
                    ).all()
                assert [row.name for row in rows] == [
                    "Report.pdf",
                    "REPORT (2).PDF",
                ]
                assert rows[0].id == uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"urn:local-rag:library-node:v1:{first_id}",
                )

                with pytest.raises(DBAPIError):
                    async with engine.begin() as connection:
                        await connection.execute(
                            text(
                                "INSERT INTO library_nodes "
                                "(id, parent_id, kind, name, name_key, document_id) "
                                "VALUES (:id, :parent, 'folder', 'invalid', "
                                "'invalid', NULL)"
                            ),
                            {"id": uuid.uuid4(), "parent": rows[0].id},
                        )
            finally:
                await engine.dispose()

        _run(verify_upgrade())
        _run(assert_isolated_target())
        command.downgrade(alembic, "0002_object_storage_foundation")

        async def verify_downgrade() -> None:
            engine = create_async_engine(rendered_target_url)
            try:
                async with engine.connect() as connection:
                    assert (
                        await connection.scalar(
                            text("SELECT to_regclass('public.library_nodes')")
                        )
                        is None
                    )
                    assert (
                        await connection.scalar(text("SELECT count(*) FROM documents"))
                        == 2
                    )
            finally:
                await engine.dispose()

        _run(verify_downgrade())
    finally:
        if target_engine is not None:
            _run(target_engine.dispose())
        get_settings.cache_clear()
        _run(
            admin(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{database_name}' AND pid <> pg_backend_pid()"
            )
        )
        _run(admin(f'DROP DATABASE IF EXISTS "{database_name}"'))
        get_settings.cache_clear()
