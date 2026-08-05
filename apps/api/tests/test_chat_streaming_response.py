import asyncio
from collections.abc import AsyncIterator
from typing import cast

import pytest
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect
from starlette.types import Message, Scope

from app.routes.chats import _DisconnectAwareStreamingResponse


def _scope() -> Scope:
    return cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "method": "POST",
            "path": "/stream",
            "headers": [],
        },
    )


def test_send_failure_closes_body_before_client_disconnect_propagates() -> None:
    async def exercise() -> None:
        transition_committed = asyncio.Event()
        response_cleanup_committed = asyncio.Event()
        listener_finished = asyncio.Event()
        background_calls = 0

        async def body() -> AsyncIterator[bytes]:
            try:
                yield b"event: sources\n\n"
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                transition_committed.set()

        async def receive() -> Message:
            try:
                await asyncio.Event().wait()
            finally:
                listener_finished.set()
            raise AssertionError("unreachable")

        async def send(message: Message) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                raise OSError("connection reset")

        async def background() -> None:
            nonlocal background_calls
            background_calls += 1

        async def on_interrupted() -> None:
            await asyncio.sleep(0)
            response_cleanup_committed.set()

        response = _DisconnectAwareStreamingResponse(
            body(),
            on_interrupted=on_interrupted,
            background=BackgroundTask(background),
        )
        with pytest.raises(ClientDisconnect) as caught:
            await response(_scope(), receive, send)

        assert isinstance(caught.value.__cause__, OSError)
        assert transition_committed.is_set()
        assert response_cleanup_committed.is_set()
        assert listener_finished.is_set()
        assert background_calls == 0

    asyncio.run(exercise())


def test_simultaneous_stream_completion_and_listener_failure_propagates_error() -> None:
    async def exercise() -> None:
        body_ready = asyncio.Event()
        listener_ready = asyncio.Event()
        release = asyncio.Event()
        background_calls = 0

        async def body() -> AsyncIterator[bytes]:
            body_ready.set()
            await release.wait()
            yield b"complete"

        async def receive() -> Message:
            listener_ready.set()
            await release.wait()
            raise RuntimeError("simultaneous receive failure")

        async def send(message: Message) -> None:
            return None

        async def background() -> None:
            nonlocal background_calls
            background_calls += 1

        async def release_both() -> None:
            await body_ready.wait()
            await listener_ready.wait()
            release.set()

        response = _DisconnectAwareStreamingResponse(
            body(), background=BackgroundTask(background)
        )
        coordinator = asyncio.create_task(release_both())
        with pytest.raises(RuntimeError, match="simultaneous receive failure"):
            await response(_scope(), receive, send)
        await coordinator

        assert background_calls == 0

    asyncio.run(exercise())


def test_disconnect_before_body_entry_cancels_send_and_runs_background_once() -> None:
    async def exercise() -> None:
        body_entered = False
        send_cancelled = asyncio.Event()
        interruption_committed = asyncio.Event()
        background_calls = 0

        async def body() -> AsyncIterator[bytes]:
            nonlocal body_entered
            body_entered = True
            yield b"unexpected"

        async def receive() -> Message:
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                send_cancelled.set()

        async def background() -> None:
            nonlocal background_calls
            background_calls += 1

        async def on_interrupted() -> None:
            await asyncio.sleep(0)
            interruption_committed.set()

        response = _DisconnectAwareStreamingResponse(
            body(),
            on_interrupted=on_interrupted,
            background=BackgroundTask(background),
        )
        await response(_scope(), receive, send)

        assert not body_entered
        assert send_cancelled.is_set()
        assert interruption_committed.is_set()
        assert background_calls == 1

    asyncio.run(exercise())


def test_normal_completion_cancels_listener_and_runs_background_once() -> None:
    async def exercise() -> None:
        listener_finished = asyncio.Event()
        sent: list[Message] = []
        interruption_calls = 0
        background_calls = 0

        async def body() -> AsyncIterator[bytes]:
            yield b"complete"

        async def receive() -> Message:
            try:
                await asyncio.Event().wait()
            finally:
                listener_finished.set()
            raise AssertionError("unreachable")

        async def send(message: Message) -> None:
            sent.append(message)

        async def background() -> None:
            nonlocal background_calls
            background_calls += 1

        async def on_interrupted() -> None:
            nonlocal interruption_calls
            interruption_calls += 1

        response = _DisconnectAwareStreamingResponse(
            body(),
            on_interrupted=on_interrupted,
            background=BackgroundTask(background),
        )
        await response(_scope(), receive, send)

        assert listener_finished.is_set()
        assert sent[-1] == {
            "type": "http.response.body",
            "body": b"",
            "more_body": False,
        }
        assert background_calls == 1
        assert interruption_calls == 0

    asyncio.run(exercise())


def test_listener_failure_closes_body_and_skips_background() -> None:
    async def exercise() -> None:
        body_entered = False
        send_cancelled = asyncio.Event()
        background_calls = 0

        async def body() -> AsyncIterator[bytes]:
            nonlocal body_entered
            body_entered = True
            yield b"unexpected"

        async def receive() -> Message:
            raise RuntimeError("receive failed")

        async def send(message: Message) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                send_cancelled.set()

        async def background() -> None:
            nonlocal background_calls
            background_calls += 1

        response = _DisconnectAwareStreamingResponse(
            body(), background=BackgroundTask(background)
        )
        with pytest.raises(RuntimeError, match="receive failed"):
            await response(_scope(), receive, send)

        assert not body_entered
        assert send_cancelled.is_set()
        assert background_calls == 0

    asyncio.run(exercise())
