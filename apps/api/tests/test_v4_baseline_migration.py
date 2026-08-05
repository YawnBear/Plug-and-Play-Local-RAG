import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.models import (
    BackupRun,
    Base,
    Chat,
    ChatTurn,
    Document,
    EffectiveDocumentAccess,
    IngestionJob,
    PreAuthChallenge,
    ServiceLease,
    Session,
    TurnSource,
    User,
)


def _load_baseline() -> ModuleType:
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0001_v4_baseline.py"
    spec = importlib.util.spec_from_file_location("v4_baseline", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v4_baseline_is_the_only_root_and_head() -> None:
    api_root = Path(__file__).parents[1]
    scripts = ScriptDirectory.from_config(Config(str(api_root / "alembic.ini")))
    assert scripts.get_bases() == ["0001_v4_baseline"]
    assert scripts.get_heads() == ["0014_restart_without_backup"]


def test_alembic_environment_requires_migrator_and_assumes_owner() -> None:
    environment = (Path(__file__).parents[1] / "alembic" / "env.py").read_text(
        encoding="utf-8"
    )
    assert 'session_user != "rag_migrator"' in environment
    assert "pg_has_role(session_user, 'rag_owner', 'MEMBER')" in environment
    assert 'connection.execute(text("SET ROLE rag_owner"))' in environment
    assert "async with connectable.begin() as connection:" in environment
    assert "async with connectable.connect() as connection:" not in environment
    assert "get_migration_settings().migration_database_url" in environment
    assert "get_settings().database_url" not in environment


def test_v4_baseline_contains_fresh_guard_roles_rls_and_safe_functions() -> None:
    migration = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0001_v4_baseline.py"
    ).read_text(encoding="utf-8")
    assert "_assert_fresh_database(connection)" in migration
    assert "_assert_roles(connection)" in migration
    assert "_assert_extensions(connection)" in migration
    assert "ALTER TABLE alembic_version OWNER TO rag_owner" in migration
    assert "SET LOCAL ROLE rag_owner" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "SECURITY DEFINER" in migration
    assert "SET search_path = pg_catalog" in migration
    assert "v4_activate_actor" in migration
    assert "v4_bootstrap_admin" in migration
    assert "v4_rebuild_effective_document_access" in migration
    assert "_create_controlled_mutation_functions()" in migration
    assert "owner_authorized_at_deletion" in migration
    assert "source_available_when_deleted" not in migration
    assert "content_path" not in migration
    assert "REVOKE ALL ON ALL TABLES" in migration
    assert "PASSWORD" not in migration
    assert "app.db.models" not in migration
    assert "Base.metadata" not in migration


def test_fresh_guard_refuses_old_application_relations() -> None:
    migration = _load_baseline()

    class _Scalars:
        def __init__(self, values: list[str]) -> None:
            self.values = values

        def scalars(self) -> list[str]:
            return self.values

    class _Connection:
        @staticmethod
        def execute(_statement: object, _parameters: object) -> _Scalars:
            return _Scalars(["documents"])

    with pytest.raises(RuntimeError, match="fresh-only.*documents"):
        migration._assert_fresh_database(_Connection())


def test_baseline_downgrade_explicitly_reverses_owned_objects() -> None:
    migration = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0001_v4_baseline.py"
    ).read_text(encoding="utf-8")
    assert "DROP VIEW v4_authorized_turn_sources" in migration
    assert "DROP VIEW v4_authorized_turn_citations" in migration
    assert "DROP TRIGGER trg_v4_turn_source_immutability" in migration
    assert "DROP FUNCTION v4_activate_actor(text)" in migration
    assert '"turn_citations",' in migration
    assert '"documents",' in migration
    assert '"service_leases",' in migration
    assert '"backup_runs",' in migration
    assert "refusing V4 baseline downgrade after bootstrap or product data" in migration
    assert "op.drop_table(table_name)" in migration


def _constraint_names(table: object) -> set[str | None]:
    return {constraint.name for constraint in table.constraints}


def test_final_v4_models_expose_security_and_fencing_contracts() -> None:
    assert "content_path" not in Document.__table__.c
    assert not Document.__table__.c.object_key.nullable
    assert "ck_documents_object_key_nonempty" in _constraint_names(Document.__table__)
    assert not Chat.__table__.c.owner_user_id.nullable
    assert "ix_chats_owner_updated_id_desc" in {
        index.name for index in Chat.__table__.indexes
    }
    turn_status = next(
        constraint.sqltext
        for constraint in ChatTurn.__table__.constraints
        if constraint.name == "ck_chat_turns_status"
    )
    assert "access_revoked" in str(turn_status)
    assert "length_limited" in str(turn_status)
    assert "citation_failed" in str(turn_status)
    assert "partial_answer" in ChatTurn.__table__.c
    assert {
        "ck_users_username_format",
        "ck_users_display_name_no_controls",
        "ck_users_active_password",
    } <= _constraint_names(User.__table__)
    assert {
        "issued_authentication_version",
        "issued_authentication_epoch",
        "issued_session_epoch",
    } <= set(Session.__table__.c.keys())
    session_expiry = next(
        constraint.sqltext
        for constraint in Session.__table__.constraints
        if constraint.name == "ck_sessions_expiry_order"
    )
    assert "idle_expires_at = absolute_expires_at" in str(session_expiry)
    assert "capability" not in Session.__table__.c
    assert "ck_pre_auth_challenges_max_expiry" in _constraint_names(
        PreAuthChallenge.__table__
    )
    assert {
        "ck_ingestion_jobs_lease_consistency",
        "ck_ingestion_jobs_fencing_nonnegative",
    } <= _constraint_names(IngestionJob.__table__)
    assert "ck_turn_sources_deleted_disposition" in _constraint_names(
        TurnSource.__table__
    )
    assert "owner_authorized_at_deletion" in TurnSource.__table__.c
    assert {
        "ck_service_leases_fencing_positive",
        "ck_service_leases_expiry_order",
    } <= _constraint_names(ServiceLease.__table__)
    assert {
        "ck_backup_runs_status_consistency",
        "ck_backup_runs_result_consistency",
    } <= _constraint_names(BackupRun.__table__)
    assert {
        "user_id",
        "document_id",
        "authorization_version",
    } <= set(EffectiveDocumentAccess.__table__.c.keys())


def test_immutable_baseline_ddl_matches_final_model_metadata() -> None:
    migration = _load_baseline()
    dialect_instance = dialect()
    expected = [
        str(CreateTable(table).compile(dialect=dialect_instance)).strip()
        for table in Base.metadata.sorted_tables
        if table.name != "folder_create_grants"
    ]
    expected.extend(
        str(CreateIndex(index).compile(dialect=dialect_instance)).strip()
        for table in Base.metadata.sorted_tables
        if table.name != "folder_create_grants"
        for index in sorted(table.indexes, key=lambda item: item.name or "")
    )

    def normalize(statement: str) -> str:
        return re.sub(r"\s+", " ", statement).strip()

    actual_normalized = [normalize(statement) for statement in migration._BASELINE_DDL]
    expected_normalized = [normalize(statement) for statement in expected]
    v5_tables = ("CREATE TABLE chunks ", "CREATE TABLE turn_sources ")
    later_versioned_objects = (
        "CREATE TABLE chunks ",
        "CREATE TABLE turn_sources ",
        "CREATE UNIQUE INDEX uq_chunks_document_ordinal ",
        "CREATE UNIQUE INDEX uq_chunks_generation_ordinal ",
    )

    assert [
        statement
        for statement in actual_normalized
        if not statement.startswith(later_versioned_objects)
    ] == [
        statement
        for statement in expected_normalized
        if not statement.startswith(later_versioned_objects)
    ]
    assert all(
        "highlight_anchor" not in statement
        for statement in actual_normalized
        if statement.startswith(v5_tables)
    )


def test_security_definer_surface_and_grants_are_command_specific() -> None:
    source = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0001_v4_baseline.py"
    ).read_text(encoding="utf-8")
    required_functions = {
        "v4_auth_lookup",
        "v4_session_view",
        "v4_refresh_session",
        "v4_consume_activation",
        "v4_change_password",
        "v4_admin_create_user",
        "v4_admin_reset_user",
        "v4_admin_set_user",
        "v4_admin_preview_acl",
        "v4_admin_apply_acl",
        "v4_claim_service_lease",
        "v4_claim_ingestion_job",
        "v4_update_ingestion_progress",
        "v4_claim_object_deletion",
        "v4_get_job",
        "v4_admin_delete_document",
        "v4_interrupt_turn",
        "v4_retry_turn",
        "v4_finalize_turn",
        "v4_mark_turn_access_revoked",
        "v4_mark_turn_access_revoked_trusted",
        "v4_mark_turn_citation_failed",
        "v4_mark_turn_length_limited",
        "v4_begin_backup_run",
        "v4_finish_backup_run",
        "v4_readiness",
    }
    for function_name in required_functions:
        match = re.search(
            rf"CREATE FUNCTION {function_name}\b"
            r"(?P<body>.*?)(?=\n\s*CREATE FUNCTION|\Z)",
            source,
            flags=re.DOTALL,
        )
        assert match is not None, function_name
        assert "SECURITY DEFINER" in match.group("body")
        assert "SET search_path = pg_catalog" in match.group("body")
    assert "GRANT INSERT" not in source
    assert "GRANT UPDATE" not in source
    assert "GRANT DELETE" not in source
    assert "GRANT ALL" not in source
    migration = _load_baseline()
    defined = set(re.findall(r"CREATE FUNCTION (v4_[a-z0-9_]+)", source))
    assert set(migration._EXPECTED_V4_FUNCTIONS) == defined
    assert (
        "v4_repair_interrupted_turns"
        in migration._EXPECTED_FUNCTION_GRANTS["rag_maintenance"]
    )
    for role_name, function_names in migration._EXPECTED_FUNCTION_GRANTS.items():
        if role_name != "rag_maintenance":
            assert "v4_repair_interrupted_turns" not in function_names
    assert "v4_interrupt_turn" in migration._EXPECTED_FUNCTION_GRANTS["rag_api"]
    assert "v4_get_job" in migration._EXPECTED_FUNCTION_GRANTS["rag_api"]
    assert "v4_refresh_session" in migration._EXPECTED_FUNCTION_GRANTS["rag_api"]
    assert (
        "v4_mark_turn_access_revoked_trusted"
        in migration._EXPECTED_FUNCTION_GRANTS["rag_api"]
    )
    assert (
        "v4_mark_turn_citation_failed" in migration._EXPECTED_FUNCTION_GRANTS["rag_api"]
    )
    assert (
        "v4_mark_turn_length_limited" in migration._EXPECTED_FUNCTION_GRANTS["rag_api"]
    )
    assert (
        "v4_update_ingestion_progress"
        in migration._EXPECTED_FUNCTION_GRANTS["rag_worker"]
    )
    for role_name, function_names in migration._EXPECTED_FUNCTION_GRANTS.items():
        if role_name != "rag_api":
            assert "v4_interrupt_turn" not in function_names
            assert "v4_get_job" not in function_names
            assert "v4_mark_turn_access_revoked_trusted" not in function_names
            assert "v4_mark_turn_citation_failed" not in function_names
            assert "v4_mark_turn_length_limited" not in function_names
        if role_name != "rag_worker":
            assert "v4_update_ingestion_progress" not in function_names
    signatures = {
        signature.removeprefix("public.").split("(", 1)[0]
        for signature in migration._EXPECTED_V4_REGPROCEDURES
    }
    assert signatures == set(migration._EXPECTED_V4_FUNCTIONS)
    assert len(migration._EXPECTED_V4_REGPROCEDURES) == len(signatures)
    identity = re.search(
        r"CREATE FUNCTION v4_runtime_identity\b"
        r"(?P<body>.*?)(?=\n\s*CREATE FUNCTION|\Z)",
        source,
        flags=re.DOTALL,
    )
    assert identity is not None
    assert "SECURITY INVOKER" in identity.group("body")


def test_v45_session_validation_refresh_and_admin_contract_is_frozen() -> None:
    source = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0001_v4_baseline.py"
    ).read_text(encoding="utf-8")
    session_view = re.search(
        r"CREATE FUNCTION v4_session_view\b(?P<body>.*?)"
        r"(?=\n\s*CREATE FUNCTION v4_refresh_session)",
        source,
        flags=re.DOTALL,
    )
    refresh = re.search(
        r"CREATE FUNCTION v4_refresh_session\b(?P<body>.*?)"
        r"(?=\n\s*CREATE FUNCTION v4_record_login_failure)",
        source,
        flags=re.DOTALL,
    )
    admin = re.search(
        r"CREATE FUNCTION v4_require_admin\b(?P<body>.*?)"
        r"(?=\n\s*CREATE FUNCTION v4_append_audit)",
        source,
        flags=re.DOTALL,
    )
    assert session_view is not None
    assert "UPDATE public.sessions" not in session_view.group("body")
    assert "LANGUAGE sql" in session_view.group("body")
    assert refresh is not None
    assert "p_csrf_token_hash" in refresh.group("body")
    assert "interval '5 minutes'" in refresh.group("body")
    assert "idle_expires_at = p_expires_at" in refresh.group("body")
    assert "absolute_expires_at = p_expires_at" in refresh.group("body")
    assert admin is not None
    assert "recent_reauthenticated_at" not in admin.group("body")
    assert "interval '15 minutes'" not in admin.group("body")
    assert "p_idle_expires_at IS DISTINCT FROM" in source
    assert "p_absolute_expires_at" in source


def test_readiness_covers_practical_schema_sequence_and_trigger_drift() -> None:
    source = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0001_v4_baseline.py"
    ).read_text(encoding="utf-8")

    assert "'pg_database_owner'::regrole" in source
    assert "pg_catalog.acldefault(" in source
    assert "'S', sequence.relowner" in source
    assert "trigger.tgenabled = 'O'" in source
    assert "trigger.tgtype = 27" in source
    assert (
        "routine.proname NOT IN (\n"
        "                                            'v4_runtime_identity',\n"
        "                                            "
        "'v4_enforce_turn_source_immutability'"
    ) in source
    assert (
        "'public.v4_enforce_turn_source_immutability()'\n"
        "                                      ::regprocedure"
    ) in source
