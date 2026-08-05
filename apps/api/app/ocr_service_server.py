import argparse
import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

from app.processes.settings import OcrProcessSettings
from app.runtime.managed_uvicorn import run_managed_uvicorn
from app.runtime.network import (
    DEFAULT_OCR_SERVICE_HOST,
    DEFAULT_OCR_SERVICE_PORT,
    require_loopback_host,
)
from app.runtime.ocr_adapter import IsolatedOcrAdapter
from app.runtime.ocr_service import (
    OcrServiceAdapter,
    create_ocr_service_app,
)
from app.runtime.ocr_workspace import OcrWorkspaceManager
from app.runtime.ownership import SingleInstanceOwnership
from app.runtime.startup_diagnostics import run_with_startup_diagnostics
from app.services.parsing.ocr_subprocess import OcrSubprocessAdapter


def run(
    *,
    host: str,
    port: int,
    service_token: str,
    workspace_root: Path,
    adapter: OcrServiceAdapter,
    ownership_path: Path,
) -> None:
    require_loopback_host(host)
    if not 1 <= port <= 65535:
        raise ValueError("port must be from 1 to 65535")
    application = create_ocr_service_app(
        service_token=service_token,
        adapter=adapter,
        workspaces=OcrWorkspaceManager(workspace_root),
    )
    loop: str | Callable[[], asyncio.AbstractEventLoop] = (
        asyncio.SelectorEventLoop if sys.platform == "win32" else "auto"
    )
    with SingleInstanceOwnership.acquire(ownership_path):
        run_managed_uvicorn(
            service="ocr",
            application=application,
            host=host,
            port=port,
            loop=loop,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated local OCR service")
    parser.add_argument("--host", default=DEFAULT_OCR_SERVICE_HOST)
    parser.add_argument("--port", default=DEFAULT_OCR_SERVICE_PORT, type=int)
    arguments = parser.parse_args()
    settings = OcrProcessSettings()
    run(
        host=arguments.host,
        port=arguments.port,
        service_token=settings.ocr_service_token.get_secret_value(),
        workspace_root=settings.ocr_workspace_root.resolve(),
        adapter=IsolatedOcrAdapter(
            OcrSubprocessAdapter(
                settings.ocr_python_executable.resolve(),
                timeout_seconds=settings.ocr_timeout_seconds,
                pipeline_version=settings.ocr_pipeline_version,
                device=settings.ocr_device,
                cpu_threads=settings.ocr_cpu_threads,
            ),
            process_count=settings.ocr_process_count,
        ),
        ownership_path=settings.ocr_ownership_path.resolve(),
    )


if __name__ == "__main__":
    run_with_startup_diagnostics("ocr", main)
