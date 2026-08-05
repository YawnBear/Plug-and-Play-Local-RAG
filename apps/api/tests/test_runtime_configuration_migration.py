from pathlib import Path


def migration_source() -> str:
    return (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0009_runtime_configuration.py"
    ).read_text(encoding="utf-8")


def test_runtime_configuration_is_forced_rls_and_function_only() -> None:
    source = migration_source()
    for table in (
        "runtime_configuration_revisions",
        "runtime_configuration_previews",
        "system_reauthentication_grants",
        "backup_restore_verifications",
        "personal_backup_operations",
        "runtime_configuration_changes",
    ):
        assert f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY" in source
    assert "NOT has_table_privilege('rag_api'," in source
    assert (
        "FROM PUBLIC, rag_worker, rag_backup, rag_migrator, rag_maintenance" in source
    )
    assert "0009_runtime_configuration is forward-only" in source


def test_apply_is_bound_to_preview_reauth_backup_and_controller_nonce() -> None:
    source = migration_source()
    assert "preview.session_id <> current_session_id" in source
    assert "preview.impact_digest <> p_impact_digest" in source
    assert "grant_row.impact_digest <> p_impact_digest" in source
    assert "grant_row.expires_at <= statement_timestamp()" in source
    assert "a current restore-verified backup is required" in source
    assert "controller_nonce_hash = p_controller_nonce_hash" in source
    assert "runtime configuration preview is stale" in source


def test_controller_payload_contains_profile_ids_but_no_runtime_authority() -> None:
    source = migration_source()
    controller_body = source.split(
        "CREATE FUNCTION public.v9_controller_runtime_configuration_change", 1
    )[1].split("CREATE FUNCTION public.v9_controller_advance", 1)[0]
    for allowed in (
        "generation_profile_id",
        "reranker_profile_id",
        "ocr_mode",
        "ocr_profile_id",
        "ocr_preset_id",
    ):
        assert allowed in controller_body
    for forbidden in (
        "executable",
        "arguments",
        "working_directory",
        "environment_name",
        "base_url",
        "shell",
    ):
        assert forbidden not in controller_body


def test_failed_or_rolled_back_change_keeps_prior_revision_effective() -> None:
    source = migration_source()
    assert "IF p_result = 'effective' THEN" in source
    assert "THEN change_row.desired_revision_id" in source
    assert "ELSE change_row.prior_revision_id END" in source
    assert "'runtime_configuration_finished'" in source


def test_personal_backup_requires_real_export_then_isolated_restore_evidence() -> None:
    source = migration_source()
    assert "v9_admin_start_personal_backup" in source
    assert "v9_controller_finish_personal_backup_export" in source
    assert "v9_controller_record_restore_verification" in source
    assert "personal.isolated-restore.v1" in source
    assert "state = 'succeeded', stage = 'succeeded'" in source
    assert "controller_nonce_hash = p_controller_nonce_hash" in source
