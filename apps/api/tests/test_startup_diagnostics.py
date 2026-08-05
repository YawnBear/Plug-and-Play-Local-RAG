import json
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

from app.runtime.startup_diagnostics import run_with_startup_diagnostics


class _DiagnosticSettings(BaseModel):
    service_token: SecretStr
    positive_count: int


def test_startup_diagnostic_records_only_safe_validation_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "diagnostic-secret-must-not-appear"
    monkeypatch.setenv("TEMP", str(tmp_path))

    def fail_validation() -> None:
        try:
            _DiagnosticSettings.model_validate(
                {"service_token": secret, "positive_count": "not-an-integer"}
            )
        except ValidationError as error:
            raise RuntimeError(f"unsafe wrapper includes {secret}") from error

    with pytest.raises(RuntimeError, match="unsafe wrapper"):
        run_with_startup_diagnostics("inference", fail_validation)

    raw = (tmp_path / "startup-failure.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert secret not in raw
    assert "unsafe wrapper" not in raw
    assert payload["service"] == "inference"
    assert payload["exception_chain"][0] == {"type": "RuntimeError"}
    validation = payload["exception_chain"][1]
    assert validation["type"] == "ValidationError"
    assert validation["validation"] == [
        {"location": ["positive_count"], "type": "int_parsing"}
    ]


def test_startup_diagnostic_is_cleared_before_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "startup-failure.json"
    path.write_text('{"stale":true}', encoding="utf-8")
    monkeypatch.setenv("TEMP", str(tmp_path))

    result = run_with_startup_diagnostics("api", lambda: "ready")

    assert result == "ready"
    assert not path.exists()


def test_startup_diagnostic_does_not_mask_original_when_temp_is_unwritable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / "nested"
    monkeypatch.setenv("TEMP", str(missing))

    with pytest.raises(ValueError, match="original failure"):
        run_with_startup_diagnostics(
            "ocr",
            lambda: (_ for _ in ()).throw(ValueError("original failure")),
        )
