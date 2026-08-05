from types import SimpleNamespace

import pytest

from app.maintenance_cli import main


class _StorageService:
    async def bootstrap_bucket(self) -> SimpleNamespace:
        return SimpleNamespace(created=True, detail="configured bucket created")

    async def audit_bucket(self, prefix: str) -> SimpleNamespace:
        assert prefix == "originals/"
        return SimpleNamespace(
            object_count=1001,
            byte_count=2002,
            missing_checksum_metadata=(),
            invalid_checksum_metadata=(),
            invalid_size_metadata=(),
        )


def _patch_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.maintenance_cli.get_maintenance_settings",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "app.maintenance_cli.S3ObjectStore.from_settings",
        lambda settings: object(),
    )
    monkeypatch.setattr(
        "app.maintenance_cli.StorageMaintenanceService",
        lambda store: _StorageService(),
    )


def test_storage_bootstrap_does_not_require_document_worker_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_storage(monkeypatch)

    assert main(["storage-bootstrap"]) == 0
    assert "configured bucket created" in capsys.readouterr().out


def test_storage_audit_reports_complete_inventory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_storage(monkeypatch)

    assert main(["storage-audit", "--prefix", "originals/"]) == 0
    output = capsys.readouterr().out
    assert "1001 object(s)" in output
    assert "missing checksum metadata=0" in output
    assert "invalid checksum metadata=0" in output
