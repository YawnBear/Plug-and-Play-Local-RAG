import asyncio

from app import dev_server


def test_windows_server_uses_selector_loop() -> None:
    assert dev_server.resolve_loop("win32") is asyncio.SelectorEventLoop


def test_non_windows_server_keeps_uvicorn_auto_loop() -> None:
    assert dev_server.resolve_loop("linux") == "auto"


def test_run_scopes_development_reload_to_application_directory(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def uvicorn_run(application: str, **kwargs: object) -> None:
        captured["application"] = application
        captured.update(kwargs)

    monkeypatch.setattr(dev_server.uvicorn, "run", uvicorn_run)
    monkeypatch.setattr(dev_server.sys, "platform", "win32")

    dev_server.run(host="127.0.0.1", port=8000, reload=True)

    assert captured == {
        "application": "app.main:app",
        "host": "127.0.0.1",
        "port": 8000,
        "reload": True,
        "reload_dirs": [str(dev_server.APPLICATION_DIRECTORY)],
        "loop": asyncio.SelectorEventLoop,
    }


def test_main_disables_reload_by_default(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def run(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(dev_server, "run", run)
    monkeypatch.setattr(dev_server.sys, "argv", ["app.dev_server"])

    dev_server.main()

    assert captured == {"host": "127.0.0.1", "port": 8000, "reload": False}


def test_main_allows_explicit_source_only_reload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def run(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(dev_server, "run", run)
    monkeypatch.setattr(dev_server.sys, "argv", ["app.dev_server", "--reload"])

    dev_server.main()

    assert captured == {"host": "127.0.0.1", "port": 8000, "reload": True}
