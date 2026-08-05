import asyncio

import pytest

from app.runtime.coordinator import AdapterUnavailableError, CoordinatorRuntime
from app.runtime.limits import Stage, StageLimits
from app.runtime.protocol import CoordinatorRequest
from app.runtime.scheduler import Priority, PriorityScheduler


class Clock:
    value = 0.0

    def __call__(self) -> float:
        return self.value


def test_interactive_priority_with_one_background_starvation_escape() -> None:
    async def exercise() -> None:
        clock = Clock()
        scheduler: PriorityScheduler[str] = PriorityScheduler(clock=clock)
        await scheduler.enqueue("background-a", "a", Priority.BACKGROUND)
        await scheduler.enqueue("background-b", "b", Priority.BACKGROUND)
        await scheduler.enqueue("interactive-a", "i1", Priority.INTERACTIVE)

        clock.value = 60
        first = await scheduler.next()
        assert first.request_id == "background-a"
        scheduler.finish(first.request_id)

        await scheduler.enqueue("interactive-b", "i2", Priority.INTERACTIVE)
        second = await scheduler.next()
        assert second.request_id == "interactive-a"
        scheduler.finish(second.request_id)

        third = await scheduler.next()
        assert third.request_id == "interactive-b"
        scheduler.finish(third.request_id)

        fourth = await scheduler.next()
        assert fourth.request_id == "background-b"
        scheduler.finish(fourth.request_id)

    asyncio.run(exercise())


def test_queue_cancellation_and_metrics_are_observable() -> None:
    async def exercise() -> None:
        scheduler: PriorityScheduler[str] = PriorityScheduler()
        await scheduler.enqueue("cancel-me", "payload", Priority.BACKGROUND)

        assert await scheduler.cancel("cancel-me") is True
        assert await scheduler.cancel("missing") is False

        metrics = scheduler.metrics()
        assert metrics.queued_background == 0
        assert metrics.cancelled == 1

    asyncio.run(exercise())


def test_stage_limits_match_contract_and_enforce_digital_io_cap() -> None:
    async def exercise() -> None:
        limits = StageLimits()
        release = asyncio.Event()
        entered: list[int] = []

        async def occupy(index: int) -> None:
            async with limits.acquire(Stage.DIGITAL_IO):
                entered.append(index)
                await release.wait()

        tasks = [asyncio.create_task(occupy(index)) for index in range(3)]
        for _ in range(100):
            if len(entered) == 2:
                break
            await asyncio.sleep(0)

        assert len(entered) == 2
        assert limits.snapshot()["digital_io"] == {"limit": 2, "active": 2}
        assert limits.snapshot()["generation"]["limit"] == 1
        assert limits.snapshot()["rerank"]["limit"] == 1
        assert limits.snapshot()["embedding"]["limit"] == 1
        assert limits.snapshot()["ocr"]["limit"] == 1

        release.set()
        await asyncio.gather(*tasks)
        assert len(entered) == 3

    asyncio.run(exercise())


def test_coordinator_duplicate_id_fails_without_corrupting_active_request() -> None:
    class BlockingAdapter:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def execute(
            self,
            stage: Stage,
            inputs: list[str],
            cancellation: asyncio.Event,
        ) -> list[str | float]:
            self.entered.set()
            await self.release.wait()
            return ["complete"]

    async def exercise() -> None:
        adapter = BlockingAdapter()
        runtime = CoordinatorRuntime(adapter)
        request = CoordinatorRequest(
            request_id="same-request",
            stage="generation",
            priority="interactive",
            inputs=["prompt"],
        )
        first = asyncio.create_task(runtime.submit(request))
        await asyncio.wait_for(adapter.entered.wait(), timeout=1)

        with pytest.raises(ValueError, match="already"):
            await runtime.submit(request)

        adapter.release.set()
        assert await first == ["complete"]
        await runtime.close()

    asyncio.run(exercise())


def test_coordinator_drain_waits_for_active_boundary_and_rejects_new_work() -> None:
    class BlockingAdapter:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def execute(
            self,
            stage: Stage,
            inputs: list[str],
            cancellation: asyncio.Event,
        ) -> list[str | float]:
            self.entered.set()
            await self.release.wait()
            return ["complete"]

    async def exercise() -> None:
        adapter = BlockingAdapter()
        runtime = CoordinatorRuntime(adapter)
        request = CoordinatorRequest(
            request_id="active-answer",
            stage="generation",
            priority="interactive",
            inputs=["prompt"],
        )
        active = asyncio.create_task(runtime.submit(request))
        await asyncio.wait_for(adapter.entered.wait(), timeout=1)
        draining = asyncio.create_task(runtime.drain(timeout_seconds=1))
        await asyncio.sleep(0)

        with pytest.raises(AdapterUnavailableError, match="draining"):
            await runtime.submit(
                CoordinatorRequest(
                    request_id="late-answer",
                    stage="generation",
                    priority="interactive",
                    inputs=["prompt"],
                )
            )
        assert not draining.done()

        adapter.release.set()
        assert await active == ["complete"]
        await draining
        assert runtime.health()["status"] == "draining"
        runtime.resume()
        assert runtime.health()["status"] == "ready"
        await runtime.close()

    asyncio.run(exercise())
