from __future__ import annotations

import asyncio
import socket
import ssl
from collections.abc import Callable
from pathlib import Path

import uvicorn
from uvicorn._types import ASGIApplication

from app.runtime.startup_diagnostics import record_startup_stage

LoopFactory = str | Callable[[], asyncio.AbstractEventLoop]


class _DiagnosedServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, service: str) -> None:
        super().__init__(config)
        self._diagnostic_service = service

    async def serve(self, sockets: list[socket.socket] | None = None) -> None:
        record_startup_stage(self._diagnostic_service, "event_loop")
        await super().serve(sockets=sockets)

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        record_startup_stage(self._diagnostic_service, "lifespan_and_bind")
        await super().startup(sockets=sockets)
        record_startup_stage(self._diagnostic_service, "listening")


def run_managed_uvicorn(
    *,
    service: str,
    application: ASGIApplication,
    host: str,
    port: int,
    loop: LoopFactory,
    proxy_headers: bool = True,
    forwarded_allow_ips: str | None = None,
    certificate: Path | None = None,
    private_key: Path | None = None,
    client_ca: Path | None = None,
) -> None:
    tls_paths = (certificate, private_key, client_ca)
    if any(path is not None for path in tls_paths) and not all(
        path is not None for path in tls_paths
    ):
        raise ValueError("managed Uvicorn TLS paths must be supplied together")
    record_startup_stage(service, "configure_server")
    config = uvicorn.Config(
        application,
        host=host,
        port=port,
        loop=loop,
        proxy_headers=proxy_headers,
        forwarded_allow_ips=forwarded_allow_ips,
        log_config=None,
        access_log=False,
        ssl_certfile=str(certificate) if certificate is not None else None,
        ssl_keyfile=str(private_key) if private_key is not None else None,
        ssl_ca_certs=str(client_ca) if client_ca is not None else None,
        ssl_cert_reqs=ssl.CERT_REQUIRED if certificate is not None else ssl.CERT_NONE,
    )
    record_startup_stage(service, "construct_server")
    _DiagnosedServer(config, service).run()
