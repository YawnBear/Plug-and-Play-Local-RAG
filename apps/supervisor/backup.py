from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SYNTHETIC_GUARD = "RAG-V4-SYNTHETIC-BACKUP-FIXTURE-ONLY"
SYNTHETIC_MARKER = ".rag-v4-synthetic-backup-fixture.json"
RUN_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")

RESTORE_VERIFICATION_REQUIREMENTS = (
    "verifier-controlled authenticated run record",
    "PostgreSQL dump restored into an isolated cluster",
    "expected PostgreSQL catalogs and row counts",
    "expected roles, ownership, and grants",
    "expected RLS policies with FORCE ROW LEVEL SECURITY",
    "backup role read-only behavior and runtime-role denial",
    "session and activation-token invalidation",
    "exact RustFS object inventory, sizes, and SHA-256 hashes",
)


class BackupError(ValueError):
    """Backup ledger, evidence, or retention safety failure."""


@dataclass(frozen=True, slots=True)
class BackupPair:
    backup_id: str
    captured_at: datetime
    directory: Path
    database_sha256: str
    manifest_sha256: str
    verified: bool


def fixture_marker(run_token: str) -> dict[str, object]:
    _validate_run_token(run_token)
    return {
        "schema_version": 2,
        "guard": SYNTHETIC_GUARD,
        "synthetic": True,
        "run_token_sha256": hashlib.sha256(run_token.encode("utf-8")).hexdigest(),
    }


def assert_synthetic_store(
    root: Path,
    confirmation: str,
    *,
    run_token: str,
    allowed_temp_root: Path,
) -> Path:
    if confirmation != SYNTHETIC_GUARD:
        raise BackupError("exact synthetic fixture confirmation is required")
    _validate_run_token(run_token)
    try:
        resolved_temp = allowed_temp_root.resolve(strict=True)
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise BackupError("synthetic fixture path does not exist") from exc
    if (
        not resolved_temp.is_dir()
        or resolved == resolved_temp
        or not resolved.is_relative_to(resolved_temp)
        or resolved.parent == resolved
    ):
        raise BackupError("synthetic fixture must be a child of the allowed temp root")
    marker = resolved / SYNTHETIC_MARKER
    if not marker.is_file() or marker.is_symlink():
        raise BackupError("synthetic fixture marker is missing")
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("synthetic fixture marker is invalid") from exc
    if value != fixture_marker(run_token):
        raise BackupError("synthetic fixture marker is not bound to this run token")
    return resolved


def load_captured_pair(directory: Path) -> BackupPair:
    root = directory.resolve(strict=True)
    ledger_path = root / "pair-ledger.json"
    dump_path = root / "database.dump"
    manifest_path = root / "manifest.json"
    if any(
        path.is_symlink() or not path.is_file()
        for path in (ledger_path, dump_path, manifest_path)
    ):
        raise BackupError("paired backup files must be regular files")
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("pair ledger is invalid") from exc
    required = {
        "schema_version",
        "backup_id",
        "captured_at",
        "database_sha256",
        "manifest_sha256",
        "state",
        "verified",
        "restore_verification",
    }
    if not isinstance(ledger, dict) or set(ledger) != required:
        raise BackupError("pair ledger fields are invalid")
    if (
        ledger["schema_version"] != 2
        or ledger["state"] != "captured"
        or ledger["verified"] is not False
        or ledger["restore_verification"] is not None
    ):
        raise BackupError("capture ledger cannot claim restore verification")
    database_sha = _sha256_file(dump_path)
    manifest_sha = _sha256_file(manifest_path)
    if (
        database_sha != ledger["database_sha256"]
        or manifest_sha != ledger["manifest_sha256"]
    ):
        raise BackupError("paired backup checksum mismatch")
    try:
        captured = datetime.fromisoformat(ledger["captured_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise BackupError("captured_at is invalid") from exc
    if captured.tzinfo is None:
        raise BackupError("captured_at must include a timezone")
    backup_id = ledger["backup_id"]
    if not isinstance(backup_id, str) or not backup_id:
        raise BackupError("backup_id is invalid")
    return BackupPair(
        backup_id=backup_id,
        captured_at=captured.astimezone(UTC),
        directory=root,
        database_sha256=database_sha,
        manifest_sha256=manifest_sha,
        verified=False,
    )


def write_synthetic_pair_ledger(
    directory: Path,
    confirmation: str,
    *,
    run_token: str,
    allowed_temp_root: Path,
    backup_id: str,
    captured_at: datetime,
) -> Path:
    root = assert_synthetic_store(
        directory,
        confirmation,
        run_token=run_token,
        allowed_temp_root=allowed_temp_root,
    )
    if not backup_id or captured_at.tzinfo is None:
        raise BackupError(
            "backup identity and timezone-aware capture time are required"
        )
    dump_path = root / "database.dump"
    manifest_path = root / "manifest.json"
    if any(
        path.is_symlink() or not path.is_file() for path in (dump_path, manifest_path)
    ):
        raise BackupError("synthetic pair is incomplete")
    ledger_path = root / "pair-ledger.json"
    if ledger_path.exists():
        raise BackupError("pair ledger already exists")
    value = {
        "schema_version": 2,
        "backup_id": backup_id,
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "database_sha256": _sha256_file(dump_path),
        "manifest_sha256": _sha256_file(manifest_path),
        "state": "captured",
        "verified": False,
        "restore_verification": None,
    }
    temporary_path = root / ".pair-ledger.json.tmp"
    if temporary_path.exists():
        raise BackupError("temporary pair ledger already exists")
    temporary_path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary_path, ledger_path)
    return ledger_path


def retention_plan(
    pairs: list[BackupPair],
    *,
    fixture_root: Path,
    confirmation: str,
    run_token: str,
    allowed_temp_root: Path,
    daily: int = 7,
    weekly: int = 4,
) -> dict[str, object]:
    """Return a non-destructive plan for one guarded synthetic fixture."""
    resolved_fixture = assert_synthetic_store(
        fixture_root,
        confirmation,
        run_token=run_token,
        allowed_temp_root=allowed_temp_root,
    )
    if daily != 7 or weekly != 4:
        raise BackupError("V4 retention is fixed at seven daily and four weekly pairs")
    ids = [item.backup_id for item in pairs]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise BackupError("backup pair IDs must be nonempty and unique")
    ordered = sorted(pairs, key=lambda item: item.captured_at, reverse=True)
    for item in ordered:
        try:
            pair_directory = item.directory.resolve(strict=True)
        except OSError as exc:
            raise BackupError("backup pair directory does not exist") from exc
        if pair_directory == resolved_fixture or not pair_directory.is_relative_to(
            resolved_fixture
        ):
            raise BackupError("backup pair directory is outside the synthetic fixture")
    if any(item.verified for item in ordered):
        raise BackupError(
            "current synthetic scope cannot accept caller-asserted verified pairs"
        )
    keep: set[str] = {item.backup_id for item in ordered[:daily]}
    week_keys: set[tuple[int, int]] = set()
    for item in ordered:
        iso = item.captured_at.isocalendar()
        week_key = (iso.year, iso.week)
        if week_key in week_keys:
            continue
        week_keys.add(week_key)
        keep.add(item.backup_id)
        if len(week_keys) == weekly:
            break
    return {
        "state": "blocked_unverified",
        "retain": ids,
        "would_retain_after_authenticated_verification": [
            item.backup_id for item in ordered if item.backup_id in keep
        ],
        "eligible_for_atomic_rotation": [],
        "reason": "restore-verified authenticated evidence is unavailable",
    }


def restore_verification_status() -> dict[str, object]:
    return {
        "state": "blocked",
        "verified": False,
        "authenticated_evidence_available": False,
        "requirements": list(RESTORE_VERIFICATION_REQUIREMENTS),
        "reason": (
            "no verifier-controlled trust key, isolated restore run, or approved "
            "backup destination exists"
        ),
    }


def backup_status(destination: str | None) -> dict[str, Any]:
    if destination is None:
        return {
            "state": "not_configured",
            "schedule_enabled": False,
            "schedule": None,
            "destination": None,
            "reason": (
                "administrator has not approved an encrypted external destination"
            ),
        }
    return {
        "state": "configured",
        "schedule_enabled": False,
        "schedule": "02:00",
        "destination": destination,
        "reason": "enablement requires an attended encrypted-destination verification",
    }


def _validate_run_token(run_token: str) -> None:
    if not isinstance(run_token, str) or RUN_TOKEN_PATTERN.fullmatch(run_token) is None:
        raise BackupError(
            "run token must be a caller-supplied 32-128 character opaque value"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
