import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.runtime import startup_bootstrap


def test_bootstrap_records_import_and_runs_allowlisted_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[list[str]] = []
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(
        startup_bootstrap.importlib,
        "import_module",
        lambda name: SimpleNamespace(main=lambda: events.append(sys.argv.copy())),
    )

    startup_bootstrap.main(["inference", "--host", "127.0.0.1", "--port", "8100"])

    assert events == [
        ["app.coordinator_server", "--host", "127.0.0.1", "--port", "8100"]
    ]
    assert json.loads(
        (tmp_path / "startup-failure.json").read_text(encoding="utf-8")
    ) == {
        "schema_version": 1,
        "service": "inference",
        "startup_stage": "run_main",
    }


def test_bootstrap_captures_import_failure_without_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "import-error-secret"
    monkeypatch.setenv("TEMP", str(tmp_path))

    def fail_import(_name: str) -> None:
        raise ImportError(secret)

    monkeypatch.setattr(startup_bootstrap.importlib, "import_module", fail_import)

    with pytest.raises(ImportError, match=secret):
        startup_bootstrap.main(["inference"])

    raw = (tmp_path / "startup-failure.json").read_text(encoding="utf-8")
    assert secret not in raw
    assert json.loads(raw)["exception_chain"] == [{"type": "ImportError"}]
