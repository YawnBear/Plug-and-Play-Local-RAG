import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass
from typing import Protocol

from fastapi import Depends, FastAPI, HTTPException, Path, status
from fastapi.responses import StreamingResponse

from app.runtime.auth import LocalServiceAuthenticator
from app.runtime.limits import Stage, StageLimits
from app.runtime.protocol import (
    REQUEST_ID_PATTERN,
    BoundedBodyMiddleware,
    CoordinatorRequest,
    EmbedRequest,
    EmbedResponse,
    GenerateRequest,
    RerankRequest,
    RerankResponse,
    require_bounded_json,
)
from app.runtime.scheduler import Priority, PriorityScheduler, ScheduledItem
from app.services.ollama_generation import GenerationChunk


class CoordinatorAdapter(Protocol):
    async def execute(
        self,
        stage: Stage,
        inputs: Sequence[str],
        cancellation: asyncio.Event,
    ) -> list[str | float]: ...

    def stream(
        self,
        prompt: str,
        cancellation: asyncio.Event,
        *,
        think: bool = True,
    ) -> AsyncIterator[GenerationChunk]: ...


class AdapterUnavailableError(RuntimeError):
    pass


class UnconfiguredCoordinatorAdapter:
    async def execute(
        self,
        stage: Stage,
        inputs: Sequence[str],
        cancellation: asyncio.Event,
    ) -> list[str | float]:
        raise AdapterUnavailableError("coordinator adapter is not configured")

    async def stream(
        self,
        prompt: str,
        cancellation: asyncio.Event,
        *,
        think: bool = True,
    ) -> AsyncIterator[GenerationChunk]:
        raise AdapterUnavailableError("coordinator adapter is not configured")
        yield


@dataclass(frozen=True, slots=True)
class _StreamFailure:
    error: BaseException


_STREAM_DONE = object()


class CoordinatorRuntime:
    def __init__(
        self,
        adapter: CoordinatorAdapter,
        *,
        scheduler: PriorityScheduler[CoordinatorRequest] | None = None,
        stage_limits: StageLimits | None = None,
    ) -> None:
        self._adapter = adapter
        self.scheduler = scheduler or PriorityScheduler()
        self.stage_limits = stage_limits or StageLimits()
        self._dispatcher: asyncio.Task[None] | None = None
        self._execution_tasks: set[asyncio.Task[None]] = set()
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._futures: dict[str, asyncio.Future[list[str | float]]] = {}
        self._streams: dict[
            str, asyncio.Queue[GenerationChunk | _StreamFailure | object]
        ] = {}
        self._cancellations: dict[str, asyncio.Event] = {}
        self._closed = False
        self._draining = False
        self._idle = asyncio.Event()
        self._idle.set()

    async def submit(self, request: CoordinatorRequest) -> list[str | float]:
        if self._closed:
            raise RuntimeError("coordinator runtime is closed")
        if self._draining:
            raise AdapterUnavailableError("coordinator is draining")
        if (
            request.request_id in self._futures
            or request.request_id in self._cancellations
        ):
            raise ValueError("request_id is already queued or active")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[list[str | float]] = loop.create_future()
        self._futures[request.request_id] = future
        self._cancellations[request.request_id] = asyncio.Event()
        self._idle.clear()
        try:
            await self.scheduler.enqueue(
                request.request_id,
                request,
                Priority(request.priority),
            )
        except BaseException:
            self._futures.pop(request.request_id, None)
            self._cancellations.pop(request.request_id, None)
            if not self._cancellations:
                self._idle.set()
            raise
        self._ensure_dispatcher()
        try:
            return await future
        except asyncio.CancelledError:
            await self.cancel(request.request_id)
            raise

    async def stream(
        self, request: CoordinatorRequest
    ) -> AsyncIterator[GenerationChunk]:
        if self._closed:
            raise RuntimeError("coordinator runtime is closed")
        if self._draining:
            raise AdapterUnavailableError("coordinator is draining")
        if (
            request.request_id in self._futures
            or request.request_id in self._streams
            or request.request_id in self._cancellations
        ):
            raise ValueError("request_id is already queued or active")
        queue: asyncio.Queue[GenerationChunk | _StreamFailure | object] = asyncio.Queue(
            maxsize=64
        )
        self._streams[request.request_id] = queue
        self._cancellations[request.request_id] = asyncio.Event()
        self._idle.clear()
        try:
            await self.scheduler.enqueue(
                request.request_id,
                request,
                Priority(request.priority),
            )
        except BaseException:
            self._streams.pop(request.request_id, None)
            self._cancellations.pop(request.request_id, None)
            if not self._cancellations:
                self._idle.set()
            raise
        self._ensure_dispatcher()
        try:
            while True:
                item = await queue.get()
                if item is _STREAM_DONE:
                    return
                if isinstance(item, _StreamFailure):
                    raise item.error
                if not isinstance(item, GenerationChunk):
                    raise RuntimeError("invalid coordinator stream item")
                yield item
        finally:
            if request.request_id in self._cancellations:
                await self.cancel(request.request_id)

    async def cancel(self, request_id: str) -> bool:
        event = self._cancellations.get(request_id)
        if event is None:
            return False
        event.set()
        queued = await self.scheduler.cancel(request_id)
        if queued:
            future = self._futures.pop(request_id, None)
            stream = self._streams.pop(request_id, None)
            self._cancellations.pop(request_id)
            if future is not None and not future.done():
                future.cancel()
            if stream is not None:
                await stream.put(_StreamFailure(asyncio.CancelledError()))
        active = self._active_tasks.get(request_id)
        if active is not None:
            active.cancel()
        return True

    def health(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": (
                "stopping"
                if self._closed
                else "draining"
                if self._draining
                else "ready"
            ),
            "queue": asdict(self.scheduler.metrics()),
            "stages": self.stage_limits.snapshot(),
        }
        diagnostics = getattr(self._adapter, "diagnostics", None)
        if callable(diagnostics):
            result["diagnostics"] = diagnostics()
        return result

    async def drain(self, timeout_seconds: float = 900.0) -> None:
        """Reject new work and wait for the exact queued/active boundary."""
        self._draining = True
        if not self._cancellations:
            self._idle.set()
        await asyncio.wait_for(self._idle.wait(), timeout=timeout_seconds)

    def resume(self) -> None:
        if not self._closed:
            self._draining = False

    async def close(self) -> None:
        self._closed = True
        self._draining = True
        for event in self._cancellations.values():
            event.set()
        for task in self._execution_tasks:
            task.cancel()
        if self._execution_tasks:
            await asyncio.gather(*self._execution_tasks, return_exceptions=True)
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            with suppress(asyncio.CancelledError):
                await self._dispatcher
        for future in self._futures.values():
            if not future.done():
                future.cancel()
        self._futures.clear()
        self._streams.clear()
        self._cancellations.clear()
        close = getattr(self._adapter, "close", None)
        if close is not None:
            await close()

    def _ensure_dispatcher(self) -> None:
        if self._dispatcher is None or self._dispatcher.done():
            self._dispatcher = asyncio.create_task(
                self._dispatch(),
                name="coordinator-dispatcher",
            )

    async def _dispatch(self) -> None:
        while not self._closed:
            item = await self.scheduler.next()
            task = asyncio.create_task(
                self._execute(item),
                name=f"coordinator-{item.request_id}",
            )
            self._execution_tasks.add(task)
            self._active_tasks[item.request_id] = task
            task.add_done_callback(self._execution_tasks.discard)
            task.add_done_callback(
                lambda _task, request_id=item.request_id: self._active_tasks.pop(
                    request_id, None
                )
            )

    async def _execute(self, item: ScheduledItem[CoordinatorRequest]) -> None:
        stream = self._streams.get(item.request_id)
        if stream is not None:
            await self._execute_stream(item, stream)
            return
        request = item.payload
        future = self._futures[item.request_id]
        cancellation = self._cancellations[item.request_id]
        failed = False
        try:
            async with self.stage_limits.acquire(Stage(request.stage)):
                if cancellation.is_set():
                    raise asyncio.CancelledError
                outputs = await self._adapter.execute(
                    Stage(request.stage),
                    request.inputs,
                    cancellation,
                )
                if cancellation.is_set():
                    raise asyncio.CancelledError
            if not future.done():
                future.set_result(outputs)
        except asyncio.CancelledError:
            failed = True
            if not future.done():
                future.cancel()
        except BaseException as exc:
            failed = True
            if not future.done():
                future.set_exception(exc)
        finally:
            self.scheduler.finish(item.request_id, failed=failed)
            self._futures.pop(item.request_id, None)
            self._cancellations.pop(item.request_id, None)
            if not self._cancellations:
                self._idle.set()

    async def _execute_stream(
        self,
        item: ScheduledItem[CoordinatorRequest],
        queue: asyncio.Queue[GenerationChunk | _StreamFailure | object],
    ) -> None:
        request = item.payload
        cancellation = self._cancellations[item.request_id]
        failed = False
        try:
            async with self.stage_limits.acquire(Stage.GENERATION):
                async for chunk in self._adapter.stream(
                    request.inputs[0], cancellation, think=request.think
                ):
                    if cancellation.is_set():
                        raise asyncio.CancelledError
                    if not isinstance(chunk, GenerationChunk):
                        raise RuntimeError(
                            "generation adapter returned an invalid result"
                        )
                    await queue.put(chunk)
            await queue.put(_STREAM_DONE)
        except asyncio.CancelledError as exc:
            failed = True
            _put_stream_failure(queue, exc)
        except BaseException as exc:
            failed = True
            _put_stream_failure(queue, exc)
        finally:
            self.scheduler.finish(item.request_id, failed=failed)
            self._streams.pop(item.request_id, None)
            self._cancellations.pop(item.request_id, None)
            if not self._cancellations:
                self._idle.set()


def create_coordinator_app(
    *,
    service_token: str,
    adapter: CoordinatorAdapter,
    runtime_factory: Callable[[CoordinatorAdapter], CoordinatorRuntime] = (
        CoordinatorRuntime
    ),
) -> FastAPI:
    authenticator = LocalServiceAuthenticator(service_token)
    runtime = runtime_factory(adapter)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await runtime.close()

    app = FastAPI(
        title="Local RAG inference coordinator",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.add_middleware(BoundedBodyMiddleware)

    @app.get("/health", dependencies=[Depends(authenticator)])
    async def health() -> dict[str, object]:
        return runtime.health()

    @app.post(
        "/admin/drain",
        dependencies=[Depends(authenticator), Depends(require_bounded_json)],
    )
    async def drain() -> dict[str, object]:
        try:
            await runtime.drain()
        except TimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="coordinator drain timed out",
            ) from exc
        return {"drained": True, "active": 0}

    @app.post(
        "/admin/resume",
        dependencies=[Depends(authenticator), Depends(require_bounded_json)],
    )
    async def resume() -> dict[str, bool]:
        runtime.resume()
        return {"draining": False}

    @app.post(
        "/embed",
        response_model=EmbedResponse,
        dependencies=[Depends(authenticator), Depends(require_bounded_json)],
    )
    async def embed(request: EmbedRequest) -> EmbedResponse:
        outputs = await _submit_typed(
            runtime,
            CoordinatorRequest(
                request_id=request.request_id,
                stage="embedding",
                priority=request.priority,
                inputs=request.texts,
            ),
        )
        if len(outputs) != len(request.texts) or any(
            not isinstance(output, str) for output in outputs
        ):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="embedding adapter returned an invalid result",
            )
        try:
            embeddings = [
                [float(value) for value in output.split(",")] for output in outputs
            ]
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="embedding adapter returned an invalid result",
            ) from exc
        return EmbedResponse(request_id=request.request_id, embeddings=embeddings)

    @app.post(
        "/rerank",
        response_model=RerankResponse,
        dependencies=[Depends(authenticator), Depends(require_bounded_json)],
    )
    async def rerank(request: RerankRequest) -> RerankResponse:
        outputs = await _submit_typed(
            runtime,
            CoordinatorRequest(
                request_id=request.request_id,
                stage="rerank",
                priority=request.priority,
                inputs=[request.query, *request.passages],
            ),
        )
        if len(outputs) != len(request.passages) or any(
            isinstance(output, bool) or not isinstance(output, int | float)
            for output in outputs
        ):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="reranker adapter returned an invalid result",
            )
        return RerankResponse(
            request_id=request.request_id,
            scores=[float(output) for output in outputs],
        )

    @app.post(
        "/generate/stream",
        dependencies=[Depends(authenticator), Depends(require_bounded_json)],
    )
    async def generate(request: GenerateRequest) -> StreamingResponse:
        async def frames():
            coordinator_request = CoordinatorRequest(
                request_id=request.request_id,
                stage="generation",
                priority=request.priority,
                inputs=[request.prompt],
                think=request.think,
            )
            terminal = False
            async for output in runtime.stream(coordinator_request):
                if output.type == "done":
                    terminal = True
                    yield (
                        json.dumps(
                            {
                                "request_id": request.request_id,
                                "done": True,
                                "done_reason": output.done_reason,
                                "usage": (
                                    None
                                    if output.usage is None
                                    else asdict(output.usage)
                                ),
                            }
                        )
                        + "\n"
                    )
                    continue
                yield (
                    json.dumps(
                        {
                            "request_id": request.request_id,
                            "type": output.type,
                            "text": output.text,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            if not terminal:
                raise RuntimeError("generation adapter ended without a stop reason")

        return StreamingResponse(frames(), media_type="application/x-ndjson")

    @app.post(
        "/cancel/{request_id}",
        dependencies=[Depends(authenticator), Depends(require_bounded_json)],
    )
    async def cancel(
        request_id: str = Path(pattern=REQUEST_ID_PATTERN, max_length=64),
    ) -> dict[str, bool]:
        return {"cancelled": await runtime.cancel(request_id)}

    return app


async def _submit_typed(
    runtime: CoordinatorRuntime,
    request: CoordinatorRequest,
) -> list[str | float]:
    try:
        return await runtime.submit(request)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except AdapterUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def _put_stream_failure(
    queue: asyncio.Queue[GenerationChunk | _StreamFailure | object],
    error: BaseException,
) -> None:
    try:
        queue.put_nowait(_StreamFailure(error))
    except asyncio.QueueFull:
        pass
    except AdapterUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
