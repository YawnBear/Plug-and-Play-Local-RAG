from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATION = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0004_create_children_capability.py"
)


def test_create_children_migration_is_forward_only_head() -> None:
    api_root = Path(__file__).parents[1]
    scripts = ScriptDirectory.from_config(Config(str(api_root / "alembic.ini")))
    assert scripts.get_heads() == ["0014_restart_without_backup"]
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0003_v6_ingestion_version_guard"' in source
    assert "forward-only" in source


def test_capability_is_separate_folder_only_and_not_backfilled() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE public.folder_create_grants" in source
    assert "v4_require_folder_create_grant_target" in source
    assert "kind = 'folder'" in source
    assert "ALTER TABLE public.folder_create_grants FORCE ROW LEVEL SECURITY" in source
    assert "INSERT INTO public.folder_create_grants" in source
    assert (
        "INSERT INTO public.folder_create_grants"
        not in source.split("CREATE FUNCTION public.v4_admin_apply_acl", 1)[0]
    )


def test_controlled_creation_and_visibility_contract_are_database_enforced() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE FUNCTION public.v4_can_create_children" in source
    assert "public.v4_can_read_folder(p_folder_id)" in source
    assert "CREATE FUNCTION public.v4_create_folder" in source
    assert "p_parent_id IS NULL" in source
    assert "library exceeds maximum depth" in source
    assert "library exceeds maximum node count" in source
    assert "'folder_created'" in source
    assert "public.v4_can_read_folder(target.id)" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "TO rag_api" in source
    assert "REVOKE ALL ON TABLE public.folder_create_grants" in source
    assert "GRANT SELECT ON TABLE public.folder_create_grants TO rag_backup" in source
    assert (
        "GRANT EXECUTE ON FUNCTION public.v4_acl_impact(jsonb) TO rag_api" not in source
    )


def test_acl_and_user_lifecycle_include_create_grants() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert source.count("'set_create_children_grant'") >= 3
    assert "'direct_create_grants'" in source
    assert "'inherited_create_grants'" in source
    assert "DELETE FROM public.folder_create_grants" in source
    assert "'document_ids', '[]'::jsonb" in source
    for operation in (
        "set_grant",
        "set_membership",
        "set_team_active",
        "set_boundary",
        "move_node",
        "set_create_children_grant",
    ):
        assert f"v_kind = '{operation}'" in source
    assert "effective_before AS" in source
    assert "effective_after AS" in source
    assert "UNION SELECT folder_id FROM changed" in source
