import asyncio
import hashlib
import json
import re
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse

from app.runtime.auth import LocalServiceAuthenticator
from app.runtime.limits import Stage, StageLimits
from app.runtime.ocr_workspace import OcrWorkspaceManager, WorkspaceError
from app.runtime.protocol import (
    REQUEST_ID_PATTERN,
    BoundedBodyMiddleware,
    OcrRequest,
    OcrResponse,
    require_bounded_json,
)
from app.services.parsing.types import OcrMode


class OcrServiceAdapter(Protocol):
    async def process(
        self,
        *,
        job_id: str,
        workspace: str,
        pages: Sequence[int],
        mode: OcrMode,
        cancellation: asyncio.Event,
    ) -> Sequence[int]: ...


class OcrAdapterUnavailableError(RuntimeError):
    pass


class UnconfiguredOcrServiceAdapter:
    async def process(
        self,
        *,
        job_id: str,
        workspace: str,
        pages: Sequence[int],
        mode: OcrMode,
        cancellation: asyncio.Event,
    ) -> Sequence[int]:
        raise OcrAdapterUnavailableError("OCR service adapter is not configured")


class OcrServiceRuntime:
    def __init__(
        self,
        adapter: OcrServiceAdapter,
        workspaces: OcrWorkspaceManager,
        *,
        stage_limits: StageLimits | None = None,
    ) -> None:
        self._adapter = adapter
        self._workspaces = workspaces
        self._stage_limits = stage_limits or StageLimits()
        self._cancellations: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._jobs: dict[str, str] = {}
        self._draining = False
        self._idle = asyncio.Event()
        self._idle.set()

    async def execute(
        self, request: OcrRequest
    ) -> tuple[list[int], dict[str, int | float | None] | None]:
        if self._draining:
            raise OcrAdapterUnavailableError("OCR service is draining")
        if request.request_id in self._cancellations:
            raise ValueError("request_id is already active")
        workspace = self._workspaces.get(request.job_id)
        cancellation = asyncio.Event()
        self._cancellations[request.request_id] = cancellation
        self._idle.clear()
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("OCR request has no owning task")
        self._tasks[request.request_id] = current_task
        self._jobs[request.request_id] = request.job_id
        try:
            async with self._stage_limits.acquire(Stage.OCR):
                if cancellation.is_set():
                    raise asyncio.CancelledError
                completed = list(
                    await self._adapter.process(
                        job_id=request.job_id,
                        workspace=str(workspace),
                        pages=request.pages,
                        mode=OcrMode(request.mode),
                        cancellation=cancellation,
                    )
                )
                if cancellation.is_set():
                    raise asyncio.CancelledError
                if completed != request.pages:
                    raise RuntimeError("OCR adapter returned an unexpected page set")
                metrics = (
                    dict(self._adapter.last_metrics)
                    if getattr(self._adapter, "last_metrics", None)
                    else None
                )
                return completed, metrics
        finally:
            self._cancellations.pop(request.request_id, None)
            self._tasks.pop(request.request_id, None)
            self._jobs.pop(request.request_id, None)
            if not self._cancellations:
                self._idle.set()

    def active(self, request_id: str) -> bool:
        return request_id in self._cancellations

    def job_active(self, job_id: str) -> bool:
        return job_id in self._jobs.values()

    def workspace(self, job_id: str) -> str:
        return str(self._workspaces.get(job_id))

    def cleanup(self, job_id: str) -> None:
        self._workspaces.cleanup(job_id)

    def cancel(self, request_id: str) -> bool:
        cancellation = self._cancellations.get(request_id)
        if cancellation is None:
            return False
        cancellation.set()
        task = self._tasks.get(request_id)
        if task is not None:
            task.cancel()
        return True

    def health(self) -> dict[str, object]:
        return {
            "status": "draining" if self._draining else "ready",
            "active": len(self._cancellations),
            "stages": self._stage_limits.snapshot(),
        }

    async def drain(self, timeout_seconds: float = 900.0) -> None:
        """Reject new OCR work and wait for the active job boundary."""
        self._draining = True
        if not self._cancellations:
            self._idle.set()
        await asyncio.wait_for(self._idle.wait(), timeout=timeout_seconds)

    def resume(self) -> None:
        self._draining = False

    async def close(self) -> None:
        self._draining = True
        for cancellation in self._cancellations.values():
            cancellation.set()
        for task in self._tasks.values():
            task.cancel()


def create_ocr_service_app(
    *,
    service_token: str,
    adapter: OcrServiceAdapter,
    workspaces: OcrWorkspaceManager,
) -> FastAPI:
    authenticator = LocalServiceAuthenticator(service_token)
    runtime = OcrServiceRuntime(adapter, workspaces)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await runtime.close()

    app = FastAPI(
        title="Local RAG isolated OCR service",
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
                detail="OCR drain timed out",
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
        "/ocr",
        response_model=OcrResponse,
        dependencies=[Depends(authenticator), Depends(require_bounded_json)],
    )
    async def ocr(request: OcrRequest) -> OcrResponse:
        try:
            completed, metrics = await runtime.execute(request)
        except (ValueError, WorkspaceError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except OcrAdapterUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        return OcrResponse(
            request_id=request.request_id,
            completed_pages=completed,
            mode=request.mode,
            duration_seconds=(
                metrics.get("duration_seconds") if metrics is not None else None
            ),
            peak_working_set_bytes=(
                metrics.get("peak_working_set_bytes") if metrics is not None else None
            ),
        )

    @app.put(
        "/jobs/{job_id}/input",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(authenticator)],
    )
    async def upload_input(
        request: Request,
        job_id: str = Path(pattern=REQUEST_ID_PATTERN, max_length=64),
        content_length: int | None = Header(default=None, ge=1),
        content_type: str | None = Header(default=None),
        x_content_sha256: str | None = Header(default=None),
    ) -> dict[str, object]:
        maximum_bytes = 100 * 1024 * 1024
        if content_length is None:
            raise HTTPException(
                status.HTTP_411_LENGTH_REQUIRED, "Content-Length required"
            )
        if content_length > maximum_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "PDF is too large")
        if content_type != "application/pdf":
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "application/pdf required"
            )
        if (
            x_content_sha256 is None
            or re.fullmatch(r"[0-9a-f]{64}", x_content_sha256) is None
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid SHA-256"
            )
        try:
            workspace = workspaces.create(job_id)
        except WorkspaceError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        path = workspace / "input.pdf"
        digest = hashlib.sha256()
        received = 0
        try:
            with path.open("xb") as output:
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > content_length or received > maximum_bytes:
                        raise HTTPException(
                            status.HTTP_413_CONTENT_TOO_LARGE, "PDF is too large"
                        )
                    digest.update(chunk)
                    output.write(chunk)
            if received != content_length or digest.hexdigest() != x_content_sha256:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "PDF length or SHA-256 mismatch",
                )
        except BaseException:
            workspaces.cleanup(job_id)
            raise
        return {"job_id": job_id, "byte_size": received, "sha256": digest.hexdigest()}

    @app.get(
        "/jobs/{job_id}/result",
        dependencies=[Depends(authenticator)],
    )
    async def result(
        job_id: str = Path(pattern=REQUEST_ID_PATTERN, max_length=64),
    ) -> JSONResponse:
        try:
            result_path = workspaces.get(job_id) / "result.json"
            if result_path.stat().st_size > 8 * 1024 * 1024:
                raise OSError("OCR result exceeds protocol limit")
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (WorkspaceError, FileNotFoundError) as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "OCR result not found"
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "OCR result is invalid"
            ) from exc
        return JSONResponse(payload)

    @app.delete(
        "/jobs/{job_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(authenticator)],
    )
    async def delete(
        job_id: str = Path(pattern=REQUEST_ID_PATTERN, max_length=64),
    ) -> None:
        if runtime.job_active(job_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "OCR job is active")
        workspaces.cleanup(job_id)

    @app.post(
        "/cancel/{request_id}",
        dependencies=[Depends(authenticator), Depends(require_bounded_json)],
    )
    async def cancel(
        request_id: str = Path(pattern=REQUEST_ID_PATTERN, max_length=64),
    ) -> dict[str, bool]:
        return {"cancelled": runtime.cancel(request_id)}

    return app
