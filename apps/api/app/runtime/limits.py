import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum


class Stage(StrEnum):
    DIGITAL_IO = "digital_io"
    GENERATION = "generation"
    RERANK = "rerank"
    EMBEDDING = "embedding"
    OCR = "ocr"


DEFAULT_STAGE_LIMITS: dict[Stage, int] = {
    Stage.DIGITAL_IO: 2,
    Stage.GENERATION: 1,
    Stage.RERANK: 1,
    Stage.EMBEDDING: 1,
    Stage.OCR: 1,
}


class StageLimits:
    """One shared set of stage semaphores for a runtime process."""

    def __init__(self, limits: dict[Stage, int] | None = None) -> None:
        configured = dict(DEFAULT_STAGE_LIMITS if limits is None else limits)
        if set(configured) != set(Stage):
            raise ValueError("limits must define every runtime stage exactly once")
        if any(type(limit) is not int or limit < 1 for limit in configured.values()):
            raise ValueError("stage limits must be positive integers")
        self._limits = configured
        self._semaphores = {
            stage: asyncio.Semaphore(limit) for stage, limit in configured.items()
        }
        self._active = dict.fromkeys(Stage, 0)

    @asynccontextmanager
    async def acquire(self, stage: Stage) -> AsyncIterator[None]:
        semaphore = self._semaphores[stage]
        await semaphore.acquire()
        self._active[stage] += 1
        try:
            yield
        finally:
            self._active[stage] -= 1
            semaphore.release()

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {
            stage.value: {
                "limit": self._limits[stage],
                "active": self._active[stage],
            }
            for stage in Stage
        }
