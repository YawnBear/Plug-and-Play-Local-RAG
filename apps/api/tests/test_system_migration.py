from pathlib import Path


def test_system_migration_preserves_admin_and_owner_setup_security_contracts() -> None:
    source = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0008_system_visibility.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0007_owner_setup"' in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "PERFORM public.v4_require_admin()" in source
    assert "completion_token_hash" in source
    assert "document text" not in source.lower()
    assert "v8_complete_owner_setup(text,text,text,text)" in source
    assert (
        "NOT has_table_privilege(\n"
        "                       'rag_api', 'public.owner_setup'"
    ) in source
    assert "NOT has_table_privilege('rag_api', 'public.system_operations'" in source
    assert (
        "NOT has_function_privilege('public',\n"
        "                       'public.v8_admin_system_operations"
    ) in source
    assert "0008_system_visibility is forward-only" in source


def test_system_completion_accepts_only_bounded_metric_names() -> None:
    source = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0008_system_visibility.py"
    ).read_text(encoding="utf-8")

    for metric in (
        "duration_seconds",
        "peak_working_set_bytes",
        "fixture_sha256",
        "embedding_dimension",
        "relevant_score",
    ):
        assert f"'{metric}'" in source
    assert "pg_column_size(p_metrics) > 8192" in source
    assert "jsonb_object_keys(p_metrics)" in source
