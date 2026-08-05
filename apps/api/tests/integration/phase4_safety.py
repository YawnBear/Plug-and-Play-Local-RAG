import asyncio
import os
from collections.abc import Coroutine
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings

DEDICATED_CONFIRMATION = "PHASE4-DEDICATED-ONLY"
EXPECTED_REVISION = "0004_chat_foundation"


def run_selector(coroutine: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)


def _database_identity(database_url: str) -> tuple[str, int, str]:
    parsed = make_url(database_url)
    host = (parsed.host or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return host, parsed.port or 5432, parsed.database or ""


def dedicated_phase4_environment(*extra_names: str) -> dict[str, str]:
    names = (
        "TEST_DATABASE_URL",
        "PHASE4_TEST_DATABASE_NAME",
        "PHASE4_DEDICATED_DATABASE_CONFIRM",
        *extra_names,
    )
    environment = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in environment.items() if not value]
    if missing:
        pytest.skip(f"Phase 4 environment is missing: {', '.join(missing)}")
    if environment["PHASE4_DEDICATED_DATABASE_CONFIRM"] != DEDICATED_CONFIRMATION:
        pytest.fail(
            "refusing Phase 4 integration without explicit dedicated-database "
            f"confirmation {DEDICATED_CONFIRMATION!r}"
        )
    test_identity = _database_identity(environment["TEST_DATABASE_URL"])
    primary_identity = _database_identity(Settings().database_url)
    if test_identity == primary_identity or test_identity[2] == primary_identity[2]:
        pytest.fail("refusing to run Phase 4 integration against DATABASE_URL")
    if test_identity[2] != environment["PHASE4_TEST_DATABASE_NAME"]:
        pytest.fail(
            "TEST_DATABASE_URL database does not match PHASE4_TEST_DATABASE_NAME"
        )
    return environment


async def assert_phase4_database(
    connection: AsyncConnection, expected_database_name: str
) -> None:
    assert await connection.scalar(text("SELECT current_database()")) == (
        expected_database_name
    )
    assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == (
        EXPECTED_REVISION
    )
    counts = (
        await connection.execute(
            text(
                "SELECT 'chats', count(*) FROM chats "
                "UNION ALL SELECT 'chat_scopes', count(*) FROM chat_scopes "
                "UNION ALL SELECT 'chat_turns', count(*) FROM chat_turns "
                "UNION ALL SELECT 'turn_sources', count(*) FROM turn_sources "
                "UNION ALL SELECT 'turn_citations', count(*) FROM turn_citations "
                "UNION ALL SELECT 'documents', count(*) FROM documents "
                "UNION ALL SELECT 'library_nodes', count(*) FROM library_nodes"
            )
        )
    ).all()
    populated = {name: count for name, count in counts if count}
    if populated:
        pytest.fail(
            "refusing Phase 4 integration against a reused database; "
            f"expected an empty baseline, found {populated}"
        )
