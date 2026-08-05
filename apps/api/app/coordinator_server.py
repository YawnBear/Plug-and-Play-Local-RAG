import argparse
import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

from app.processes.settings import CoordinatorProcessSettings
from app.runtime.coordinator import (
    CoordinatorAdapter,
    create_coordinator_app,
)
from app.runtime.coordinator_adapter import ModelCoordinatorAdapter
from app.runtime.managed_uvicorn import run_managed_uvicorn
from app.runtime.network import (
    DEFAULT_COORDINATOR_HOST,
    DEFAULT_COORDINATOR_PORT,
    require_loopback_host,
)
from app.runtime.ownership import SingleInstanceOwnership
from app.runtime.startup_diagnostics import (
    record_startup_stage,
    run_with_startup_diagnostics,
)
from app.services.ollama_embeddings import OllamaEmbeddingClient
from app.services.ollama_generation import OllamaGenerationClient
from app.services.reranker import BgeReranker


def run(
    *,
    host: str,
    port: int,
    service_token: str,
    adapter: CoordinatorAdapter,
    ownership_path: Path,
) -> None:
    record_startup_stage("inference", "validate_server")
    require_loopback_host(host)
    if not 1 <= port <= 65535:
        raise ValueError("port must be from 1 to 65535")
    record_startup_stage("inference", "create_app")
    application = create_coordinator_app(
        service_token=service_token,
        adapter=adapter,
    )
    loop: str | Callable[[], asyncio.AbstractEventLoop] = (
        asyncio.SelectorEventLoop if sys.platform == "win32" else "auto"
    )
    record_startup_stage("inference", "acquire_ownership")
    with SingleInstanceOwnership.acquire(ownership_path):
        record_startup_stage("inference", "serve")
        run_managed_uvicorn(
            service="inference",
            application=application,
            host=host,
            port=port,
            loop=loop,
        )


def main() -> None:
    record_startup_stage("inference", "parse_arguments")
    parser = argparse.ArgumentParser(description="Run the local inference coordinator")
    parser.add_argument("--host", default=DEFAULT_COORDINATOR_HOST)
    parser.add_argument("--port", default=DEFAULT_COORDINATOR_PORT, type=int)
    arguments = parser.parse_args()
    record_startup_stage("inference", "load_settings")
    settings = CoordinatorProcessSettings()
    record_startup_stage("inference", "construct_adapter")
    adapter = ModelCoordinatorAdapter(
        OllamaEmbeddingClient(str(settings.ollama_base_url), settings.embedding_model),
        BgeReranker(str(settings.reranker_model_path.resolve())),
        OllamaGenerationClient(
            str(settings.ollama_base_url),
            settings.generation_model,
            context_size=settings.maximum_generation_context,
            output_tokens=settings.maximum_generation_output,
            timeout_seconds=settings.generation_timeout_seconds,
        ),
    )
    run(
        host=arguments.host,
        port=arguments.port,
        service_token=settings.coordinator_service_token.get_secret_value(),
        adapter=adapter,
        ownership_path=settings.coordinator_ownership_path.resolve(),
    )


if __name__ == "__main__":
    run_with_startup_diagnostics("inference", main)
