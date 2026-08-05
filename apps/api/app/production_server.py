import argparse
import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path

from app.main import create_app
from app.runtime.managed_uvicorn import run_managed_uvicorn
from app.runtime.network import require_loopback_host
from app.runtime.startup_diagnostics import run_with_startup_diagnostics

TLS_CERT_PATH_ENVIRONMENT = "RAG_API_TLS_CERT_PATH"
TLS_KEY_PATH_ENVIRONMENT = "RAG_API_TLS_KEY_PATH"
CLIENT_CA_PATH_ENVIRONMENT = "RAG_API_CLIENT_CA_PATH"


def _required_file(environment_name: str) -> Path:
    value = os.environ.get(environment_name, "")
    path = Path(value)
    if not value or not path.is_absolute() or not path.is_file():
        raise RuntimeError(f"{environment_name} must name an existing absolute file")
    return path.resolve()


def run(
    *,
    host: str,
    port: int,
    certificate: Path,
    private_key: Path,
    client_ca: Path,
) -> None:
    require_loopback_host(host)
    if not 1 <= port <= 65535:
        raise ValueError("port must be from 1 to 65535")
    for path in (certificate, private_key, client_ca):
        if not path.is_absolute() or not path.is_file():
            raise ValueError("TLS paths must be existing absolute files")
    loop: str | Callable[[], asyncio.AbstractEventLoop] = (
        asyncio.SelectorEventLoop if sys.platform == "win32" else "auto"
    )
    run_managed_uvicorn(
        service="api",
        application=create_app(),
        host=host,
        port=port,
        loop=loop,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        certificate=certificate,
        private_key=private_key,
        client_ca=client_ca,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the production loopback API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8443, type=int)
    arguments = parser.parse_args()
    run(
        host=arguments.host,
        port=arguments.port,
        certificate=_required_file(TLS_CERT_PATH_ENVIRONMENT),
        private_key=_required_file(TLS_KEY_PATH_ENVIRONMENT),
        client_ca=_required_file(CLIENT_CA_PATH_ENVIRONMENT),
    )


if __name__ == "__main__":
    run_with_startup_diagnostics("api", main)
