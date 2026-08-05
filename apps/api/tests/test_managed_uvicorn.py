import asyncio
import ssl
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.runtime import managed_uvicorn


def test_managed_uvicorn_disables_console_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = Mock()
    config = Mock(return_value=configuration)
    server = Mock()
    diagnosed_server = Mock(return_value=server)
    monkeypatch.setattr(managed_uvicorn.uvicorn, "Config", config)
    monkeypatch.setattr(managed_uvicorn, "_DiagnosedServer", diagnosed_server)
    monkeypatch.setattr(managed_uvicorn, "record_startup_stage", Mock())
    application = Mock()

    managed_uvicorn.run_managed_uvicorn(
        service="inference",
        application=application,
        host="127.0.0.1",
        port=8100,
        loop=asyncio.SelectorEventLoop,
    )

    config.assert_called_once_with(
        application,
        host="127.0.0.1",
        port=8100,
        loop=asyncio.SelectorEventLoop,
        proxy_headers=True,
        forwarded_allow_ips=None,
        log_config=None,
        access_log=False,
        ssl_certfile=None,
        ssl_keyfile=None,
        ssl_ca_certs=None,
        ssl_cert_reqs=ssl.CERT_NONE,
    )
    diagnosed_server.assert_called_once_with(configuration, "inference")
    server.run.assert_called_once_with()


def test_managed_uvicorn_requires_complete_tls_tuple() -> None:
    with pytest.raises(ValueError, match="TLS paths"):
        managed_uvicorn.run_managed_uvicorn(
            service="api",
            application=Mock(),
            host="127.0.0.1",
            port=8443,
            loop=asyncio.SelectorEventLoop,
            certificate=Path("server.crt"),
        )
