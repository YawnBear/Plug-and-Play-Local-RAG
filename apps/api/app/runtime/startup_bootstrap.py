from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence

from app.runtime.startup_diagnostics import (
    record_startup_stage,
    run_with_startup_diagnostics,
)

ENTRYPOINTS = {
    "api": "app.production_server",
    "ingestion": "app.processes.ingestion_worker",
    "deletion": "app.processes.deletion_worker",
    "inference": "app.coordinator_server",
    "ocr": "app.ocr_service_server",
}


def _load_entrypoint(service: str, arguments: Sequence[str]) -> Callable[[], None]:
    record_startup_stage(service, "import_entrypoint")
    module = importlib.import_module(ENTRYPOINTS[service])
    main = getattr(module, "main", None)
    if not callable(main):
        raise RuntimeError("managed entrypoint has no callable main")
    sys.argv = [ENTRYPOINTS[service], *arguments]
    record_startup_stage(service, "run_main")
    return main


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a diagnosed managed process")
    parser.add_argument("service", choices=tuple(ENTRYPOINTS))
    parsed, entrypoint_arguments = parser.parse_known_args(arguments)

    def run() -> None:
        _load_entrypoint(parsed.service, entrypoint_arguments)()

    run_with_startup_diagnostics(parsed.service, run)


if __name__ == "__main__":
    main()
