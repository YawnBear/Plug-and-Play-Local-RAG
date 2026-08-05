from pathlib import Path


def test_restart_scoped_runtime_changes_do_not_require_backup_evidence() -> None:
    source = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0014_restart_without_backup.py"
    ).read_text(encoding="utf-8")

    assert "ALTER COLUMN backup_run_id DROP NOT NULL" in source
    assert "preview.operation_class <> 'restart_scoped'" in source
    assert "preview.id, NULL, p_impact_digest" in source
    assert "'backup_required', false" in source
    assert "a current restore-verified backup is required" not in source
