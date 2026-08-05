from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.supervisor.personal_update import _extract_archive, verify_personal_update
from apps.supervisor.personal_update_check_cli import main as check_main
from apps.supervisor.updates import UpdateArtifact, UpdateError, VerifiedUpdate

ROOT = Path(__file__).resolve().parents[3]
V8A = ROOT / "ops" / "windows" / "v8a"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_personal_update_verifies_exact_release_and_extracts_candidate(
    tmp_path: Path,
) -> None:
    staged = tmp_path / "immutable-stage"
    payload = tmp_path / "payload"
    contracts = payload / "ops" / "windows" / "v8a"
    contracts.mkdir(parents=True)
    for name in ("product-profiles.json", "capability-profiles.json"):
        (contracts / name).write_bytes((V8A / name).read_bytes())
    release = json.loads((V8A / "personal-release.json").read_text("utf-8"))
    release["payload_state"] = "packaged"
    (contracts / "personal-release.json").write_text(
        json.dumps(release), encoding="utf-8"
    )
    staged.mkdir()
    archive = staged / "Local-RAG-Personal.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in payload.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(payload).as_posix())
    (staged / "SBOM.cdx.json").write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "components": [{"name": "test"}],
            }
        ),
        encoding="utf-8",
    )
    (staged / "release-trust-metadata.json").write_text("{}", encoding="utf-8")
    (staged / "Verify-and-Install-Local-RAG.ps1").write_text(
        "# test\n", encoding="utf-8"
    )
    (staged / "Install-Local-RAG.cmd").write_text("@echo off\n", encoding="utf-8")
    checksummed = (
        "Local-RAG-Personal.zip",
        "SBOM.cdx.json",
        "release-trust-metadata.json",
        "Verify-and-Install-Local-RAG.ps1",
        "Install-Local-RAG.cmd",
    )
    (staged / "SHA256SUMS").write_text(
        "".join(f"{_sha(staged / name)}  {name}\n" for name in checksummed),
        encoding="ascii",
    )
    artifacts = tuple(
        UpdateArtifact(name, _sha(staged / name), (staged / name).stat().st_size)
        for name in (*checksummed, "SHA256SUMS")
    )
    update = VerifiedUpdate("8.0.1", "a" * 64, artifacts, staged)
    trust = SimpleNamespace(
        release_id="personal-8.0.1",
        release_sequence=2,
        artifacts_sha256={
            name: _sha(contracts / name)
            for name in (
                "personal-release.json",
                "product-profiles.json",
                "capability-profiles.json",
            )
        },
    )
    with (
        patch("apps.supervisor.personal_update.verify_update", return_value=update),
        patch(
            "apps.supervisor.personal_update.verify_trust_metadata",
            return_value=trust,
        ),
    ):
        result = verify_personal_update(
            manifest_path=tmp_path / "update-manifest.json",
            signature_path=tmp_path / "update-manifest.json.sig",
            artifact_root=tmp_path,
            allowed_signers_path=tmp_path / "allowed_signers",
            pinned_public_key_sha256="a" * 64,
            stage_root=tmp_path / "stages",
            candidate_root=tmp_path / "candidate",
            installed_release_sequence=1,
            installed_release_id="personal-8.0.0",
            now=datetime.now(UTC),
        )
    assert result.release_sequence == 2
    assert result.expected_alembic_revision == "0014_restart_without_backup"
    assert (result.candidate_root / "ops/windows/v8a/personal-release.json").is_file()


def test_personal_update_archive_rejects_path_escape(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../outside.txt", "bad")
    with pytest.raises(UpdateError, match="unsafe"):
        _extract_archive(archive, tmp_path / "candidate")
    assert not (tmp_path / "outside.txt").exists()


def test_personal_update_discovery_checks_freshness_and_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    trust = tmp_path / "release-trust-metadata.json"
    trust.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "policy_id": "local-rag-v8-release-trust",
                "root_id": "rag-root-v8",
                "release_id": "personal-8.0.2",
                "release_sequence": 2,
                "issued_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expires_at": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "artifacts_sha256": {
                    "personal-release.json": "a" * 64,
                    "product-profiles.json": "b" * 64,
                    "capability-profiles.json": "c" * 64,
                },
                "revoked_release_ids": [],
                "revoked_profile_ids": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "personal-update-check",
            "--trust-metadata",
            str(trust),
            "--installed-state",
            str(tmp_path / "missing-state.json"),
        ],
    )
    check_main()
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "release_id": "personal-8.0.2",
        "release_sequence": 2,
        "result": "available",
    }
