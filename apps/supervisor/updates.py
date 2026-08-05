from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .backup import BackupPair, restore_verification_status


class UpdateError(ValueError):
    """Signed update validation failure."""


@dataclass(frozen=True, slots=True)
class UpdateArtifact:
    filename: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class VerifiedUpdate:
    version: str
    manifest_sha256: str
    artifacts: tuple[UpdateArtifact, ...]
    stage_directory: Path


ARTIFACT_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def verify_update(
    manifest_path: Path,
    signature_path: Path,
    artifact_root: Path,
    allowed_signers_path: Path,
    pinned_public_key_sha256: str,
    stage_root: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> VerifiedUpdate:
    if manifest_path.name != "update-manifest.json":
        raise UpdateError("signed manifest filename must be update-manifest.json")
    if signature_path.name != "update-manifest.json.sig":
        raise UpdateError("signature filename must be update-manifest.json.sig")
    if (
        manifest_path.is_symlink()
        or signature_path.is_symlink()
        or allowed_signers_path.is_symlink()
        or not manifest_path.is_file()
        or not signature_path.is_file()
        or not allowed_signers_path.is_file()
    ):
        raise UpdateError("update trust and manifest inputs must be regular files")
    if not _valid_sha256(pinned_public_key_sha256):
        raise UpdateError("pinned update trust checksum is invalid")
    manifest_bytes = _read_stable_regular(manifest_path, 1024 * 1024)
    signature_bytes = _read_stable_regular(signature_path, 1024 * 1024)
    allowed_signers_bytes = _read_stable_regular(allowed_signers_path, 1024 * 1024)
    if hashlib.sha256(allowed_signers_bytes).hexdigest() != pinned_public_key_sha256:
        raise UpdateError("offline update trust key does not match its pinned checksum")
    stage = _create_immutable_stage(stage_root)
    staged_manifest = _stage_bytes(stage, manifest_path.name, manifest_bytes)
    staged_signature = _stage_bytes(stage, signature_path.name, signature_bytes)
    staged_allowed_signers = _stage_bytes(
        stage, "allowed_signers", allowed_signers_bytes
    )
    try:
        preliminary = json.loads(
            manifest_bytes, object_pairs_hook=_reject_duplicate_pairs
        )
    except json.JSONDecodeError as exc:
        raise UpdateError("signed update manifest is invalid JSON") from exc
    if not isinstance(preliminary, dict):
        raise UpdateError("signed update manifest fields are invalid")
    preliminary_artifacts = preliminary.get("artifacts")
    if not isinstance(preliminary_artifacts, list) or not preliminary_artifacts:
        raise UpdateError("update manifest must contain artifacts")
    root = artifact_root.resolve(strict=True)
    preliminary_seen: set[str] = set()
    for item in preliminary_artifacts:
        if not isinstance(item, dict) or set(item) != {"filename", "sha256", "size"}:
            raise UpdateError("update artifact fields are invalid")
        filename = item["filename"]
        expected_size = item["size"]
        if filename in preliminary_seen:
            raise UpdateError("update artifact filename is unsafe or duplicated")
        if (
            not isinstance(filename, str)
            or ARTIFACT_FILENAME_PATTERN.fullmatch(filename) is None
        ):
            raise UpdateError("update artifact filename is unsafe or duplicated")
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 1
        ):
            raise UpdateError("update artifact preflight is invalid")
        preliminary_seen.add(filename)
        source = (root / filename).resolve(strict=True)
        if source.parent != root:
            raise UpdateError("update artifact path is unsafe")
        _stage_bytes(
            stage,
            filename,
            _read_stable_regular(source, expected_size),
        )
    _freeze_stage(stage)
    staged_manifest_bytes = _read_stable_regular(staged_manifest, 1024 * 1024)
    if (
        staged_manifest_bytes != manifest_bytes
        or _read_stable_regular(staged_signature, 1024 * 1024) != signature_bytes
        or _read_stable_regular(staged_allowed_signers, 1024 * 1024)
        != allowed_signers_bytes
    ):
        raise UpdateError("immutable staged update trust bytes changed")
    try:
        system_root = _windows_system_directory().resolve(strict=True)
        ssh_keygen = (system_root / "OpenSSH" / "ssh-keygen.exe").resolve(strict=True)
    except OSError as exc:
        raise UpdateError("trusted Windows OpenSSH verifier is unavailable") from exc
    if (
        ssh_keygen.parent != system_root / "OpenSSH"
        or ssh_keygen.is_symlink()
        or not ssh_keygen.is_file()
    ):
        raise UpdateError("trusted Windows OpenSSH verifier is unavailable")
    command = [
        str(ssh_keygen),
        "-Y",
        "verify",
        "-f",
        str(staged_allowed_signers),
        "-I",
        "rag-release",
        "-n",
        "file",
        "-s",
        str(staged_signature),
    ]
    try:
        result = run(
            command,
            input=staged_manifest_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise UpdateError("Windows OpenSSH Ed25519 verifier is unavailable") from exc
    if result.returncode != 0:
        raise UpdateError("Ed25519 update signature verification failed")
    try:
        value = json.loads(
            staged_manifest_bytes, object_pairs_hook=_reject_duplicate_pairs
        )
    except json.JSONDecodeError as exc:
        raise UpdateError("signed update manifest is invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "version",
        "artifacts",
    }:
        raise UpdateError("signed update manifest fields are invalid")
    if value["schema_version"] != 1:
        raise UpdateError("signed update manifest schema is unsupported")
    version = value["version"]
    if (
        not isinstance(version, str)
        or not version
        or len(version) > 64
        or any(character.isspace() for character in version)
    ):
        raise UpdateError("update version is invalid")
    artifacts_value = value["artifacts"]
    if not isinstance(artifacts_value, list) or not artifacts_value:
        raise UpdateError("update manifest must contain artifacts")
    artifacts: list[UpdateArtifact] = []
    seen: set[str] = set()
    for item in artifacts_value:
        if not isinstance(item, dict) or set(item) != {"filename", "sha256", "size"}:
            raise UpdateError("update artifact fields are invalid")
        filename = item["filename"]
        expected_sha = item["sha256"]
        expected_size = item["size"]
        if (
            not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
            or ARTIFACT_FILENAME_PATTERN.fullmatch(filename) is None
            or filename in seen
        ):
            raise UpdateError("update artifact filename is unsafe or duplicated")
        if not _valid_sha256(expected_sha):
            raise UpdateError("update artifact checksum is invalid")
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 1
        ):
            raise UpdateError("update artifact size is invalid")
        staged_path = stage / filename
        if (
            staged_path.stat().st_size != expected_size
            or _sha256_file(staged_path) != expected_sha
        ):
            raise UpdateError(f"update artifact verification failed: {filename}")
        seen.add(filename)
        artifacts.append(UpdateArtifact(filename, expected_sha, expected_size))
    return VerifiedUpdate(
        version=version,
        manifest_sha256=hashlib.sha256(staged_manifest_bytes).hexdigest(),
        artifacts=tuple(artifacts),
        stage_directory=stage,
    )


def build_admin_install_plan(
    update: VerifiedUpdate,
    backup: BackupPair | None,
) -> dict[str, object]:
    del update
    evidence = restore_verification_status()
    if backup is None or not backup.verified:
        raise UpdateError(
            "admin update is blocked until authenticated restore-verified paired "
            "backup evidence exists"
        )
    raise UpdateError(
        "admin update is blocked because verifier-controlled backup evidence "
        f"is unavailable: {evidence['reason']}"
    )


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise UpdateError(f"signed update manifest contains duplicate field: {key}")
        value[key] = item
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_stable_regular(path: Path, maximum_size: int) -> bytes:
    before = path.stat(follow_symlinks=False)
    if (
        path.is_symlink()
        or not path.is_file()
        or bool(getattr(before, "st_file_attributes", 0) & 0x400)
        or before.st_size > maximum_size
    ):
        raise UpdateError("update input must be a bounded regular non-reparse file")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        value = stream.read(maximum_size + 1)
        completed = os.fstat(stream.fileno())
    after = path.stat(follow_symlinks=False)
    identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    if (
        len(value) > maximum_size
        or identity
        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        or identity
        != (
            completed.st_dev,
            completed.st_ino,
            completed.st_size,
            completed.st_mtime_ns,
        )
        or identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise UpdateError("update input changed while it was staged")
    return value


def _create_immutable_stage(stage_root: Path) -> Path:
    root = stage_root.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise UpdateError("update stage root must be a regular directory")
    stage = root / f"update-{uuid.uuid4().hex}"
    stage.mkdir(mode=0o700)
    _protect_stage(stage, writable=True)
    return stage


def _stage_bytes(stage: Path, filename: str, value: bytes) -> Path:
    destination = stage / filename
    with destination.open("xb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    destination.chmod(0o400)
    if destination.read_bytes() != value:
        raise UpdateError("immutable staged update bytes changed")
    return destination


def _freeze_stage(stage: Path) -> None:
    _protect_stage(stage, writable=False)


def _protect_stage(stage: Path, *, writable: bool) -> None:
    if os.name != "nt":
        stage.chmod(0o700 if writable else 0o500)
        return
    icacls = _windows_system_directory() / "icacls.exe"
    current_sid = (
        subprocess.run(
            [
                str(_windows_system_directory() / "whoami.exe"),
                "/user",
                "/fo",
                "csv",
                "/nh",
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        .stdout.split(",")[-1]
        .strip()
        .strip('"')
    )
    rights = "M" if writable else "RX"
    result = subprocess.run(
        [
            str(icacls),
            str(stage),
            "/inheritance:r",
            "/grant:r",
            f"*S-1-5-18:(OI)(CI)({rights})",
            f"*S-1-5-32-544:(OI)(CI)({rights})",
            f"*{current_sid}:(OI)(CI)({rights})",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise UpdateError("could not protect immutable update stage")


def _windows_system_directory() -> Path:
    if os.name != "nt":
        raise UpdateError("Windows System32 OpenSSH verifier is required")
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if length == 0 or length >= len(buffer):
        raise UpdateError("cannot resolve the Windows system directory")
    return Path(buffer.value)
