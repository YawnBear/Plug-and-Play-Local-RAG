from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).parents[1]
MIGRATION = API_ROOT / "alembic" / "versions" / "0005_document_reingest_action.py"


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_document_reingest_migration_is_forward_only_head() -> None:
    scripts = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    assert scripts.get_heads() == ["0014_restart_without_backup"]
    source = _source()
    assert 'down_revision: str | None = "0004_create_children_capability"' in source
    assert "forward-only" in source


def test_prepare_and_commit_are_fenced_actor_aware_commands() -> None:
    source = _source()
    assert "CREATE FUNCTION public.v4_prepare_document_reingest" in source
    assert "CREATE FUNCTION public.v4_commit_document_reingest" in source
    assert source.count("SECURITY DEFINER") >= 4
    assert source.count("SET search_path = pg_catalog") >= 4
    assert "node.uploader_user_id = v_actor" in source
    assert "public.v4_current_actor_is_admin()" in source
    assert "FOR UPDATE OF document, node" in source
    assert "FOR UPDATE OF job" in source
    assert "status IN ('queued', 'running')" in source
    assert "status NOT IN ('failed', 'interrupted')" in source
    assert "v_current_snapshot_token <> p_snapshot_token" in source
    assert source.count("INTO v_authorized") == 2
    assert "INTO v_document, v_uploader_user_id" not in source
    assert source.count("v_document := v_authorized.document_row") == 2
    assert source.count("v_uploader_user_id := v_authorized.uploader_user_id") == 2
    commit = source.split("CREATE FUNCTION public.v4_commit_document_reingest", 1)[1]
    assert commit.index("IF v_document.state <> 'failed'") < commit.index(
        "PERFORM job.id"
    )


def test_commit_preserves_versions_and_audits_content_free_identity() -> None:
    source = _source()
    commit = source.split("CREATE FUNCTION public.v4_commit_document_reingest", 1)[
        1
    ].split("REVOKE ALL ON FUNCTION", 1)[0]
    assert "DELETE FROM public.chunks" in commit
    assert "state = 'uploaded', stage = 'uploaded', error = NULL" in commit
    assert "attempt," in commit
    assert "'queued', 'uploaded', 0, 0, NULL" in commit
    assert "'document_reingest_queued'" in commit
    assert "'job_status', 'queued'" in commit
    assert "'previous_job_id'" in commit
    assert "'previous_job_status'" in commit
    assert "parser_version =" not in commit
    assert "chunking_version =" not in commit
    assert "embedding_version =" not in commit
    assert "document_text" not in commit


def test_reingest_grants_are_api_only_and_readiness_inventory_is_exact() -> None:
    source = _source()
    assert "public.v4_prepare_document_reingest(uuid) TO rag_api" in source
    assert "public.v4_commit_document_reingest(uuid, text, uuid) TO rag_api" in source
    denied_roles = "FROM PUBLIC, rag_worker, rag_maintenance, rag_backup, rag_migrator"
    assert denied_roles in source
    assert "SELECT count(*) = 11" in source
    assert "routine.proconfig =" in source
    assert "ARRAY['search_path=pg_catalog']::text[]" in source
    assert "'0005_document_reingest_action'::text" in source


def test_job_polling_is_admin_or_unique_file_uploader_only() -> None:
    source = _source()
    get_job = source.split("CREATE OR REPLACE FUNCTION public.v4_get_job", 1)[1].split(
        "CREATE OR REPLACE FUNCTION public.v4_schema_revision", 1
    )[0]
    assert "public.v4_current_actor_is_admin()" in get_job
    assert "node.kind = 'file'" in get_job
    assert "node.uploader_user_id =" in get_job
    assert "public.v4_current_actor_id()" in get_job
    assert "public.v4_can_read_document" not in get_job
