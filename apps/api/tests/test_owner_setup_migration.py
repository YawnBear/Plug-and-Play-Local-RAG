from pathlib import Path


def test_owner_setup_migration_is_atomic_bounded_and_least_privilege() -> None:
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0007_owner_setup.py"
    source = path.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0006_versioned_claim"' in source
    assert "CREATE TABLE public.owner_setup" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "code_hash varchar(64)" in source
    assert "challenge_hash varchar(64)" in source
    assert "p_expires_at > statement_timestamp() + interval '15 minutes'" in source
    assert "setup_row.max_attempts" in source
    assert "FOR UPDATE" in source
    assert "pg_advisory_xact_lock" in source
    assert "public.v4_bootstrap_admin(" in source
    assert "SET code_hash = NULL" in source
    assert "TO rag_maintenance" in source
    assert "TO rag_api" in source
    assert "NOT has_table_privilege" in source
    assert "0007_owner_setup is forward-only" in source
