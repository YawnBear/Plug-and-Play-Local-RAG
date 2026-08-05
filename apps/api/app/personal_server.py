import argparse
import asyncio
import sys
from collections.abc import Callable

from app.main import create_app
from app.runtime.managed_uvicorn import run_managed_uvicorn
from app.runtime.network import require_loopback_host
from app.runtime.startup_diagnostics import run_with_startup_diagnostics


def run(*, host: str, port: int) -> None:
    require_loopback_host(host)
    if not 1 <= port <= 65535:
        raise ValueError("port must be from 1 to 65535")
    application = create_app()
    if application.state.settings.product_profile != "personal":
        raise RuntimeError("the plaintext loopback server is Personal-only")
    loop: str | Callable[[], asyncio.AbstractEventLoop] = (
        asyncio.SelectorEventLoop if sys.platform == "win32" else "auto"
    )
    run_managed_uvicorn(
        service="api",
        application=application,
        host=host,
        port=port,
        loop=loop,
        proxy_headers=False,
        forwarded_allow_ips="127.0.0.1",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Personal loopback API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    arguments = parser.parse_args()
    run(host=arguments.host, port=arguments.port)


if __name__ == "__main__":
    run_with_startup_diagnostics("api", main)
