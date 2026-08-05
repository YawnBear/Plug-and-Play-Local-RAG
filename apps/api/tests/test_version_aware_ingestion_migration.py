from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).parents[1]
MIGRATION = API_ROOT / "alembic" / "versions" / "0006_versioned_claim.py"


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_version_aware_claim_is_forward_only_head() -> None:
    scripts = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    assert scripts.get_heads() == ["0014_restart_without_backup"]
    source = _source()
    assert 'down_revision: str | None = "0005_document_reingest_action"' in source
    assert "forward-only" in source


def test_claim_preserves_fencing_and_returns_document_versions() -> None:
    source = _source()
    claim = source.split("CREATE FUNCTION public.v4_claim_ingestion_job", 1)[1].split(
        "REVOKE ALL ON FUNCTION", 1
    )[0]
    for field in (
        "parser_version text",
        "chunking_version text",
        "embedding_version text",
    ):
        assert field in claim
    assert "FOR UPDATE SKIP LOCKED" in claim
    assert "fencing_token = job.fencing_token + 1" in claim
    assert "lease_token = public.gen_random_uuid()" in claim
    assert "lease_expires_at = statement_timestamp()" in claim
    assert "document.parser_version::text" in claim
    assert "document.chunking_version::text" in claim
    assert "document.embedding_version::text" in claim
    assert "stage = 'uploaded'" in claim
    assert "completed_units = 0" in claim
    assert "total_units = NULL" in claim
    assert "parser_version =" not in claim
    assert "chunking_version =" not in claim
    assert "embedding_version =" not in claim


def test_claim_function_security_and_readiness_metadata_are_exact() -> None:
    source = _source()
    assert "DROP FUNCTION public.v4_claim_ingestion_job(text, integer)" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path = pg_catalog" in source
    assert "public.v4_claim_ingestion_job(text, integer) TO rag_worker" in source
    assert "FROM PUBLIC, rag_api, rag_maintenance, rag_backup, rag_migrator" in source
    assert "'public.v4_claim_ingestion_job(text,integer)'::regprocedure" in source
    assert "SELECT count(*) = 12" in source
    assert "SELECT count(*) = 3" in source
    assert source.count("'0006_versioned_claim'::text") == 2
    for denied_role in (
        "public",
        "rag_api",
        "rag_maintenance",
        "rag_backup",
        "rag_migrator",
    ):
        assert (
            f"'{denied_role}',\n"
            "                       "
            "'public.v4_claim_ingestion_job(text,integer)'"
        ) in source
