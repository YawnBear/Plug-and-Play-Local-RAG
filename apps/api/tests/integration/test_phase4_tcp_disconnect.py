import asyncio
import json
import socket
import struct
import sys
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import delete, select

from app.db.models import Chat, ChatScope, ChatTurn, Document
from app.db.repositories import RetrievedChunk
from app.routes.chats import router as chats_router
from app.services.chats import ChatService
from app.services.reranker import RerankedChunk
from tests.integration.phase4_safety import run_selector
from tests.integration.test_phase4_postgres import (
    _chunk,
    _document,
    _factory,
    _file,
    _release_factory,
)

pytestmark = pytest.mark.integration


async def _read_chunked_sse_event(
    reader: asyncio.StreamReader,
) -> tuple[str, dict[str, object]]:
    raw_headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
    header_lines = raw_headers.decode("ascii").split("\r\n")
    assert header_lines[0].startswith("HTTP/1.1 200 "), header_lines[0]
    headers = {
        name.lower(): value.strip()
        for line in header_lines[1:]
        if line
        for name, value in [line.split(":", 1)]
    }
    assert headers.get("content-type", "").startswith("text/event-stream")
    assert headers.get("transfer-encoding", "").lower() == "chunked"

    body = bytearray()
    while b"\n\n" not in body:
        size_line = await asyncio.wait_for(reader.readline(), timeout=5)
        assert size_line.endswith(b"\r\n")
        size = int(size_line.split(b";", 1)[0], 16)
        assert size > 0
        body.extend(await asyncio.wait_for(reader.readexactly(size), timeout=5))
        assert await asyncio.wait_for(reader.readexactly(2), timeout=5) == b"\r\n"

    event_block = bytes(body).split(b"\n\n", 1)[0].decode("utf-8")
    fields = {
        name: value
        for line in event_block.splitlines()
        for name, value in [line.split(": ", 1)]
    }
    return fields["event"], json.loads(fields["data"])


def test_tcp_disconnect_interrupts_and_retry_reuses_turn() -> None:
    async def exercise() -> None:
        engine, factory, guard = await _factory()
        marker = uuid.uuid4().hex
        document = _document(f"phase4-{marker}")
        chunk = _chunk(document)
        file_node = _file(document, None)
        chat = Chat(id=uuid.uuid4(), title="tcp disconnect", scope_mode="selected")
        candidate = RetrievedChunk(chunk, 0.1)

        class Retrieval:
            async def retrieve(self, *args: object, **kwargs: object) -> list:
                return [candidate]

        class Reranker:
            async def rerank(self, *args: object, **kwargs: object) -> list:
                return [RerankedChunk(candidate, 0.9)]

        class PausingGenerator:
            def __init__(self) -> None:
                self.entered = asyncio.Event()
                self.release = asyncio.Event()

            async def check_available(self) -> None:
                return None

            async def stream(self, prompt: str) -> AsyncIterator[str]:
                self.entered.set()
                await self.release.wait()
                yield "never emitted"

        class SuccessfulGenerator:
            async def check_available(self) -> None:
                return None

            async def stream(self, prompt: str) -> AsyncIterator[str]:
                yield "The controlled answer is cobalt [S1]"

        pausing_generator = PausingGenerator()
        service = ChatService(factory, Retrieval(), Reranker(), pausing_generator)
        app = FastAPI()
        app.state.container = SimpleNamespace(chats=service)
        app.include_router(chats_router)

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                lifespan="off",
                access_log=False,
                log_level="error",
            )
        )
        server_task: asyncio.Task[None] | None = None
        disconnect_writer: asyncio.StreamWriter | None = None
        turn_id: uuid.UUID | None = None
        try:
            async with factory() as session, session.begin():
                session.add_all([document, chunk, file_node, chat])
                await session.flush()
                session.add(ChatScope(chat_id=chat.id, node_id=file_node.id))

            server_task = asyncio.create_task(server.serve(sockets=[listener]))
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started

            reader, disconnect_writer = await asyncio.open_connection("127.0.0.1", port)
            request_body = json.dumps(
                {"question": "What is the controlled answer?"}
            ).encode("utf-8")
            disconnect_writer.write(
                (
                    f"POST /api/chats/{chat.id}/messages/stream HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    "Accept: text/event-stream\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(request_body)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                + request_body
            )
            await disconnect_writer.drain()
            event, payload = await _read_chunked_sse_event(reader)
            assert event == "sources"
            turn_id = uuid.UUID(str(payload["turn_id"]))
            await asyncio.wait_for(pausing_generator.entered.wait(), timeout=5)

            client_socket = disconnect_writer.get_extra_info("socket")
            assert client_socket is not None
            linger_format = "hh" if sys.platform == "win32" else "ii"
            client_socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                struct.pack(linger_format, 1, 0),
            )
            disconnect_writer.transport.abort()
            await disconnect_writer.wait_closed()
            disconnect_writer = None

            deadline = asyncio.get_running_loop().time() + 5
            interrupted: ChatTurn | None = None
            while asyncio.get_running_loop().time() < deadline:
                async with factory() as session:
                    interrupted = await session.get(ChatTurn, turn_id)
                    if interrupted is not None and interrupted.status == "interrupted":
                        break
                await asyncio.sleep(0.02)
            assert interrupted is not None
            assert interrupted.status == "interrupted"
            assert interrupted.ordinal == 1
            assert interrupted.attempt == 1

            service._generator = SuccessfulGenerator()
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=10
            ) as client:
                retry = await client.post(
                    f"/api/chats/{chat.id}/turns/{turn_id}/retry/stream",
                    headers={"Accept": "text/event-stream"},
                )
            assert retry.status_code == 200
            assert "event: final" in retry.text
            assert f'"turn_id": "{turn_id}"' in retry.text

            async with factory() as session:
                completed = await session.scalar(
                    select(ChatTurn).where(ChatTurn.id == turn_id)
                )
                assert completed is not None
                assert completed.status == "complete"
                assert completed.ordinal == 1
                assert completed.attempt == 2
                assert completed.final_answer is not None
                assert "cobalt" in completed.final_answer
        finally:
            if disconnect_writer is not None:
                disconnect_writer.transport.abort()
            pausing_generator.release.set()
            server.should_exit = True
            if server_task is not None:
                try:
                    await asyncio.wait_for(server_task, timeout=5)
                except TimeoutError:
                    server.force_exit = True
                    server_task.cancel()
                    await asyncio.gather(server_task, return_exceptions=True)
            listener.close()
            try:
                async with factory() as session, session.begin():
                    await session.execute(delete(Chat).where(Chat.id == chat.id))
                    await session.execute(
                        delete(Document).where(Document.id == document.id)
                    )
            finally:
                await _release_factory(engine, guard)

    run_selector(exercise())
