import asyncio
import heapq
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")
STARVATION_SECONDS = 60.0


class Priority(StrEnum):
    INTERACTIVE = "interactive"
    BACKGROUND = "background"


@dataclass(frozen=True, slots=True)
class QueueMetrics:
    queued_interactive: int
    queued_background: int
    active: int
    completed: int
    failed: int
    cancelled: int
    oldest_background_wait_seconds: float | None


@dataclass(order=True, slots=True)
class _Entry[T]:
    sequence: int
    enqueued_at: float
    request_id: str = field(compare=False)
    payload: T = field(compare=False)
    priority: Priority = field(compare=False)
    cancelled: bool = field(default=False, compare=False)


@dataclass(frozen=True, slots=True)
class ScheduledItem[T]:
    request_id: str
    payload: T
    priority: Priority
    enqueued_at: float


class PriorityScheduler[T]:
    """Interactive-first queue with a bounded background starvation escape."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        starvation_seconds: float = STARVATION_SECONDS,
    ) -> None:
        if starvation_seconds <= 0:
            raise ValueError("starvation_seconds must be positive")
        self._clock = clock
        self._starvation_seconds = starvation_seconds
        self._interactive: list[_Entry[T]] = []
        self._background: list[_Entry[T]] = []
        self._entries: dict[str, _Entry[T]] = {}
        self._sequence = 0
        self._condition = asyncio.Condition()
        self._last_background_relief: float | None = None
        self._active: set[str] = set()
        self._completed = 0
        self._failed = 0
        self._cancelled = 0

    async def enqueue(
        self,
        request_id: str,
        payload: T,
        priority: Priority,
    ) -> None:
        async with self._condition:
            if request_id in self._entries or request_id in self._active:
                raise ValueError("request_id is already queued or active")
            entry = _Entry(
                sequence=self._sequence,
                enqueued_at=self._clock(),
                request_id=request_id,
                payload=payload,
                priority=priority,
            )
            self._sequence += 1
            queue = (
                self._interactive
                if priority is Priority.INTERACTIVE
                else self._background
            )
            heapq.heappush(
                queue,
                entry,
            )
            self._entries[request_id] = entry
            self._condition.notify()

    async def next(self) -> ScheduledItem[T]:
        async with self._condition:
            while True:
                self._discard_cancelled(self._interactive)
                self._discard_cancelled(self._background)
                entry = self._choose_entry()
                if entry is not None:
                    self._entries.pop(entry.request_id)
                    self._active.add(entry.request_id)
                    return ScheduledItem(
                        request_id=entry.request_id,
                        payload=entry.payload,
                        priority=entry.priority,
                        enqueued_at=entry.enqueued_at,
                    )
                await self._condition.wait()

    async def cancel(self, request_id: str) -> bool:
        async with self._condition:
            entry = self._entries.pop(request_id, None)
            if entry is None:
                return False
            entry.cancelled = True
            self._cancelled += 1
            self._condition.notify_all()
            return True

    def finish(self, request_id: str, *, failed: bool = False) -> None:
        if request_id not in self._active:
            raise ValueError("request_id is not active")
        self._active.remove(request_id)
        if failed:
            self._failed += 1
        else:
            self._completed += 1

    def metrics(self) -> QueueMetrics:
        now = self._clock()
        background = [entry for entry in self._background if not entry.cancelled]
        oldest_wait = (
            max(0.0, now - min(entry.enqueued_at for entry in background))
            if background
            else None
        )
        return QueueMetrics(
            queued_interactive=sum(not entry.cancelled for entry in self._interactive),
            queued_background=len(background),
            active=len(self._active),
            completed=self._completed,
            failed=self._failed,
            cancelled=self._cancelled,
            oldest_background_wait_seconds=oldest_wait,
        )

    def _choose_entry(self) -> _Entry[T] | None:
        now = self._clock()
        if self._background:
            oldest = self._background[0]
            starvation_started = (
                oldest.enqueued_at
                if self._last_background_relief is None
                else max(oldest.enqueued_at, self._last_background_relief)
            )
            if now - starvation_started >= self._starvation_seconds:
                self._last_background_relief = now
                return heapq.heappop(self._background)
        if self._interactive:
            return heapq.heappop(self._interactive)
        if self._background:
            return heapq.heappop(self._background)
        return None

    @staticmethod
    def _discard_cancelled(queue: list[_Entry[T]]) -> None:
        while queue and queue[0].cancelled:
            heapq.heappop(queue)
