import asyncio

import pytest

from tests.integration.phase4_safety import (
    EXPECTED_REVISION,
    assert_phase4_database,
)


class _Result:
    def __init__(self, counts: list[tuple[str, int]]) -> None:
        self._counts = counts

    def all(self) -> list[tuple[str, int]]:
        return self._counts


class _Connection:
    def __init__(self, counts: list[tuple[str, int]]) -> None:
        self._scalars = iter(["phase4_test", EXPECTED_REVISION])
        self._counts = counts

    async def scalar(self, statement: object) -> str:
        return next(self._scalars)

    async def execute(self, statement: object) -> _Result:
        return _Result(self._counts)


def test_phase4_guard_accepts_only_an_empty_baseline() -> None:
    counts = [
        ("chats", 0),
        ("chat_scopes", 0),
        ("chat_turns", 0),
        ("turn_sources", 0),
        ("turn_citations", 0),
        ("documents", 0),
        ("library_nodes", 0),
    ]
    asyncio.run(assert_phase4_database(_Connection(counts), "phase4_test"))


def test_phase4_guard_rejects_reused_state_before_mutation() -> None:
    counts = [("chats", 1), ("documents", 2)]
    with pytest.raises(pytest.fail.Exception, match="reused database"):
        asyncio.run(assert_phase4_database(_Connection(counts), "phase4_test"))
