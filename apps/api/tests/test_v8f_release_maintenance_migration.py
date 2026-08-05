from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATION = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0011_v8f_release_maintenance.py"
)


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_v8f_release_maintenance_is_the_single_forward_head() -> None:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0014_restart_without_backup"]
    assert 'down_revision: str | None = "0010_versioned_reprocessing"' in _source()
    assert "forward-only; restore a paired pre-update backup" in _source()


def test_backup_history_is_admin_only_bounded_and_restore_aware() -> None:
    source = _source()

    assert "v11_admin_personal_backup_history" in source
    assert "IF p_limit < 1 OR p_limit > 100" in source
    assert "PERFORM public.v4_require_admin()" in source
    assert "public.backup_restore_verifications" in source
    assert (
        "GRANT EXECUTE ON FUNCTION "
        "public.v11_admin_personal_backup_history(integer)" in source
    )
    assert (
        "REVOKE ALL ON FUNCTION "
        "public.v11_admin_personal_backup_history(integer)" in source
    )


def test_readiness_tracks_the_v8f_revision_and_function_privileges() -> None:
    source = _source()

    assert "SELECT '0011_v8f_release_maintenance'::text" in source
    assert "WHERE version_num = '0011_v8f_release_maintenance'" in source
    assert "has_function_privilege('rag_api'" in source
    assert "NOT has_function_privilege('public'" in source
