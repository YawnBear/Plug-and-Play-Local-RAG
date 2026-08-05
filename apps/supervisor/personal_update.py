from __future__ import annotations

import json
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from .updates import UpdateError, verify_update
from .v8a_trust import TrustMetadataError, verify_trust_metadata

PERSONAL_ARTIFACTS = frozenset(
    {
        "Local-RAG-Personal.zip",
        "SBOM.cdx.json",
        "SHA256SUMS",
        "release-trust-metadata.json",
        "Verify-and-Install-Local-RAG.ps1",
        "Install-Local-RAG.cmd",
    }
)
TRUST_ROOT_ID = "rag-root-v8"
MAX_ARCHIVE_BYTES = 24 * 1024**3
MAX_EXTRACTED_BYTES = 48 * 1024**3
SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class VerifiedPersonalUpdate:
    version: str
    release_id: str
    release_sequence: int
    expected_alembic_revision: str
    candidate_root: Path
    manifest_sha256: str
    trust_metadata_sha256: str


def verify_personal_update(
    *,
    manifest_path: Path,
    signature_path: Path,
    artifact_root: Path,
    allowed_signers_path: Path,
    pinned_public_key_sha256: str,
    stage_root: Path,
    candidate_root: Path,
    installed_release_sequence: int,
    installed_release_id: str | None,
    now: datetime | None = None,
) -> VerifiedPersonalUpdate:
    verified = verify_update(
        manifest_path,
        signature_path,
        artifact_root,
        allowed_signers_path,
        pinned_public_key_sha256,
        stage_root,
    )
    names = frozenset(item.filename for item in verified.artifacts)
    if names != PERSONAL_ARTIFACTS:
        raise UpdateError("Personal update artifact set is not exact")
    staged = verified.stage_directory
    _verify_checksums(staged / "SHA256SUMS", staged)
    _verify_sbom(staged / "SBOM.cdx.json")
    trust_path = staged / "release-trust-metadata.json"
    try:
        trust = verify_trust_metadata(
            trust_path,
            authenticated_root_id=TRUST_ROOT_ID,
            installed_release_sequence=installed_release_sequence,
            installed_release_id=installed_release_id,
            now=now,
        )
    except TrustMetadataError as exc:
        raise UpdateError(str(exc)) from exc
    destination = candidate_root.resolve()
    if destination.exists():
        raise UpdateError("Personal update candidate already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _extract_archive(staged / "Local-RAG-Personal.zip", destination)
    contract_root = destination / "ops" / "windows" / "v8a"
    for name, expected in trust.artifacts_sha256.items():
        path = contract_root / name
        if _sha256_regular(path) != expected:
            shutil.rmtree(destination, ignore_errors=True)
            raise UpdateError(f"Personal release contract hash mismatch: {name}")
    release = _read_json(contract_root / "personal-release.json")
    expected_revision = release.get("expected_alembic_revision")
    if (
        release.get("profile_id") != "personal"
        or release.get("payload_state") != "packaged"
        or not isinstance(expected_revision, str)
        or re.fullmatch(r"[0-9]{4}_[a-z0-9_]+", expected_revision) is None
    ):
        shutil.rmtree(destination, ignore_errors=True)
        raise UpdateError("Personal packaged release contract is invalid")
    return VerifiedPersonalUpdate(
        version=verified.version,
        release_id=trust.release_id,
        release_sequence=trust.release_sequence,
        expected_alembic_revision=expected_revision,
        candidate_root=destination,
        manifest_sha256=verified.manifest_sha256,
        trust_metadata_sha256=_sha256_regular(trust_path),
    )


def _verify_checksums(path: Path, root: Path) -> None:
    lines = _read_regular(path, 1024 * 1024).decode("ascii").splitlines()
    expected_names = PERSONAL_ARTIFACTS - {"SHA256SUMS"}
    observed: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if match is None or match.group(2) in observed:
            raise UpdateError("Personal release checksum file is invalid")
        observed[match.group(2)] = match.group(1)
    if set(observed) != expected_names:
        raise UpdateError("Personal release checksum set is not exact")
    for name, digest in observed.items():
        if _sha256_regular(root / name) != digest:
            raise UpdateError(f"Personal release checksum mismatch: {name}")


def _verify_sbom(path: Path) -> None:
    value = _read_json(path)
    if (
        value.get("bomFormat") != "CycloneDX"
        or value.get("specVersion") != "1.6"
        or value.get("version") != 1
        or not isinstance(value.get("components"), list)
        or not value["components"]
    ):
        raise UpdateError("Personal release SBOM is invalid")


def _extract_archive(path: Path, destination: Path) -> None:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise UpdateError("Personal release archive is too large")
    total = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if not members:
                raise UpdateError("Personal release archive is empty")
            for member in members:
                name = PurePosixPath(member.filename)
                normalized = name.as_posix().rstrip("/")
                if (
                    name.is_absolute()
                    or not normalized
                    or ".." in name.parts
                    or "\\" in member.filename
                    or ":" in member.filename
                    or normalized.casefold() in seen
                ):
                    raise UpdateError("Personal release archive path is unsafe")
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise UpdateError("Personal release archive contains a link")
                seen.add(normalized.casefold())
                total += member.file_size
                if total > MAX_EXTRACTED_BYTES:
                    raise UpdateError("Personal release expands beyond the size limit")
            destination.mkdir(parents=False)
            for member in members:
                target = destination.joinpath(*PurePosixPath(member.filename).parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
    except (OSError, zipfile.BadZipFile) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise UpdateError("Personal release archive could not be extracted") from exc


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(_read_regular(path, 8 * 1024 * 1024))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Personal release JSON is invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise UpdateError(f"Personal release JSON is invalid: {path.name}")
    return value


def _read_regular(path: Path, limit: int) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise UpdateError(f"Personal release file is invalid: {path.name}")
    before = path.stat()
    if before.st_size > limit:
        raise UpdateError(f"Personal release file is too large: {path.name}")
    value = path.read_bytes()
    after = path.stat()
    if (
        before.st_size != len(value)
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise UpdateError(f"Personal release file changed while reading: {path.name}")
    return value


def _sha256_regular(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    value = digest.hexdigest()
    if SHA256.fullmatch(value) is None:
        raise AssertionError("SHA-256 implementation returned an invalid digest")
    return value
