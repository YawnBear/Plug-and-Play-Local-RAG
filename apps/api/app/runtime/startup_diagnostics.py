from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

SCHEMA_VERSION = 1
DIAGNOSTIC_FILENAME = "startup-failure.json"
MAXIMUM_CHAIN_LENGTH = 8
MAXIMUM_VALIDATION_ERRORS = 32
MAXIMUM_TEXT_LENGTH = 128


def _bounded_text(value: object) -> str:
    return str(value)[:MAXIMUM_TEXT_LENGTH]


def _exception_item(error: BaseException) -> dict[str, object]:
    item: dict[str, object] = {"type": type(error).__name__}
    if isinstance(error, OSError):
        if isinstance(error.errno, int):
            item["errno"] = error.errno
        winerror = getattr(error, "winerror", None)
        if isinstance(winerror, int):
            item["winerror"] = winerror
    if isinstance(error, ModuleNotFoundError) and error.name:
        item["module"] = _bounded_text(error.name)

    errors = getattr(error, "errors", None)
    if type(error).__name__ == "ValidationError" and callable(errors):
        try:
            validation_errors = errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )
        except (TypeError, ValueError):
            validation_errors = ()
        sanitized: list[dict[str, object]] = []
        for validation_error in validation_errors[:MAXIMUM_VALIDATION_ERRORS]:
            if not isinstance(validation_error, dict):
                continue
            location = validation_error.get("loc", ())
            if not isinstance(location, (list, tuple)):
                location = ()
            sanitized.append(
                {
                    "location": [_bounded_text(part) for part in location[:8]],
                    "type": _bounded_text(validation_error.get("type", "unknown")),
                }
            )
        if sanitized:
            item["validation"] = sanitized
    return item


def failure_payload(service: str, error: BaseException) -> dict[str, object]:
    chain: list[dict[str, object]] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while (
        current is not None
        and len(chain) < MAXIMUM_CHAIN_LENGTH
        and id(current) not in seen
    ):
        seen.add(id(current))
        chain.append(_exception_item(current))
        current = current.__cause__ or current.__context__
    return {
        "schema_version": SCHEMA_VERSION,
        "service": _bounded_text(service),
        "exception_chain": chain,
    }


def diagnostic_path() -> Path:
    temporary_directory = os.environ.get("TEMP") or os.environ.get("TMP")
    if not temporary_directory:
        raise RuntimeError("TEMP or TMP is required for startup diagnostics")
    return Path(temporary_directory) / DIAGNOSTIC_FILENAME


def _remove_diagnostic(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_diagnostic(path: Path, payload: dict[str, object]) -> None:
    temporary_path = path.with_name(f"{path.name}.tmp")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        path.parent.mkdir(parents=False, exist_ok=True)
        temporary_path.write_bytes(encoded)
        temporary_path.replace(path)
    except OSError:
        _remove_diagnostic(temporary_path)


def record_startup_stage(service: str, stage: str) -> None:
    _write_diagnostic(
        diagnostic_path(),
        {
            "schema_version": SCHEMA_VERSION,
            "service": _bounded_text(service),
            "startup_stage": _bounded_text(stage),
        },
    )


def run_with_startup_diagnostics[Result](
    service: str,
    callback: Callable[[], Result],
) -> Result:
    path = diagnostic_path()
    _remove_diagnostic(path)
    try:
        return callback()
    except BaseException as error:
        _write_diagnostic(path, failure_payload(service, error))
        raise
