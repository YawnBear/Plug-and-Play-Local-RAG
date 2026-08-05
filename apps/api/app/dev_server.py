import argparse
import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

import uvicorn

APPLICATION_DIRECTORY = Path(__file__).resolve().parent


def resolve_loop(platform: str) -> str | Callable[[], asyncio.AbstractEventLoop]:
    if platform == "win32":
        return asyncio.SelectorEventLoop
    return "auto"


def run(*, host: str, port: int, reload: bool) -> None:
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=[str(APPLICATION_DIRECTORY)] if reload else None,
        loop=resolve_loop(sys.platform),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RAG API development server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="watch the application source directory for Python changes",
    )
    arguments = parser.parse_args()
    run(host=arguments.host, port=arguments.port, reload=arguments.reload)


if __name__ == "__main__":
    main()
