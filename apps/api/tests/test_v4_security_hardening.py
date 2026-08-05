import re
from pathlib import Path

from app.db.models import (
    Chunk,
    Document,
    IngestionJob,
    LibraryNode,
    UploadReservation,
    User,
)

API_ROOT = Path(__file__).parents[1]
MIGRATION_PATH = API_ROOT / "alembic" / "versions" / "0001_v4_baseline.py"
ROLE_SCRIPT_PATH = (
    API_ROOT.parents[1] / "ops" / "security" / ("provision-postgres-roles.ps1")
)


def _migration() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    match = re.search(
        rf"CREATE FUNCTION {name}\b(?P<body>.*?)(?=\n\s*CREATE FUNCTION|\Z)",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, name
    return match.group("body")


def _constraint_names(model: object) -> set[str | None]:
    return {constraint.name for constraint in model.__table__.constraints}


def test_safe_search_path_qualifies_pgcrypto_symbols() -> None:
    source = _migration()

    assert not re.search(r"(?<!public\.)gen_random_uuid\(\)", source)
    assert not re.search(r"(?<![.\w])digest\(", source)
    assert "public.gen_random_uuid()" in source
    assert "public.digest(" in source


def test_visibility_acl_preview_and_admin_counts_are_bound_to_real_impact() -> None:
    source = _migration()
    visibility = _function(source, "v4_can_view_library_node")
    preview = _function(source, "v4_admin_preview_acl")
    apply_acl = _function(source, "v4_admin_apply_acl")
    visible_nodes = source[source.index("CREATE VIEW v4_visible_library_nodes") :]

    assert "JOIN public.access_grants AS grant_row" in visibility
    assert "grant_row.node_id = granted_descendant.id" in visibility
    assert "v4_acl_impact(p_operation)" in preview
    assert "v4_acl_impact(v_preview.operation)" in apply_acl
    assert "v4_current_actor_is_admin()" in visible_nodes


def test_preview_confirmed_move_is_exact_atomic_and_depth_bounded() -> None:
    source = _migration()
    impact = _function(source, "v4_acl_impact")
    apply_acl = _function(source, "v4_admin_apply_acl")

    assert "'set_team_active', 'move_node'" in impact
    assert "jsonb_object_keys(p_operation)" in impact
    assert "NOT p_operation ? 'node_id'" in impact
    assert "NOT p_operation ? 'parent_id'" in impact
    assert "jsonb_typeof(p_operation->'parent_id')" in impact
    assert "v_kind = 'move_node'" in impact
    cycle_guard = impact.index("move target creates a cycle")
    simulated_tree = impact.index("nodes_after AS")
    assert cycle_guard < simulated_tree
    assert "JOIN subtree AS parent" in impact[:simulated_tree]
    assert "nodes_after AS" in impact
    assert "paths_before AS" in impact
    assert "paths_after AS" in impact
    assert "removed_access AS" in impact
    assert "added_access AS" in impact
    assert "changed_access AS" in impact

    assert "hashtextextended('rag-v4-library-acl', 0)" in apply_acl
    assert "v_preview.impact_digest <> p_impact_digest" in apply_acl
    assert "ELSIF v_kind = 'move_node'" in apply_acl
    assert "FOR UPDATE" in apply_acl
    assert "v_target_kind <> 'folder'" in apply_acl
    assert "COALESCE(bool_or(id = v_target_id), false)" in apply_acl
    assert "v_target_depth + 1 + v_subtree_depth > 256" in apply_acl
    assert "sibling.parent_id IS NOT DISTINCT FROM v_target_id" in apply_acl
    assert "sibling.name_key = v_node.name_key" in apply_acl
    assert "SET parent_id = v_target_id" in apply_acl
    assert "moved_paths AS" in apply_acl
    assert "'logical_path', v_new_logical_path" in apply_acl
    assert "v4_rebuild_effective_document_access()" in apply_acl
    assert "'acl_operation_applied'" in apply_acl


def test_acl_impact_reports_only_real_boundary_aware_access_delta() -> None:
    impact = _function(_migration(), "v4_acl_impact")

    # Both snapshots use the same stop-at-first-boundary traversal as the
    # authoritative effective-access rebuild.
    assert impact.count("WHERE NOT path.boundary_seen") == 2
    assert "path.boundary_seen OR parent.access_boundary" in impact
    assert "principals_before AS" in impact
    assert "principals_after AS" in impact
    assert "EXCEPT" in impact

    # Users/documents/nodes come from the symmetric access-pair delta. A grant
    # that leaves access unchanged therefore produces an empty impact, and a
    # nested boundary prevents outer changes from listing protected descendants.
    assert "SELECT DISTINCT user_id FROM changed_access" in impact
    assert "SELECT DISTINCT document_id FROM changed_access" in impact
    assert "JOIN changed_documents AS changed" in impact
    assert "FROM affected_nodes" not in impact
    assert "FROM team_descendants" not in impact


def test_runtime_rls_is_select_only_and_mutations_are_function_only() -> None:
    source = _migration()

    assert "FOR SELECT USING" in source
    assert "WITH CHECK" not in source
    assert "GRANT INSERT" not in source
    assert "GRANT UPDATE" not in source
    assert "GRANT DELETE" not in source


def test_turn_finalization_and_snapshot_parent_operations_fail_closed() -> None:
    source = _migration()
    finalize = _function(source, "v4_finalize_turn")
    store_sources = _function(source, "v4_store_turn_sources")
    trigger = _function(source, "v4_enforce_turn_source_immutability")
    history = source[
        source.index("CREATE VIEW v4_chat_history") : source.index(
            "CREATE VIEW v4_visible_library_nodes"
        )
    ]
    authorized_sources = source[
        source.index("CREATE VIEW v4_authorized_turn_sources") : source.index(
            "CREATE VIEW v4_authorized_turn_citations"
        )
    ]
    authorized_citations = source[
        source.index("CREATE VIEW v4_authorized_turn_citations") : source.index(
            "for table_name, predicate in _EXPECTED_POLICIES.items():"
        )
    ]

    assert "owner_authorized_at_deletion = true" in finalize
    assert "source.document_id IS NOT NULL" in finalize
    assert "status = 'access_revoked'" in finalize
    assert "error = 'access_revoked'" in finalize
    for protected_view in (history, authorized_sources, authorized_citations):
        assert "document_id IS NOT NULL" in protected_view
        assert "owner_authorized_at_deletion = true" in protected_view
    assert "'parent_delete', 'turn_retry'" in trigger
    assert "CREATE FUNCTION v4_delete_chat" in source
    assert "CREATE FUNCTION v4_retry_turn" in source
    assert "FOR SHARE" in store_sources
    assert "JOIN public.chunks AS chunk" in store_sources
    assert "document.id = chunk.document_id" in store_sources
    assert "document.original_filename" in store_sources
    assert "chunk.source_sha256" in store_sources
    assert "chunk.text_sha256" in store_sources
    assert "chunk.text, chunk.token_count" in store_sources
    assert "source.snapshot_text" not in store_sources


def test_deleted_user_and_product_state_constraints_are_in_metadata() -> None:
    assert {
        "ck_users_deleted_irreversible",
    } <= _constraint_names(User)
    assert {
        "ck_documents_sha256",
        "ck_documents_pdf_only",
        "ck_documents_state_stage",
        "ck_documents_error_state",
    } <= _constraint_names(Document)
    assert "ck_ingestion_jobs_state_consistency" in _constraint_names(IngestionJob)
    assert {
        "ck_chunks_text_sha256",
        "ck_chunks_source_sha256",
        "ck_chunks_text_nonempty",
    } <= _constraint_names(Chunk)
    assert {
        "ck_upload_reservations_sha256",
        "ck_upload_reservations_metadata_digest",
        "ck_upload_reservations_expiry",
        "ck_upload_reservations_terminal_state",
    } <= _constraint_names(UploadReservation)


def test_role_acl_hardening_precedes_password_collection_and_removes_memberships() -> (
    None
):
    script = ROLE_SCRIPT_PATH.read_text(encoding="utf-8")

    hardening = script.index("& icacls.exe $temporaryDirectory /inheritance:r")
    verification = script.index("& icacls.exe $temporaryDirectory /verify")
    secrets = script.index(
        "$passwords[$roleName] = Read-RoleSecret -RoleName $roleName"
    )
    assert hardening < verification < secrets
    assert "Temporary role directory ACL verification failed." in script
    assert "FROM pg_auth_members AS edge" in script
    assert "'REVOKE %I FROM %I'" in script
    assert "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC" in script
    assert "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO rag_owner" in script
    assert "public.cosine_distance(vector, vector) TO rag_api" in script


def test_readiness_uses_only_controlled_database_function() -> None:
    session_source = (API_ROOT / "app" / "db" / "session.py").read_text(
        encoding="utf-8"
    )

    assert "FROM v5_readiness()" in session_source
    assert "v4_runtime_identity(:expected_role)" in session_source
    assert "FROM alembic_version" not in session_source
    assert "FROM pg_extension" not in session_source
    readiness = _function(_migration(), "v4_readiness")
    assert "bootstrap_required boolean" in readiness
    assert "catalog_integrity boolean" in readiness
    assert "expected_tables(name)" in readiness
    assert "expected_views(name)" in readiness
    assert "expected_functions(signature)" in readiness
    assert "_EXPECTED_V4_FUNCTIONS" in _migration()
    assert "_EXPECTED_V4_REGPROCEDURES" in _migration()
    assert "expected_function_grants(role_name, function_signature)" in readiness
    assert "has_function_privilege" in readiness
    assert "policy.polcmd <> 'r'" in readiness
    assert "role_table_grants" in readiness


def test_runtime_identity_is_invoker_exact_and_membership_free() -> None:
    source = _migration()
    identity = _function(source, "v4_runtime_identity")

    assert "SECURITY INVOKER" in identity
    assert "SECURITY DEFINER" not in identity
    assert "current_user::text = p_expected_role" in identity
    assert "session_user::text = p_expected_role" in identity
    for forbidden_attribute in (
        "NOT expected.rolinherit",
        "NOT expected.rolsuper",
        "NOT expected.rolbypassrls",
        "NOT expected.rolcreatedb",
        "NOT expected.rolcreaterole",
        "NOT expected.rolreplication",
    ):
        assert forbidden_attribute in identity
    assert "pg_catalog.pg_auth_members" in identity
    assert "'rag_api', 'rag_worker', 'rag_maintenance'" in identity
    assert "rag_backup" not in identity
    assert "rag_migrator" not in identity


def test_chat_worker_and_lexical_surfaces_are_narrow_and_fenced() -> None:
    source = _migration()
    issue_login = _function(source, "v4_issue_login_session")
    get_job = _function(source, "v4_get_job")
    claim = _function(source, "v4_claim_ingestion_job")
    commit = _function(source, "v4_commit_ingestion_job")

    assert "UPDATE public.sessions" not in issue_login
    for response_field in (
        "job_id uuid",
        "document_id uuid",
        "status text",
        "stage text",
        "completed_units integer",
        "total_units integer",
        "error text",
    ):
        assert response_field in get_job
    assert "job.id = p_job_id" in get_job
    assert "public.v4_current_actor_is_admin()" in get_job
    assert "public.v4_can_read_document(job.document_id)" in get_job
    assert "lease_token" not in get_job
    assert "lease_owner" not in get_job
    assert "fencing_token" not in get_job
    for name in (
        "v4_list_chats",
        "v4_create_chat",
        "v4_delete_chat",
        "v4_replace_chat_scope",
        "v4_begin_turn",
        "v4_store_turn_sources",
        "v4_fail_turn",
        "v4_retry_turn",
        "v4_commit_ingestion_job",
        "v4_requeue_ingestion_job",
        "v4_poison_ingestion_job",
    ):
        assert f"CREATE FUNCTION {name}" in source
    assert "lease_token = p_lease_token" in commit
    assert "fencing_token = p_fencing_token" in commit
    for authoritative_field in (
        "document_id uuid",
        "object_key text",
        "original_filename text",
        "sha256 text",
        "byte_size bigint",
        "attempt integer",
    ):
        assert authoritative_field in claim
    assert "FROM candidate, public.documents AS document" in claim
    assert "DELETE FROM public.chunks" in commit
    assert "UPDATE public.documents" in commit
    assert "UPDATE public.ingestion_jobs" in commit
    assert "ix_chunks_text_fts" in {index.name for index in Chunk.__table__.indexes}
    assert "ix_library_nodes_name_fts" in {
        index.name for index in LibraryNode.__table__.indexes
    }


def test_worker_progress_is_strict_monotonic_and_fenced() -> None:
    source = _migration()
    progress = _function(source, "v4_update_ingestion_progress")

    for parameter in (
        "p_job_id uuid",
        "p_lease_token uuid",
        "p_fencing_token bigint",
        "p_stage text",
        "p_completed_units integer",
        "p_total_units integer",
    ):
        assert parameter in progress
    assert "'parsing', 'chunking', 'embedding', 'indexing'" in progress
    assert "p_total_units NOT BETWEEN 1 AND 1000000" in progress
    assert "p_completed_units NOT BETWEEN 0 AND p_total_units" in progress
    assert "v_job.status <> 'running'" in progress
    assert "v_job.lease_token <> p_lease_token" in progress
    assert "v_job.fencing_token <> p_fencing_token" in progress
    assert "v_job.lease_expires_at <= statement_timestamp()" in progress
    assert "v_requested_stage_order < v_current_stage_order" in progress
    assert "p_completed_units < v_job.completed_units" in progress
    assert "p_total_units < v_job.total_units" in progress
    assert "SET stage = p_stage" in progress
    assert "SET state = p_stage" in progress
    assert "RETURN 'accepted'" in progress
    assert "RETURN 'stale'" in progress


def test_first_turn_auto_title_and_offline_turn_repair_are_controlled() -> None:
    source = _migration()
    begin_turn = _function(source, "v4_begin_turn")
    repair = _function(source, "v4_repair_interrupted_turns")

    assert "p_auto_title text" in begin_turn
    assert "p_auto_title IS NULL" in begin_turn
    assert "char_length(btrim(p_auto_title)) NOT BETWEEN 1 AND 255" in begin_turn
    assert "v_chat.next_turn_ordinal = 1" in begin_turn
    assert "AND NOT v_chat.title_is_manual" in begin_turn
    assert "THEN btrim(p_auto_title)" in begin_turn
    assert "title_is_manual = true" not in begin_turn

    assert "WHERE status = 'generating'" in repair
    assert "SET status = 'interrupted'" in repair
    assert "generation_token = NULL" in repair
    assert "GET DIAGNOSTICS v_count = ROW_COUNT" in repair


def test_retry_and_interruption_preserve_atomic_chat_contracts() -> None:
    source = _migration()
    retry = _function(source, "v4_retry_turn")
    interrupt = _function(source, "v4_interrupt_turn")

    assert "AS $$\n        AS $$" not in interrupt
    assert "p_chat_id uuid" in retry
    assert "p_turn_id uuid" in retry
    assert "p_generation_token uuid" in retry
    assert retry.index("FROM public.chats") < retry.index("FOR UPDATE")
    assert "AND chat_id = p_chat_id" in retry
    assert "SELECT max(candidate.ordinal)" in retry
    assert "v_turn.ordinal <> v_latest_ordinal" in retry
    assert (
        "'failed', 'interrupted', 'length_limited', 'citation_failed'"
        in retry
    )
    assert "IF v_turn.status <> 'length_limited' THEN" in retry
    assert "THEN v_turn.partial_answer" in retry
    assert "active_turn.status = 'generating'" in retry
    assert "WHEN v_turn.status = 'length_limited'" in retry
    assert "THEN v_turn.scope_version" in retry
    assert "ELSE v_chat.scope_version" in retry
    assert "'retried'::text" in retry
    for result_status in ("not_found", "not_latest", "not_retryable"):
        assert f"'{result_status}'::text" in retry

    assert "RETURNS text" in interrupt
    assert "owner_user_id = public.v4_current_actor_id()" in interrupt
    assert "AND chat_id = p_chat_id" in interrupt
    assert "p_generation_token IS NULL" in interrupt
    assert "v_turn.generation_token <> p_generation_token" in interrupt
    assert "v_turn.status <> 'generating'" in interrupt
    assert "SET status = 'interrupted'" in interrupt
    assert "generation_token = NULL" in interrupt
    assert "status = 'failed'" not in interrupt
    for result_status in (
        "interrupted",
        "already_interrupted",
        "stale",
        "not_found",
    ):
        assert f"'{result_status}'" in interrupt


def test_trusted_access_revocation_is_actor_free_token_fenced_and_terminal() -> None:
    source = _migration()
    trusted = _function(source, "v4_mark_turn_access_revoked_trusted")

    assert "RETURNS text" in trusted
    assert "v4_current_actor_id" not in trusted
    assert "public.chats" not in trusted
    assert "FROM public.chat_turns" in trusted
    assert "FOR UPDATE" in trusted
    assert "v_turn.status <> 'generating'" in trusted
    assert "p_generation_token IS NULL" in trusted
    assert "v_turn.generation_token <> p_generation_token" in trusted
    assert "SET status = 'access_revoked'" in trusted
    assert "generation_token = NULL" in trusted
    assert "final_answer = NULL" in trusted
    for result_status in ("updated", "already_terminal", "stale", "not_found"):
        assert f"'{result_status}'" in trusted


def test_readiness_checks_exact_function_and_policy_semantics() -> None:
    source = _migration()
    readiness = _function(source, "v4_readiness")

    assert "expected_functions(signature)" in source
    assert "expected_function_grants(role_name, function_signature)" in source
    assert "pg_catalog.to_regprocedure(" in readiness
    assert "routine.proconfig IS DISTINCT FROM" in readiness
    assert "routine.prosecdef IS DISTINCT FROM" in readiness
    assert "expected_policies(table_name, normalized_qual)" in source
    assert "policy.polpermissive IS DISTINCT FROM true" in readiness
    assert "policy.polwithcheck IS NOT NULL" in readiness
    assert "pg_catalog.pg_get_expr(" in readiness
    assert "IS DISTINCT FROM expected.normalized_qual" in readiness


def test_maintenance_cli_has_no_legacy_original_migration_surface() -> None:
    source = (API_ROOT / "app" / "maintenance_cli.py").read_text(encoding="utf-8")

    assert "migrate-originals-to-object-store" not in source
    assert "legacy_document_ids" not in source
    assert '"storage-bootstrap"' in source
    assert '"storage-audit"' in source
    assert '"bootstrap-admin"' in source
    assert "from_maintenance_settings(settings)" in source
    assert "DatabaseManager.from_settings(settings)" not in source


def test_upload_commit_and_delete_share_checksum_fencing() -> None:
    source = _migration()
    preflight = _function(source, "v4_admin_upload_preflight")
    commit = _function(source, "v4_admin_commit_upload")
    delete = _function(source, "v4_admin_delete_document")

    for upload_function in (preflight, commit):
        assert "v_actor := public.v4_current_actor_id()" in upload_function
        assert "IF v_actor IS NULL THEN" in upload_function
        assert "invalid upload team selection" in upload_function
        assert "members must upload into a folder" in upload_function
        assert "v4_require_admin()" not in upload_function
    assert "rag-v4-upload:" in preflight
    assert "rag-v4-upload:" in commit
    assert "rag-v4-upload:" in delete
    assert "UPDATE public.turn_sources AS source" in delete
    assert "owner_authorized_at_deletion =" in delete
    assert "WHERE source.document_id = p_document_id" in delete
    assert "INSERT INTO public.object_deletions" in delete
    assert "DELETE FROM public.documents" in delete
    assert delete.index("UPDATE public.turn_sources AS source") < delete.index(
        "INSERT INTO public.object_deletions"
    )
    assert delete.index("INSERT INTO public.object_deletions") < delete.index(
        "DELETE FROM public.documents"
    )
    assert "DELETE FROM public.turn_sources" not in delete
    assert "DELETE FROM public.turn_citations" not in delete
    assert "INSERT INTO public.documents" in commit
    assert "INSERT INTO public.library_nodes" in commit
    assert "INSERT INTO public.ingestion_jobs" in commit
    rebuild = "PERFORM public.v4_rebuild_effective_document_access()"
    assert rebuild in commit
    assert commit.index("INSERT INTO public.ingestion_jobs") < commit.index(rebuild)
    assert commit.index(rebuild) < commit.index("RETURN QUERY SELECT 'created'")
    assert "p_reservation_id uuid" in commit
    assert "public.upload_reservations%ROWTYPE" in commit
    assert "outcome = 'created'" in commit
    assert "retain_uploaded_object" not in commit
    assert "reservation_id uuid" in preflight
    assert "reservation_expires_at timestamptz" in preflight
    assert "interval '15 minutes'" in preflight
    assert "v_active_reservation.actor_user_id = v_actor" in preflight
    assert (
        "v_active_reservation.parent_id IS NOT DISTINCT FROM p_parent_id" in preflight
    )
    assert "v_active_reservation.metadata_digest =" in preflight
    assert "v_active_reservation.id, v_active_reservation.expires_at" in preflight
    assert "SELECT id, parent_id, name" not in preflight
    assert "SELECT id, parent_id, name" not in commit
    assert "ancestry.name, '/' ORDER BY ancestry.depth DESC" in preflight
    assert commit.count("ancestry.name, '/' ORDER BY ancestry.depth DESC") == 2
    cleanup = _function(source, "v4_queue_expired_upload_orphans")
    assert "p_grace_seconds NOT BETWEEN 60 AND 86400" in cleanup
    assert "NOT EXISTS" in cleanup
    assert commit.index("rag-v4-upload:") < commit.index("rag-v4-library-acl")
    assert delete.index("rag-v4-upload:") < delete.index("rag-v4-library-acl")


def test_library_mutations_and_chat_history_are_controlled_surfaces() -> None:
    source = _migration()

    for name in ("v4_admin_create_folder", "v4_admin_delete_folder"):
        body = _function(source, name)
        assert "v4_require_admin()" in body
        assert "rag-v4-library-acl" in body
    rename = _function(source, "v4_admin_rename_library_node")
    assert "v_actor uuid := public.v4_current_actor_id()" in rename
    assert "public.v4_current_actor_is_admin()" in rename
    assert "node.uploader_user_id = v_actor" in rename
    assert "rag-v4-library-acl" in rename
    assert "CREATE VIEW v4_authorized_turn_citations" in source
    assert "GRANT SELECT ON v4_current_user" in source
    assert "v4_authorized_turn_citations" in source
    assert "GRANT SELECT ON chat_turns" not in source
    assert "GRANT SELECT ON turn_sources" not in source
    assert "GRANT SELECT ON turn_citations" not in source


def test_object_deletion_retry_and_poison_are_database_owned() -> None:
    source = _migration()
    claim = _function(source, "v4_claim_object_deletion")
    finish = _function(source, "v4_finish_object_deletion")

    assert "attempt integer" in claim
    assert "attempt = deletion_row.attempt + 1" in claim
    assert "attempt >= 5" in finish
    assert "interval '5 seconds' * power(2, attempt - 1)" in finish
    assert "RETURN 'requeued'" in finish
    assert "RETURN 'poisoned'" in finish
    assert "RETURN 'stale'" in finish
    assert "public.upload_reservations" in claim
    assert "reservation.expires_at > statement_timestamp()" in claim


def test_admin_and_throttle_transitions_are_fail_closed() -> None:
    source = _migration()
    reset = _function(source, "v4_admin_reset_user")
    set_user = _function(source, "v4_admin_set_user")
    delete_document = _function(source, "v4_admin_delete_document")
    throttle = _function(source, "v4_record_login_failure")
    blocked = _function(source, "v4_login_blocked_until")

    assert "IF NOT FOUND" in reset
    assert "RAG03" in reset and "RAG04" in reset
    assert "RAG03" in set_user and "RAG04" in set_user
    assert "account.status = 'active'" in delete_document
    assert "account.deleted_at IS NULL" in delete_document
    assert "LEAST(" in throttle and "100" in throttle
    assert "interval '24 hours'" in throttle
    assert "interval '24 hours'" in blocked
