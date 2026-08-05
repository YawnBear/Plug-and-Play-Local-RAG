"""Create the fresh-only Version 4 database baseline."""

# Generated SQL literals preserve exact model-metadata output.
# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_v4_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_RELATIONS = (
    "documents",
    "ingestion_jobs",
    "chunks",
    "object_deletions",
    "upload_reservations",
    "library_nodes",
    "chats",
    "chat_scopes",
    "chat_turns",
    "turn_sources",
    "turn_citations",
)
_REQUIRED_ROLES = (
    "rag_owner",
    "rag_migrator",
    "rag_api",
    "rag_worker",
    "rag_maintenance",
    "rag_backup",
)
_EXPECTED_V4_FUNCTIONS = (
    "v4_acl_impact",
    "v4_account_active_teams",
    "v4_activate_actor",
    "v4_admin_apply_acl",
    "v4_admin_access_context",
    "v4_admin_commit_upload",
    "v4_admin_create_folder",
    "v4_admin_create_team",
    "v4_admin_create_user",
    "v4_admin_delete_document",
    "v4_admin_delete_folder",
    "v4_admin_preview_acl",
    "v4_admin_rename_library_node",
    "v4_admin_reset_user",
    "v4_admin_set_user",
    "v4_admin_upload_preflight",
    "v4_append_audit",
    "v4_auth_lookup",
    "v4_begin_backup_run",
    "v4_begin_turn",
    "v4_bootstrap_admin",
    "v4_can_read_document",
    "v4_can_read_folder",
    "v4_can_view_library_node",
    "v4_change_password",
    "v4_claim_ingestion_job",
    "v4_claim_object_deletion",
    "v4_claim_service_lease",
    "v4_clear_login_failures",
    "v4_commit_ingestion_job",
    "v4_consume_activation",
    "v4_create_chat",
    "v4_current_actor_id",
    "v4_current_actor_is_admin",
    "v4_delete_chat",
    "v4_document_team_recipients",
    "v4_enforce_turn_source_immutability",
    "v4_fail_turn",
    "v4_finalize_turn",
    "v4_finish_backup_run",
    "v4_finish_ingestion_job",
    "v4_finish_object_deletion",
    "v4_get_job",
    "v4_heartbeat_ingestion_job",
    "v4_heartbeat_object_deletion",
    "v4_heartbeat_service_lease",
    "v4_interrupt_turn",
    "v4_issue_login_session",
    "v4_list_chats",
    "v4_login_blocked_until",
    "v4_logout",
    "v4_maintenance_document_snapshot_token",
    "v4_maintenance_get_document",
    "v4_maintenance_list_documents",
    "v4_maintenance_requeue_document",
    "v4_maintenance_storage_snapshot",
    "v4_mark_turn_access_revoked",
    "v4_mark_turn_access_revoked_trusted",
    "v4_mark_turn_citation_failed",
    "v4_mark_turn_length_limited",
    "v4_password_change_lookup",
    "v4_poison_ingestion_job",
    "v4_queue_expired_upload_orphans",
    "v4_readiness",
    "v4_rebuild_effective_document_access",
    "v4_record_login_failure",
    "v4_refresh_session",
    "v4_repair_interrupted_turns",
    "v4_rename_chat",
    "v4_replace_chat_scope",
    "v4_requeue_ingestion_job",
    "v4_require_admin",
    "v4_retry_turn",
    "v4_runtime_identity",
    "v4_schema_revision",
    "v4_session_view",
    "v4_store_turn_sources",
    "v4_update_ingestion_progress",
    "v4_upload_metadata_digest",
)
_EXPECTED_V4_REGPROCEDURES = (
    "public.v4_runtime_identity(text)",
    "public.v4_current_actor_id()",
    "public.v4_current_actor_is_admin()",
    "public.v4_activate_actor(text)",
    "public.v4_can_read_document(uuid)",
    "public.v4_can_read_folder(uuid)",
    "public.v4_can_view_library_node(uuid)",
    "public.v4_rebuild_effective_document_access()",
    "public.v4_bootstrap_admin(text,text,text)",
    "public.v4_maintenance_document_snapshot_token(uuid)",
    "public.v4_maintenance_get_document(uuid)",
    "public.v4_maintenance_list_documents()",
    "public.v4_maintenance_requeue_document(uuid,text,uuid,boolean)",
    "public.v4_maintenance_storage_snapshot()",
    "public.v4_schema_revision()",
    "public.v4_require_admin()",
    "public.v4_append_audit(text,text,uuid,jsonb)",
    "public.v4_auth_lookup(text,text)",
    "public.v4_login_blocked_until(text)",
    "public.v4_session_view(text)",
    "public.v4_refresh_session(text,text,timestamptz)",
    "public.v4_record_login_failure(text)",
    "public.v4_clear_login_failures(text)",
    "public.v4_issue_login_session(uuid,bigint,text,text,timestamptz,timestamptz)",
    "public.v4_logout(text)",
    "public.v4_consume_activation(text,text,text,text,timestamptz,timestamptz)",
    "public.v4_password_change_lookup(text)",
    "public.v4_change_password(text,bigint,text,text,text,timestamptz,timestamptz)",
    "public.v4_enforce_turn_source_immutability()",
    "public.v4_admin_create_user(text,text,text,text,timestamptz)",
    "public.v4_admin_reset_user(uuid,text,timestamptz)",
    "public.v4_admin_set_user(uuid,text,text)",
    "public.v4_admin_create_team(text,text)",
    "public.v4_account_active_teams()",
    "public.v4_acl_impact(jsonb)",
    "public.v4_admin_preview_acl(jsonb)",
    "public.v4_admin_apply_acl(uuid,text)",
    "public.v4_admin_access_context(uuid)",
    "public.v4_claim_service_lease(text,text,integer)",
    "public.v4_heartbeat_service_lease(text,text,uuid,bigint,integer)",
    "public.v4_get_job(uuid)",
    "public.v4_claim_ingestion_job(text,integer)",
    "public.v4_heartbeat_ingestion_job(uuid,uuid,bigint,integer)",
    "public.v4_update_ingestion_progress(uuid,uuid,bigint,text,integer,integer)",
    "public.v4_commit_ingestion_job(uuid,uuid,bigint,integer,jsonb)",
    "public.v4_requeue_ingestion_job(uuid,uuid,bigint,timestamptz)",
    "public.v4_poison_ingestion_job(uuid,uuid,bigint,text)",
    "public.v4_finish_ingestion_job(uuid,uuid,bigint,boolean,text)",
    "public.v4_claim_object_deletion(text,integer)",
    "public.v4_finish_object_deletion(uuid,uuid,bigint,boolean,text)",
    "public.v4_heartbeat_object_deletion(uuid,uuid,bigint,integer)",
    "public.v4_upload_metadata_digest(text,text,text,text,bigint,text,text,text)",
    "public.v4_admin_upload_preflight(text,text,text,text,text,text,bigint,text,text,text,uuid,uuid[])",
    "public.v4_admin_commit_upload(uuid,uuid,uuid,uuid,text,text,text,text,text,text,bigint,text,text,text,uuid,uuid[])",
    "public.v4_queue_expired_upload_orphans(integer)",
    "public.v4_admin_create_folder(uuid,uuid,text,text)",
    "public.v4_admin_rename_library_node(uuid,text,text)",
    "public.v4_admin_delete_folder(uuid)",
    "public.v4_document_team_recipients(uuid[])",
    "public.v4_list_chats()",
    "public.v4_create_chat(text,text)",
    "public.v4_rename_chat(uuid,text)",
    "public.v4_delete_chat(uuid)",
    "public.v4_replace_chat_scope(uuid,uuid[])",
    "public.v4_begin_turn(uuid,text,uuid,text)",
    "public.v4_store_turn_sources(uuid,uuid,jsonb)",
    "public.v4_fail_turn(uuid,uuid,text)",
    "public.v4_retry_turn(uuid,uuid,uuid)",
    "public.v4_interrupt_turn(uuid,uuid,uuid)",
    "public.v4_mark_turn_citation_failed(uuid,uuid,uuid,text)",
    "public.v4_mark_turn_length_limited(uuid,uuid,uuid,text)",
    "public.v4_admin_delete_document(uuid,uuid)",
    "public.v4_mark_turn_access_revoked(uuid,uuid)",
    "public.v4_mark_turn_access_revoked_trusted(uuid,uuid)",
    "public.v4_finalize_turn(uuid,uuid,text,boolean,smallint[])",
    "public.v4_repair_interrupted_turns()",
    "public.v4_begin_backup_run(text)",
    "public.v4_finish_backup_run(uuid,boolean,text,text,bigint,bigint,text)",
    "public.v4_readiness()",
)
_EXPECTED_POLICIES = {
    "documents": "v4_can_read_document(id)",
    "chunks": "v4_can_read_document(document_id)",
    "library_nodes": "v4_can_view_library_node(id)",
    "ingestion_jobs": (
        "v4_current_actor_is_admin() AND v4_can_read_document(document_id)"
    ),
    "chats": "owner_user_id = v4_current_actor_id()",
    "chat_scopes": (
        "EXISTS (SELECT 1 FROM chats AS c WHERE c.id = chat_id "
        "AND c.owner_user_id = v4_current_actor_id())"
    ),
    "chat_turns": (
        "EXISTS (SELECT 1 FROM chats AS c WHERE c.id = chat_id "
        "AND c.owner_user_id = v4_current_actor_id())"
    ),
    "turn_sources": (
        "EXISTS (SELECT 1 FROM chat_turns AS t JOIN chats AS c "
        "ON c.id = t.chat_id WHERE t.id = turn_id "
        "AND c.owner_user_id = v4_current_actor_id())"
    ),
    "turn_citations": (
        "EXISTS (SELECT 1 FROM chat_turns AS t JOIN chats AS c "
        "ON c.id = t.chat_id WHERE t.id = turn_id "
        "AND c.owner_user_id = v4_current_actor_id())"
    ),
    "users": "id = v4_current_actor_id() OR v4_current_actor_is_admin()",
    "sessions": "user_id = v4_current_actor_id() OR v4_current_actor_is_admin()",
    "teams": "v4_current_actor_is_admin()",
    "team_members": "v4_current_actor_is_admin()",
    "access_grants": "v4_current_actor_is_admin()",
    "effective_document_access": (
        "user_id = v4_current_actor_id() OR v4_current_actor_is_admin()"
    ),
    "acl_previews": (
        "actor_user_id = v4_current_actor_id() AND v4_current_actor_is_admin()"
    ),
    "audit_events": "v4_current_actor_is_admin()",
    "pre_auth_challenges": "false",
    "login_throttles": "false",
    "security_epochs": "false",
    "object_deletions": "false",
    "upload_reservations": "false",
    "service_leases": "false",
    "backup_runs": "false",
}
_EXPECTED_FUNCTION_GRANTS = {
    "rag_api": (
        "v4_activate_actor",
        "v4_current_actor_id",
        "v4_current_actor_is_admin",
        "v4_can_read_document",
        "v4_can_read_folder",
        "v4_can_view_library_node",
        "v4_schema_revision",
        "v4_readiness",
        "v4_runtime_identity",
        "v4_auth_lookup",
        "v4_login_blocked_until",
        "v4_session_view",
        "v4_refresh_session",
        "v4_record_login_failure",
        "v4_clear_login_failures",
        "v4_issue_login_session",
        "v4_logout",
        "v4_consume_activation",
        "v4_password_change_lookup",
        "v4_change_password",
        "v4_admin_create_user",
        "v4_admin_reset_user",
        "v4_admin_set_user",
        "v4_admin_create_team",
        "v4_account_active_teams",
        "v4_document_team_recipients",
        "v4_admin_access_context",
        "v4_admin_preview_acl",
        "v4_admin_apply_acl",
        "v4_admin_create_folder",
        "v4_admin_rename_library_node",
        "v4_admin_delete_folder",
        "v4_admin_upload_preflight",
        "v4_admin_commit_upload",
        "v4_admin_delete_document",
        "v4_get_job",
        "v4_list_chats",
        "v4_create_chat",
        "v4_rename_chat",
        "v4_delete_chat",
        "v4_replace_chat_scope",
        "v4_begin_turn",
        "v4_store_turn_sources",
        "v4_fail_turn",
        "v4_interrupt_turn",
        "v4_retry_turn",
        "v4_finalize_turn",
        "v4_mark_turn_access_revoked",
        "v4_mark_turn_access_revoked_trusted",
        "v4_mark_turn_citation_failed",
        "v4_mark_turn_length_limited",
    ),
    "rag_worker": (
        "v4_runtime_identity",
        "v4_claim_service_lease",
        "v4_heartbeat_service_lease",
        "v4_claim_ingestion_job",
        "v4_heartbeat_ingestion_job",
        "v4_update_ingestion_progress",
        "v4_commit_ingestion_job",
        "v4_requeue_ingestion_job",
        "v4_poison_ingestion_job",
        "v4_queue_expired_upload_orphans",
        "v4_claim_object_deletion",
        "v4_heartbeat_object_deletion",
        "v4_finish_object_deletion",
    ),
    "rag_maintenance": (
        "v4_runtime_identity",
        "v4_rebuild_effective_document_access",
        "v4_maintenance_get_document",
        "v4_maintenance_list_documents",
        "v4_maintenance_requeue_document",
        "v4_maintenance_storage_snapshot",
        "v4_repair_interrupted_turns",
        "v4_queue_expired_upload_orphans",
        "v4_bootstrap_admin",
        "v4_schema_revision",
        "v4_readiness",
        "v4_claim_service_lease",
        "v4_heartbeat_service_lease",
        "v4_begin_backup_run",
        "v4_finish_backup_run",
    ),
    "rag_migrator": ("v4_schema_revision",),
    "rag_backup": ("v4_schema_revision",),
}

# BEGIN GENERATED BASELINE DDL
_BASELINE_DDL: tuple[str, ...] = (
    "CREATE TABLE backup_runs (\n"
    "\tid UUID NOT NULL, \n"
    "\tstatus VARCHAR(16) NOT NULL, \n"
    "\tdestination_id VARCHAR(255) NOT NULL, \n"
    "\tdatabase_sha256 VARCHAR(64), \n"
    "\tstorage_manifest_sha256 VARCHAR(64), \n"
    "\tdatabase_bytes BIGINT, \n"
    "\tstorage_bytes BIGINT, \n"
    "\terror_code VARCHAR(80), \n"
    "\tstarted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tfinished_at TIMESTAMP WITH TIME ZONE, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_backup_runs_status CHECK (status IN "
    "('running','succeeded','failed')), \n"
    "\tCONSTRAINT ck_backup_runs_finished_order CHECK (finished_at IS NULL OR finished_at "
    ">= started_at), \n"
    "\tCONSTRAINT ck_backup_runs_status_consistency CHECK ((status = 'running' AND "
    "finished_at IS NULL) OR (status IN ('succeeded','failed') AND finished_at IS NOT "
    "NULL)), \n"
    "\tCONSTRAINT ck_backup_runs_database_bytes CHECK (database_bytes IS NULL OR "
    "database_bytes >= 0), \n"
    "\tCONSTRAINT ck_backup_runs_storage_bytes CHECK (storage_bytes IS NULL OR "
    "storage_bytes >= 0), \n"
    "\tCONSTRAINT ck_backup_runs_destination_nonempty CHECK (char_length(destination_id) "
    "> 0), \n"
    "\tCONSTRAINT ck_backup_runs_database_sha256 CHECK (database_sha256 IS NULL OR "
    "database_sha256 ~ '^[0-9a-f]{64}$'), \n"
    "\tCONSTRAINT ck_backup_runs_storage_manifest_sha256 CHECK (storage_manifest_sha256 "
    "IS NULL OR storage_manifest_sha256 ~ '^[0-9a-f]{64}$'), \n"
    "\tCONSTRAINT ck_backup_runs_result_consistency CHECK ((status = 'succeeded' AND "
    "database_sha256 IS NOT NULL AND storage_manifest_sha256 IS NOT NULL AND "
    "database_bytes IS NOT NULL AND storage_bytes IS NOT NULL AND error_code IS NULL) OR "
    "(status = 'failed' AND error_code IS NOT NULL) OR (status = 'running' AND "
    "database_sha256 IS NULL AND storage_manifest_sha256 IS NULL AND database_bytes IS "
    "NULL AND storage_bytes IS NULL AND error_code IS NULL))\n"
    ")",
    "CREATE TABLE documents (\n"
    "\tid UUID NOT NULL, \n"
    "\tsha256 VARCHAR(64) NOT NULL, \n"
    "\toriginal_filename VARCHAR(512) NOT NULL, \n"
    "\tmime_type VARCHAR(127) NOT NULL, \n"
    "\tbyte_size BIGINT NOT NULL, \n"
    "\tobject_key VARCHAR(512) NOT NULL, \n"
    "\tstate VARCHAR(32) NOT NULL, \n"
    "\tstage VARCHAR(32) NOT NULL, \n"
    "\terror TEXT, \n"
    "\tparser_version VARCHAR(64) NOT NULL, \n"
    "\tchunking_version VARCHAR(64) NOT NULL, \n"
    "\tembedding_version VARCHAR(128) NOT NULL, \n"
    "\tpage_count INTEGER, \n"
    "\tchunk_count INTEGER NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_documents_byte_size_nonnegative CHECK (byte_size >= 0), \n"
    "\tCONSTRAINT ck_documents_state CHECK (state IN "
    "('uploaded','parsing','chunking','embedding','indexing','ready','failed')), \n"
    "\tCONSTRAINT ck_documents_stage CHECK (stage IN "
    "('uploaded','parsing','chunking','embedding','indexing','ready','failed')), \n"
    "\tCONSTRAINT ck_documents_page_count_nonnegative CHECK (page_count IS NULL OR "
    "page_count >= 0), \n"
    "\tCONSTRAINT ck_documents_chunk_count_nonnegative CHECK (chunk_count >= 0), \n"
    "\tCONSTRAINT ck_documents_object_key_nonempty CHECK (char_length(object_key) > 0), \n"
    "\tCONSTRAINT ck_documents_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'), \n"
    "\tCONSTRAINT ck_documents_pdf_only CHECK (mime_type = 'application/pdf'), \n"
    "\tCONSTRAINT ck_documents_state_stage CHECK (state = stage), \n"
    "\tCONSTRAINT ck_documents_error_state CHECK ((state = 'failed' AND error IS NOT "
    "NULL) OR (state <> 'failed' AND error IS NULL))\n"
    ")",
    "CREATE TABLE login_throttles (\n"
    '\tkey_hash VARCHAR(64) COLLATE "C" NOT NULL, \n'
    "\tfailure_count INTEGER DEFAULT '0' NOT NULL, \n"
    "\tfirst_failure_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tblocked_until TIMESTAMP WITH TIME ZONE, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (key_hash), \n"
    "\tCONSTRAINT ck_login_throttles_failure_count CHECK (failure_count >= 0)\n"
    ")",
    "CREATE TABLE object_deletions (\n"
    "\tid UUID NOT NULL, \n"
    "\tobject_key VARCHAR(512) NOT NULL, \n"
    "\tstatus VARCHAR(16) DEFAULT 'queued' NOT NULL, \n"
    "\tattempt INTEGER DEFAULT '0' NOT NULL, \n"
    "\tavailable_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tlease_expires_at TIMESTAMP WITH TIME ZONE, \n"
    "\tlease_token UUID, \n"
    "\tlease_owner VARCHAR(255), \n"
    "\tfencing_token BIGINT DEFAULT '0' NOT NULL, \n"
    "\tlast_error VARCHAR(2000), \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_object_deletions_status CHECK (status IN "
    "('queued','leased','failed')), \n"
    "\tCONSTRAINT ck_object_deletions_attempt_nonnegative CHECK (attempt >= 0), \n"
    "\tCONSTRAINT ck_object_deletions_object_key_nonempty CHECK (char_length(object_key) "
    "> 0), \n"
    "\tCONSTRAINT ck_object_deletions_last_error_bounded CHECK (last_error IS NULL OR "
    "char_length(last_error) <= 2000), \n"
    "\tCONSTRAINT ck_object_deletions_lease_consistency CHECK ((status = 'queued' AND "
    "lease_expires_at IS NULL AND lease_token IS NULL AND lease_owner IS NULL) OR (status "
    "= 'leased' AND lease_expires_at IS NOT NULL AND lease_token IS NOT NULL AND "
    "lease_owner IS NOT NULL) OR (status = 'failed' AND lease_expires_at IS NULL AND "
    "lease_token IS NULL AND lease_owner IS NULL)), \n"
    "\tCONSTRAINT ck_object_deletions_fencing_nonnegative CHECK (fencing_token >= 0)\n"
    ")",
    "CREATE TABLE security_epochs (\n"
    "\tsingleton BOOLEAN DEFAULT 'true' NOT NULL, \n"
    "\tauthentication_version BIGINT DEFAULT '1' NOT NULL, \n"
    "\tauthorization_version BIGINT DEFAULT '1' NOT NULL, \n"
    "\tsession_epoch UUID NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (singleton), \n"
    "\tCONSTRAINT ck_security_epochs_singleton CHECK (singleton = true), \n"
    "\tCONSTRAINT ck_security_epochs_versions CHECK (authentication_version >= 1 AND "
    "authorization_version >= 1)\n"
    ")",
    "CREATE TABLE service_leases (\n"
    "\tservice_name VARCHAR(32) NOT NULL, \n"
    "\towner_id VARCHAR(255) NOT NULL, \n"
    "\tlease_token UUID NOT NULL, \n"
    "\tfencing_token BIGINT NOT NULL, \n"
    "\theartbeat_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tlease_expires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (service_name), \n"
    "\tCONSTRAINT ck_service_leases_name CHECK (service_name IN "
    "('ingestion_worker','deletion_worker','inference_coordinator','ocr_service')), \n"
    "\tCONSTRAINT ck_service_leases_owner CHECK (char_length(owner_id) BETWEEN 1 AND "
    "255), \n"
    "\tCONSTRAINT ck_service_leases_fencing_positive CHECK (fencing_token >= 1), \n"
    "\tCONSTRAINT ck_service_leases_expiry_order CHECK (heartbeat_at <= "
    "lease_expires_at)\n"
    ")",
    "CREATE TABLE teams (\n"
    "\tid UUID NOT NULL, \n"
    "\tname VARCHAR(80) NOT NULL, \n"
    '\tname_key VARCHAR(160) COLLATE "C" NOT NULL, \n'
    "\tis_active BOOLEAN DEFAULT 'true' NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_teams_name_length CHECK (char_length(name) BETWEEN 1 AND 80)\n"
    ")",
    "CREATE TABLE users (\n"
    "\tid UUID NOT NULL, \n"
    '\tusername VARCHAR(32) COLLATE "C" NOT NULL, \n'
    "\tdisplay_name VARCHAR(80) NOT NULL, \n"
    "\trole VARCHAR(16) NOT NULL, \n"
    "\tstatus VARCHAR(24) NOT NULL, \n"
    "\tpassword_hash TEXT, \n"
    "\tauthentication_version BIGINT DEFAULT '1' NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_users_username_format CHECK (username ~ "
    "'^[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]$'), \n"
    "\tCONSTRAINT ck_users_display_name_length CHECK (char_length(display_name) BETWEEN 1 "
    "AND 80), \n"
    "\tCONSTRAINT ck_users_display_name_no_controls CHECK (display_name !~ "
    "'[[:cntrl:]]'), \n"
    "\tCONSTRAINT ck_users_role CHECK (role IN ('admin','member')), \n"
    "\tCONSTRAINT ck_users_status CHECK (status IN "
    "('pending_activation','active','disabled','deleted')), \n"
    "\tCONSTRAINT ck_users_active_password CHECK ((status = 'active' AND password_hash IS "
    "NOT NULL) OR (status <> 'active')), \n"
    "\tCONSTRAINT ck_users_deleted_irreversible CHECK ((status = 'deleted' AND "
    "password_hash IS NULL AND deleted_at IS NOT NULL) OR (status <> 'deleted' AND "
    "deleted_at IS NULL)), \n"
    "\tCONSTRAINT ck_users_auth_version CHECK (authentication_version >= 1)\n"
    ")",
    "CREATE TABLE acl_previews (\n"
    "\tid UUID NOT NULL, \n"
    "\tactor_user_id UUID NOT NULL, \n"
    "\toperation JSONB NOT NULL, \n"
    "\timpact_digest VARCHAR(64) NOT NULL, \n"
    "\tauthorization_version BIGINT NOT NULL, \n"
    "\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tconsumed_at TIMESTAMP WITH TIME ZONE, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_acl_previews_digest_length CHECK (char_length(impact_digest) = "
    "64), \n"
    "\tCONSTRAINT ck_acl_previews_expiry CHECK (expires_at > created_at), \n"
    "\tFOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE CASCADE\n"
    ")",
    "CREATE TABLE audit_events (\n"
    "\tid UUID NOT NULL, \n"
    "\tactor_user_id UUID, \n"
    "\tevent_type VARCHAR(80) NOT NULL, \n"
    "\ttarget_type VARCHAR(80), \n"
    "\ttarget_id UUID, \n"
    "\tdetails JSONB NOT NULL, \n"
    "\tcorrelation_id UUID, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_audit_events_type_length CHECK (char_length(event_type) BETWEEN 1 "
    "AND 80), \n"
    "\tFOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL\n"
    ")",
    "CREATE TABLE chats (\n"
    "\tid UUID NOT NULL, \n"
    "\ttitle VARCHAR(255) DEFAULT 'New chat' NOT NULL, \n"
    "\ttitle_is_manual BOOLEAN DEFAULT 'false' NOT NULL, \n"
    "\tscope_mode VARCHAR(16) DEFAULT 'all_ready' NOT NULL, \n"
    "\tscope_version BIGINT DEFAULT '1' NOT NULL, \n"
    "\tnext_turn_ordinal BIGINT DEFAULT '1' NOT NULL, \n"
    "\towner_user_id UUID NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_chats_scope_mode CHECK (scope_mode IN ('all_ready','selected')), \n"
    "\tCONSTRAINT ck_chats_scope_version_positive CHECK (scope_version >= 1), \n"
    "\tCONSTRAINT ck_chats_next_turn_ordinal_positive CHECK (next_turn_ordinal >= 1), \n"
    "\tCONSTRAINT ck_chats_title_length CHECK (char_length(title) BETWEEN 1 AND 255), \n"
    "\tFOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT\n"
    ")",
    "CREATE TABLE chunks (\n"
    "\tid UUID NOT NULL, \n"
    "\tdocument_id UUID NOT NULL, \n"
    "\tordinal INTEGER NOT NULL, \n"
    "\tfilename VARCHAR(512) NOT NULL, \n"
    "\tpage_start INTEGER NOT NULL, \n"
    "\tpage_end INTEGER NOT NULL, \n"
    "\tsection TEXT, \n"
    "\ttext TEXT NOT NULL, \n"
    "\ttoken_count INTEGER NOT NULL, \n"
    "\ttext_sha256 VARCHAR(64) NOT NULL, \n"
    "\tsource_sha256 VARCHAR(64) NOT NULL, \n"
    "\tparse_method VARCHAR(16) NOT NULL, \n"
    "\tparser_version VARCHAR(64) NOT NULL, \n"
    "\tchunking_version VARCHAR(64) NOT NULL, \n"
    "\tembedding_version VARCHAR(128) NOT NULL, \n"
    "\tschema_version VARCHAR(64) NOT NULL, \n"
    "\tcitation_label VARCHAR(64) NOT NULL, \n"
    "\tembedding VECTOR(1024) NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_chunks_ordinal_nonnegative CHECK (ordinal >= 0), \n"
    "\tCONSTRAINT ck_chunks_page_start_positive CHECK (page_start >= 1), \n"
    "\tCONSTRAINT ck_chunks_page_range_ordered CHECK (page_end >= page_start), \n"
    "\tCONSTRAINT ck_chunks_token_count_positive CHECK (token_count > 0), \n"
    "\tCONSTRAINT ck_chunks_parse_method CHECK (parse_method IN ('direct','ocr')), \n"
    "\tCONSTRAINT ck_chunks_text_sha256 CHECK (text_sha256 ~ '^[0-9a-f]{64}$'), \n"
    "\tCONSTRAINT ck_chunks_source_sha256 CHECK (source_sha256 ~ '^[0-9a-f]{64}$'), \n"
    "\tCONSTRAINT ck_chunks_text_nonempty CHECK (char_length(text) > 0), \n"
    "\tFOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE\n"
    ")",
    "CREATE TABLE effective_document_access (\n"
    "\tuser_id UUID NOT NULL, \n"
    "\tdocument_id UUID NOT NULL, \n"
    "\tauthorization_version BIGINT NOT NULL, \n"
    "\tcomputed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (user_id, document_id), \n"
    "\tCONSTRAINT uq_effective_document_access UNIQUE (user_id, document_id), \n"
    "\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, \n"
    "\tFOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE\n"
    ")",
    "CREATE TABLE ingestion_jobs (\n"
    "\tid UUID NOT NULL, \n"
    "\tdocument_id UUID NOT NULL, \n"
    "\tstatus VARCHAR(32) NOT NULL, \n"
    "\tstage VARCHAR(32) NOT NULL, \n"
    "\tattempt INTEGER NOT NULL, \n"
    "\tcompleted_units INTEGER NOT NULL, \n"
    "\ttotal_units INTEGER, \n"
    "\tavailable_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tstarted_at TIMESTAMP WITH TIME ZONE, \n"
    "\theartbeat_at TIMESTAMP WITH TIME ZONE, \n"
    "\tlease_expires_at TIMESTAMP WITH TIME ZONE, \n"
    "\tlease_token UUID, \n"
    "\tlease_owner VARCHAR(255), \n"
    "\tfencing_token BIGINT DEFAULT '0' NOT NULL, \n"
    "\terror TEXT, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_ingestion_jobs_status CHECK (status IN "
    "('queued','running','completed','failed','interrupted')), \n"
    "\tCONSTRAINT ck_ingestion_jobs_attempt_nonnegative CHECK (attempt >= 0), \n"
    "\tCONSTRAINT ck_ingestion_jobs_completed_nonnegative CHECK (completed_units >= 0), \n"
    "\tCONSTRAINT ck_ingestion_jobs_total_units CHECK (total_units IS NULL OR total_units "
    ">= completed_units), \n"
    "\tCONSTRAINT ck_ingestion_jobs_stage CHECK (stage IN "
    "('uploaded','parsing','chunking','embedding','indexing','ready','failed')), \n"
    "\tCONSTRAINT ck_ingestion_jobs_lease_consistency CHECK ((status = 'running' AND "
    "lease_expires_at IS NOT NULL AND lease_token IS NOT NULL AND lease_owner IS NOT "
    "NULL) OR (status <> 'running' AND lease_expires_at IS NULL AND lease_token IS NULL "
    "AND lease_owner IS NULL)), \n"
    "\tCONSTRAINT ck_ingestion_jobs_fencing_nonnegative CHECK (fencing_token >= 0), \n"
    "\tCONSTRAINT ck_ingestion_jobs_state_consistency CHECK ((status = 'completed' AND "
    "stage = 'ready' AND error IS NULL) OR (status = 'failed' AND stage = 'failed' AND "
    "error IS NOT NULL) OR (status IN ('queued','running') AND stage <> 'ready' AND error "
    "IS NULL) OR (status = 'interrupted' AND stage <> 'ready')), \n"
    "\tFOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE\n"
    ")",
    "CREATE TABLE library_nodes (\n"
    "\tid UUID NOT NULL, \n"
    "\tparent_id UUID, \n"
    "\tkind VARCHAR(16) NOT NULL, \n"
    '\tname VARCHAR(255) COLLATE "C" NOT NULL, \n'
    '\tname_key TEXT COLLATE "C" NOT NULL, \n'
    "\tdocument_id UUID, \n"
    "\tuploader_user_id UUID, \n"
    "\taccess_boundary BOOLEAN DEFAULT 'false' NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_library_nodes_kind CHECK (kind IN ('folder','file')), \n"
    "\tCONSTRAINT ck_library_nodes_kind_document CHECK ((kind = 'folder' AND document_id "
    "IS NULL) OR (kind = 'file' AND document_id IS NOT NULL)), \n"
    "\tCONSTRAINT ck_library_nodes_kind_uploader CHECK ((kind = 'folder' AND "
    "uploader_user_id IS NULL) OR (kind = 'file' AND uploader_user_id IS NOT NULL)), \n"
    "\tCONSTRAINT ck_library_nodes_boundary_folder CHECK (access_boundary = false OR kind "
    "= 'folder'), \n"
    "\tCONSTRAINT ck_library_nodes_name_length CHECK (char_length(name) BETWEEN 1 AND "
    "255), \n"
    "\tCONSTRAINT ck_library_nodes_name_key_bytes CHECK (octet_length(name_key) <= "
    "1024), \n"
    "\tCONSTRAINT ck_library_nodes_name_key_nonempty CHECK (char_length(name_key) > 0), \n"
    "\tCONSTRAINT uq_library_nodes_parent_name_key UNIQUE NULLS NOT DISTINCT (parent_id, "
    "name_key), \n"
    "\tFOREIGN KEY(parent_id) REFERENCES library_nodes (id) ON DELETE RESTRICT, \n"
    "\tFOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, \n"
    "\tFOREIGN KEY(uploader_user_id) REFERENCES users (id) ON DELETE RESTRICT\n"
    ")",
    "CREATE TABLE pre_auth_challenges (\n"
    "\tid UUID NOT NULL, \n"
    "\tuser_id UUID NOT NULL, \n"
    "\tpurpose VARCHAR(24) NOT NULL, \n"
    '\ttoken_hash VARCHAR(64) COLLATE "C" NOT NULL, \n'
    "\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tconsumed_at TIMESTAMP WITH TIME ZONE, \n"
    "\trevoked_at TIMESTAMP WITH TIME ZONE, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_pre_auth_challenges_purpose CHECK (purpose IN "
    "('activation','password_reset')), \n"
    "\tCONSTRAINT ck_pre_auth_challenges_token_hash_length CHECK (char_length(token_hash) "
    "= 64), \n"
    "\tCONSTRAINT ck_pre_auth_challenges_expiry CHECK (expires_at > created_at), \n"
    "\tCONSTRAINT ck_pre_auth_challenges_max_expiry CHECK (expires_at <= created_at + "
    "interval '30 minutes'), \n"
    "\tCONSTRAINT ck_pre_auth_challenges_terminal_state CHECK (consumed_at IS NULL OR "
    "revoked_at IS NULL), \n"
    "\tCONSTRAINT ck_pre_auth_challenges_consumed_order CHECK (consumed_at IS NULL OR "
    "consumed_at >= created_at), \n"
    "\tCONSTRAINT ck_pre_auth_challenges_revoked_order CHECK (revoked_at IS NULL OR "
    "revoked_at >= created_at), \n"
    "\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE\n"
    ")",
    "CREATE TABLE sessions (\n"
    "\tid UUID NOT NULL, \n"
    "\tuser_id UUID NOT NULL, \n"
    '\ttoken_hash VARCHAR(64) COLLATE "C" NOT NULL, \n'
    '\tcsrf_token_hash VARCHAR(64) COLLATE "C" NOT NULL, \n'
    "\tissued_authentication_version BIGINT NOT NULL, \n"
    "\tissued_authentication_epoch BIGINT NOT NULL, \n"
    "\tissued_session_epoch UUID NOT NULL, \n"
    "\tissued_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tlast_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tidle_expires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tabsolute_expires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\trecent_reauthenticated_at TIMESTAMP WITH TIME ZONE, \n"
    "\trevoked_at TIMESTAMP WITH TIME ZONE, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_sessions_token_hash_length CHECK (char_length(token_hash) = 64), \n"
    "\tCONSTRAINT ck_sessions_csrf_hash_length CHECK (char_length(csrf_token_hash) = "
    "64), \n"
    "\tCONSTRAINT ck_sessions_expiry_order CHECK (issued_at <= last_seen_at AND "
    "last_seen_at <= idle_expires_at AND idle_expires_at = absolute_expires_at), \n"
    "\tCONSTRAINT ck_sessions_reauthentication_order CHECK (recent_reauthenticated_at IS "
    "NULL OR (recent_reauthenticated_at >= issued_at AND recent_reauthenticated_at <= "
    "last_seen_at)), \n"
    "\tCONSTRAINT ck_sessions_versions_positive CHECK (issued_authentication_version >= 1 "
    "AND issued_authentication_epoch >= 1), \n"
    "\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE\n"
    ")",
    "CREATE TABLE team_members (\n"
    "\tteam_id UUID NOT NULL, \n"
    "\tuser_id UUID NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (team_id, user_id), \n"
    "\tFOREIGN KEY(team_id) REFERENCES teams (id) ON DELETE CASCADE, \n"
    "\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE\n"
    ")",
    "CREATE TABLE access_grants (\n"
    "\tid UUID NOT NULL, \n"
    "\tnode_id UUID NOT NULL, \n"
    "\tuser_id UUID, \n"
    "\tteam_id UUID, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_access_grants_one_principal CHECK ((user_id IS NOT NULL AND team_id "
    "IS NULL) OR (user_id IS NULL AND team_id IS NOT NULL)), \n"
    "\tCONSTRAINT uq_access_grants_node_principal UNIQUE NULLS NOT DISTINCT (node_id, "
    "user_id, team_id), \n"
    "\tFOREIGN KEY(node_id) REFERENCES library_nodes (id) ON DELETE CASCADE, \n"
    "\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, \n"
    "\tFOREIGN KEY(team_id) REFERENCES teams (id) ON DELETE CASCADE\n"
    ")",
    "CREATE TABLE chat_scopes (\n"
    "\tchat_id UUID NOT NULL, \n"
    "\tnode_id UUID NOT NULL, \n"
    "\tPRIMARY KEY (chat_id, node_id), \n"
    "\tFOREIGN KEY(chat_id) REFERENCES chats (id) ON DELETE CASCADE, \n"
    "\tFOREIGN KEY(node_id) REFERENCES library_nodes (id) ON DELETE CASCADE\n"
    ")",
    "CREATE TABLE chat_turns (\n"
    "\tid UUID NOT NULL, \n"
    "\tchat_id UUID NOT NULL, \n"
    "\tordinal BIGINT NOT NULL, \n"
    "\tquestion TEXT NOT NULL, \n"
    "\tstatus VARCHAR(16) NOT NULL, \n"
    "\tattempt INTEGER DEFAULT '1' NOT NULL, \n"
    "\tscope_version BIGINT NOT NULL, \n"
    "\tgeneration_token UUID, \n"
    "\tfinal_answer TEXT, \n"
    "\tpartial_answer TEXT, \n"
    "\tinsufficient_context BOOLEAN DEFAULT 'false' NOT NULL, \n"
    "\terror VARCHAR(500), \n"
    "\tcompleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_chat_turns_ordinal_positive CHECK (ordinal >= 1), \n"
    "\tCONSTRAINT ck_chat_turns_attempt_positive CHECK (attempt >= 1), \n"
    "\tCONSTRAINT ck_chat_turns_scope_version_positive CHECK (scope_version >= 1), \n"
    "\tCONSTRAINT ck_chat_turns_question_length CHECK (char_length(question) BETWEEN 1 "
    "AND 2000), \n"
    "\tCONSTRAINT ck_chat_turns_status CHECK (status IN "
    "('generating','complete','failed','interrupted','length_limited',"
    "'citation_failed','access_revoked')), \n"
    "\tCONSTRAINT ck_chat_turns_status_consistency CHECK ((status = 'generating' AND "
    "generation_token IS NOT NULL AND final_answer IS NULL AND error IS NULL AND "
    "(partial_answer IS NULL OR char_length(partial_answer) > 0) AND "
    "completed_at IS NULL AND insufficient_context = false) OR (status = 'complete' AND "
    "generation_token IS NULL AND final_answer IS NOT NULL AND char_length(final_answer) "
    "> 0 AND partial_answer IS NULL AND error IS NULL AND completed_at IS NOT NULL) OR "
    "(status = 'length_limited' AND generation_token IS NULL AND final_answer IS NULL "
    "AND partial_answer IS NOT NULL AND char_length(partial_answer) > 0 AND "
    "completed_at IS NULL AND error = 'response reached generation limit' AND "
    "insufficient_context = false) OR (status = 'citation_failed' AND "
    "generation_token IS NULL AND final_answer IS NULL AND partial_answer IS NOT NULL "
    "AND char_length(partial_answer) > 0 AND completed_at IS NULL AND "
    "error = 'citation validation failed' AND insufficient_context = false) OR "
    "(status IN "
    "('failed','interrupted','access_revoked') AND generation_token IS NULL AND "
    "final_answer IS NULL AND partial_answer IS NULL AND completed_at IS NULL AND "
    "error IS NOT NULL AND "
    "char_length(error) BETWEEN 1 AND 500 AND insufficient_context = false)), \n"
    "\tCONSTRAINT uq_chat_turns_chat_ordinal UNIQUE (chat_id, ordinal), \n"
    "\tFOREIGN KEY(chat_id) REFERENCES chats (id) ON DELETE CASCADE\n"
    ")",
    "CREATE TABLE upload_reservations (\n"
    "\tid UUID NOT NULL, \n"
    "\tactor_user_id UUID NOT NULL, \n"
    '\tsha256 VARCHAR(64) COLLATE "C" NOT NULL, \n'
    "\tobject_key VARCHAR(512) NOT NULL, \n"
    "\tparent_id UUID, \n"
    "\tselected_team_ids UUID[] NOT NULL, \n"
    '\tmetadata_digest VARCHAR(64) COLLATE "C" NOT NULL, \n'
    "\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tconsumed_at TIMESTAMP WITH TIME ZONE, \n"
    "\toutcome VARCHAR(16), \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_upload_reservations_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'), \n"
    "\tCONSTRAINT ck_upload_reservations_metadata_digest CHECK (metadata_digest ~ "
    "'^[0-9a-f]{64}$'), \n"
    "\tCONSTRAINT ck_upload_reservations_expiry CHECK (expires_at > created_at), \n"
    "\tCONSTRAINT ck_upload_reservations_terminal_state CHECK ((consumed_at IS NULL AND "
    "outcome IS NULL) OR (consumed_at IS NOT NULL AND outcome IS NOT NULL)), \n"
    "\tCONSTRAINT ck_upload_reservations_outcome CHECK (outcome IS NULL OR outcome IN "
    "('created','duplicate','expired')), \n"
    "\tFOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE CASCADE, \n"
    "\tFOREIGN KEY(parent_id) REFERENCES library_nodes (id) ON DELETE RESTRICT\n"
    ")",
    "CREATE TABLE turn_sources (\n"
    "\tturn_id UUID NOT NULL, \n"
    "\trank SMALLINT NOT NULL, \n"
    "\tlabel VARCHAR(16) NOT NULL, \n"
    "\tdocument_id UUID, \n"
    "\tchunk_id UUID, \n"
    "\tdocument_id_snapshot UUID NOT NULL, \n"
    "\tchunk_id_snapshot UUID NOT NULL, \n"
    "\toriginal_filename VARCHAR(512) NOT NULL, \n"
    "\tdisplay_name VARCHAR(255) NOT NULL, \n"
    "\tlogical_path TEXT NOT NULL, \n"
    "\tpage_start INTEGER NOT NULL, \n"
    "\tpage_end INTEGER NOT NULL, \n"
    "\tsection TEXT, \n"
    "\tsource_sha256 VARCHAR(64) NOT NULL, \n"
    "\ttext_sha256 VARCHAR(64) NOT NULL, \n"
    "\tretrieval_distance FLOAT NOT NULL, \n"
    "\trerank_score FLOAT NOT NULL, \n"
    "\tsnapshot_text TEXT NOT NULL, \n"
    "\ttoken_count INTEGER NOT NULL, \n"
    "\towner_authorized_at_deletion BOOLEAN, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (turn_id, rank), \n"
    "\tCONSTRAINT ck_turn_sources_rank CHECK (rank BETWEEN 1 AND 8), \n"
    "\tCONSTRAINT ck_turn_sources_label CHECK (label = 'S' || rank::text), \n"
    "\tCONSTRAINT ck_turn_sources_page_range CHECK (page_start >= 1 AND page_end >= "
    "page_start), \n"
    "\tCONSTRAINT ck_turn_sources_source_sha256 CHECK (source_sha256 ~ "
    "'^[0-9a-f]{64}$'), \n"
    "\tCONSTRAINT ck_turn_sources_text_sha256 CHECK (text_sha256 ~ '^[0-9a-f]{64}$'), \n"
    "\tCONSTRAINT ck_turn_sources_token_count_positive CHECK (token_count > 0), \n"
    "\tCONSTRAINT ck_turn_sources_deleted_disposition CHECK (document_id IS NOT NULL OR "
    "owner_authorized_at_deletion IS NOT NULL), \n"
    "\tCONSTRAINT uq_turn_sources_turn_label UNIQUE (turn_id, label), \n"
    "\tFOREIGN KEY(turn_id) REFERENCES chat_turns (id) ON DELETE CASCADE, \n"
    "\tFOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE SET NULL, \n"
    "\tFOREIGN KEY(chunk_id) REFERENCES chunks (id) ON DELETE SET NULL\n"
    ")",
    "CREATE TABLE turn_citations (\n"
    "\tturn_id UUID NOT NULL, \n"
    "\tordinal SMALLINT NOT NULL, \n"
    "\tsource_rank SMALLINT NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tPRIMARY KEY (turn_id, ordinal), \n"
    "\tCONSTRAINT ck_turn_citations_ordinal_positive CHECK (ordinal >= 1), \n"
    "\tCONSTRAINT ck_turn_citations_source_rank CHECK (source_rank BETWEEN 1 AND 8), \n"
    "\tCONSTRAINT fk_turn_citations_source FOREIGN KEY(turn_id, source_rank) REFERENCES "
    "turn_sources (turn_id, rank) ON DELETE CASCADE, \n"
    "\tCONSTRAINT uq_turn_citations_turn_source UNIQUE (turn_id, source_rank)\n"
    ")",
    "CREATE INDEX ix_backup_runs_started_id ON backup_runs (started_at DESC, id DESC)",
    "CREATE UNIQUE INDEX uq_documents_object_key ON documents (object_key)",
    "CREATE UNIQUE INDEX uq_documents_sha256 ON documents (sha256)",
    "CREATE INDEX ix_object_deletions_lease_expiry ON object_deletions (lease_expires_at) "
    "WHERE status = 'leased'",
    "CREATE INDEX ix_object_deletions_queue ON object_deletions (available_at, "
    "created_at) WHERE status = 'queued'",
    "CREATE UNIQUE INDEX uq_object_deletions_object_key ON object_deletions (object_key)",
    "CREATE INDEX ix_service_leases_expiry ON service_leases (lease_expires_at)",
    "CREATE UNIQUE INDEX uq_teams_name_key ON teams (name_key)",
    "CREATE UNIQUE INDEX uq_users_username ON users (username)",
    "CREATE INDEX ix_acl_previews_actor_active ON acl_previews (actor_user_id, "
    "expires_at)",
    "CREATE INDEX ix_audit_events_actor_id ON audit_events (actor_user_id)",
    "CREATE INDEX ix_audit_events_created_id ON audit_events (created_at, id)",
    "CREATE INDEX ix_chats_owner_updated_id_desc ON chats (owner_user_id, updated_at "
    "DESC, id DESC)",
    "CREATE INDEX ix_chats_updated_id_desc ON chats (updated_at DESC, id DESC)",
    "CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding "
    "vector_cosine_ops)",
    "CREATE INDEX ix_chunks_document_id ON chunks (document_id)",
    "CREATE INDEX ix_chunks_text_fts ON chunks USING gin (to_tsvector('simple', text))",
    "CREATE UNIQUE INDEX uq_chunks_document_ordinal ON chunks (document_id, ordinal)",
    "CREATE INDEX ix_effective_access_document_user ON effective_document_access "
    "(document_id, user_id)",
    "CREATE INDEX ix_ingestion_jobs_queue ON ingestion_jobs (available_at, created_at) "
    "WHERE status = 'queued'",
    "CREATE INDEX ix_library_nodes_name_fts ON library_nodes USING gin "
    "(to_tsvector('simple', name))",
    "CREATE INDEX ix_library_nodes_parent_id ON library_nodes (parent_id)",
    "CREATE UNIQUE INDEX uq_library_nodes_document_id ON library_nodes (document_id)",
    "CREATE UNIQUE INDEX uq_pre_auth_challenges_token_hash ON pre_auth_challenges "
    "(token_hash)",
    "CREATE UNIQUE INDEX uq_pre_auth_challenges_user_active ON pre_auth_challenges "
    "(user_id) WHERE consumed_at IS NULL AND revoked_at IS NULL",
    "CREATE INDEX ix_sessions_user_active ON sessions (user_id, revoked_at)",
    "CREATE UNIQUE INDEX uq_sessions_token_hash ON sessions (token_hash)",
    "CREATE INDEX ix_access_grants_team_id ON access_grants (team_id)",
    "CREATE INDEX ix_access_grants_user_id ON access_grants (user_id)",
    "CREATE INDEX ix_chat_scopes_node_id ON chat_scopes (node_id)",
    "CREATE UNIQUE INDEX uq_chat_turns_one_generating ON chat_turns (chat_id) WHERE "
    "status = 'generating'",
    "CREATE INDEX ix_upload_reservations_expiry ON upload_reservations (expires_at) WHERE "
    "consumed_at IS NULL",
    "CREATE UNIQUE INDEX uq_upload_reservations_active_object ON upload_reservations "
    "(object_key) WHERE consumed_at IS NULL",
    "CREATE INDEX ix_turn_sources_chunk_id ON turn_sources (chunk_id)",
    "CREATE INDEX ix_turn_sources_document_id ON turn_sources (document_id)",
)
# END GENERATED BASELINE DDL


def _assert_fresh_database(connection: sa.Connection) -> None:
    old_relations = connection.execute(
        sa.text(
            "SELECT c.relname FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() "
            "AND c.relkind IN ('r','p','v','m') "
            "AND c.relname = ANY(:legacy_relations) "
            "ORDER BY c.relname"
        ),
        {"legacy_relations": list(_LEGACY_RELATIONS)},
    ).scalars()
    existing = list(old_relations)
    if existing:
        raise RuntimeError(
            "0001_v4_baseline is fresh-only; refusing database containing old "
            f"application relations: {', '.join(existing)}"
        )

    old_revisions = connection.execute(
        sa.text(
            "SELECT version_num FROM alembic_version "
            "WHERE version_num <> '0001_v4_baseline'"
        )
    ).scalars()
    revisions = list(old_revisions)
    if revisions:
        raise RuntimeError(
            "0001_v4_baseline is fresh-only; refusing database containing old "
            f"Alembic revisions: {', '.join(revisions)}"
        )


def _assert_roles(connection: sa.Connection) -> None:
    role_rows = connection.execute(
        sa.text(
            "SELECT rolname, rolcanlogin, rolinherit, rolsuper, "
            "rolbypassrls, rolcreatedb, rolcreaterole, rolreplication "
            "FROM pg_roles WHERE rolname = ANY(:roles)"
        ),
        {"roles": list(_REQUIRED_ROLES)},
    ).mappings()
    roles = {str(row["rolname"]): row for row in role_rows}
    missing = sorted(set(_REQUIRED_ROLES) - set(roles))
    if missing:
        raise RuntimeError(
            "provision V4 PostgreSQL roles before migration; missing: "
            + ", ".join(missing)
        )

    for role_name, row in roles.items():
        expected_login = role_name not in {"rag_owner", "rag_backup"}
        expected_bypass = role_name in {"rag_owner", "rag_backup"}
        if (
            bool(row["rolcanlogin"]) != expected_login
            or bool(row["rolinherit"])
            or bool(row["rolsuper"])
            or bool(row["rolbypassrls"]) != expected_bypass
            or bool(row["rolcreatedb"])
            or bool(row["rolcreaterole"])
            or bool(row["rolreplication"])
        ):
            raise RuntimeError(
                f"PostgreSQL role {role_name} does not match the V4 role contract"
            )
    migrator_membership = connection.scalar(
        sa.text("SELECT pg_has_role('rag_migrator', 'rag_owner', 'MEMBER')")
    )
    if migrator_membership is not True:
        raise RuntimeError(
            "rag_migrator must be a member of the no-login rag_owner role"
        )
    membership_rows = connection.execute(
        sa.text(
            "SELECT member.rolname AS member_name, "
            "granted.rolname AS granted_name "
            "FROM pg_auth_members AS membership "
            "JOIN pg_roles AS member ON member.oid = membership.member "
            "JOIN pg_roles AS granted ON granted.oid = membership.roleid "
            "WHERE member.rolname = ANY(:roles) "
            "OR granted.rolname = ANY(:roles)"
        ),
        {"roles": list(_REQUIRED_ROLES)},
    ).mappings()
    memberships = {
        (str(row["member_name"]), str(row["granted_name"])) for row in membership_rows
    }
    if memberships != {("rag_migrator", "rag_owner")}:
        raise RuntimeError(
            "dangerous PostgreSQL role membership detected; only "
            "rag_migrator membership in rag_owner is permitted"
        )


def _assert_extensions(connection: sa.Connection) -> None:
    extension_rows = list(
        connection.execute(
            sa.text(
                "SELECT extension.extname, namespace.nspname "
                "FROM pg_extension AS extension "
                "JOIN pg_namespace AS namespace "
                "ON namespace.oid = extension.extnamespace "
                "WHERE extname IN ('vector', 'pgcrypto')"
            )
        ).mappings()
    )
    extensions = {str(row["extname"]): str(row["nspname"]) for row in extension_rows}
    missing = sorted({"vector", "pgcrypto"} - set(extensions))
    if missing:
        raise RuntimeError(
            "provision V4 PostgreSQL extensions before migration; missing: "
            + ", ".join(missing)
        )
    misplaced = sorted(
        name for name, schema in extensions.items() if schema != "public"
    )
    if misplaced:
        raise RuntimeError(
            "V4 PostgreSQL extensions must be installed in public for "
            "schema-qualified runtime use: " + ", ".join(misplaced)
        )
    public_execute = connection.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_proc AS routine "
            "JOIN pg_namespace AS namespace "
            "ON namespace.oid = routine.pronamespace "
            "WHERE namespace.nspname = 'public' "
            "AND has_function_privilege('public', routine.oid, 'EXECUTE')"
            ")"
        )
    )
    owner_crypto = connection.scalar(
        sa.text(
            "SELECT "
            "has_function_privilege("
            "'rag_owner', 'public.digest(bytea,text)', 'EXECUTE') "
            "AND has_function_privilege("
            "'rag_owner', 'public.gen_random_uuid()', 'EXECUTE')"
        )
    )
    exact_acl = connection.scalar(
        sa.text(
            "SELECT "
            "NOT EXISTS ("
            "SELECT 1 FROM pg_proc AS routine "
            "JOIN pg_namespace AS namespace "
            "ON namespace.oid = routine.pronamespace "
            "WHERE namespace.nspname = 'public' "
            "AND NOT has_function_privilege("
            "'rag_owner', routine.oid, 'EXECUTE')) "
            "AND has_function_privilege("
            "'rag_api', 'public.cosine_distance(vector,vector)', 'EXECUTE') "
            "AND NOT EXISTS ("
            "SELECT 1 FROM pg_proc AS routine "
            "JOIN pg_namespace AS namespace "
            "ON namespace.oid = routine.pronamespace "
            "WHERE namespace.nspname = 'public' "
            "AND routine.oid <> "
            "'public.cosine_distance(vector,vector)'::regprocedure "
            "AND has_function_privilege('rag_api', routine.oid, 'EXECUTE')) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM unnest(ARRAY["
            "'rag_migrator','rag_worker','rag_maintenance','rag_backup'"
            "]) AS runtime(role_name) "
            "CROSS JOIN pg_proc AS routine "
            "JOIN pg_namespace AS namespace "
            "ON namespace.oid = routine.pronamespace "
            "WHERE namespace.nspname = 'public' "
            "AND has_function_privilege("
            "runtime.role_name, routine.oid, 'EXECUTE'))"
        )
    )
    if public_execute is True or owner_crypto is not True or exact_acl is not True:
        raise RuntimeError(
            "V4 extension function ACLs must deny PUBLIC, grant extension "
            "execution to rag_owner, and grant only cosine distance to rag_api"
        )


def _create_security_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION v4_runtime_identity(p_expected_role text)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $$
            SELECT (
                p_expected_role IN (
                    'rag_api', 'rag_worker', 'rag_maintenance'
                )
                AND current_user::text = p_expected_role
                AND session_user::text = p_expected_role
                AND EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_roles AS expected
                    WHERE expected.rolname = p_expected_role
                      AND expected.rolcanlogin
                      AND NOT expected.rolinherit
                      AND NOT expected.rolsuper
                      AND NOT expected.rolbypassrls
                      AND NOT expected.rolcreatedb
                      AND NOT expected.rolcreaterole
                      AND NOT expected.rolreplication
                      AND NOT EXISTS (
                          SELECT 1
                          FROM pg_catalog.pg_auth_members AS membership
                          WHERE membership.roleid = expected.oid
                             OR membership.member = expected.oid
                      )
                )
            )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_current_actor_id()
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT account.id
            FROM public.sessions AS session_row
            JOIN public.users AS account ON account.id = session_row.user_id
            CROSS JOIN public.security_epochs AS epoch
            WHERE session_row.token_hash =
                NULLIF(
                    current_setting('rag.session_token_hash', true),
                    ''
                )
              AND session_row.revoked_at IS NULL
              AND session_row.idle_expires_at > statement_timestamp()
              AND session_row.absolute_expires_at > statement_timestamp()
              AND account.status = 'active'
              AND account.deleted_at IS NULL
              AND session_row.issued_authentication_version =
                  account.authentication_version
              AND session_row.issued_authentication_epoch =
                  epoch.authentication_version
              AND session_row.issued_session_epoch = epoch.session_epoch
              AND epoch.singleton
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_current_actor_is_admin()
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT EXISTS (
                SELECT 1
                FROM public.users AS account
                WHERE account.id = public.v4_current_actor_id()
                  AND account.role = 'admin'
                  AND account.status = 'active'
                  AND account.deleted_at IS NULL
            )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_activate_actor(p_token_hash text)
        RETURNS TABLE (
            user_id uuid,
            actor_role text,
            authentication_version bigint,
            authorization_version bigint,
            session_id uuid
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_user_id uuid;
            v_role text;
            v_authentication_version bigint;
            v_authorization_version bigint;
            v_session_id uuid;
        BEGIN
            SELECT u.id, u.role, u.authentication_version,
                   e.authorization_version, s.id
            INTO STRICT v_user_id, v_role, v_authentication_version,
                v_authorization_version, v_session_id
            FROM public.sessions AS s
            JOIN public.users AS u ON u.id = s.user_id
            CROSS JOIN public.security_epochs AS e
            WHERE s.token_hash = p_token_hash
              AND s.revoked_at IS NULL
              AND s.idle_expires_at > statement_timestamp()
              AND s.absolute_expires_at > statement_timestamp()
              AND u.status = 'active'
              AND u.deleted_at IS NULL
              AND s.issued_authentication_version = u.authentication_version
              AND s.issued_authentication_epoch = e.authentication_version
              AND s.issued_session_epoch = e.session_epoch
              AND e.singleton;

            PERFORM set_config('rag.actor_id', v_user_id::text, true);
            PERFORM set_config('rag.actor_role', v_role, true);
            PERFORM set_config('rag.session_token_hash', p_token_hash, true);
            PERFORM set_config(
                'rag.authentication_version',
                v_authentication_version::text,
                true
            );
            PERFORM set_config(
                'rag.authorization_version',
                v_authorization_version::text,
                true
            );
            RETURN QUERY SELECT v_user_id, v_role,
                v_authentication_version, v_authorization_version,
                v_session_id;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                RAISE EXCEPTION 'invalid or expired session'
                    USING ERRCODE = '28000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_can_read_document(p_document_id uuid)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT public.v4_current_actor_is_admin()
                OR EXISTS (
                    SELECT 1
                    FROM public.library_nodes AS node
                    WHERE node.document_id = p_document_id
                      AND node.uploader_user_id =
                          public.v4_current_actor_id()
                )
                OR EXISTS (
                    SELECT 1
                    FROM public.effective_document_access AS access
                    JOIN public.security_epochs AS epoch ON epoch.singleton
                    WHERE access.user_id = public.v4_current_actor_id()
                      AND access.document_id = p_document_id
                      AND access.authorization_version =
                          epoch.authorization_version
                )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_can_read_folder(p_node_id uuid)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT public.v4_current_actor_is_admin()
                OR EXISTS (
                    WITH RECURSIVE ancestry AS (
                        SELECT node.id, node.parent_id,
                               node.access_boundary AS boundary_seen
                        FROM public.library_nodes AS node
                        WHERE node.id = p_node_id
                          AND node.kind = 'folder'
                        UNION ALL
                        SELECT parent.id, parent.parent_id,
                               child.boundary_seen OR parent.access_boundary
                        FROM public.library_nodes AS parent
                        JOIN ancestry AS child
                          ON parent.id = child.parent_id
                        WHERE NOT child.boundary_seen
                    )
                    SELECT 1
                    FROM ancestry
                    JOIN public.access_grants AS grant_row
                      ON grant_row.node_id = ancestry.id
                    LEFT JOIN public.team_members AS membership
                      ON membership.team_id = grant_row.team_id
                     AND membership.user_id =
                         public.v4_current_actor_id()
                    LEFT JOIN public.teams AS team
                      ON team.id = grant_row.team_id
                     AND team.is_active
                    WHERE grant_row.user_id =
                              public.v4_current_actor_id()
                       OR membership.user_id IS NOT NULL
                          AND team.id IS NOT NULL
                    LIMIT 1
                )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_can_view_library_node(p_node_id uuid)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT public.v4_current_actor_is_admin()
                OR EXISTS (
                    WITH RECURSIVE descendants AS (
                        SELECT n.id, n.document_id
                        FROM public.library_nodes AS n
                        WHERE n.id = p_node_id
                        UNION ALL
                        SELECT child.id, child.document_id
                        FROM public.library_nodes AS child
                        JOIN descendants AS parent
                          ON child.parent_id = parent.id
                    )
                    SELECT 1
                    FROM descendants AS node
                    JOIN public.effective_document_access AS access
                      ON access.document_id = node.document_id
                    JOIN public.security_epochs AS epoch ON epoch.singleton
                    WHERE access.user_id = public.v4_current_actor_id()
                      AND access.authorization_version =
                          epoch.authorization_version
                    UNION ALL
                    SELECT 1
                    FROM descendants AS granted_descendant
                    JOIN public.access_grants AS grant_row
                      ON grant_row.node_id = granted_descendant.id
                    LEFT JOIN public.team_members AS membership
                      ON membership.team_id = grant_row.team_id
                    LEFT JOIN public.teams AS team
                      ON team.id = grant_row.team_id
                    WHERE (
                          grant_row.user_id = public.v4_current_actor_id()
                          OR (
                              membership.user_id =
                                  public.v4_current_actor_id()
                              AND team.is_active
                          )
                      )
                    LIMIT 1
                )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_rebuild_effective_document_access()
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_version bigint;
        BEGIN
            SELECT authorization_version + 1
            INTO STRICT v_version
            FROM public.security_epochs
            WHERE singleton
            FOR UPDATE;

            DELETE FROM public.effective_document_access;

            WITH RECURSIVE paths AS (
                SELECT n.id AS document_node_id, n.id AS ancestor_id,
                       n.parent_id, n.document_id, n.access_boundary,
                       n.access_boundary AS boundary_seen
                FROM public.library_nodes AS n
                WHERE n.kind = 'file'
                UNION ALL
                SELECT path.document_node_id, parent.id, parent.parent_id,
                       path.document_id, parent.access_boundary,
                       path.boundary_seen OR parent.access_boundary
                FROM paths AS path
                JOIN public.library_nodes AS parent
                  ON parent.id = path.parent_id
                WHERE NOT path.boundary_seen
            ),
            principals AS (
                SELECT path.document_id, node.uploader_user_id AS user_id
                FROM paths AS path
                JOIN public.library_nodes AS node
                  ON node.id = path.document_node_id
                WHERE node.uploader_user_id IS NOT NULL
                UNION
                SELECT path.document_id, grant_row.user_id
                FROM paths AS path
                JOIN public.access_grants AS grant_row
                  ON grant_row.node_id = path.ancestor_id
                WHERE grant_row.user_id IS NOT NULL
                UNION
                SELECT path.document_id, membership.user_id
                FROM paths AS path
                JOIN public.access_grants AS grant_row
                  ON grant_row.node_id = path.ancestor_id
                JOIN public.team_members AS membership
                  ON membership.team_id = grant_row.team_id
                JOIN public.teams AS team_row
                  ON team_row.id = membership.team_id
                 AND team_row.is_active
            )
            INSERT INTO public.effective_document_access (
                user_id, document_id, authorization_version
            )
            SELECT DISTINCT principal.user_id, principal.document_id, v_version
            FROM principals AS principal
            JOIN public.users AS account ON account.id = principal.user_id
            WHERE account.status = 'active'
              AND account.deleted_at IS NULL
              AND account.role = 'member';

            UPDATE public.security_epochs
            SET authorization_version = v_version,
                updated_at = statement_timestamp()
            WHERE singleton;
            RETURN v_version;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_bootstrap_admin(
            p_username text,
            p_display_name text,
            p_password_hash text
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_user_id uuid := public.gen_random_uuid();
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-bootstrap-admin', 0)
            );
            IF EXISTS (SELECT 1 FROM public.users) THEN
                RAISE EXCEPTION 'bootstrap is unavailable after the first user'
                    USING ERRCODE = '55000';
            END IF;
            IF p_username !~ '^[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]$' THEN
                RAISE EXCEPTION 'invalid canonical username'
                    USING ERRCODE = '22023';
            END IF;
            IF char_length(p_display_name) NOT BETWEEN 1 AND 80 THEN
                RAISE EXCEPTION 'invalid display name'
                    USING ERRCODE = '22023';
            END IF;
            IF p_password_hash IS NULL
               OR p_password_hash NOT LIKE '$argon2id$%' THEN
                RAISE EXCEPTION 'invalid Argon2id password verifier'
                    USING ERRCODE = '22023';
            END IF;

            INSERT INTO public.users (
                id, username, display_name, role, status, password_hash
            ) VALUES (
                v_user_id, p_username, p_display_name, 'admin', 'active',
                p_password_hash
            );
            INSERT INTO public.audit_events (
                id, actor_user_id, event_type, target_type, target_id, details
            ) VALUES (
                public.gen_random_uuid(), v_user_id, 'bootstrap_admin_created',
                'user', v_user_id, '{}'::jsonb
            );
            RETURN v_user_id;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_maintenance_document_snapshot_token(
            p_document_id uuid
        )
        RETURNS text
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT encode(
                public.digest(
                    convert_to(
                        jsonb_build_object(
                            'document_id', document.id,
                            'state', document.state,
                            'sha256', document.sha256,
                            'byte_size', document.byte_size,
                            'object_key', document.object_key,
                            'updated_at', document.updated_at,
                            'jobs', COALESCE(
                                (
                                    SELECT jsonb_agg(
                                        jsonb_build_array(
                                            job.id, job.status, job.stage,
                                            job.updated_at
                                        )
                                        ORDER BY job.created_at DESC, job.id DESC
                                    )
                                    FROM public.ingestion_jobs AS job
                                    WHERE job.document_id = document.id
                                ),
                                '[]'::jsonb
                            )
                        )::text,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            )
            FROM public.documents AS document
            WHERE document.id = p_document_id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_maintenance_get_document(p_document_id uuid)
        RETURNS TABLE (
            document_id uuid,
            state text,
            sha256 text,
            byte_size bigint,
            object_key text,
            job_statuses text[],
            snapshot_token text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT document.id, document.state::text,
                   document.sha256::text, document.byte_size,
                   document.object_key::text,
                   COALESCE(
                       (
                           SELECT array_agg(
                               job.status::text
                               ORDER BY job.created_at DESC, job.id DESC
                           )
                           FROM public.ingestion_jobs AS job
                           WHERE job.document_id = document.id
                       ),
                       ARRAY[]::text[]
                   ),
                   public.v4_maintenance_document_snapshot_token(document.id)
            FROM public.documents AS document
            WHERE document.id = p_document_id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_maintenance_list_documents()
        RETURNS TABLE (
            document_id uuid,
            state text,
            sha256 text,
            byte_size bigint,
            object_key text,
            job_statuses text[],
            snapshot_token text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT document.id, document.state::text,
                   document.sha256::text, document.byte_size,
                   document.object_key::text,
                   COALESCE(
                       (
                           SELECT array_agg(
                               job.status::text
                               ORDER BY job.created_at DESC, job.id DESC
                           )
                           FROM public.ingestion_jobs AS job
                           WHERE job.document_id = document.id
                       ),
                       ARRAY[]::text[]
                   ),
                   public.v4_maintenance_document_snapshot_token(document.id)
            FROM public.documents AS document
            ORDER BY document.created_at, document.id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_maintenance_requeue_document(
            p_document_id uuid,
            p_snapshot_token text,
            p_job_id uuid,
            p_retry_only boolean
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_state text;
            v_latest_status text;
        BEGIN
            IF p_snapshot_token !~ '^[0-9a-f]{64}$'
               OR p_job_id IS NULL
               OR p_retry_only IS NULL THEN
                RAISE EXCEPTION 'invalid maintenance requeue request'
                    USING ERRCODE = '22023';
            END IF;
            SELECT state INTO v_state
            FROM public.documents
            WHERE id = p_document_id
            FOR UPDATE;
            IF v_state IS NULL THEN
                RETURN 'not_found';
            END IF;
            PERFORM 1
            FROM public.ingestion_jobs
            WHERE document_id = p_document_id
            FOR UPDATE;
            IF public.v4_maintenance_document_snapshot_token(p_document_id)
               <> p_snapshot_token THEN
                RETURN 'stale';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.ingestion_jobs
                WHERE document_id = p_document_id
                  AND status IN ('queued', 'running')
            ) THEN
                RETURN 'active_job';
            END IF;
            SELECT status INTO v_latest_status
            FROM public.ingestion_jobs
            WHERE document_id = p_document_id
            ORDER BY created_at DESC, id DESC
            LIMIT 1;
            IF p_retry_only AND (
                v_state <> 'failed'
                OR v_latest_status NOT IN ('failed', 'interrupted')
            ) THEN
                RETURN 'not_retryable';
            END IF;
            DELETE FROM public.chunks
            WHERE document_id = p_document_id;
            UPDATE public.documents
            SET state = 'uploaded', stage = 'uploaded', error = NULL,
                page_count = NULL, chunk_count = 0,
                updated_at = statement_timestamp()
            WHERE id = p_document_id;
            INSERT INTO public.ingestion_jobs (
                id, document_id, status, stage, attempt,
                completed_units, total_units
            ) VALUES (
                p_job_id, p_document_id, 'queued', 'uploaded', 0, 0, NULL
            );
            RETURN 'created';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_maintenance_storage_snapshot()
        RETURNS TABLE (
            database_identity text,
            schema_revision text,
            database_inventory jsonb,
            database_fingerprint text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            WITH inventory AS (
                SELECT jsonb_build_object(
                    'documents',
                    COALESCE(
                        (
                            SELECT jsonb_agg(
                                jsonb_build_object(
                                    'document_id', document.id,
                                    'object_key', document.object_key,
                                    'sha256', document.sha256,
                                    'byte_size', document.byte_size
                                )
                                ORDER BY document.id
                            )
                            FROM public.documents AS document
                        ),
                        '[]'::jsonb
                    ),
                    'object_deletions',
                    COALESCE(
                        (
                            SELECT jsonb_agg(
                                deletion.object_key
                                ORDER BY deletion.object_key
                            )
                            FROM public.object_deletions AS deletion
                        ),
                        '[]'::jsonb
                    )
                ) AS value
            )
            SELECT current_database()::text,
                   '0001_v4_baseline'::text,
                   inventory.value,
                   encode(
                       public.digest(
                           convert_to(inventory.value::text, 'UTF8'),
                           'sha256'
                       ),
                       'hex'
                   )
            FROM inventory
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_schema_revision()
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ SELECT '0001_v4_baseline'::text $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_require_admin()
        RETURNS uuid
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_actor uuid := public.v4_current_actor_id();
        BEGIN
            IF v_actor IS NULL OR NOT public.v4_current_actor_is_admin() THEN
                RAISE EXCEPTION 'administrator capability required'
                    USING ERRCODE = '42501';
            END IF;
            RETURN v_actor;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_append_audit(
            p_event_type text,
            p_target_type text,
            p_target_id uuid,
            p_details jsonb
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_id uuid := public.gen_random_uuid();
            v_actor uuid := public.v4_current_actor_id();
        BEGIN
            IF char_length(p_event_type) NOT BETWEEN 1 AND 80
               OR p_details ?| ARRAY[
                   'document_text', 'prompt', 'answer', 'snapshot_text',
                   'password', 'token'
               ] THEN
                RAISE EXCEPTION 'invalid audit event payload'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.audit_events (
                id, actor_user_id, event_type, target_type, target_id, details
            ) VALUES (
                v_id, v_actor, p_event_type, p_target_type, p_target_id,
                COALESCE(p_details, '{}'::jsonb)
            );
            RETURN v_id;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_auth_lookup(
            p_username text,
            p_client_key_hash text
        )
        RETURNS TABLE (
            user_id uuid,
            username text,
            display_name text,
            actor_role text,
            account_status text,
            password_hash text,
            authentication_version bigint,
            blocked_until timestamptz
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT account.id, account.username::text,
                   account.display_name::text, account.role::text,
                   account.status::text, account.password_hash,
                   account.authentication_version, throttle.blocked_until
            FROM (VALUES (true)) AS request(singleton)
            LEFT JOIN public.users AS account
              ON account.username = p_username
             AND p_username = lower(btrim(p_username))
             AND char_length(p_username) BETWEEN 3 AND 32
            LEFT JOIN public.login_throttles AS throttle
              ON throttle.key_hash = p_client_key_hash
             AND p_client_key_hash ~ '^[0-9a-f]{64}$'
             AND throttle.blocked_until > statement_timestamp()
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_login_blocked_until(p_key_hash text)
        RETURNS timestamptz
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_blocked_until timestamptz;
        BEGIN
            IF p_key_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'invalid throttle key'
                    USING ERRCODE = '22023';
            END IF;
            DELETE FROM public.login_throttles
            WHERE updated_at <
                statement_timestamp() - interval '24 hours';
            SELECT blocked_until INTO v_blocked_until
            FROM public.login_throttles
            WHERE key_hash = p_key_hash
              AND blocked_until > statement_timestamp();
            RETURN v_blocked_until;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_session_view(p_session_token_hash text)
        RETURNS TABLE (
            user_id uuid,
            username text,
            display_name text,
            actor_role text,
            account_status text,
            authentication_version bigint,
            authorization_version bigint,
            session_id uuid,
            csrf_token_hash text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT account.id, account.username::text,
                   account.display_name::text, account.role::text,
                   account.status::text, account.authentication_version,
                   epoch.authorization_version, session_row.id,
                   session_row.csrf_token_hash::text
            FROM public.sessions AS session_row
            JOIN public.users AS account ON account.id = session_row.user_id
            CROSS JOIN public.security_epochs AS epoch
            WHERE session_row.token_hash = p_session_token_hash
              AND session_row.revoked_at IS NULL
              AND session_row.idle_expires_at > statement_timestamp()
              AND session_row.absolute_expires_at > statement_timestamp()
              AND account.status = 'active'
              AND account.deleted_at IS NULL
              AND session_row.issued_authentication_version =
                  account.authentication_version
              AND session_row.issued_authentication_epoch =
                  epoch.authentication_version
              AND session_row.issued_session_epoch = epoch.session_epoch
              AND epoch.singleton
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_refresh_session(
            p_session_token_hash text,
            p_csrf_token_hash text,
            p_expires_at timestamptz
        )
        RETURNS TABLE (
            user_id uuid,
            username text,
            display_name text,
            actor_role text,
            account_status text,
            authentication_version bigint,
            authorization_version bigint,
            session_id uuid,
            csrf_token_hash text,
            refreshed boolean
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            IF p_session_token_hash !~ '^[0-9a-f]{64}$'
               OR p_csrf_token_hash !~ '^[0-9a-f]{64}$'
               OR p_expires_at <= statement_timestamp()
               OR p_expires_at >
                    statement_timestamp() + interval '30 minutes' THEN
                RAISE EXCEPTION 'invalid session refresh'
                    USING ERRCODE = '22023';
            END IF;
            RETURN QUERY
            WITH valid AS MATERIALIZED (
                SELECT session_row.id, session_row.user_id,
                       session_row.csrf_token_hash,
                       session_row.last_seen_at
                FROM public.sessions AS session_row
                JOIN public.users AS account ON account.id = session_row.user_id
                CROSS JOIN public.security_epochs AS epoch
                WHERE session_row.token_hash = p_session_token_hash
                  AND session_row.csrf_token_hash = p_csrf_token_hash
                  AND epoch.singleton
                  AND session_row.revoked_at IS NULL
                  AND session_row.idle_expires_at > statement_timestamp()
                  AND session_row.absolute_expires_at > statement_timestamp()
                  AND account.status = 'active'
                  AND account.deleted_at IS NULL
                  AND session_row.issued_authentication_version =
                      account.authentication_version
                  AND session_row.issued_authentication_epoch =
                      epoch.authentication_version
                  AND session_row.issued_session_epoch = epoch.session_epoch
            ),
            updated AS (
                UPDATE public.sessions AS session_row
                SET last_seen_at = statement_timestamp(),
                    idle_expires_at = p_expires_at,
                    absolute_expires_at = p_expires_at
                FROM valid
                WHERE session_row.id = valid.id
                  AND session_row.last_seen_at <=
                      statement_timestamp() - interval '5 minutes'
                RETURNING session_row.id, session_row.user_id,
                          session_row.csrf_token_hash
            ),
            selected AS (
                SELECT updated.id, updated.user_id,
                       updated.csrf_token_hash, true AS refreshed
                FROM updated
                UNION ALL
                SELECT valid.id, valid.user_id,
                       valid.csrf_token_hash, false AS refreshed
                FROM valid
                WHERE NOT EXISTS (SELECT 1 FROM updated)
            )
            SELECT account.id, account.username::text,
                   account.display_name::text, account.role::text,
                   account.status::text, account.authentication_version,
                   epoch.authorization_version, selected.id,
                   selected.csrf_token_hash::text, selected.refreshed
            FROM selected
            JOIN public.users AS account ON account.id = selected.user_id
            CROSS JOIN public.security_epochs AS epoch
            WHERE epoch.singleton;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_record_login_failure(p_key_hash text)
        RETURNS timestamptz
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_blocked_until timestamptz;
        BEGIN
            IF p_key_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'invalid throttle key'
                    USING ERRCODE = '22023';
            END IF;
            DELETE FROM public.login_throttles
            WHERE updated_at <
                statement_timestamp() - interval '24 hours';
            INSERT INTO public.login_throttles (
                key_hash, failure_count, blocked_until
            ) VALUES (p_key_hash, 1, NULL)
            ON CONFLICT (key_hash) DO UPDATE
            SET failure_count = CASE
                    WHEN public.login_throttles.updated_at <
                         statement_timestamp() - interval '24 hours'
                    THEN 1
                    ELSE LEAST(
                        public.login_throttles.failure_count + 1, 100
                    )
                END,
                first_failure_at = CASE
                    WHEN public.login_throttles.updated_at <
                         statement_timestamp() - interval '24 hours'
                    THEN statement_timestamp()
                    ELSE public.login_throttles.first_failure_at
                END,
                blocked_until = CASE
                    WHEN (
                        CASE
                            WHEN public.login_throttles.updated_at <
                                 statement_timestamp() - interval '24 hours'
                            THEN 1
                            ELSE LEAST(
                                public.login_throttles.failure_count + 1, 100
                            )
                        END
                    ) >= 5
                    THEN statement_timestamp() + LEAST(
                        interval '15 minutes',
                        interval '1 minute' * (
                            LEAST(
                                public.login_throttles.failure_count + 1, 100
                            ) - 4
                        )
                    )
                    ELSE NULL
                END,
                updated_at = statement_timestamp();
            SELECT blocked_until INTO v_blocked_until
            FROM public.login_throttles WHERE key_hash = p_key_hash;
            RETURN v_blocked_until;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_clear_login_failures(p_key_hash text)
        RETURNS void
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            DELETE FROM public.login_throttles
            WHERE key_hash = p_key_hash
              AND p_key_hash ~ '^[0-9a-f]{64}$'
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_issue_login_session(
            p_user_id uuid,
            p_expected_authentication_version bigint,
            p_session_token_hash text,
            p_csrf_token_hash text,
            p_idle_expires_at timestamptz,
            p_absolute_expires_at timestamptz
        )
        RETURNS TABLE (
            user_id uuid,
            username text,
            display_name text,
            actor_role text,
            account_status text
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_account public.users%ROWTYPE;
            v_epoch public.security_epochs%ROWTYPE;
        BEGIN
            SELECT * INTO STRICT v_account
            FROM public.users
            WHERE id = p_user_id
              AND status = 'active'
              AND deleted_at IS NULL
              AND authentication_version =
                  p_expected_authentication_version
            FOR UPDATE;
            SELECT * INTO STRICT v_epoch
            FROM public.security_epochs WHERE singleton;
            IF p_session_token_hash !~ '^[0-9a-f]{64}$'
               OR p_csrf_token_hash !~ '^[0-9a-f]{64}$'
               OR p_idle_expires_at <= statement_timestamp()
               OR p_idle_expires_at >
                  statement_timestamp() + interval '30 minutes'
               OR p_idle_expires_at IS DISTINCT FROM
                  p_absolute_expires_at THEN
                RAISE EXCEPTION 'invalid session issue request'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.sessions (
                id, user_id, token_hash, csrf_token_hash,
                issued_authentication_version, issued_authentication_epoch,
                issued_session_epoch, issued_at, last_seen_at,
                idle_expires_at, absolute_expires_at,
                recent_reauthenticated_at
            ) VALUES (
                public.gen_random_uuid(), p_user_id, p_session_token_hash,
                p_csrf_token_hash, v_account.authentication_version,
                v_epoch.authentication_version, v_epoch.session_epoch,
                statement_timestamp(), statement_timestamp(),
                p_idle_expires_at, p_absolute_expires_at,
                statement_timestamp()
            );
            RETURN QUERY SELECT v_account.id, v_account.username::text,
                v_account.display_name::text, v_account.role::text,
                v_account.status::text;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_logout(p_session_token_hash text)
        RETURNS void
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            UPDATE public.sessions
            SET revoked_at = statement_timestamp()
            WHERE token_hash = p_session_token_hash
              AND revoked_at IS NULL
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_consume_activation(
            p_activation_token_hash text,
            p_password_hash text,
            p_session_token_hash text,
            p_csrf_token_hash text,
            p_idle_expires_at timestamptz,
            p_absolute_expires_at timestamptz
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_user_id uuid;
            v_version bigint;
        BEGIN
            SELECT challenge.user_id INTO STRICT v_user_id
            FROM public.pre_auth_challenges AS challenge
            JOIN public.users AS account ON account.id = challenge.user_id
            WHERE challenge.token_hash = p_activation_token_hash
              AND challenge.consumed_at IS NULL
              AND challenge.revoked_at IS NULL
              AND challenge.expires_at > statement_timestamp()
              AND account.status = 'pending_activation'
              AND account.password_hash IS NULL
            FOR UPDATE OF challenge, account;
            IF p_password_hash NOT LIKE '$argon2id$%' THEN
                RAISE EXCEPTION 'invalid password verifier'
                    USING ERRCODE = '22023';
            END IF;
            UPDATE public.pre_auth_challenges
            SET consumed_at = statement_timestamp()
            WHERE token_hash = p_activation_token_hash;
            UPDATE public.users
            SET status = 'active', password_hash = p_password_hash,
                authentication_version = authentication_version + 1,
                updated_at = statement_timestamp()
            WHERE id = v_user_id
            RETURNING authentication_version INTO v_version;
            PERFORM public.v4_issue_login_session(
                v_user_id, v_version, p_session_token_hash, p_csrf_token_hash,
                p_idle_expires_at, p_absolute_expires_at
            );
            RETURN v_user_id;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_password_change_lookup(p_session_token_hash text)
        RETURNS TABLE (
            user_id uuid,
            password_hash text,
            authentication_version bigint
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT account.id, account.password_hash,
                   account.authentication_version
            FROM public.sessions AS session_row
            JOIN public.users AS account ON account.id = session_row.user_id
            CROSS JOIN public.security_epochs AS epoch
            WHERE session_row.token_hash = p_session_token_hash
              AND session_row.revoked_at IS NULL
              AND session_row.idle_expires_at > statement_timestamp()
              AND session_row.absolute_expires_at > statement_timestamp()
              AND account.status = 'active'
              AND account.deleted_at IS NULL
              AND session_row.issued_authentication_version =
                  account.authentication_version
              AND session_row.issued_authentication_epoch =
                  epoch.authentication_version
              AND session_row.issued_session_epoch = epoch.session_epoch
              AND epoch.singleton
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_change_password(
            p_current_session_token_hash text,
            p_expected_authentication_version bigint,
            p_new_password_hash text,
            p_replacement_session_token_hash text,
            p_csrf_token_hash text,
            p_idle_expires_at timestamptz,
            p_absolute_expires_at timestamptz
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_user_id uuid;
            v_new_version bigint;
        BEGIN
            SELECT account.id INTO STRICT v_user_id
            FROM public.sessions AS session_row
            JOIN public.users AS account ON account.id = session_row.user_id
            CROSS JOIN public.security_epochs AS epoch
            WHERE session_row.token_hash = p_current_session_token_hash
              AND session_row.revoked_at IS NULL
              AND session_row.idle_expires_at > statement_timestamp()
              AND session_row.absolute_expires_at > statement_timestamp()
              AND account.status = 'active'
              AND account.deleted_at IS NULL
              AND account.authentication_version =
                  p_expected_authentication_version
              AND session_row.issued_authentication_version =
                  account.authentication_version
              AND session_row.issued_authentication_epoch =
                  epoch.authentication_version
              AND session_row.issued_session_epoch = epoch.session_epoch
              AND epoch.singleton
            FOR UPDATE OF account;
            IF p_new_password_hash NOT LIKE '$argon2id$%' THEN
                RAISE EXCEPTION 'invalid password verifier'
                    USING ERRCODE = '22023';
            END IF;
            UPDATE public.users
            SET password_hash = p_new_password_hash,
                authentication_version = authentication_version + 1,
                updated_at = statement_timestamp()
            WHERE id = v_user_id
            RETURNING authentication_version INTO v_new_version;
            UPDATE public.sessions SET revoked_at = statement_timestamp()
            WHERE user_id = v_user_id AND revoked_at IS NULL;
            PERFORM public.v4_issue_login_session(
                v_user_id, v_new_version, p_replacement_session_token_hash,
                p_csrf_token_hash, p_idle_expires_at, p_absolute_expires_at
            );
            RETURN v_user_id;
        END;
        $$
        """
    )


def _create_snapshot_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION v4_enforce_turn_source_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF current_setting(
                    'rag.snapshot_maintenance', true
                ) IN ('parent_delete', 'turn_retry') THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'turn source snapshots cannot be deleted'
                    USING ERRCODE = '23514';
            END IF;
            IF ROW(
                NEW.turn_id, NEW.rank, NEW.label,
                NEW.document_id_snapshot, NEW.chunk_id_snapshot,
                NEW.original_filename, NEW.display_name, NEW.logical_path,
                NEW.page_start, NEW.page_end, NEW.section,
                NEW.source_sha256, NEW.text_sha256,
                NEW.retrieval_distance, NEW.rerank_score,
                NEW.snapshot_text, NEW.token_count, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.turn_id, OLD.rank, OLD.label,
                OLD.document_id_snapshot, OLD.chunk_id_snapshot,
                OLD.original_filename, OLD.display_name, OLD.logical_path,
                OLD.page_start, OLD.page_end, OLD.section,
                OLD.source_sha256, OLD.text_sha256,
                OLD.retrieval_distance, OLD.rerank_score,
                OLD.snapshot_text, OLD.token_count, OLD.created_at
            ) THEN
                RAISE EXCEPTION 'turn source snapshots are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.document_id IS DISTINCT FROM OLD.document_id AND NOT (
                OLD.document_id IS NOT NULL
                AND NEW.document_id IS NULL
                AND NEW.owner_authorized_at_deletion IS NOT NULL
                AND (
                    OLD.owner_authorized_at_deletion IS NULL
                    OR NEW.owner_authorized_at_deletion =
                        OLD.owner_authorized_at_deletion
                )
            ) THEN
                RAISE EXCEPTION 'invalid live-source disposition transition'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.chunk_id IS DISTINCT FROM OLD.chunk_id AND NOT (
                OLD.chunk_id IS NOT NULL AND NEW.chunk_id IS NULL
            ) THEN
                RAISE EXCEPTION 'turn source live chunk reference is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.owner_authorized_at_deletion IS NOT NULL
               AND NEW.owner_authorized_at_deletion IS DISTINCT FROM
                   OLD.owner_authorized_at_deletion THEN
                RAISE EXCEPTION 'deleted-source disposition is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_v4_turn_source_immutability
        BEFORE UPDATE OR DELETE ON turn_sources
        FOR EACH ROW EXECUTE FUNCTION v4_enforce_turn_source_immutability()
        """
    )


def _create_controlled_mutation_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION v4_admin_create_user(
            p_username text,
            p_display_name text,
            p_role text,
            p_challenge_token_hash text,
            p_expires_at timestamptz
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_id uuid := public.gen_random_uuid();
        BEGIN
            PERFORM public.v4_require_admin();
            IF p_role NOT IN ('admin', 'member')
               OR p_challenge_token_hash !~ '^[0-9a-f]{64}$'
               OR p_expires_at > statement_timestamp() + interval '30 minutes'
               OR p_expires_at <= statement_timestamp() THEN
                RAISE EXCEPTION 'invalid pending user request'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.users (
                id, username, display_name, role, status, password_hash
            ) VALUES (
                v_id, p_username, p_display_name, p_role,
                'pending_activation', NULL
            );
            INSERT INTO public.pre_auth_challenges (
                id, user_id, purpose, token_hash, expires_at
            ) VALUES (
                public.gen_random_uuid(), v_id, 'activation',
                p_challenge_token_hash, p_expires_at
            );
            PERFORM public.v4_append_audit(
                'user_created', 'user', v_id,
                jsonb_build_object('role', p_role)
            );
            RETURN v_id;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_reset_user(
            p_user_id uuid,
            p_challenge_token_hash text,
            p_expires_at timestamptz
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_current public.users%ROWTYPE;
        BEGIN
            PERFORM public.v4_require_admin();
            SELECT * INTO v_current
            FROM public.users WHERE id = p_user_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'user state changed or no longer exists'
                    USING ERRCODE = 'RAG02';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-final-admin', 0)
            );
            IF p_challenge_token_hash !~ '^[0-9a-f]{64}$'
               OR p_expires_at > statement_timestamp() + interval '30 minutes'
               OR p_expires_at <= statement_timestamp() THEN
                RAISE EXCEPTION 'invalid reset request'
                    USING ERRCODE = '22023';
            END IF;
            IF v_current.status = 'deleted'
               OR v_current.deleted_at IS NOT NULL THEN
                RAISE EXCEPTION 'deleted users are irreversible'
                    USING ERRCODE = 'RAG04';
            END IF;
            IF v_current.role = 'admin'
               AND v_current.status = 'active'
               AND (
                   SELECT count(*) FROM public.users
                   WHERE role = 'admin' AND status = 'active'
                     AND deleted_at IS NULL
               ) <= 1 THEN
                RAISE EXCEPTION 'cannot reset the final active administrator'
                    USING ERRCODE = 'RAG03';
            END IF;
            UPDATE public.users
            SET status = 'pending_activation', password_hash = NULL,
                authentication_version = authentication_version + 1,
                updated_at = statement_timestamp()
            WHERE id = p_user_id AND deleted_at IS NULL;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'deleted users are irreversible'
                    USING ERRCODE = 'RAG04';
            END IF;
            UPDATE public.sessions SET revoked_at = statement_timestamp()
            WHERE user_id = p_user_id AND revoked_at IS NULL;
            UPDATE public.pre_auth_challenges
            SET revoked_at = statement_timestamp()
            WHERE user_id = p_user_id
              AND consumed_at IS NULL AND revoked_at IS NULL;
            INSERT INTO public.pre_auth_challenges (
                id, user_id, purpose, token_hash, expires_at
            ) VALUES (
                public.gen_random_uuid(), p_user_id, 'password_reset',
                p_challenge_token_hash, p_expires_at
            );
            PERFORM public.v4_append_audit(
                'password_reset_issued', 'user', p_user_id, '{}'::jsonb
            );
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_set_user(
            p_user_id uuid,
            p_role text,
            p_status text
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_current public.users%ROWTYPE;
        BEGIN
            PERFORM public.v4_require_admin();
            SELECT * INTO v_current
            FROM public.users WHERE id = p_user_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'user state changed or no longer exists'
                    USING ERRCODE = 'RAG02';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-final-admin', 0)
            );
            IF p_role NOT IN ('admin', 'member')
               OR p_status NOT IN ('active', 'disabled', 'deleted') THEN
                RAISE EXCEPTION 'invalid user state'
                    USING ERRCODE = '22023';
            END IF;
            IF v_current.status = 'deleted' THEN
                RAISE EXCEPTION 'deleted users are irreversible'
                    USING ERRCODE = 'RAG04';
            END IF;
            IF v_current.role = 'admin'
               AND v_current.status = 'active'
               AND (p_role <> 'admin' OR p_status <> 'active')
               AND (
                   SELECT count(*) FROM public.users
                   WHERE role = 'admin' AND status = 'active'
                     AND deleted_at IS NULL
               ) <= 1 THEN
                RAISE EXCEPTION 'cannot remove the final enabled administrator'
                    USING ERRCODE = 'RAG03';
            END IF;
            UPDATE public.users
            SET role = p_role, status = p_status,
                password_hash = CASE WHEN p_status = 'deleted'
                    THEN NULL ELSE password_hash END,
                deleted_at = CASE WHEN p_status = 'deleted'
                    THEN statement_timestamp() ELSE NULL END,
                authentication_version = authentication_version + 1,
                updated_at = statement_timestamp()
            WHERE id = p_user_id;
            UPDATE public.sessions SET revoked_at = statement_timestamp()
            WHERE user_id = p_user_id AND revoked_at IS NULL;
            IF p_status <> 'active' THEN
                DELETE FROM public.team_members WHERE user_id = p_user_id;
                DELETE FROM public.access_grants WHERE user_id = p_user_id;
            END IF;
            PERFORM public.v4_rebuild_effective_document_access();
            PERFORM public.v4_append_audit(
                'user_state_changed', 'user', p_user_id,
                jsonb_build_object('role', p_role, 'status', p_status)
            );
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_create_team(p_name text, p_name_key text)
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_id uuid := public.gen_random_uuid();
        BEGIN
            PERFORM public.v4_require_admin();
            INSERT INTO public.teams (id, name, name_key)
            VALUES (v_id, p_name, p_name_key);
            PERFORM public.v4_append_audit(
                'team_created', 'team', v_id, '{}'::jsonb
            );
            RETURN v_id;
        END;
        $$ 
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_account_active_teams()
        RETURNS TABLE (team_id uuid, team_name text, is_active boolean)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT team.id, team.name::text, team.is_active
            FROM public.team_members AS membership
            JOIN public.teams AS team
              ON team.id = membership.team_id
             AND team.is_active
            WHERE membership.user_id = public.v4_current_actor_id()
            ORDER BY team.name COLLATE "C", team.id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_document_team_recipients(p_document_ids uuid[])
        RETURNS TABLE (document_id uuid, team_ids uuid[])
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT node.document_id,
                   COALESCE(
                       array_agg(grant_row.team_id ORDER BY grant_row.team_id)
                           FILTER (
                               WHERE grant_row.team_id IS NOT NULL
                                 AND (
                                     public.v4_current_actor_is_admin()
                                     OR node.uploader_user_id =
                                        public.v4_current_actor_id()
                                     OR EXISTS (
                                         SELECT 1
                                         FROM public.team_members AS membership
                                         JOIN public.teams AS team
                                           ON team.id = membership.team_id
                                          AND team.is_active
                                         WHERE membership.user_id =
                                               public.v4_current_actor_id()
                                           AND membership.team_id =
                                               grant_row.team_id
                                     )
                                 )
                           ),
                       ARRAY[]::uuid[]
                   )
            FROM public.library_nodes AS node
            LEFT JOIN public.access_grants AS grant_row
              ON grant_row.node_id = node.id
             AND grant_row.team_id IS NOT NULL
            WHERE node.document_id = ANY(p_document_ids)
              AND public.v4_can_read_document(node.document_id)
            GROUP BY node.document_id, node.uploader_user_id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_access_context(p_node_id uuid)
        RETURNS jsonb
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_result jsonb;
        BEGIN
            PERFORM public.v4_require_admin();
            IF NOT EXISTS (
                SELECT 1 FROM public.library_nodes WHERE id = p_node_id
            ) THEN
                RAISE EXCEPTION 'library node not found'
                    USING ERRCODE = 'P0002';
            END IF;
            WITH RECURSIVE ancestry AS (
                SELECT node.id, node.parent_id, node.access_boundary, 0 AS depth
                FROM public.library_nodes AS node
                WHERE node.id = p_node_id
                UNION ALL
                SELECT parent.id, parent.parent_id, parent.access_boundary,
                       child.depth + 1
                FROM public.library_nodes AS parent
                JOIN ancestry AS child ON parent.id = child.parent_id
                WHERE NOT child.access_boundary
            ),
            direct_grants AS (
                SELECT grant_row.id, grant_row.node_id,
                       grant_row.user_id, grant_row.team_id
                FROM public.access_grants AS grant_row
                WHERE grant_row.node_id = p_node_id
            ),
            inherited_grants AS (
                SELECT grant_row.node_id AS source_node_id,
                       grant_row.user_id, grant_row.team_id
                FROM ancestry
                JOIN public.access_grants AS grant_row
                  ON grant_row.node_id = ancestry.id
                WHERE ancestry.depth > 0
            )
            SELECT jsonb_build_object(
                'node_id', p_node_id,
                'nearest_boundary_node_id', (
                    SELECT id FROM ancestry
                    WHERE access_boundary
                    ORDER BY depth
                    LIMIT 1
                ),
                'direct_grants', COALESCE((
                    SELECT jsonb_agg(
                        jsonb_build_object(
                            'id', id,
                            'node_id', node_id,
                            'user_id', user_id,
                            'team_id', team_id
                        )
                        ORDER BY id
                    )
                    FROM direct_grants
                ), '[]'::jsonb),
                'inherited_grants', COALESCE((
                    SELECT jsonb_agg(
                        jsonb_build_object(
                            'source_node_id', source_node_id,
                            'user_id', user_id,
                            'team_id', team_id
                        )
                        ORDER BY source_node_id, user_id, team_id
                    )
                    FROM inherited_grants
                ), '[]'::jsonb)
            ) INTO v_result;
            RETURN v_result;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_acl_impact(p_operation jsonb)
        RETURNS jsonb
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_kind text := p_operation->>'kind';
            v_principals jsonb;
            v_nodes jsonb;
            v_documents jsonb;
        BEGIN
            IF p_operation IS NULL
               OR jsonb_typeof(p_operation) <> 'object'
               OR v_kind IS NULL THEN
                RAISE EXCEPTION 'unsupported ACL operation'
                    USING ERRCODE = '22023';
            END IF;
            IF v_kind NOT IN (
                'set_grant', 'set_membership', 'set_boundary',
                'set_team_active', 'move_node'
            ) THEN
                RAISE EXCEPTION 'unsupported ACL operation'
                    USING ERRCODE = '22023';
            END IF;
            IF v_kind = 'move_node' AND (
                p_operation IS NULL
                OR jsonb_typeof(p_operation) <> 'object'
                OR (SELECT count(*) FROM jsonb_object_keys(p_operation)) <> 3
                OR NOT p_operation ? 'kind'
                OR NOT p_operation ? 'node_id'
                OR NOT p_operation ? 'parent_id'
                OR jsonb_typeof(p_operation->'kind') <> 'string'
                OR jsonb_typeof(p_operation->'node_id') <> 'string'
                OR (p_operation->>'node_id') !~
                    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                OR jsonb_typeof(p_operation->'parent_id')
                    NOT IN ('string', 'null')
                OR (
                    jsonb_typeof(p_operation->'parent_id') = 'string'
                    AND (p_operation->>'parent_id') !~
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                )
            ) THEN
                RAISE EXCEPTION 'invalid move operation'
                    USING ERRCODE = '22023';
            END IF;
            IF v_kind = 'move_node' AND EXISTS (
                WITH RECURSIVE subtree AS (
                    SELECT node.id
                    FROM public.library_nodes AS node
                    WHERE node.id =
                        (p_operation->>'node_id')::uuid
                    UNION ALL
                    SELECT child.id
                    FROM public.library_nodes AS child
                    JOIN subtree AS parent
                      ON child.parent_id = parent.id
                )
                SELECT 1
                FROM subtree
                WHERE id = NULLIF(
                    p_operation->>'parent_id', ''
                )::uuid
            ) THEN
                RAISE EXCEPTION 'move target creates a cycle'
                    USING ERRCODE = '22023';
            END IF;

            WITH RECURSIVE
            nodes_after AS (
                SELECT node.id,
                       CASE
                           WHEN v_kind = 'move_node'
                            AND node.id =
                                (p_operation->>'node_id')::uuid
                           THEN NULLIF(
                               p_operation->>'parent_id', ''
                           )::uuid
                           ELSE node.parent_id
                       END AS parent_id,
                       node.kind, node.document_id,
                       node.uploader_user_id,
                       CASE
                           WHEN v_kind = 'set_boundary'
                            AND node.id =
                                (p_operation->>'node_id')::uuid
                           THEN (p_operation->>'enabled')::boolean
                           ELSE node.access_boundary
                       END AS access_boundary
                FROM public.library_nodes AS node
            ),
            grants_after AS (
                SELECT grant_row.node_id, grant_row.user_id,
                       grant_row.team_id
                FROM public.access_grants AS grant_row
                WHERE NOT (
                    v_kind = 'set_grant'
                    AND grant_row.node_id =
                        (p_operation->>'node_id')::uuid
                    AND grant_row.user_id IS NOT DISTINCT FROM
                        NULLIF(p_operation->>'user_id', '')::uuid
                    AND grant_row.team_id IS NOT DISTINCT FROM
                        NULLIF(p_operation->>'team_id', '')::uuid
                )
                UNION
                SELECT (p_operation->>'node_id')::uuid,
                       NULLIF(p_operation->>'user_id', '')::uuid,
                       NULLIF(p_operation->>'team_id', '')::uuid
                WHERE v_kind = 'set_grant'
                  AND (p_operation->>'present')::boolean
            ),
            memberships_after AS (
                SELECT membership.team_id, membership.user_id
                FROM public.team_members AS membership
                WHERE NOT (
                    v_kind = 'set_membership'
                    AND membership.team_id =
                        (p_operation->>'team_id')::uuid
                    AND membership.user_id =
                        (p_operation->>'user_id')::uuid
                )
                UNION
                SELECT (p_operation->>'team_id')::uuid,
                       (p_operation->>'user_id')::uuid
                WHERE v_kind = 'set_membership'
                  AND (p_operation->>'present')::boolean
            ),
            teams_after AS (
                SELECT team.id,
                       CASE
                           WHEN v_kind = 'set_team_active'
                            AND team.id =
                                (p_operation->>'team_id')::uuid
                           THEN (p_operation->>'active')::boolean
                           ELSE team.is_active
                       END AS is_active
                FROM public.teams AS team
            ),
            paths_before AS (
                SELECT node.id AS document_node_id,
                       node.id AS ancestor_id, node.parent_id,
                       node.document_id, node.access_boundary AS boundary_seen
                FROM public.library_nodes AS node
                WHERE node.kind = 'file'
                UNION ALL
                SELECT path.document_node_id, parent.id, parent.parent_id,
                       path.document_id,
                       path.boundary_seen OR parent.access_boundary
                FROM paths_before AS path
                JOIN public.library_nodes AS parent
                  ON parent.id = path.parent_id
                WHERE NOT path.boundary_seen
            ),
            paths_after AS (
                SELECT node.id AS document_node_id,
                       node.id AS ancestor_id, node.parent_id,
                       node.document_id, node.access_boundary AS boundary_seen
                FROM nodes_after AS node
                WHERE node.kind = 'file'
                UNION ALL
                SELECT path.document_node_id, parent.id, parent.parent_id,
                       path.document_id,
                       path.boundary_seen OR parent.access_boundary
                FROM paths_after AS path
                JOIN nodes_after AS parent
                  ON parent.id = path.parent_id
                WHERE NOT path.boundary_seen
            ),
            principals_before AS (
                SELECT node.document_id, node.uploader_user_id AS user_id
                FROM public.library_nodes AS node
                WHERE node.kind = 'file'
                  AND node.uploader_user_id IS NOT NULL
                UNION
                SELECT path.document_id, grant_row.user_id
                FROM paths_before AS path
                JOIN public.access_grants AS grant_row
                  ON grant_row.node_id = path.ancestor_id
                WHERE grant_row.user_id IS NOT NULL
                UNION
                SELECT path.document_id, membership.user_id
                FROM paths_before AS path
                JOIN public.access_grants AS grant_row
                  ON grant_row.node_id = path.ancestor_id
                JOIN public.team_members AS membership
                  ON membership.team_id = grant_row.team_id
                JOIN public.teams AS team
                  ON team.id = membership.team_id
                 AND team.is_active
            ),
            principals_after AS (
                SELECT node.document_id, node.uploader_user_id AS user_id
                FROM nodes_after AS node
                WHERE node.kind = 'file'
                  AND node.uploader_user_id IS NOT NULL
                UNION
                SELECT path.document_id, grant_row.user_id
                FROM paths_after AS path
                JOIN grants_after AS grant_row
                  ON grant_row.node_id = path.ancestor_id
                WHERE grant_row.user_id IS NOT NULL
                UNION
                SELECT path.document_id, membership.user_id
                FROM paths_after AS path
                JOIN grants_after AS grant_row
                  ON grant_row.node_id = path.ancestor_id
                JOIN memberships_after AS membership
                  ON membership.team_id = grant_row.team_id
                JOIN teams_after AS team
                  ON team.id = membership.team_id
                 AND team.is_active
            ),
            access_before AS (
                SELECT DISTINCT principal.user_id, principal.document_id
                FROM principals_before AS principal
                JOIN public.users AS account
                  ON account.id = principal.user_id
                WHERE account.status = 'active'
                  AND account.deleted_at IS NULL
                  AND account.role = 'member'
            ),
            access_after AS (
                SELECT DISTINCT principal.user_id, principal.document_id
                FROM principals_after AS principal
                JOIN public.users AS account
                  ON account.id = principal.user_id
                WHERE account.status = 'active'
                  AND account.deleted_at IS NULL
                  AND account.role = 'member'
            ),
            removed_access AS (
                SELECT user_id, document_id FROM access_before
                EXCEPT
                SELECT user_id, document_id FROM access_after
            ),
            added_access AS (
                SELECT user_id, document_id FROM access_after
                EXCEPT
                SELECT user_id, document_id FROM access_before
            ),
            changed_access AS (
                SELECT user_id, document_id FROM removed_access
                UNION
                SELECT user_id, document_id FROM added_access
            ),
            changed_users AS (
                SELECT DISTINCT user_id FROM changed_access
            ),
            changed_documents AS (
                SELECT DISTINCT document_id FROM changed_access
            ),
            changed_nodes AS (
                SELECT node.id
                FROM public.library_nodes AS node
                JOIN changed_documents AS changed
                  ON changed.document_id = node.document_id
            )
            SELECT
                COALESCE((
                    SELECT jsonb_agg(user_id ORDER BY user_id)
                    FROM changed_users
                ), '[]'::jsonb),
                COALESCE((
                    SELECT jsonb_agg(id ORDER BY id)
                    FROM changed_nodes
                ), '[]'::jsonb),
                COALESCE((
                    SELECT jsonb_agg(document_id ORDER BY document_id)
                    FROM changed_documents
                ), '[]'::jsonb)
            INTO v_principals, v_nodes, v_documents;

            RETURN jsonb_build_object(
                'operation', p_operation,
                'user_ids', v_principals,
                'node_ids', v_nodes,
                'document_ids', v_documents,
                'user_count', jsonb_array_length(v_principals),
                'node_count', jsonb_array_length(v_nodes),
                'document_count', jsonb_array_length(v_documents)
            );
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_preview_acl(p_operation jsonb)
        RETURNS TABLE (
            preview_id uuid,
            impact_digest text,
            impact jsonb
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_actor uuid := public.v4_current_actor_id();
            v_id uuid := public.gen_random_uuid();
            v_impact jsonb := public.v4_acl_impact(p_operation);
            v_digest text := encode(public.digest(
                convert_to(v_impact::text, 'UTF8'), 'sha256'
            ), 'hex');
            v_version bigint;
        BEGIN
            IF v_actor IS NULL THEN
                RAISE EXCEPTION 'authentication required'
                    USING ERRCODE = '28000';
            END IF;
            IF NOT public.v4_current_actor_is_admin() AND (
                p_operation->>'kind' <> 'move_node'
                OR NULLIF(p_operation->>'parent_id', '') IS NULL
                OR NOT EXISTS (
                    SELECT 1
                    FROM public.library_nodes AS node
                    WHERE node.id = (p_operation->>'node_id')::uuid
                      AND node.kind = 'file'
                      AND node.uploader_user_id = v_actor
                )
                OR NOT EXISTS (
                    SELECT 1
                    FROM public.library_nodes AS target
                    WHERE target.id = (p_operation->>'parent_id')::uuid
                      AND target.kind = 'folder'
                      AND public.v4_can_read_folder(target.id)
                )
            ) THEN
                RAISE EXCEPTION 'capability denied'
                    USING ERRCODE = '42501';
            END IF;
            SELECT authorization_version INTO STRICT v_version
            FROM public.security_epochs WHERE singleton;
            INSERT INTO public.acl_previews (
                id, actor_user_id, operation, impact_digest,
                authorization_version, expires_at
            ) VALUES (
                v_id, v_actor, p_operation, v_digest, v_version,
                statement_timestamp() + interval '5 minutes'
            );
            RETURN QUERY SELECT
                v_id,
                v_digest,
                v_impact - 'operation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_apply_acl(
            p_preview_id uuid,
            p_impact_digest text
        )
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_actor uuid := public.v4_current_actor_id();
            v_preview public.acl_previews%ROWTYPE;
            v_kind text;
            v_version bigint;
            v_node public.library_nodes%ROWTYPE;
            v_target_id uuid;
            v_target_kind text;
            v_target_depth integer;
            v_subtree_depth integer;
            v_target_in_subtree boolean;
            v_moved_node_count bigint;
            v_new_logical_path text;
            v_audit_details jsonb;
        BEGIN
            IF v_actor IS NULL THEN
                RAISE EXCEPTION 'authentication required'
                    USING ERRCODE = '28000';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-library-acl', 0)
            );
            SELECT * INTO STRICT v_preview
            FROM public.acl_previews WHERE id = p_preview_id FOR UPDATE;
            SELECT authorization_version INTO STRICT v_version
            FROM public.security_epochs WHERE singleton FOR UPDATE;
            IF v_preview.actor_user_id <> v_actor
               OR v_preview.impact_digest <> p_impact_digest
               OR v_preview.authorization_version <> v_version
               OR v_preview.expires_at <= statement_timestamp()
               OR v_preview.consumed_at IS NOT NULL
               OR encode(public.digest(
                   convert_to(
                       public.v4_acl_impact(v_preview.operation)::text,
                       'UTF8'
                   ),
                   'sha256'
               ), 'hex') <> p_impact_digest THEN
                RAISE EXCEPTION 'stale or invalid ACL preview'
                    USING ERRCODE = '40001';
            END IF;
            v_kind := v_preview.operation->>'kind';
            IF NOT public.v4_current_actor_is_admin() AND (
                v_kind <> 'move_node'
                OR NULLIF(v_preview.operation->>'parent_id', '') IS NULL
                OR NOT EXISTS (
                    SELECT 1
                    FROM public.library_nodes AS node
                    WHERE node.id =
                          (v_preview.operation->>'node_id')::uuid
                      AND node.kind = 'file'
                      AND node.uploader_user_id = v_actor
                )
                OR NOT EXISTS (
                    SELECT 1
                    FROM public.library_nodes AS target
                    WHERE target.id =
                          (v_preview.operation->>'parent_id')::uuid
                      AND target.kind = 'folder'
                      AND public.v4_can_read_folder(target.id)
                )
            ) THEN
                RAISE EXCEPTION 'capability denied'
                    USING ERRCODE = '42501';
            END IF;
            IF v_kind = 'set_grant' THEN
                IF (v_preview.operation->>'present')::boolean THEN
                    INSERT INTO public.access_grants (
                        id, node_id, user_id, team_id
                    ) VALUES (
                        public.gen_random_uuid(),
                        (v_preview.operation->>'node_id')::uuid,
                        NULLIF(v_preview.operation->>'user_id', '')::uuid,
                        NULLIF(v_preview.operation->>'team_id', '')::uuid
                    ) ON CONFLICT DO NOTHING;
                ELSE
                    DELETE FROM public.access_grants
                    WHERE node_id =
                        (v_preview.operation->>'node_id')::uuid
                      AND user_id IS NOT DISTINCT FROM
                        NULLIF(v_preview.operation->>'user_id', '')::uuid
                      AND team_id IS NOT DISTINCT FROM
                        NULLIF(v_preview.operation->>'team_id', '')::uuid;
                END IF;
            ELSIF v_kind = 'set_membership' THEN
                IF (v_preview.operation->>'present')::boolean THEN
                    INSERT INTO public.team_members (team_id, user_id)
                    VALUES (
                        (v_preview.operation->>'team_id')::uuid,
                        (v_preview.operation->>'user_id')::uuid
                    ) ON CONFLICT DO NOTHING;
                ELSE
                    DELETE FROM public.team_members
                    WHERE team_id =
                        (v_preview.operation->>'team_id')::uuid
                      AND user_id =
                        (v_preview.operation->>'user_id')::uuid;
                END IF;
            ELSIF v_kind = 'set_boundary' THEN
                UPDATE public.library_nodes
                SET access_boundary =
                    (v_preview.operation->>'enabled')::boolean,
                    updated_at = statement_timestamp()
                WHERE id = (v_preview.operation->>'node_id')::uuid
                  AND kind = 'folder';
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'boundary node is not a folder'
                        USING ERRCODE = '22023';
                END IF;
            ELSIF v_kind = 'move_node' THEN
                v_target_id :=
                    NULLIF(v_preview.operation->>'parent_id', '')::uuid;
                SELECT * INTO v_node
                FROM public.library_nodes
                WHERE id = (v_preview.operation->>'node_id')::uuid
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'move node does not exist'
                        USING ERRCODE = '22023';
                END IF;
                IF v_target_id = v_node.id THEN
                    RAISE EXCEPTION 'move target cannot be the moved node'
                        USING ERRCODE = '22023';
                END IF;
                IF v_target_id IS NOT NULL THEN
                    SELECT kind INTO v_target_kind
                    FROM public.library_nodes
                    WHERE id = v_target_id
                    FOR UPDATE;
                    IF NOT FOUND OR v_target_kind <> 'folder' THEN
                        RAISE EXCEPTION 'move target is not a folder'
                            USING ERRCODE = '22023';
                    END IF;
                END IF;
                WITH RECURSIVE subtree AS (
                    SELECT node.id, 0 AS depth
                    FROM public.library_nodes AS node
                    WHERE node.id = v_node.id
                    UNION ALL
                    SELECT child.id, parent.depth + 1
                    FROM public.library_nodes AS child
                    JOIN subtree AS parent
                      ON child.parent_id = parent.id
                )
                SELECT COALESCE(max(depth), 0),
                       COALESCE(bool_or(id = v_target_id), false)
                INTO v_subtree_depth, v_target_in_subtree
                FROM subtree;
                IF v_target_in_subtree THEN
                    RAISE EXCEPTION 'move target creates a cycle'
                        USING ERRCODE = '22023';
                END IF;
                WITH RECURSIVE ancestors AS (
                    SELECT node.id, node.parent_id, 1 AS depth
                    FROM public.library_nodes AS node
                    WHERE node.id = v_target_id
                    UNION ALL
                    SELECT parent.id, parent.parent_id, child.depth + 1
                    FROM public.library_nodes AS parent
                    JOIN ancestors AS child
                      ON parent.id = child.parent_id
                )
                SELECT COALESCE(max(depth), 0) INTO v_target_depth
                FROM ancestors;
                IF v_target_depth + 1 + v_subtree_depth > 256 THEN
                    RAISE EXCEPTION 'move exceeds maximum library depth'
                        USING ERRCODE = '22023';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM public.library_nodes AS sibling
                    WHERE sibling.parent_id IS NOT DISTINCT FROM v_target_id
                      AND sibling.name_key = v_node.name_key
                      AND sibling.id <> v_node.id
                ) THEN
                    RAISE EXCEPTION 'move target has a sibling name conflict'
                        USING ERRCODE = '23505',
                              CONSTRAINT =
                                  'uq_library_nodes_parent_name_key';
                END IF;
                UPDATE public.library_nodes
                SET parent_id = v_target_id,
                    updated_at = statement_timestamp()
                WHERE id = v_node.id;
                WITH RECURSIVE ancestry AS (
                    SELECT node.id, node.parent_id, node.name, 0 AS depth
                    FROM public.library_nodes AS node
                    WHERE node.id = v_node.id
                    UNION ALL
                    SELECT parent.id, parent.parent_id, parent.name,
                           child.depth + 1
                    FROM public.library_nodes AS parent
                    JOIN ancestry AS child
                      ON parent.id = child.parent_id
                ),
                moved_root AS (
                    SELECT '/' || string_agg(
                        name, '/' ORDER BY depth DESC
                    ) AS logical_path
                    FROM ancestry
                ),
                moved_paths AS (
                    SELECT node.id, moved_root.logical_path
                    FROM public.library_nodes AS node
                    CROSS JOIN moved_root
                    WHERE node.id = v_node.id
                    UNION ALL
                    SELECT child.id,
                           parent.logical_path || '/' || child.name
                    FROM public.library_nodes AS child
                    JOIN moved_paths AS parent
                      ON child.parent_id = parent.id
                )
                SELECT count(*),
                       max(logical_path) FILTER (WHERE id = v_node.id)
                INTO v_moved_node_count, v_new_logical_path
                FROM moved_paths;
                v_audit_details := jsonb_build_object(
                    'kind', v_kind,
                    'node_id', v_node.id,
                    'parent_id', v_target_id,
                    'logical_path', v_new_logical_path,
                    'moved_node_count', v_moved_node_count
                );
            ELSE
                UPDATE public.teams
                SET is_active = (v_preview.operation->>'active')::boolean,
                    updated_at = statement_timestamp()
                WHERE id = (v_preview.operation->>'team_id')::uuid;
            END IF;
            UPDATE public.acl_previews
            SET consumed_at = statement_timestamp()
            WHERE id = p_preview_id;
            v_version := public.v4_rebuild_effective_document_access();
            PERFORM public.v4_append_audit(
                'acl_operation_applied', 'acl_preview', p_preview_id,
                COALESCE(
                    v_audit_details,
                    jsonb_build_object('kind', v_kind)
                )
            );
            RETURN v_version;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE FUNCTION v4_claim_service_lease(
            p_service_name text,
            p_owner_id text,
            p_lease_seconds integer
        )
        RETURNS TABLE (
            lease_token uuid,
            fencing_token bigint,
            lease_expires_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_token uuid := public.gen_random_uuid();
        BEGIN
            IF p_lease_seconds NOT BETWEEN 5 AND 300 THEN
                RAISE EXCEPTION 'invalid service lease duration'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.service_leases (
                service_name, owner_id, lease_token, fencing_token,
                heartbeat_at, lease_expires_at
            ) VALUES (
                p_service_name, p_owner_id, v_token, 1,
                statement_timestamp(),
                statement_timestamp() + make_interval(secs => p_lease_seconds)
            )
            ON CONFLICT (service_name) DO UPDATE
            SET owner_id = EXCLUDED.owner_id,
                lease_token = EXCLUDED.lease_token,
                fencing_token = public.service_leases.fencing_token + 1,
                heartbeat_at = EXCLUDED.heartbeat_at,
                lease_expires_at = EXCLUDED.lease_expires_at,
                updated_at = statement_timestamp()
            WHERE public.service_leases.lease_expires_at <=
                    statement_timestamp()
               OR public.service_leases.owner_id = p_owner_id
            RETURNING public.service_leases.lease_token,
                public.service_leases.fencing_token,
                public.service_leases.lease_expires_at
            INTO lease_token, fencing_token, lease_expires_at;
            IF lease_token IS NULL THEN
                RAISE EXCEPTION 'service lease is already owned'
                    USING ERRCODE = '55P03';
            END IF;
            RETURN NEXT;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_heartbeat_service_lease(
            p_service_name text,
            p_owner_id text,
            p_lease_token uuid,
            p_fencing_token bigint,
            p_lease_seconds integer
        )
        RETURNS boolean
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            WITH renewed AS (
                UPDATE public.service_leases
                SET heartbeat_at = statement_timestamp(),
                    lease_expires_at = statement_timestamp()
                        + make_interval(secs => p_lease_seconds),
                    updated_at = statement_timestamp()
                WHERE service_name = p_service_name
                  AND owner_id = p_owner_id
                  AND lease_token = p_lease_token
                  AND fencing_token = p_fencing_token
                  AND lease_expires_at > statement_timestamp()
                  AND p_lease_seconds BETWEEN 5 AND 300
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM renewed)
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_get_job(p_job_id uuid)
        RETURNS TABLE (
            job_id uuid,
            document_id uuid,
            status text,
            stage text,
            completed_units integer,
            total_units integer,
            error text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT job.id, job.document_id, job.status::text,
                   job.stage::text, job.completed_units, job.total_units,
                   job.error::text
            FROM public.ingestion_jobs AS job
            WHERE job.id = p_job_id
              AND public.v4_current_actor_is_admin()
              AND public.v4_can_read_document(job.document_id)
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_claim_ingestion_job(
            p_owner_id text,
            p_lease_seconds integer
        )
        RETURNS TABLE (
            job_id uuid,
            document_id uuid,
            object_key text,
            original_filename text,
            sha256 text,
            byte_size bigint,
            attempt integer,
            lease_token uuid,
            fencing_token bigint
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            IF p_lease_seconds NOT BETWEEN 5 AND 3600 THEN
                RAISE EXCEPTION 'invalid ingestion lease duration'
                    USING ERRCODE = '22023';
            END IF;
            RETURN QUERY
            WITH candidate AS (
                SELECT queued_job.id
                FROM public.ingestion_jobs AS queued_job
                WHERE (
                    queued_job.status = 'queued'
                    AND queued_job.available_at <= statement_timestamp()
                ) OR (
                    queued_job.status = 'running'
                    AND queued_job.lease_expires_at <= statement_timestamp()
                )
                ORDER BY queued_job.available_at, queued_job.created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE public.ingestion_jobs AS job
            SET status = 'running',
                attempt = job.attempt + 1,
                started_at = COALESCE(started_at, statement_timestamp()),
                heartbeat_at = statement_timestamp(),
                lease_owner = p_owner_id,
                lease_token = public.gen_random_uuid(),
                lease_expires_at = statement_timestamp()
                    + make_interval(secs => p_lease_seconds),
                fencing_token = job.fencing_token + 1,
                updated_at = statement_timestamp()
            FROM candidate, public.documents AS document
            WHERE job.id = candidate.id
              AND document.id = job.document_id
            RETURNING job.id, document.id, document.object_key::text,
                document.original_filename::text, document.sha256::text,
                document.byte_size, job.attempt, job.lease_token,
                job.fencing_token;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_heartbeat_ingestion_job(
            p_job_id uuid,
            p_lease_token uuid,
            p_fencing_token bigint,
            p_lease_seconds integer
        )
        RETURNS boolean
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            WITH renewed AS (
                UPDATE public.ingestion_jobs
                SET heartbeat_at = statement_timestamp(),
                    lease_expires_at = statement_timestamp()
                        + make_interval(secs => p_lease_seconds),
                    updated_at = statement_timestamp()
                WHERE id = p_job_id AND status = 'running'
                  AND lease_token = p_lease_token
                  AND fencing_token = p_fencing_token
                  AND lease_expires_at > statement_timestamp()
                  AND p_lease_seconds BETWEEN 5 AND 3600
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM renewed)
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_update_ingestion_progress(
            p_job_id uuid,
            p_lease_token uuid,
            p_fencing_token bigint,
            p_stage text,
            p_completed_units integer,
            p_total_units integer
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_job public.ingestion_jobs%ROWTYPE;
            v_current_stage_order integer;
            v_requested_stage_order integer;
        BEGIN
            IF p_stage IS NULL
               OR p_completed_units IS NULL
               OR p_total_units IS NULL
               OR p_stage NOT IN (
                'parsing', 'chunking', 'embedding', 'indexing'
            )
               OR p_total_units NOT BETWEEN 1 AND 1000000
               OR p_completed_units NOT BETWEEN 0 AND p_total_units THEN
                RAISE EXCEPTION 'invalid ingestion progress'
                    USING ERRCODE = '22023';
            END IF;
            SELECT * INTO v_job
            FROM public.ingestion_jobs
            WHERE id = p_job_id
            FOR UPDATE;
            IF NOT FOUND
               OR v_job.status <> 'running'
               OR p_lease_token IS NULL
               OR p_fencing_token IS NULL
               OR v_job.lease_token <> p_lease_token
               OR v_job.fencing_token <> p_fencing_token
               OR v_job.lease_expires_at <= statement_timestamp() THEN
                RETURN 'stale';
            END IF;
            v_current_stage_order := CASE v_job.stage
                WHEN 'uploaded' THEN 0
                WHEN 'parsing' THEN 1
                WHEN 'chunking' THEN 2
                WHEN 'embedding' THEN 3
                WHEN 'indexing' THEN 4
                ELSE 5
            END;
            v_requested_stage_order := CASE p_stage
                WHEN 'parsing' THEN 1
                WHEN 'chunking' THEN 2
                WHEN 'embedding' THEN 3
                WHEN 'indexing' THEN 4
            END;
            IF v_requested_stage_order < v_current_stage_order
               OR (
                   v_requested_stage_order = v_current_stage_order
                   AND (
                       p_completed_units < v_job.completed_units
                       OR (
                           v_job.total_units IS NOT NULL
                           AND p_total_units < v_job.total_units
                       )
                   )
               ) THEN
                RETURN 'stale';
            END IF;
            UPDATE public.ingestion_jobs
            SET stage = p_stage,
                completed_units = p_completed_units,
                total_units = p_total_units,
                heartbeat_at = statement_timestamp(),
                updated_at = statement_timestamp()
            WHERE id = p_job_id;
            UPDATE public.documents
            SET state = p_stage,
                stage = p_stage,
                error = NULL,
                updated_at = statement_timestamp()
            WHERE id = v_job.document_id;
            RETURN 'accepted';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_commit_ingestion_job(
            p_job_id uuid,
            p_lease_token uuid,
            p_fencing_token bigint,
            p_page_count integer,
            p_chunks jsonb
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_document_id uuid;
            v_chunk_count integer;
        BEGIN
            SELECT document_id INTO v_document_id
            FROM public.ingestion_jobs
            WHERE id = p_job_id AND status = 'running'
              AND lease_token = p_lease_token
              AND fencing_token = p_fencing_token
              AND lease_expires_at > statement_timestamp()
            FOR UPDATE;
            IF v_document_id IS NULL
               OR p_page_count < 0
               OR jsonb_typeof(p_chunks) <> 'array' THEN
                RETURN false;
            END IF;
            DELETE FROM public.chunks
            WHERE document_id = v_document_id;
            INSERT INTO public.chunks (
                id, document_id, ordinal, filename, page_start, page_end,
                section, text, token_count, text_sha256, source_sha256,
                parse_method, parser_version, chunking_version,
                embedding_version, schema_version, citation_label, embedding
            )
            SELECT source.id, v_document_id, source.ordinal,
                   source.filename, source.page_start, source.page_end,
                   source.section, source.text, source.token_count,
                   source.text_sha256, source.source_sha256,
                   source.parse_method, source.parser_version,
                   source.chunking_version, source.embedding_version,
                   source.schema_version, source.citation_label,
                   source.embedding::public.vector
            FROM jsonb_to_recordset(p_chunks) AS source(
                id uuid, ordinal integer, filename text,
                page_start integer, page_end integer, section text,
                text text, token_count integer, text_sha256 text,
                source_sha256 text, parse_method text,
                parser_version text, chunking_version text,
                embedding_version text, schema_version text,
                citation_label text, embedding text
            );
            GET DIAGNOSTICS v_chunk_count = ROW_COUNT;
            IF v_chunk_count <> jsonb_array_length(p_chunks) THEN
                RAISE EXCEPTION 'chunk payload count changed'
                    USING ERRCODE = '22023';
            END IF;
            UPDATE public.documents
            SET state = 'ready', stage = 'ready', error = NULL,
                page_count = p_page_count, chunk_count = v_chunk_count,
                updated_at = statement_timestamp()
            WHERE id = v_document_id;
            UPDATE public.ingestion_jobs
            SET status = 'completed', stage = 'ready',
                completed_units = v_chunk_count,
                total_units = v_chunk_count, error = NULL,
                lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL,
                heartbeat_at = statement_timestamp(),
                updated_at = statement_timestamp()
            WHERE id = p_job_id;
            RETURN true;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_requeue_ingestion_job(
            p_job_id uuid,
            p_lease_token uuid,
            p_fencing_token bigint,
            p_available_at timestamptz
        )
        RETURNS boolean
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            WITH changed AS (
                UPDATE public.ingestion_jobs
                SET status = 'queued', error = NULL,
                    available_at = p_available_at,
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    updated_at = statement_timestamp()
                WHERE id = p_job_id AND status = 'running'
                  AND lease_token = p_lease_token
                  AND fencing_token = p_fencing_token
                  AND lease_expires_at > statement_timestamp()
                  AND p_available_at > statement_timestamp()
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM changed)
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_poison_ingestion_job(
            p_job_id uuid,
            p_lease_token uuid,
            p_fencing_token bigint,
            p_error text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_document_id uuid;
        BEGIN
            UPDATE public.ingestion_jobs
            SET status = 'failed', stage = 'failed',
                error = left(btrim(p_error), 500),
                lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL,
                heartbeat_at = statement_timestamp(),
                updated_at = statement_timestamp()
            WHERE id = p_job_id AND status = 'running'
              AND lease_token = p_lease_token
              AND fencing_token = p_fencing_token
              AND lease_expires_at > statement_timestamp()
              AND char_length(btrim(p_error)) BETWEEN 1 AND 500
            RETURNING document_id INTO v_document_id;
            IF v_document_id IS NULL THEN
                RETURN false;
            END IF;
            UPDATE public.documents
            SET state = 'failed', stage = 'failed',
                error = left(btrim(p_error), 500),
                updated_at = statement_timestamp()
            WHERE id = v_document_id;
            RETURN true;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_finish_ingestion_job(
            p_job_id uuid,
            p_lease_token uuid,
            p_fencing_token bigint,
            p_success boolean,
            p_error text
        )
        RETURNS boolean
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            WITH finished AS (
                UPDATE public.ingestion_jobs
                SET status = CASE WHEN p_success THEN 'completed' ELSE 'failed' END,
                    stage = CASE WHEN p_success THEN 'ready' ELSE 'failed' END,
                    error = CASE WHEN p_success THEN NULL
                        ELSE left(p_error, 500) END,
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = statement_timestamp(),
                    updated_at = statement_timestamp()
                WHERE id = p_job_id AND status = 'running'
                  AND lease_token = p_lease_token
                  AND fencing_token = p_fencing_token
                  AND lease_expires_at > statement_timestamp()
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM finished)
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_claim_object_deletion(
            p_owner_id text,
            p_lease_seconds integer
        )
        RETURNS TABLE (
            deletion_id uuid,
            object_key text,
            lease_token uuid,
            fencing_token bigint,
            attempt integer
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            IF p_lease_seconds NOT BETWEEN 5 AND 3600 THEN
                RAISE EXCEPTION 'invalid deletion lease duration'
                    USING ERRCODE = '22023';
            END IF;
            RETURN QUERY
            WITH candidate AS (
                SELECT deletion_candidate.id
                FROM public.object_deletions AS deletion_candidate
                WHERE (
                    (
                        deletion_candidate.status = 'queued'
                        AND deletion_candidate.available_at <=
                            statement_timestamp()
                    )
                    OR (
                        deletion_candidate.status = 'leased'
                        AND deletion_candidate.lease_expires_at <=
                            statement_timestamp()
                    )
                )
                  AND NOT EXISTS (
                      SELECT 1 FROM public.documents AS document
                      WHERE document.object_key =
                          deletion_candidate.object_key
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.upload_reservations AS reservation
                      WHERE reservation.object_key =
                          deletion_candidate.object_key
                        AND reservation.consumed_at IS NULL
                        AND reservation.expires_at > statement_timestamp()
                  )
                ORDER BY deletion_candidate.available_at,
                         deletion_candidate.created_at
                FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE public.object_deletions AS deletion_row
            SET status = 'leased',
                attempt = deletion_row.attempt + 1,
                lease_owner = p_owner_id,
                lease_token = public.gen_random_uuid(),
                lease_expires_at = statement_timestamp()
                    + make_interval(secs => p_lease_seconds),
                fencing_token = deletion_row.fencing_token + 1,
                updated_at = statement_timestamp()
            FROM candidate
            WHERE deletion_row.id = candidate.id
            RETURNING deletion_row.id, deletion_row.object_key::text,
                deletion_row.lease_token, deletion_row.fencing_token,
                deletion_row.attempt;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_finish_object_deletion(
            p_deletion_id uuid,
            p_lease_token uuid,
            p_fencing_token bigint,
            p_success boolean,
            p_error text
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            IF p_success IS NULL OR (
                NOT p_success AND (
                    p_error IS NULL
                    OR char_length(btrim(p_error)) = 0
                )
            ) THEN
                RAISE EXCEPTION 'invalid deletion completion'
                    USING ERRCODE = '22023';
            END IF;
            IF p_success THEN
                DELETE FROM public.object_deletions
                WHERE id = p_deletion_id AND status = 'leased'
                  AND lease_token = p_lease_token
                  AND fencing_token = p_fencing_token
                  AND lease_expires_at > statement_timestamp();
                IF FOUND THEN
                    RETURN 'deleted';
                END IF;
            ELSE
                UPDATE public.object_deletions
                SET status = CASE WHEN attempt >= 5
                        THEN 'failed' ELSE 'queued' END,
                    available_at = CASE WHEN attempt >= 5
                        THEN available_at
                        ELSE statement_timestamp() + LEAST(
                            interval '15 minutes',
                            interval '5 seconds' * power(2, attempt - 1)
                        )
                    END,
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, last_error = left(p_error, 2000),
                    updated_at = statement_timestamp()
                WHERE id = p_deletion_id AND status = 'leased'
                  AND lease_token = p_lease_token
                  AND fencing_token = p_fencing_token
                  AND lease_expires_at > statement_timestamp();
                IF FOUND THEN
                    IF EXISTS (
                        SELECT 1 FROM public.object_deletions
                        WHERE id = p_deletion_id AND status = 'failed'
                    ) THEN
                        RETURN 'poisoned';
                    END IF;
                    RETURN 'requeued';
                END IF;
            END IF;
            RETURN 'stale';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_heartbeat_object_deletion(
            p_deletion_id uuid,
            p_lease_token uuid,
            p_fencing_token bigint,
            p_lease_seconds integer
        )
        RETURNS boolean
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            WITH renewed AS (
                UPDATE public.object_deletions
                SET lease_expires_at = statement_timestamp()
                        + make_interval(secs => p_lease_seconds),
                    updated_at = statement_timestamp()
                WHERE id = p_deletion_id AND status = 'leased'
                  AND lease_token = p_lease_token
                  AND fencing_token = p_fencing_token
                  AND lease_expires_at > statement_timestamp()
                  AND p_lease_seconds BETWEEN 5 AND 3600
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM renewed)
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_upload_metadata_digest(
            p_filename text,
            p_display_name text,
            p_name_key text,
            p_mime_type text,
            p_byte_size bigint,
            p_parser_version text,
            p_chunking_version text,
            p_embedding_version text
        )
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT encode(
                public.digest(
                    convert_to(
                        jsonb_build_array(
                            p_filename, p_display_name, p_name_key,
                            p_mime_type, p_byte_size, p_parser_version,
                            p_chunking_version, p_embedding_version
                        )::text,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_upload_preflight(
            p_sha256 text,
            p_object_key text,
            p_filename text,
            p_display_name text,
            p_name_key text,
            p_mime_type text,
            p_byte_size bigint,
            p_parser_version text,
            p_chunking_version text,
            p_embedding_version text,
            p_parent_id uuid,
            p_selected_team_ids uuid[]
        )
        RETURNS TABLE (
            result_status text,
            document_id uuid,
            job_id uuid,
            node_id uuid,
            parent_id uuid,
            display_name text,
            logical_path text,
            duplicate_of uuid,
            location_reused boolean,
            reservation_id uuid,
            reservation_expires_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_actor uuid;
            v_reservation_id uuid := public.gen_random_uuid();
            v_expires_at timestamptz :=
                statement_timestamp() + interval '15 minutes';
            v_existing_document uuid;
            v_existing_job uuid;
            v_existing_node uuid;
            v_existing_parent uuid;
            v_existing_name text;
            v_logical_path text;
            v_active_reservation public.upload_reservations%ROWTYPE;
        BEGIN
            v_actor := public.v4_current_actor_id();
            IF v_actor IS NULL THEN
                RAISE EXCEPTION 'authentication required'
                    USING ERRCODE = '28000';
            END IF;
            IF p_sha256 !~ '^[0-9a-f]{64}$'
               OR p_object_key <>
                  'originals/' || left(p_sha256, 2) || '/' ||
                  p_sha256 || '.pdf'
               OR p_mime_type <> 'application/pdf'
               OR p_byte_size <= 0
               OR p_filename <> btrim(p_filename)
               OR char_length(p_filename) NOT BETWEEN 1 AND 512
               OR char_length(p_display_name) NOT BETWEEN 1 AND 255
               OR char_length(p_name_key) NOT BETWEEN 1 AND 1024
               OR char_length(p_parser_version) NOT BETWEEN 1 AND 64
               OR char_length(p_chunking_version) NOT BETWEEN 1 AND 64
               OR char_length(p_embedding_version) NOT BETWEEN 1 AND 128 THEN
                RAISE EXCEPTION 'invalid canonical upload metadata'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-upload:' || p_sha256, 0)
            );
            IF p_selected_team_ids IS NULL
               OR cardinality(p_selected_team_ids) <>
                  cardinality(ARRAY(
                      SELECT DISTINCT team_id
                      FROM unnest(p_selected_team_ids) AS team_id
                  ))
               OR EXISTS (
                   SELECT 1
                   FROM unnest(p_selected_team_ids) AS selected(team_id)
                   LEFT JOIN public.team_members AS membership
                     ON membership.team_id = selected.team_id
                    AND membership.user_id = v_actor
                   LEFT JOIN public.teams AS team
                     ON team.id = selected.team_id
                    AND team.is_active
                   WHERE membership.user_id IS NULL OR team.id IS NULL
               )
               OR (
                   EXISTS (
                       SELECT 1
                       FROM public.team_members AS membership
                       JOIN public.teams AS team
                         ON team.id = membership.team_id
                        AND team.is_active
                       WHERE membership.user_id = v_actor
                   )
                   AND cardinality(p_selected_team_ids) = 0
               ) THEN
                RAISE EXCEPTION 'invalid upload team selection'
                    USING ERRCODE = '22023';
            END IF;
            IF NOT public.v4_current_actor_is_admin() AND p_parent_id IS NULL THEN
                RAISE EXCEPTION 'members must upload into a folder'
                    USING ERRCODE = '22023';
            END IF;
            UPDATE public.upload_reservations
            SET consumed_at = statement_timestamp(), outcome = 'expired',
                updated_at = statement_timestamp()
            WHERE object_key = p_object_key
              AND consumed_at IS NULL
              AND expires_at <= statement_timestamp();
            IF EXISTS (
                SELECT 1 FROM public.object_deletions
                WHERE object_key = p_object_key
            ) THEN
                RETURN QUERY SELECT 'pending_deletion'::text,
                    NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                    NULL::text, NULL::text, NULL::uuid, false,
                    NULL::uuid, NULL::timestamptz;
                RETURN;
            END IF;
            SELECT document.id, job.id, node.id, node.parent_id, node.name
            INTO v_existing_document, v_existing_job, v_existing_node,
                 v_existing_parent, v_existing_name
            FROM public.documents AS document
            JOIN public.library_nodes AS node
              ON node.document_id = document.id
            LEFT JOIN LATERAL (
                SELECT candidate.id
                FROM public.ingestion_jobs AS candidate
                WHERE candidate.document_id = document.id
                ORDER BY candidate.created_at DESC, candidate.id DESC
                LIMIT 1
            ) AS job ON true
            WHERE document.sha256 = p_sha256;
            IF v_existing_document IS NOT NULL THEN
                IF NOT public.v4_can_read_document(v_existing_document) THEN
                    RETURN QUERY SELECT 'duplicate_forbidden'::text,
                        NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                        NULL::text, NULL::text, NULL::uuid, false,
                        NULL::uuid, NULL::timestamptz;
                    RETURN;
                END IF;
                WITH RECURSIVE ancestry AS (
                    SELECT node.id, node.parent_id, node.name, 0 AS depth
                    FROM public.library_nodes AS node
                    WHERE node.id = v_existing_node
                    UNION ALL
                    SELECT parent.id, parent.parent_id, parent.name,
                           child.depth + 1
                    FROM public.library_nodes AS parent
                    JOIN ancestry AS child ON parent.id = child.parent_id
                )
                SELECT '/' || string_agg(
                    ancestry.name, '/' ORDER BY ancestry.depth DESC
                )
                INTO v_logical_path FROM ancestry;
                RETURN QUERY SELECT 'duplicate'::text,
                    v_existing_document, v_existing_job, v_existing_node,
                    v_existing_parent, v_existing_name, v_logical_path,
                    v_existing_document, true, NULL::uuid,
                    NULL::timestamptz;
                RETURN;
            END IF;
            SELECT reservation.* INTO v_active_reservation
            FROM public.upload_reservations AS reservation
            WHERE reservation.object_key = p_object_key
              AND reservation.consumed_at IS NULL
              AND reservation.expires_at > statement_timestamp()
            FOR UPDATE;
            IF FOUND
               AND v_active_reservation.actor_user_id = v_actor
               AND v_active_reservation.parent_id IS NOT DISTINCT FROM p_parent_id
               AND v_active_reservation.selected_team_ids =
                  p_selected_team_ids
               AND v_active_reservation.metadata_digest =
                  public.v4_upload_metadata_digest(
                      p_filename, p_display_name, p_name_key, p_mime_type,
                      p_byte_size, p_parser_version, p_chunking_version,
                      p_embedding_version
                  ) THEN
                RETURN QUERY SELECT 'upload_required'::text,
                    NULL::uuid, NULL::uuid, NULL::uuid, p_parent_id,
                    p_display_name, NULL::text, NULL::uuid, false,
                    v_active_reservation.id, v_active_reservation.expires_at;
                RETURN;
            ELSIF FOUND THEN
                RETURN QUERY SELECT 'reservation_active'::text,
                    NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                    NULL::text, NULL::text, NULL::uuid, false,
                    NULL::uuid, NULL::timestamptz;
                RETURN;
            END IF;
            IF p_parent_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM public.library_nodes
                WHERE id = p_parent_id
                  AND kind = 'folder'
                  AND public.v4_can_read_folder(id)
            ) THEN
                RETURN QUERY SELECT 'parent_not_found'::text,
                    NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                    NULL::text, NULL::text, NULL::uuid, false,
                    NULL::uuid, NULL::timestamptz;
                RETURN;
            END IF;
            INSERT INTO public.upload_reservations (
                id, actor_user_id, sha256, object_key, parent_id,
                selected_team_ids,
                metadata_digest, expires_at
            ) VALUES (
                v_reservation_id, v_actor, p_sha256, p_object_key,
                p_parent_id, p_selected_team_ids,
                public.v4_upload_metadata_digest(
                    p_filename, p_display_name, p_name_key, p_mime_type,
                    p_byte_size, p_parser_version, p_chunking_version,
                    p_embedding_version
                ),
                v_expires_at
            );
            RETURN QUERY SELECT 'upload_required'::text,
                NULL::uuid, NULL::uuid, NULL::uuid, p_parent_id,
                p_display_name, NULL::text, NULL::uuid, false,
                v_reservation_id, v_expires_at;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_commit_upload(
            p_reservation_id uuid,
            p_document_id uuid,
            p_job_id uuid,
            p_node_id uuid,
            p_sha256 text,
            p_object_key text,
            p_filename text,
            p_display_name text,
            p_name_key text,
            p_mime_type text,
            p_byte_size bigint,
            p_parser_version text,
            p_chunking_version text,
            p_embedding_version text,
            p_parent_id uuid,
            p_selected_team_ids uuid[]
        )
        RETURNS TABLE (
            result_status text,
            document_id uuid,
            job_id uuid,
            node_id uuid,
            parent_id uuid,
            display_name text,
            logical_path text,
            duplicate_of uuid,
            location_reused boolean
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_existing_document uuid;
            v_existing_node uuid;
            v_existing_job uuid;
            v_existing_parent uuid;
            v_existing_name text;
            v_logical_path text;
            v_actor uuid;
            v_reservation public.upload_reservations%ROWTYPE;
        BEGIN
            v_actor := public.v4_current_actor_id();
            IF v_actor IS NULL THEN
                RAISE EXCEPTION 'authentication required'
                    USING ERRCODE = '28000';
            END IF;
            IF p_reservation_id IS NULL
               OR p_document_id IS NULL OR p_job_id IS NULL OR p_node_id IS NULL
               OR p_sha256 !~ '^[0-9a-f]{64}$'
               OR p_object_key <>
                  'originals/' || left(p_sha256, 2) || '/' ||
                  p_sha256 || '.pdf'
               OR p_mime_type <> 'application/pdf'
               OR p_byte_size <= 0
               OR p_filename <> btrim(p_filename)
               OR char_length(p_filename) NOT BETWEEN 1 AND 512
               OR char_length(p_display_name) NOT BETWEEN 1 AND 255
               OR char_length(p_name_key) NOT BETWEEN 1 AND 1024
               OR char_length(p_parser_version) NOT BETWEEN 1 AND 64
               OR char_length(p_chunking_version) NOT BETWEEN 1 AND 64
               OR char_length(p_embedding_version) NOT BETWEEN 1 AND 128 THEN
                RAISE EXCEPTION 'invalid canonical upload metadata'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-upload:' || p_sha256, 0)
            );
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-library-acl', 0)
            );
            SELECT * INTO v_reservation
            FROM public.upload_reservations
            WHERE id = p_reservation_id
            FOR UPDATE;
            IF NOT FOUND
               OR v_reservation.actor_user_id <> v_actor
               OR v_reservation.consumed_at IS NOT NULL
               OR v_reservation.expires_at <= statement_timestamp()
               OR v_reservation.sha256 <> p_sha256
               OR v_reservation.object_key <> p_object_key
               OR v_reservation.parent_id IS DISTINCT FROM p_parent_id
               OR v_reservation.selected_team_ids <> p_selected_team_ids
               OR v_reservation.metadata_digest <>
                  public.v4_upload_metadata_digest(
                      p_filename, p_display_name, p_name_key, p_mime_type,
                      p_byte_size, p_parser_version, p_chunking_version,
                      p_embedding_version
                  ) THEN
                RETURN QUERY SELECT 'invalid_reservation'::text,
                    NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                    NULL::text, NULL::text, NULL::uuid, false;
                RETURN;
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.object_deletions
                WHERE object_key = p_object_key
            ) THEN
                RETURN QUERY SELECT 'pending_deletion'::text,
                    NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                    NULL::text, NULL::text, NULL::uuid, false;
                RETURN;
            END IF;
            SELECT document.id, node.id, job.id, node.parent_id, node.name
            INTO v_existing_document, v_existing_node, v_existing_job,
                 v_existing_parent, v_existing_name
            FROM public.documents AS document
            JOIN public.library_nodes AS node
              ON node.document_id = document.id
            LEFT JOIN LATERAL (
                SELECT candidate.id
                FROM public.ingestion_jobs AS candidate
                WHERE candidate.document_id = document.id
                ORDER BY candidate.created_at DESC, candidate.id DESC
                LIMIT 1
            ) AS job ON true
            WHERE document.sha256 = p_sha256;
            IF v_existing_document IS NOT NULL THEN
                UPDATE public.upload_reservations
                SET consumed_at = statement_timestamp(),
                    outcome = 'duplicate',
                    updated_at = statement_timestamp()
                WHERE id = p_reservation_id;
                IF NOT public.v4_can_read_document(v_existing_document) THEN
                    RETURN QUERY SELECT 'duplicate_forbidden'::text,
                        NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                        NULL::text, NULL::text, NULL::uuid, false;
                    RETURN;
                END IF;
                WITH RECURSIVE ancestry AS (
                    SELECT node.id, node.parent_id, node.name, 0 AS depth
                    FROM public.library_nodes AS node
                    WHERE node.id = v_existing_node
                    UNION ALL
                    SELECT parent.id, parent.parent_id, parent.name,
                           child.depth + 1
                    FROM public.library_nodes AS parent
                    JOIN ancestry AS child ON parent.id = child.parent_id
                )
                SELECT '/' || string_agg(
                    ancestry.name, '/' ORDER BY ancestry.depth DESC
                )
                INTO v_logical_path FROM ancestry;
                RETURN QUERY SELECT 'duplicate'::text,
                    v_existing_document, v_existing_job, v_existing_node,
                    v_existing_parent, v_existing_name, v_logical_path,
                    v_existing_document, true;
                RETURN;
            END IF;
            IF p_selected_team_ids IS NULL
               OR cardinality(p_selected_team_ids) <>
                  cardinality(ARRAY(
                      SELECT DISTINCT team_id
                      FROM unnest(p_selected_team_ids) AS team_id
                  ))
               OR EXISTS (
                   SELECT 1
                   FROM unnest(p_selected_team_ids) AS selected(team_id)
                   LEFT JOIN public.team_members AS membership
                     ON membership.team_id = selected.team_id
                    AND membership.user_id = v_actor
                   LEFT JOIN public.teams AS team
                     ON team.id = selected.team_id
                    AND team.is_active
                   WHERE membership.user_id IS NULL OR team.id IS NULL
               )
               OR (
                   EXISTS (
                       SELECT 1
                       FROM public.team_members AS membership
                       JOIN public.teams AS team
                         ON team.id = membership.team_id
                        AND team.is_active
                       WHERE membership.user_id = v_actor
                   )
                   AND cardinality(p_selected_team_ids) = 0
               ) THEN
                RAISE EXCEPTION 'invalid upload team selection'
                    USING ERRCODE = '22023';
            END IF;
            IF NOT public.v4_current_actor_is_admin() AND p_parent_id IS NULL THEN
                RAISE EXCEPTION 'members must upload into a folder'
                    USING ERRCODE = '22023';
            END IF;
            IF p_parent_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM public.library_nodes
                WHERE id = p_parent_id
                  AND kind = 'folder'
                  AND public.v4_can_read_folder(id)
            ) THEN
                RETURN QUERY SELECT 'parent_not_found'::text,
                    NULL::uuid, NULL::uuid, NULL::uuid, NULL::uuid,
                    NULL::text, NULL::text, NULL::uuid, false;
                RETURN;
            END IF;
            INSERT INTO public.documents (
                id, sha256, original_filename, mime_type, byte_size,
                object_key, state, stage, error, parser_version,
                chunking_version, embedding_version, page_count, chunk_count
            ) VALUES (
                p_document_id, p_sha256, p_filename, p_mime_type,
                p_byte_size, p_object_key, 'uploaded', 'uploaded', NULL,
                p_parser_version, p_chunking_version, p_embedding_version,
                NULL, 0
            );
            INSERT INTO public.library_nodes (
                id, parent_id, kind, name, name_key, document_id,
                uploader_user_id
            ) VALUES (
                p_node_id, p_parent_id, 'file', p_display_name,
                p_name_key, p_document_id, v_actor
            );
            INSERT INTO public.access_grants (
                id, node_id, user_id, team_id
            ) VALUES (
                public.gen_random_uuid(), p_node_id, v_actor, NULL
            );
            INSERT INTO public.access_grants (
                id, node_id, user_id, team_id
            )
            SELECT public.gen_random_uuid(), p_node_id, NULL, selected.team_id
            FROM unnest(p_selected_team_ids) AS selected(team_id);
            INSERT INTO public.ingestion_jobs (
                id, document_id, status, stage, attempt,
                completed_units, total_units
            ) VALUES (
                p_job_id, p_document_id, 'queued', 'uploaded', 0, 0, NULL
            );
            PERFORM public.v4_rebuild_effective_document_access();
            WITH RECURSIVE ancestry AS (
                SELECT node.id, node.parent_id, node.name, 0 AS depth
                FROM public.library_nodes AS node
                WHERE node.id = p_node_id
                UNION ALL
                SELECT parent.id, parent.parent_id, parent.name,
                       child.depth + 1
                FROM public.library_nodes AS parent
                JOIN ancestry AS child ON parent.id = child.parent_id
            )
            SELECT '/' || string_agg(
                ancestry.name, '/' ORDER BY ancestry.depth DESC
            )
            INTO v_logical_path FROM ancestry;
            PERFORM public.v4_append_audit(
                'document_uploaded', 'document', p_document_id,
                jsonb_build_object('byte_size', p_byte_size)
            );
            UPDATE public.upload_reservations
            SET consumed_at = statement_timestamp(), outcome = 'created',
                updated_at = statement_timestamp()
            WHERE id = p_reservation_id;
            RETURN QUERY SELECT 'created'::text, p_document_id, p_job_id,
                p_node_id, p_parent_id, p_display_name, v_logical_path,
                NULL::uuid, false;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_queue_expired_upload_orphans(
            p_grace_seconds integer
        )
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_object_key text;
            v_sha256 text;
            v_count integer := 0;
        BEGIN
            IF p_grace_seconds NOT BETWEEN 60 AND 86400 THEN
                RAISE EXCEPTION 'invalid upload orphan grace period'
                    USING ERRCODE = '22023';
            END IF;
            UPDATE public.upload_reservations
            SET consumed_at = statement_timestamp(), outcome = 'expired',
                updated_at = statement_timestamp()
            WHERE consumed_at IS NULL
              AND expires_at <= statement_timestamp();
            FOR v_object_key, v_sha256 IN
                SELECT reservation.object_key, min(reservation.sha256)
                FROM public.upload_reservations AS reservation
                WHERE reservation.outcome = 'expired'
                  AND reservation.expires_at <= statement_timestamp()
                      - make_interval(secs => p_grace_seconds)
                GROUP BY reservation.object_key
            LOOP
                PERFORM pg_advisory_xact_lock(
                    hashtextextended('rag-v4-upload:' || v_sha256, 0)
                );
                IF NOT EXISTS (
                    SELECT 1 FROM public.documents
                    WHERE object_key = v_object_key
                ) AND NOT EXISTS (
                    SELECT 1 FROM public.upload_reservations
                    WHERE object_key = v_object_key
                      AND consumed_at IS NULL
                      AND expires_at > statement_timestamp()
                ) THEN
                    INSERT INTO public.object_deletions (id, object_key)
                    VALUES (public.gen_random_uuid(), v_object_key)
                    ON CONFLICT (object_key) DO NOTHING;
                    IF FOUND THEN
                        v_count := v_count + 1;
                    END IF;
                END IF;
            END LOOP;
            RETURN v_count;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_create_folder(
            p_node_id uuid,
            p_parent_id uuid,
            p_name text,
            p_name_key text
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-library-acl', 0)
            );
            IF p_node_id IS NULL
               OR p_name IS NULL
               OR p_name <> btrim(p_name)
               OR char_length(p_name) NOT BETWEEN 1 AND 255
               OR p_name_key IS NULL
               OR char_length(p_name_key) = 0
               OR octet_length(p_name_key) > 1024
               OR (
                   p_parent_id IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM public.library_nodes
                       WHERE id = p_parent_id AND kind = 'folder'
                   )
               ) THEN
                RAISE EXCEPTION 'invalid library folder'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.library_nodes (
                id, parent_id, kind, name, name_key, document_id
            ) VALUES (
                p_node_id, p_parent_id, 'folder', p_name, p_name_key, NULL
            );
            RETURN p_node_id;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_rename_library_node(
            p_node_id uuid,
            p_name text,
            p_name_key text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_actor uuid := public.v4_current_actor_id();
        BEGIN
            IF v_actor IS NULL THEN
                RAISE EXCEPTION 'authentication required'
                    USING ERRCODE = '28000';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-library-acl', 0)
            );
            IF p_name IS NULL
               OR p_name <> btrim(p_name)
               OR char_length(p_name) NOT BETWEEN 1 AND 255
               OR p_name_key IS NULL
               OR char_length(p_name_key) = 0
               OR octet_length(p_name_key) > 1024 THEN
                RAISE EXCEPTION 'invalid library node name'
                    USING ERRCODE = '22023';
            END IF;
            IF NOT public.v4_current_actor_is_admin()
               AND NOT EXISTS (
                   SELECT 1
                   FROM public.library_nodes AS node
                   WHERE node.id = p_node_id
                     AND node.kind = 'file'
                     AND node.uploader_user_id = v_actor
               ) THEN
                RETURN false;
            END IF;
            UPDATE public.library_nodes
            SET name = p_name, name_key = p_name_key,
                updated_at = statement_timestamp()
            WHERE id = p_node_id;
            RETURN FOUND;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_delete_folder(p_node_id uuid)
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_kind text;
        BEGIN
            PERFORM public.v4_require_admin();
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-library-acl', 0)
            );
            SELECT kind INTO v_kind
            FROM public.library_nodes
            WHERE id = p_node_id
            FOR UPDATE;
            IF v_kind IS NULL THEN
                RETURN 'not_found';
            ELSIF v_kind <> 'folder' THEN
                RETURN 'not_folder';
            ELSIF EXISTS (
                SELECT 1 FROM public.library_nodes
                WHERE parent_id = p_node_id
            ) THEN
                RETURN 'not_empty';
            END IF;
            DELETE FROM public.library_nodes WHERE id = p_node_id;
            RETURN 'deleted';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_list_chats()
        RETURNS TABLE (
            chat_id uuid,
            title text,
            scope_mode text,
            scope_version bigint,
            created_at timestamptz,
            updated_at timestamptz
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT chat.id, chat.title::text, chat.scope_mode::text,
                   chat.scope_version, chat.created_at, chat.updated_at
            FROM public.chats AS chat
            WHERE chat.owner_user_id = public.v4_current_actor_id()
            ORDER BY chat.updated_at DESC, chat.id DESC
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_create_chat(
            p_title text,
            p_scope_mode text
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_actor uuid := public.v4_current_actor_id();
            v_id uuid := public.gen_random_uuid();
        BEGIN
            IF v_actor IS NULL
               OR p_scope_mode NOT IN ('all_ready', 'selected')
               OR char_length(btrim(p_title)) NOT BETWEEN 1 AND 255 THEN
                RAISE EXCEPTION 'invalid chat creation'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.chats (
                id, title, title_is_manual, scope_mode, owner_user_id
            ) VALUES (
                v_id, btrim(p_title), false, p_scope_mode, v_actor
            );
            RETURN v_id;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_rename_chat(p_chat_id uuid, p_title text)
        RETURNS boolean
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            WITH changed AS (
                UPDATE public.chats
                SET title = btrim(p_title), title_is_manual = true,
                    updated_at = statement_timestamp()
                WHERE id = p_chat_id
                  AND owner_user_id = public.v4_current_actor_id()
                  AND char_length(btrim(p_title)) BETWEEN 1 AND 255
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM changed)
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_delete_chat(p_chat_id uuid)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM set_config(
                'rag.snapshot_maintenance', 'parent_delete', true
            );
            DELETE FROM public.chats
            WHERE id = p_chat_id
              AND owner_user_id = public.v4_current_actor_id();
            RETURN FOUND;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_replace_chat_scope(
            p_chat_id uuid,
            p_node_ids uuid[]
        )
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_version bigint;
        BEGIN
            PERFORM 1 FROM public.chats
            WHERE id = p_chat_id
              AND owner_user_id = public.v4_current_actor_id()
            FOR UPDATE;
            IF NOT FOUND OR p_node_ids IS NULL OR EXISTS (
                SELECT 1 FROM unnest(p_node_ids) AS node_id
                WHERE NOT public.v4_can_view_library_node(node_id)
            ) THEN
                RAISE EXCEPTION 'chat scope is inaccessible'
                    USING ERRCODE = '42501';
            END IF;
            DELETE FROM public.chat_scopes WHERE chat_id = p_chat_id;
            INSERT INTO public.chat_scopes (chat_id, node_id)
            SELECT p_chat_id, node_id
            FROM (SELECT DISTINCT unnest(p_node_ids) AS node_id) AS requested;
            UPDATE public.chats
            SET scope_mode = CASE WHEN cardinality(p_node_ids) = 0
                    THEN 'all_ready' ELSE 'selected' END,
                scope_version = scope_version + 1,
                updated_at = statement_timestamp()
            WHERE id = p_chat_id
            RETURNING scope_version INTO v_version;
            RETURN v_version;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_begin_turn(
            p_chat_id uuid,
            p_question text,
            p_generation_token uuid,
            p_auto_title text
        )
        RETURNS TABLE (turn_id uuid, ordinal bigint, scope_version bigint)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_chat public.chats%ROWTYPE;
            v_turn_id uuid := public.gen_random_uuid();
        BEGIN
            SELECT * INTO STRICT v_chat
            FROM public.chats
            WHERE id = p_chat_id
              AND owner_user_id = public.v4_current_actor_id()
            FOR UPDATE;
            IF char_length(btrim(p_question)) NOT BETWEEN 1 AND 2000
               OR p_generation_token IS NULL
               OR p_auto_title IS NULL
               OR char_length(btrim(p_auto_title)) NOT BETWEEN 1 AND 255 THEN
                RAISE EXCEPTION 'invalid turn'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.chat_turns (
                id, chat_id, ordinal, question, status, attempt,
                scope_version, generation_token
            ) VALUES (
                v_turn_id, p_chat_id, v_chat.next_turn_ordinal,
                btrim(p_question), 'generating', 1,
                v_chat.scope_version, p_generation_token
            );
            UPDATE public.chats
            SET title = CASE
                    WHEN v_chat.next_turn_ordinal = 1
                         AND NOT v_chat.title_is_manual
                    THEN btrim(p_auto_title)
                    ELSE title
                END,
                next_turn_ordinal = next_turn_ordinal + 1,
                updated_at = statement_timestamp()
            WHERE id = p_chat_id;
            RETURN QUERY SELECT v_turn_id, v_chat.next_turn_ordinal,
                v_chat.scope_version;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_store_turn_sources(
            p_turn_id uuid,
            p_generation_token uuid,
            p_sources jsonb
        )
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_count integer;
        BEGIN
            PERFORM authorization_version
            FROM public.security_epochs
            WHERE singleton
            FOR SHARE;
            PERFORM 1
            FROM public.chat_turns AS turn_row
            JOIN public.chats AS chat ON chat.id = turn_row.chat_id
            WHERE turn_row.id = p_turn_id
              AND chat.owner_user_id = public.v4_current_actor_id()
              AND turn_row.status = 'generating'
              AND turn_row.generation_token = p_generation_token
            FOR UPDATE OF turn_row;
            IF NOT FOUND OR jsonb_typeof(p_sources) <> 'array'
               OR jsonb_array_length(p_sources) > 8
               OR EXISTS (
                   SELECT 1
                   FROM jsonb_to_recordset(p_sources)
                       AS requested(rank smallint, chunk_id uuid)
                   WHERE requested.rank NOT BETWEEN 1 AND 8
                      OR requested.chunk_id IS NULL
               )
               OR (
                   SELECT count(*) <> count(DISTINCT requested.rank)
                       OR count(*) <> count(DISTINCT requested.chunk_id)
                   FROM jsonb_to_recordset(p_sources)
                       AS requested(rank smallint, chunk_id uuid)
               ) THEN
                RAISE EXCEPTION 'invalid turn sources'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.turn_sources (
                turn_id, rank, label, document_id, chunk_id,
                document_id_snapshot, chunk_id_snapshot,
                original_filename, display_name, logical_path,
                page_start, page_end, section, source_sha256,
                text_sha256, retrieval_distance, rerank_score,
                snapshot_text, token_count
            )
            SELECT p_turn_id, requested.rank,
                   'S' || requested.rank::text,
                   document.id, chunk.id, document.id, chunk.id,
                   document.original_filename, node.name,
                   location.logical_path, chunk.page_start, chunk.page_end,
                   chunk.section, chunk.source_sha256, chunk.text_sha256,
                   requested.retrieval_distance, requested.rerank_score,
                   chunk.text, chunk.token_count
            FROM jsonb_to_recordset(p_sources) AS requested(
                rank smallint, chunk_id uuid,
                retrieval_distance double precision,
                rerank_score double precision
            )
            JOIN public.chunks AS chunk
              ON chunk.id = requested.chunk_id
            JOIN public.documents AS document
              ON document.id = chunk.document_id
            JOIN public.library_nodes AS node
              ON node.document_id = document.id
            CROSS JOIN LATERAL (
                WITH RECURSIVE ancestry AS (
                    SELECT current.id, current.parent_id, current.name,
                           0 AS depth
                    FROM public.library_nodes AS current
                    WHERE current.id = node.id
                    UNION ALL
                    SELECT parent.id, parent.parent_id, parent.name,
                           child.depth + 1
                    FROM public.library_nodes AS parent
                    JOIN ancestry AS child
                      ON parent.id = child.parent_id
                )
                SELECT '/' || string_agg(
                    ancestry.name, '/' ORDER BY ancestry.depth DESC
                ) AS logical_path
                FROM ancestry
            ) AS location
            WHERE document.state = 'ready'
              AND public.v4_can_read_document(document.id);
            GET DIAGNOSTICS v_count = ROW_COUNT;
            IF v_count <> jsonb_array_length(p_sources) THEN
                RAISE EXCEPTION 'turn source access changed'
                    USING ERRCODE = '42501';
            END IF;
            RETURN v_count;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_fail_turn(
            p_turn_id uuid,
            p_generation_token uuid,
            p_error text
        )
        RETURNS boolean
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            WITH changed AS (
                UPDATE public.chat_turns AS turn_row
                SET status = 'failed', generation_token = NULL,
                    final_answer = NULL, partial_answer = NULL,
                    completed_at = NULL,
                    insufficient_context = false,
                    error = left(btrim(p_error), 500),
                    updated_at = statement_timestamp()
                FROM public.chats AS chat
                WHERE turn_row.id = p_turn_id
                  AND chat.id = turn_row.chat_id
                  AND chat.owner_user_id = public.v4_current_actor_id()
                  AND turn_row.status = 'generating'
                  AND turn_row.generation_token = p_generation_token
                  AND char_length(btrim(p_error)) BETWEEN 1 AND 500
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM changed)
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_retry_turn(
            p_chat_id uuid,
            p_turn_id uuid,
            p_generation_token uuid
        )
        RETURNS TABLE (
            result_status text,
            turn_id uuid,
            ordinal bigint,
            question text,
            attempt integer,
            scope_version bigint,
            partial_answer text
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_chat public.chats%ROWTYPE;
            v_turn public.chat_turns%ROWTYPE;
            v_latest_ordinal bigint;
        BEGIN
            SELECT * INTO v_chat
            FROM public.chats
            WHERE id = p_chat_id
              AND owner_user_id = public.v4_current_actor_id()
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN QUERY SELECT 'not_found'::text, NULL::uuid,
                    NULL::bigint, NULL::text, NULL::integer, NULL::bigint,
                    NULL::text;
                RETURN;
            END IF;
            SELECT * INTO v_turn
            FROM public.chat_turns
            WHERE id = p_turn_id
              AND chat_id = p_chat_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN QUERY SELECT 'not_found'::text, NULL::uuid,
                    NULL::bigint, NULL::text, NULL::integer, NULL::bigint,
                    NULL::text;
                RETURN;
            END IF;
            SELECT max(candidate.ordinal) INTO v_latest_ordinal
            FROM public.chat_turns AS candidate
            WHERE candidate.chat_id = p_chat_id;
            IF v_turn.ordinal <> v_latest_ordinal THEN
                RETURN QUERY SELECT 'not_latest'::text, v_turn.id,
                    v_turn.ordinal, v_turn.question::text, v_turn.attempt,
                    v_turn.scope_version, v_turn.partial_answer;
                RETURN;
            END IF;
            IF v_turn.status NOT IN (
                'failed', 'interrupted', 'length_limited', 'citation_failed'
            )
               OR p_generation_token IS NULL THEN
                RETURN QUERY SELECT 'not_retryable'::text, v_turn.id,
                    v_turn.ordinal, v_turn.question::text, v_turn.attempt,
                    v_turn.scope_version, v_turn.partial_answer;
                RETURN;
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.chat_turns AS active_turn
                WHERE active_turn.chat_id = p_chat_id
                  AND active_turn.status = 'generating'
            ) THEN
                RETURN QUERY SELECT 'not_retryable'::text, v_turn.id,
                    v_turn.ordinal, v_turn.question::text, v_turn.attempt,
                    v_turn.scope_version, v_turn.partial_answer;
                RETURN;
            END IF;
            PERFORM set_config(
                'rag.snapshot_maintenance', 'turn_retry', true
            );
            IF v_turn.status <> 'length_limited' THEN
                DELETE FROM public.turn_citations AS citation
                WHERE citation.turn_id = p_turn_id;
                DELETE FROM public.turn_sources AS source
                WHERE source.turn_id = p_turn_id;
            END IF;
            UPDATE public.chat_turns AS turn_row
            SET status = 'generating', generation_token = p_generation_token,
                attempt = turn_row.attempt + 1, final_answer = NULL,
                partial_answer = CASE
                    WHEN v_turn.status = 'length_limited'
                    THEN v_turn.partial_answer
                    ELSE NULL
                END,
                scope_version = CASE
                    WHEN v_turn.status = 'length_limited'
                    THEN v_turn.scope_version
                    ELSE v_chat.scope_version
                END,
                insufficient_context = false, error = NULL,
                completed_at = NULL, updated_at = statement_timestamp()
            WHERE id = p_turn_id;
            RETURN QUERY SELECT 'retried'::text, v_turn.id,
                v_turn.ordinal, v_turn.question::text, v_turn.attempt + 1,
                CASE
                    WHEN v_turn.status = 'length_limited'
                    THEN v_turn.scope_version
                    ELSE v_chat.scope_version
                END,
                CASE
                    WHEN v_turn.status = 'length_limited'
                    THEN v_turn.partial_answer
                    ELSE NULL
                END;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_interrupt_turn(
            p_chat_id uuid,
            p_turn_id uuid,
            p_generation_token uuid
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_turn public.chat_turns%ROWTYPE;
        BEGIN
            PERFORM 1
            FROM public.chats
            WHERE id = p_chat_id
              AND owner_user_id = public.v4_current_actor_id()
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN 'not_found';
            END IF;
            SELECT * INTO v_turn
            FROM public.chat_turns
            WHERE id = p_turn_id
              AND chat_id = p_chat_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN 'not_found';
            END IF;
            IF v_turn.status = 'interrupted' THEN
                RETURN 'already_interrupted';
            END IF;
            IF v_turn.status <> 'generating'
               OR p_generation_token IS NULL
               OR v_turn.generation_token <> p_generation_token THEN
                RETURN 'stale';
            END IF;
            UPDATE public.chat_turns
            SET status = 'interrupted',
                generation_token = NULL,
                final_answer = NULL,
                partial_answer = NULL,
                insufficient_context = false,
                error = 'generation cancelled',
                completed_at = NULL,
                updated_at = statement_timestamp()
            WHERE id = p_turn_id;
            RETURN 'interrupted';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_mark_turn_length_limited(
            p_chat_id uuid,
            p_turn_id uuid,
            p_generation_token uuid,
            p_partial_answer text
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_turn public.chat_turns%ROWTYPE;
        BEGIN
            IF p_partial_answer IS NULL
               OR char_length(btrim(p_partial_answer)) = 0 THEN
                RAISE EXCEPTION 'partial answer is required'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM 1
            FROM public.chats
            WHERE id = p_chat_id
              AND owner_user_id = public.v4_current_actor_id()
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN 'not_found';
            END IF;
            SELECT * INTO v_turn
            FROM public.chat_turns
            WHERE id = p_turn_id
              AND chat_id = p_chat_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN 'not_found';
            END IF;
            IF v_turn.status = 'length_limited' THEN
                RETURN 'already_limited';
            END IF;
            IF v_turn.status <> 'generating'
               OR p_generation_token IS NULL
               OR v_turn.generation_token <> p_generation_token THEN
                RETURN 'stale';
            END IF;
            UPDATE public.chat_turns
            SET status = 'length_limited',
                generation_token = NULL,
                final_answer = NULL,
                partial_answer = btrim(p_partial_answer),
                insufficient_context = false,
                error = 'response reached generation limit',
                completed_at = NULL,
                updated_at = statement_timestamp()
            WHERE id = p_turn_id;
            RETURN 'length_limited';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_mark_turn_citation_failed(
            p_chat_id uuid,
            p_turn_id uuid,
            p_generation_token uuid,
            p_partial_answer text
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_turn public.chat_turns%ROWTYPE;
        BEGIN
            IF p_partial_answer IS NULL
               OR char_length(btrim(p_partial_answer)) = 0 THEN
                RAISE EXCEPTION 'partial answer is required'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM 1
            FROM public.chats
            WHERE id = p_chat_id
              AND owner_user_id = public.v4_current_actor_id()
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN 'not_found';
            END IF;
            SELECT * INTO v_turn
            FROM public.chat_turns
            WHERE id = p_turn_id
              AND chat_id = p_chat_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN 'not_found';
            END IF;
            IF v_turn.status = 'citation_failed' THEN
                RETURN 'already_failed';
            END IF;
            IF v_turn.status <> 'generating'
               OR p_generation_token IS NULL
               OR v_turn.generation_token <> p_generation_token THEN
                RETURN 'stale';
            END IF;
            UPDATE public.chat_turns
            SET status = 'citation_failed',
                generation_token = NULL,
                final_answer = NULL,
                partial_answer = btrim(p_partial_answer),
                insufficient_context = false,
                error = 'citation validation failed',
                completed_at = NULL,
                updated_at = statement_timestamp()
            WHERE id = p_turn_id;
            RETURN 'citation_failed';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_admin_delete_document(
            p_document_id uuid,
            p_deletion_id uuid
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_object_key text;
            v_sha256 text;
            v_actor uuid := public.v4_current_actor_id();
        BEGIN
            IF v_actor IS NULL THEN
                RAISE EXCEPTION 'authentication required'
                    USING ERRCODE = '28000';
            END IF;
            SELECT document.object_key, document.sha256
            INTO STRICT v_object_key, v_sha256
            FROM public.documents AS document
            JOIN public.library_nodes AS node
              ON node.document_id = document.id
            WHERE document.id = p_document_id
              AND (
                  public.v4_current_actor_is_admin()
                  OR (
                      node.kind = 'file'
                      AND node.uploader_user_id = v_actor
                  )
              )
            FOR UPDATE OF document, node;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-upload:' || v_sha256, 0)
            );
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-library-acl', 0)
            );
            UPDATE public.turn_sources AS source
            SET owner_authorized_at_deletion = (
                SELECT (
                    account.role = 'admin'
                    AND account.status = 'active'
                    AND account.deleted_at IS NULL
                )
                    OR EXISTS (
                        SELECT 1
                        FROM public.effective_document_access AS access
                        JOIN public.security_epochs AS epoch
                          ON epoch.singleton
                        WHERE access.user_id = chat.owner_user_id
                          AND access.document_id = p_document_id
                          AND access.authorization_version =
                              epoch.authorization_version
                    )
                FROM public.chat_turns AS turn_row
                JOIN public.chats AS chat ON chat.id = turn_row.chat_id
                JOIN public.users AS account ON account.id = chat.owner_user_id
                WHERE turn_row.id = source.turn_id
            )
            WHERE source.document_id = p_document_id
              AND source.owner_authorized_at_deletion IS NULL;
            INSERT INTO public.object_deletions (id, object_key)
            VALUES (p_deletion_id, v_object_key);
            DELETE FROM public.documents WHERE id = p_document_id;
            PERFORM public.v4_rebuild_effective_document_access();
            PERFORM public.v4_append_audit(
                'document_deleted', 'document', p_document_id, '{}'::jsonb
            );
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_mark_turn_access_revoked(
            p_turn_id uuid,
            p_generation_token uuid
        )
        RETURNS boolean
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            WITH changed AS (
                UPDATE public.chat_turns AS turn_row
                SET status = 'access_revoked', generation_token = NULL,
                    final_answer = NULL, partial_answer = NULL,
                    completed_at = NULL,
                    insufficient_context = false,
                    error = 'access_revoked',
                    updated_at = statement_timestamp()
                FROM public.chats AS chat
                WHERE turn_row.id = p_turn_id
                  AND chat.id = turn_row.chat_id
                  AND chat.owner_user_id = public.v4_current_actor_id()
                  AND turn_row.status = 'generating'
                  AND turn_row.generation_token = p_generation_token
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM changed)
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_mark_turn_access_revoked_trusted(
            p_turn_id uuid,
            p_generation_token uuid
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_turn public.chat_turns%ROWTYPE;
        BEGIN
            SELECT * INTO v_turn
            FROM public.chat_turns
            WHERE id = p_turn_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN 'not_found';
            END IF;
            IF v_turn.status <> 'generating' THEN
                RETURN 'already_terminal';
            END IF;
            IF p_generation_token IS NULL
               OR v_turn.generation_token <> p_generation_token THEN
                RETURN 'stale';
            END IF;
            UPDATE public.chat_turns
            SET status = 'access_revoked',
                generation_token = NULL,
                final_answer = NULL,
                partial_answer = NULL,
                completed_at = NULL,
                insufficient_context = false,
                error = 'access_revoked',
                updated_at = statement_timestamp()
            WHERE id = p_turn_id;
            RETURN 'updated';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_finalize_turn(
            p_turn_id uuid,
            p_generation_token uuid,
            p_final_answer text,
            p_insufficient_context boolean,
            p_source_ranks smallint[]
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $v4_finalize_turn$
        DECLARE
            v_revoked boolean;
        BEGIN
            IF p_final_answer IS NULL
               OR char_length(btrim(p_final_answer)) = 0
               OR p_source_ranks IS NULL
               OR cardinality(p_source_ranks) > 8
               OR EXISTS (
                   SELECT 1
                   FROM unnest(p_source_ranks) AS rank
                   WHERE rank NOT BETWEEN 1 AND 8
               )
               OR cardinality(p_source_ranks) <>
                  cardinality(ARRAY(
                      SELECT DISTINCT rank
                      FROM unnest(p_source_ranks) AS rank
                  )) THEN
                RAISE EXCEPTION 'invalid turn finalization'
                    USING ERRCODE = '22023';
            END IF;
            PERFORM authorization_version
            FROM public.security_epochs
            WHERE singleton
            FOR SHARE;
            PERFORM 1
            FROM public.chat_turns AS turn_row
            JOIN public.chats AS chat ON chat.id = turn_row.chat_id
            WHERE turn_row.id = p_turn_id
              AND chat.owner_user_id = public.v4_current_actor_id()
              AND turn_row.status = 'generating'
              AND turn_row.generation_token = p_generation_token
            FOR UPDATE OF turn_row;
            IF NOT FOUND THEN
                RETURN false;
            END IF;
            SELECT EXISTS (
                SELECT 1 FROM public.turn_sources AS source
                WHERE source.turn_id = p_turn_id
                  AND NOT (
                      (
                          source.document_id IS NULL
                          AND source.owner_authorized_at_deletion = true
                      )
                      OR (
                          source.document_id IS NOT NULL
                          AND public.v4_can_read_document(source.document_id)
                      )
                  )
            ) INTO v_revoked;
            IF v_revoked THEN
                UPDATE public.chat_turns
                SET status = 'access_revoked', generation_token = NULL,
                    final_answer = NULL, partial_answer = NULL,
                    completed_at = NULL,
                    insufficient_context = false,
                    error = 'access_revoked',
                    updated_at = statement_timestamp()
                WHERE id = p_turn_id;
                RETURN false;
            END IF;
            INSERT INTO public.turn_citations (
                turn_id, ordinal, source_rank
            )
            SELECT p_turn_id, ordinal::smallint, source_rank
            FROM unnest(p_source_ranks) WITH ORDINALITY
                AS cited(source_rank, ordinal);
            UPDATE public.chat_turns
            SET status = 'complete', generation_token = NULL,
                final_answer = p_final_answer,
                partial_answer = NULL,
                insufficient_context = p_insufficient_context,
                error = NULL, completed_at = statement_timestamp(),
                updated_at = statement_timestamp()
            WHERE id = p_turn_id;
            RETURN true;
        END;
        $v4_finalize_turn$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_repair_interrupted_turns()
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_count bigint;
        BEGIN
            UPDATE public.chat_turns
            SET status = 'interrupted',
                generation_token = NULL,
                final_answer = NULL,
                partial_answer = NULL,
                insufficient_context = false,
                error = 'generation interrupted by stopped service',
                completed_at = NULL,
                updated_at = statement_timestamp()
            WHERE status = 'generating';
            GET DIAGNOSTICS v_count = ROW_COUNT;
            RETURN v_count;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_begin_backup_run(p_destination_id text)
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_run_id uuid := public.gen_random_uuid();
        BEGIN
            IF p_destination_id IS NULL
               OR char_length(btrim(p_destination_id)) = 0 THEN
                RAISE EXCEPTION 'backup destination is required'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.backup_runs (
                id, status, destination_id
            ) VALUES (v_run_id, 'running', p_destination_id);
            RETURN v_run_id;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION v4_finish_backup_run(
            p_run_id uuid,
            p_succeeded boolean,
            p_database_sha256 text,
            p_storage_manifest_sha256 text,
            p_database_bytes bigint,
            p_storage_bytes bigint,
            p_error_code text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            IF p_succeeded IS NULL THEN
                RAISE EXCEPTION 'backup result is required'
                    USING ERRCODE = '22023';
            END IF;
            IF p_succeeded AND (
                p_database_sha256 IS NULL
                OR p_database_sha256 !~ '^[0-9a-f]{64}$'
                OR p_storage_manifest_sha256 IS NULL
                OR p_storage_manifest_sha256 !~ '^[0-9a-f]{64}$'
                OR p_database_bytes IS NULL OR p_database_bytes < 0
                OR p_storage_bytes IS NULL OR p_storage_bytes < 0
                OR p_error_code IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'invalid successful backup result'
                    USING ERRCODE = '22023';
            ELSIF NOT p_succeeded AND (
                p_error_code IS NULL
                OR char_length(btrim(p_error_code)) = 0
            ) THEN
                RAISE EXCEPTION 'failed backup requires an error code'
                    USING ERRCODE = '22023';
            END IF;
            UPDATE public.backup_runs
            SET status = CASE WHEN p_succeeded THEN 'succeeded' ELSE 'failed' END,
                database_sha256 = CASE
                    WHEN p_succeeded THEN p_database_sha256 ELSE NULL END,
                storage_manifest_sha256 = CASE
                    WHEN p_succeeded THEN p_storage_manifest_sha256 ELSE NULL END,
                database_bytes = CASE
                    WHEN p_succeeded THEN p_database_bytes ELSE NULL END,
                storage_bytes = CASE
                    WHEN p_succeeded THEN p_storage_bytes ELSE NULL END,
                error_code = CASE
                    WHEN p_succeeded THEN NULL ELSE p_error_code END,
                finished_at = statement_timestamp()
            WHERE id = p_run_id AND status = 'running';
            RETURN FOUND;
        END;
        $$
        """
    )
    signature_by_name = {
        signature.removeprefix("public.").split("(", 1)[0]: signature
        for signature in _EXPECTED_V4_REGPROCEDURES
    }
    if set(signature_by_name) != set(_EXPECTED_V4_FUNCTIONS):
        raise RuntimeError("V4 readiness function signatures are incomplete")
    expected_functions_sql = ", ".join(
        f"'{signature}'" for signature in _EXPECTED_V4_REGPROCEDURES
    )
    expected_function_grants_sql = ", ".join(
        f"('{role_name}', '{signature_by_name[function_name]}')"
        for role_name, function_names in _EXPECTED_FUNCTION_GRANTS.items()
        for function_name in function_names
    )
    expected_policies_sql = ", ".join(
        "('{}', '{}')".format(
            table_name,
            "".join(
                character
                for character in predicate.lower()
                .replace("public.", "")
                .replace(" as ", " ")
                if not character.isspace() and character not in "()"
            ),
        )
        for table_name, predicate in _EXPECTED_POLICIES.items()
    )
    op.execute(
        f"""
        CREATE FUNCTION v4_readiness()
        RETURNS TABLE (
            schema_revision text,
            vector_extension boolean,
            bootstrap_required boolean,
            catalog_integrity boolean
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            WITH expected_tables(name) AS (
                SELECT unnest(ARRAY[
                    'backup_runs', 'documents', 'object_deletions',
                    'upload_reservations', 'security_epochs',
                    'service_leases', 'teams', 'users', 'chats',
                    'login_throttles', 'acl_previews', 'audit_events',
                    'ingestion_jobs', 'library_nodes',
                    'pre_auth_challenges', 'sessions', 'access_grants',
                    'chat_scopes', 'chunks', 'effective_document_access',
                    'team_members', 'chat_turns', 'turn_sources',
                    'turn_citations'
                ]::text[])
            ),
            expected_views(name) AS (
                SELECT unnest(ARRAY[
                    'v4_current_user', 'v4_chat_history',
                    'v4_visible_library_nodes', 'v4_admin_users',
                    'v4_admin_teams', 'v4_admin_grants',
                    'v4_admin_audit', 'v4_authorized_turn_sources',
                    'v4_authorized_turn_citations'
                ]::text[])
            ),
            expected_api_select(name) AS (
                SELECT unnest(ARRAY[
                    'documents', 'chunks', 'library_nodes', 'chats',
                    'chat_scopes', 'v4_current_user',
                    'v4_visible_library_nodes', 'v4_chat_history',
                    'v4_authorized_turn_sources',
                    'v4_authorized_turn_citations', 'v4_admin_users',
                    'v4_admin_teams', 'v4_admin_grants', 'v4_admin_audit'
                ]::text[])
            ),
            expected_functions(signature) AS (
                SELECT unnest(ARRAY[
                    {expected_functions_sql}
                ]::text[])
            ),
            expected_function_grants(role_name, function_signature) AS (
                VALUES {expected_function_grants_sql}
            ),
            expected_policies(table_name, normalized_qual) AS (
                VALUES {expected_policies_sql}
            )
            SELECT '0001_v4_baseline'::text,
                   EXISTS (
                       SELECT 1 FROM pg_catalog.pg_extension
                       WHERE extname = 'vector'
                         AND extnamespace = 'public'::regnamespace
                   ),
                   NOT EXISTS (SELECT 1 FROM public.users),
                   (
                       (SELECT count(*) = 1
                        FROM public.security_epochs WHERE singleton)
                       AND (
                           SELECT count(*) = 1
                              AND min(version_num) = '0001_v4_baseline'
                           FROM public.alembic_version
                       )
                       AND EXISTS (
                           SELECT 1
                           FROM pg_catalog.pg_class AS revision_table
                           JOIN pg_catalog.pg_namespace AS namespace
                             ON namespace.oid =
                                revision_table.relnamespace
                           JOIN pg_catalog.pg_roles AS owner
                             ON owner.oid = revision_table.relowner
                           WHERE namespace.nspname = 'public'
                             AND revision_table.relname =
                                 'alembic_version'
                             AND owner.rolname = 'rag_owner'
                       )
                       AND (
                           SELECT namespace_owner.rolname =
                                  'pg_database_owner'
                           FROM pg_catalog.pg_namespace AS namespace
                           JOIN pg_catalog.pg_roles AS namespace_owner
                             ON namespace_owner.oid = namespace.nspowner
                           WHERE namespace.nspname = 'public'
                       )
                       AND NOT EXISTS (
                           (
                               SELECT
                                   CASE
                                       WHEN acl.grantee = 0 THEN 'PUBLIC'
                                       ELSE COALESCE(
                                           grantee.rolname,
                                           acl.grantee::text
                                       )
                                   END,
                                   acl.privilege_type,
                                   acl.is_grantable
                               FROM pg_catalog.pg_namespace AS namespace
                               CROSS JOIN LATERAL pg_catalog.aclexplode(
                                   COALESCE(
                                       namespace.nspacl,
                                       pg_catalog.acldefault(
                                           'n', namespace.nspowner
                                       )
                                   )
                               ) AS acl
                               LEFT JOIN pg_catalog.pg_roles AS grantee
                                 ON grantee.oid = acl.grantee
                               WHERE namespace.nspname = 'public'
                                 AND acl.grantee <>
                                     'pg_database_owner'::regrole
                               EXCEPT
                               SELECT * FROM (
                                   VALUES
                                       ('PUBLIC', 'USAGE', false),
                                       ('rag_owner', 'CREATE', false),
                                       ('rag_owner', 'USAGE', false),
                                       ('rag_migrator', 'USAGE', false),
                                       ('rag_api', 'USAGE', false),
                                       ('rag_worker', 'USAGE', false),
                                       ('rag_maintenance', 'USAGE', false),
                                       ('rag_backup', 'USAGE', false)
                               ) AS expected_schema_grants(
                                   grantee,
                                   privilege_type,
                                   is_grantable
                               )
                           )
                           UNION ALL
                           (
                               SELECT * FROM (
                                   VALUES
                                       ('PUBLIC', 'USAGE', false),
                                       ('rag_owner', 'CREATE', false),
                                       ('rag_owner', 'USAGE', false),
                                       ('rag_migrator', 'USAGE', false),
                                       ('rag_api', 'USAGE', false),
                                       ('rag_worker', 'USAGE', false),
                                       ('rag_maintenance', 'USAGE', false),
                                       ('rag_backup', 'USAGE', false)
                               ) AS expected_schema_grants(
                                   grantee,
                                   privilege_type,
                                   is_grantable
                               )
                               EXCEPT
                               SELECT
                                   CASE
                                       WHEN acl.grantee = 0 THEN 'PUBLIC'
                                       ELSE COALESCE(
                                           grantee.rolname,
                                           acl.grantee::text
                                       )
                                   END,
                                   acl.privilege_type,
                                   acl.is_grantable
                               FROM pg_catalog.pg_namespace AS namespace
                               CROSS JOIN LATERAL pg_catalog.aclexplode(
                                   COALESCE(
                                       namespace.nspacl,
                                       pg_catalog.acldefault(
                                           'n', namespace.nspowner
                                       )
                                   )
                               ) AS acl
                               LEFT JOIN pg_catalog.pg_roles AS grantee
                                 ON grantee.oid = acl.grantee
                               WHERE namespace.nspname = 'public'
                                 AND acl.grantee <>
                                     'pg_database_owner'::regrole
                           )
                       )
                       AND NOT EXISTS (
                           SELECT 1
                           FROM pg_catalog.pg_class AS sequence
                           JOIN pg_catalog.pg_namespace AS namespace
                             ON namespace.oid = sequence.relnamespace
                           CROSS JOIN LATERAL pg_catalog.aclexplode(
                               COALESCE(
                                   sequence.relacl,
                                   pg_catalog.acldefault(
                                       'S', sequence.relowner
                                   )
                               )
                           ) AS acl
                           WHERE namespace.nspname = 'public'
                             AND sequence.relkind = 'S'
                             AND acl.grantee <> sequence.relowner
                       )
                       AND (
                           SELECT count(*) = 1
                              AND bool_and(
                                  trigger.tgname =
                                      'trg_v4_turn_source_immutability'
                                  AND trigger.tgenabled = 'O'
                                  AND trigger.tgtype = 27
                                  AND trigger.tgfoid =
                                      'public.v4_enforce_turn_source_immutability()'
                                      ::regprocedure
                              )
                           FROM pg_catalog.pg_trigger AS trigger
                           WHERE trigger.tgrelid =
                                     'public.turn_sources'::regclass
                             AND NOT trigger.tgisinternal
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM expected_tables AS expected
                           LEFT JOIN pg_catalog.pg_class AS relation
                             ON relation.relname = expected.name
                           LEFT JOIN pg_catalog.pg_namespace AS namespace
                             ON namespace.oid = relation.relnamespace
                           LEFT JOIN pg_catalog.pg_roles AS owner
                             ON owner.oid = relation.relowner
                           WHERE relation.oid IS NULL
                              OR namespace.nspname <> 'public'
                              OR relation.relkind <> 'r'
                              OR owner.rolname <> 'rag_owner'
                              OR (
                                 NOT relation.relrowsecurity
                                 OR NOT relation.relforcerowsecurity
                             )
                       )
                       AND NOT EXISTS (
                           SELECT 1
                           FROM pg_catalog.pg_class AS relation
                           JOIN pg_catalog.pg_namespace AS namespace
                             ON namespace.oid = relation.relnamespace
                           WHERE namespace.nspname = 'public'
                             AND relation.relkind = 'r'
                             AND relation.relname <> 'alembic_version'
                             AND NOT EXISTS (
                                 SELECT 1 FROM expected_tables AS expected
                                 WHERE expected.name = relation.relname
                             )
                       )
                       AND NOT EXISTS (
                           SELECT 1
                           FROM expected_function_grants AS expected
                           JOIN pg_catalog.pg_proc AS routine
                             ON routine.oid = pg_catalog.to_regprocedure(
                                 expected.function_signature
                             )
                           JOIN pg_catalog.pg_namespace AS namespace
                             ON namespace.oid = routine.pronamespace
                           WHERE namespace.nspname = 'public'
                             AND NOT has_function_privilege(
                                 expected.role_name,
                                 routine.oid,
                                 'EXECUTE'
                             )
                       )
                       AND NOT EXISTS (
                           SELECT 1
                           FROM unnest(ARRAY[
                               'rag_api', 'rag_worker', 'rag_maintenance',
                               'rag_migrator', 'rag_backup'
                           ]) AS runtime(role_name)
                           CROSS JOIN expected_functions AS function_row
                           JOIN pg_catalog.pg_proc AS routine
                             ON routine.oid = pg_catalog.to_regprocedure(
                                 function_row.signature
                             )
                           JOIN pg_catalog.pg_namespace AS namespace
                             ON namespace.oid = routine.pronamespace
                           WHERE namespace.nspname = 'public'
                             AND has_function_privilege(
                                 role_name, routine.oid, 'EXECUTE'
                             )
                             AND NOT EXISTS (
                                 SELECT 1
                                 FROM expected_function_grants AS expected
                                 WHERE expected.role_name = role_name
                                   AND expected.function_signature =
                                       function_row.signature
                             )
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM expected_policies AS expected
                           LEFT JOIN pg_catalog.pg_class AS relation
                             ON relation.relname = expected.table_name
                           LEFT JOIN pg_catalog.pg_namespace AS namespace
                             ON namespace.oid = relation.relnamespace
                           LEFT JOIN pg_catalog.pg_policy AS policy
                             ON policy.polrelid = relation.oid
                           WHERE namespace.nspname <> 'public'
                              OR policy.oid IS NULL
                              OR policy.polname <>
                                 expected.table_name || '_actor_select'
                              OR policy.polcmd <> 'r'
                              OR policy.polroles <> ARRAY[0]::oid[]
                              OR policy.polpermissive IS DISTINCT FROM true
                              OR policy.polwithcheck IS NOT NULL
                              OR regexp_replace(
                                  replace(
                                      replace(
                                          regexp_replace(
                                              lower(pg_catalog.pg_get_expr(
                                                  policy.polqual,
                                                  policy.polrelid
                                              )),
                                              '\\mas\\M',
                                              '',
                                              'g'
                                          ),
                                          'public.',
                                          ''
                                      ),
                                      expected.table_name || '.',
                                      ''
                                  ),
                                  '[[:space:]()]',
                                  '',
                                  'g'
                              ) IS DISTINCT FROM expected.normalized_qual
                              OR (
                                  SELECT count(*)
                                  FROM pg_catalog.pg_policy AS sibling
                                  WHERE sibling.polrelid = relation.oid
                              ) <> 1
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM expected_views AS expected
                           LEFT JOIN pg_catalog.pg_class AS relation
                             ON relation.relname = expected.name
                           LEFT JOIN pg_catalog.pg_namespace AS namespace
                             ON namespace.oid = relation.relnamespace
                           LEFT JOIN pg_catalog.pg_roles AS owner
                             ON owner.oid = relation.relowner
                           WHERE relation.oid IS NULL
                              OR namespace.nspname <> 'public'
                              OR relation.relkind <> 'v'
                              OR owner.rolname <> 'rag_owner'
                              OR NOT (
                                  COALESCE(relation.reloptions, ARRAY[]::text[])
                                  @> ARRAY['security_barrier=true']
                              )
                       )
                       AND NOT EXISTS (
                           SELECT 1
                           FROM pg_catalog.pg_class AS relation
                           JOIN pg_catalog.pg_namespace AS namespace
                             ON namespace.oid = relation.relnamespace
                           WHERE namespace.nspname = 'public'
                             AND relation.relkind = 'v'
                             AND relation.relname LIKE 'v3\\_%' ESCAPE '\\'
                             AND NOT EXISTS (
                                 SELECT 1 FROM expected_views AS expected
                                 WHERE expected.name = relation.relname
                             )
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM expected_functions AS expected
                           LEFT JOIN pg_catalog.pg_proc AS routine
                             ON routine.oid = pg_catalog.to_regprocedure(
                                 expected.signature
                             )
                           LEFT JOIN pg_catalog.pg_namespace AS namespace
                             ON namespace.oid = routine.pronamespace
                           LEFT JOIN pg_catalog.pg_roles AS owner
                             ON owner.oid = routine.proowner
                           WHERE routine.oid IS NULL
                              OR namespace.nspname <> 'public'
                              OR owner.rolname <> 'rag_owner'
                              OR (
                                 owner.rolname <> 'rag_owner'
                                 OR routine.proconfig IS DISTINCT FROM
                                    ARRAY['search_path=pg_catalog']
                                 OR routine.prosecdef IS DISTINCT FROM
                                    (
                                        routine.proname NOT IN (
                                            'v4_runtime_identity',
                                            'v4_enforce_turn_source_immutability'
                                        )
                                    )
                                 OR has_function_privilege(
                                     'public', routine.oid, 'EXECUTE'
                                 )
                             )
                       )
                       AND NOT EXISTS (
                           SELECT 1
                           FROM pg_catalog.pg_proc AS routine
                           JOIN pg_catalog.pg_namespace AS namespace
                             ON namespace.oid = routine.pronamespace
                           WHERE namespace.nspname = 'public'
                             AND routine.proname LIKE 'v3\\_%' ESCAPE '\\'
                             AND NOT EXISTS (
                                 SELECT 1
                                 FROM expected_functions AS expected
                                 WHERE pg_catalog.to_regprocedure(
                                     expected.signature
                                 ) = routine.oid
                             )
                       )
                       AND NOT EXISTS (
                           (
                               SELECT grant_row.table_name
                               FROM information_schema.role_table_grants
                                   AS grant_row
                               WHERE grant_row.table_schema = 'public'
                                 AND grant_row.grantee = 'rag_api'
                                 AND grant_row.privilege_type = 'SELECT'
                               EXCEPT
                               SELECT name FROM expected_api_select
                           )
                           UNION ALL
                           (
                               SELECT name FROM expected_api_select
                               EXCEPT
                               SELECT grant_row.table_name
                               FROM information_schema.role_table_grants
                                   AS grant_row
                               WHERE grant_row.table_schema = 'public'
                                 AND grant_row.grantee = 'rag_api'
                                 AND grant_row.privilege_type = 'SELECT'
                           )
                       )
                       AND NOT EXISTS (
                           SELECT 1
                           FROM information_schema.role_table_grants
                               AS grant_row
                           WHERE grant_row.table_schema = 'public'
                             AND grant_row.grantee IN (
                                 'rag_api', 'rag_worker', 'rag_maintenance'
                             )
                             AND (
                                 grant_row.privilege_type <> 'SELECT'
                                 OR grant_row.grantee <> 'rag_api'
                             )
                       )
                   )
        $$
        """
    )


def _create_views_and_rls() -> None:
    op.execute(
        """
        CREATE VIEW v4_current_user
        WITH (security_barrier = true)
        AS
        SELECT id, username, display_name, role, status,
               authentication_version, created_at, updated_at
        FROM users
        WHERE id = v4_current_actor_id()
        """
    )
    op.execute(
        """
        CREATE VIEW v4_chat_history
        WITH (security_barrier = true)
        AS
        SELECT turn_row.id, turn_row.chat_id, turn_row.ordinal,
               turn_row.question, turn_row.status,
               turn_row.attempt, turn_row.scope_version,
               turn_row.generation_token,
               CASE WHEN redaction.redacted THEN NULL
                    ELSE turn_row.final_answer END AS final_answer,
               CASE WHEN redaction.redacted THEN NULL
                    ELSE turn_row.partial_answer END AS partial_answer,
               turn_row.insufficient_context,
               turn_row.error,
               redaction.redacted,
               turn_row.created_at, turn_row.updated_at,
               turn_row.completed_at
        FROM chat_turns AS turn_row
        JOIN chats AS chat ON chat.id = turn_row.chat_id
        CROSS JOIN LATERAL (
            SELECT EXISTS (
                SELECT 1 FROM turn_sources AS source
                WHERE source.turn_id = turn_row.id
                  AND NOT (
                      (
                          source.document_id IS NULL
                          AND source.owner_authorized_at_deletion = true
                      )
                      OR (
                          source.document_id IS NOT NULL
                          AND v4_can_read_document(source.document_id)
                      )
                  )
            ) AS redacted
        ) AS redaction
        WHERE chat.owner_user_id = v4_current_actor_id()
        """
    )
    op.execute(
        """
        CREATE VIEW v4_visible_library_nodes
        WITH (security_barrier = true)
        AS
        SELECT node.id, node.parent_id, node.kind, node.name,
               node.document_id, node.access_boundary,
               (
                   WITH RECURSIVE subtree AS (
                       SELECT root.id, root.document_id
                       FROM library_nodes AS root
                       WHERE root.id = node.id
                       UNION ALL
                       SELECT child.id, child.document_id
                       FROM library_nodes AS child
                       JOIN subtree AS parent
                         ON child.parent_id = parent.id
                   )
                   SELECT count(*)
                   FROM subtree AS descendant
                   WHERE descendant.document_id IS NOT NULL
                     AND (
                         v4_current_actor_is_admin()
                         OR EXISTS (
                             SELECT 1
                             FROM effective_document_access AS access
                             JOIN security_epochs AS epoch ON epoch.singleton
                             WHERE access.document_id = descendant.document_id
                               AND access.user_id = v4_current_actor_id()
                               AND access.authorization_version =
                                   epoch.authorization_version
                         )
                     )
               ) AS readable_document_count
        FROM library_nodes AS node
        WHERE v4_can_view_library_node(node.id)
        """
    )
    op.execute(
        """
        CREATE VIEW v4_admin_users
        WITH (security_barrier = true)
        AS SELECT id, username, display_name, role, status,
                  authentication_version, created_at, updated_at
        FROM users WHERE v4_current_actor_is_admin()
        """
    )
    op.execute(
        """
        CREATE VIEW v4_admin_teams
        WITH (security_barrier = true)
        AS SELECT team.id, team.name, team.is_active,
                  COALESCE(
                      array_agg(member.user_id ORDER BY member.user_id)
                          FILTER (WHERE member.user_id IS NOT NULL),
                      ARRAY[]::uuid[]
                  ) AS member_ids,
                  count(member.user_id)::bigint AS member_count,
                  team.created_at, team.updated_at
        FROM teams AS team
        LEFT JOIN team_members AS member ON member.team_id = team.id
        WHERE v4_current_actor_is_admin()
        GROUP BY team.id
        """
    )
    op.execute(
        """
        CREATE VIEW v4_admin_grants
        WITH (security_barrier = true)
        AS SELECT id, node_id, user_id, team_id, created_at, updated_at
        FROM access_grants WHERE v4_current_actor_is_admin()
        """
    )
    op.execute(
        """
        CREATE VIEW v4_admin_audit
        WITH (security_barrier = true)
        AS SELECT id, actor_user_id, event_type, target_type, target_id,
                  details, correlation_id, created_at
        FROM audit_events WHERE v4_current_actor_is_admin()
        """
    )
    op.execute(
        """
        CREATE VIEW v4_authorized_turn_sources
        WITH (security_barrier = true)
        AS
        SELECT source.*
        FROM turn_sources AS source
        JOIN chat_turns AS turn_row ON turn_row.id = source.turn_id
        JOIN chats AS chat ON chat.id = turn_row.chat_id
        WHERE chat.owner_user_id = v4_current_actor_id()
          AND NOT EXISTS (
              SELECT 1
              FROM turn_sources AS all_source
              WHERE all_source.turn_id = source.turn_id
                AND NOT (
                    (
                        all_source.document_id IS NULL
                        AND all_source.owner_authorized_at_deletion = true
                    )
                    OR (
                        all_source.document_id IS NOT NULL
                        AND v4_can_read_document(all_source.document_id)
                    )
                )
          )
        """
    )
    op.execute(
        """
        CREATE VIEW v4_authorized_turn_citations
        WITH (security_barrier = true)
        AS
        SELECT citation.*
        FROM turn_citations AS citation
        JOIN chat_turns AS turn_row ON turn_row.id = citation.turn_id
        JOIN chats AS chat ON chat.id = turn_row.chat_id
        WHERE chat.owner_user_id = v4_current_actor_id()
          AND NOT EXISTS (
              SELECT 1
              FROM turn_sources AS source
              WHERE source.turn_id = citation.turn_id
                AND NOT (
                    (
                        source.document_id IS NULL
                        AND source.owner_authorized_at_deletion = true
                    )
                    OR (
                        source.document_id IS NOT NULL
                        AND v4_can_read_document(source.document_id)
                    )
                )
          )
        """
    )

    for table_name, predicate in _EXPECTED_POLICIES.items():
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_actor_select ON {table_name} "
            f"FOR SELECT USING ({predicate})"
        )


def _apply_grants() -> None:
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION v4_activate_actor(text), "
        "v4_current_actor_id(), v4_current_actor_is_admin(), "
        "v4_can_read_document(uuid), v4_can_read_folder(uuid), "
        "v4_can_view_library_node(uuid), "
        "v4_schema_revision(), v4_readiness() TO rag_api"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION v4_runtime_identity(text) "
        "TO rag_api, rag_worker, rag_maintenance"
    )
    op.execute(
        "GRANT SELECT ON documents, chunks, library_nodes, chats, chat_scopes "
        "TO rag_api"
    )
    op.execute(
        "GRANT SELECT ON v4_current_user, v4_visible_library_nodes, "
        "v4_chat_history, v4_authorized_turn_sources, "
        "v4_authorized_turn_citations, v4_admin_users, "
        "v4_admin_teams, v4_admin_grants, v4_admin_audit TO rag_api"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "v4_auth_lookup(text, text), "
        "v4_login_blocked_until(text), "
        "v4_session_view(text), "
        "v4_refresh_session(text, text, timestamptz), "
        "v4_record_login_failure(text), "
        "v4_clear_login_failures(text), "
        "v4_issue_login_session(uuid, bigint, text, text, timestamptz, timestamptz), "
        "v4_logout(text), "
        "v4_consume_activation(text, text, text, text, timestamptz, timestamptz), "
        "v4_password_change_lookup(text), "
        "v4_change_password(text, bigint, text, text, text, timestamptz, timestamptz), "
        "v4_admin_create_user(text, text, text, text, timestamptz), "
        "v4_admin_reset_user(uuid, text, timestamptz), "
        "v4_admin_set_user(uuid, text, text), "
        "v4_admin_create_team(text, text), "
        "v4_admin_access_context(uuid), "
        "v4_account_active_teams(), "
        "v4_document_team_recipients(uuid[]), "
        "v4_admin_preview_acl(jsonb), "
        "v4_admin_apply_acl(uuid, text), "
        "v4_admin_create_folder(uuid, uuid, text, text), "
        "v4_admin_rename_library_node(uuid, text, text), "
        "v4_admin_delete_folder(uuid), "
        "v4_admin_upload_preflight("
        "text, text, text, text, text, text, bigint, text, text, text, uuid, uuid[]), "
        "v4_admin_commit_upload("
        "uuid, uuid, uuid, uuid, text, text, text, text, text, text, bigint, "
        "text, text, text, uuid, uuid[]), "
        "v4_admin_delete_document(uuid, uuid), "
        "v4_get_job(uuid), "
        "v4_list_chats(), "
        "v4_create_chat(text, text), "
        "v4_rename_chat(uuid, text), "
        "v4_delete_chat(uuid), "
        "v4_replace_chat_scope(uuid, uuid[]), "
        "v4_begin_turn(uuid, text, uuid, text), "
        "v4_store_turn_sources(uuid, uuid, jsonb), "
        "v4_fail_turn(uuid, uuid, text), "
        "v4_interrupt_turn(uuid, uuid, uuid), "
        "v4_retry_turn(uuid, uuid, uuid), "
        "v4_mark_turn_citation_failed(uuid, uuid, uuid, text), "
        "v4_mark_turn_length_limited(uuid, uuid, uuid, text), "
        "v4_finalize_turn(uuid, uuid, text, boolean, smallint[]), "
        "v4_mark_turn_access_revoked(uuid, uuid), "
        "v4_mark_turn_access_revoked_trusted(uuid, uuid) TO rag_api"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "v4_claim_service_lease(text, text, integer), "
        "v4_heartbeat_service_lease(text, text, uuid, bigint, integer), "
        "v4_claim_ingestion_job(text, integer), "
        "v4_heartbeat_ingestion_job(uuid, uuid, bigint, integer), "
        "v4_update_ingestion_progress("
        "uuid, uuid, bigint, text, integer, integer), "
        "v4_commit_ingestion_job(uuid, uuid, bigint, integer, jsonb), "
        "v4_requeue_ingestion_job(uuid, uuid, bigint, timestamptz), "
        "v4_poison_ingestion_job(uuid, uuid, bigint, text), "
        "v4_queue_expired_upload_orphans(integer), "
        "v4_claim_object_deletion(text, integer), "
        "v4_heartbeat_object_deletion(uuid, uuid, bigint, integer), "
        "v4_finish_object_deletion(uuid, uuid, bigint, boolean, text) "
        "TO rag_worker"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION v4_rebuild_effective_document_access() "
        "TO rag_maintenance"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "v4_maintenance_get_document(uuid), "
        "v4_maintenance_list_documents(), "
        "v4_maintenance_requeue_document(uuid, text, uuid, boolean), "
        "v4_maintenance_storage_snapshot(), "
        "v4_repair_interrupted_turns(), "
        "v4_queue_expired_upload_orphans(integer) TO rag_maintenance"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION v4_bootstrap_admin(text, text, text) "
        "TO rag_maintenance"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION v4_schema_revision(), v4_readiness(), "
        "v4_claim_service_lease(text, text, integer), "
        "v4_heartbeat_service_lease(text, text, uuid, bigint, integer), "
        "v4_begin_backup_run(text), "
        "v4_finish_backup_run(uuid, boolean, text, text, bigint, bigint, text) "
        "TO rag_maintenance"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION v4_schema_revision() TO rag_migrator, rag_backup"
    )
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO rag_backup")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE rag_owner REVOKE ALL ON TABLES FROM PUBLIC"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE rag_owner "
        "REVOKE ALL ON FUNCTIONS FROM PUBLIC"
    )


def upgrade() -> None:
    connection = op.get_bind()
    _assert_fresh_database(connection)
    _assert_roles(connection)
    _assert_extensions(connection)
    op.execute("ALTER TABLE alembic_version OWNER TO rag_owner")
    op.execute("SET LOCAL ROLE rag_owner")
    for statement in _BASELINE_DDL:
        op.execute(statement)
    op.execute(
        """
        INSERT INTO security_epochs (
            singleton, authentication_version, authorization_version,
            session_epoch
        ) VALUES (true, 1, 1, public.gen_random_uuid())
        """
    )
    _create_security_functions()
    _create_snapshot_trigger()
    _create_controlled_mutation_functions()
    _create_views_and_rls()
    _apply_grants()


def downgrade() -> None:
    op.execute("SET LOCAL ROLE rag_owner")
    connection = op.get_bind()
    populated = connection.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM users UNION ALL "
            "SELECT 1 FROM documents UNION ALL "
            "SELECT 1 FROM chats UNION ALL "
            "SELECT 1 FROM ingestion_jobs UNION ALL "
            "SELECT 1 FROM object_deletions UNION ALL "
            "SELECT 1 FROM upload_reservations UNION ALL "
            "SELECT 1 FROM teams UNION ALL "
            "SELECT 1 FROM access_grants UNION ALL "
            "SELECT 1 FROM audit_events UNION ALL "
            "SELECT 1 FROM login_throttles UNION ALL "
            "SELECT 1 FROM service_leases UNION ALL "
            "SELECT 1 FROM backup_runs"
            ")"
        )
    )
    if populated:
        raise RuntimeError(
            "refusing V4 baseline downgrade after bootstrap or product data"
        )
    policy_tables = (
        "documents",
        "chunks",
        "library_nodes",
        "ingestion_jobs",
        "chats",
        "chat_scopes",
        "chat_turns",
        "turn_sources",
        "turn_citations",
        "users",
        "sessions",
        "teams",
        "team_members",
        "access_grants",
        "effective_document_access",
        "acl_previews",
        "audit_events",
        "pre_auth_challenges",
        "login_throttles",
        "security_epochs",
        "object_deletions",
        "upload_reservations",
        "service_leases",
        "backup_runs",
    )
    for table_name in policy_tables:
        op.execute(f"DROP POLICY {table_name}_actor_select ON {table_name}")

    op.execute("DROP VIEW v4_authorized_turn_sources")
    op.execute("DROP VIEW v4_authorized_turn_citations")
    op.execute("DROP VIEW v4_admin_audit")
    op.execute("DROP VIEW v4_admin_grants")
    op.execute("DROP VIEW v4_admin_teams")
    op.execute("DROP VIEW v4_admin_users")
    op.execute("DROP VIEW v4_visible_library_nodes")
    op.execute("DROP VIEW v4_chat_history")
    op.execute("DROP VIEW v4_current_user")
    op.execute("DROP TRIGGER trg_v4_turn_source_immutability ON turn_sources")

    op.execute("DROP FUNCTION v4_readiness()")
    op.execute(
        "DROP FUNCTION v4_finish_backup_run("
        "uuid, boolean, text, text, bigint, bigint, text)"
    )
    op.execute("DROP FUNCTION v4_begin_backup_run(text)")
    op.execute("DROP FUNCTION v4_repair_interrupted_turns()")
    op.execute("DROP FUNCTION v4_maintenance_storage_snapshot()")
    op.execute(
        "DROP FUNCTION v4_maintenance_requeue_document(uuid, text, uuid, boolean)"
    )
    op.execute("DROP FUNCTION v4_maintenance_list_documents()")
    op.execute("DROP FUNCTION v4_maintenance_get_document(uuid)")
    op.execute("DROP FUNCTION v4_maintenance_document_snapshot_token(uuid)")
    op.execute("DROP FUNCTION v4_finalize_turn(uuid, uuid, text, boolean, smallint[])")
    op.execute("DROP FUNCTION v4_mark_turn_citation_failed(uuid, uuid, uuid, text)")
    op.execute("DROP FUNCTION v4_mark_turn_length_limited(uuid, uuid, uuid, text)")
    op.execute("DROP FUNCTION v4_mark_turn_access_revoked_trusted(uuid, uuid)")
    op.execute("DROP FUNCTION v4_mark_turn_access_revoked(uuid, uuid)")
    op.execute("DROP FUNCTION v4_interrupt_turn(uuid, uuid, uuid)")
    op.execute("DROP FUNCTION v4_retry_turn(uuid, uuid, uuid)")
    op.execute("DROP FUNCTION v4_fail_turn(uuid, uuid, text)")
    op.execute("DROP FUNCTION v4_store_turn_sources(uuid, uuid, jsonb)")
    op.execute("DROP FUNCTION v4_begin_turn(uuid, text, uuid, text)")
    op.execute("DROP FUNCTION v4_replace_chat_scope(uuid, uuid[])")
    op.execute("DROP FUNCTION v4_delete_chat(uuid)")
    op.execute("DROP FUNCTION v4_rename_chat(uuid, text)")
    op.execute("DROP FUNCTION v4_create_chat(text, text)")
    op.execute("DROP FUNCTION v4_list_chats()")
    op.execute("DROP FUNCTION v4_admin_delete_folder(uuid)")
    op.execute("DROP FUNCTION v4_admin_rename_library_node(uuid, text, text)")
    op.execute("DROP FUNCTION v4_admin_create_folder(uuid, uuid, text, text)")
    op.execute(
        "DROP FUNCTION v4_admin_commit_upload("
        "uuid, uuid, uuid, uuid, text, text, text, text, text, text, bigint, "
        "text, text, text, uuid, uuid[])"
    )
    op.execute(
        "DROP FUNCTION v4_admin_upload_preflight("
        "text, text, text, text, text, text, bigint, text, text, text, uuid, uuid[])"
    )
    op.execute("DROP FUNCTION v4_admin_delete_document(uuid, uuid)")
    op.execute(
        "DROP FUNCTION v4_heartbeat_object_deletion(uuid, uuid, bigint, integer)"
    )
    op.execute(
        "DROP FUNCTION v4_finish_object_deletion(uuid, uuid, bigint, boolean, text)"
    )
    op.execute("DROP FUNCTION v4_claim_object_deletion(text, integer)")
    op.execute("DROP FUNCTION v4_queue_expired_upload_orphans(integer)")
    op.execute(
        "DROP FUNCTION v4_finish_ingestion_job(uuid, uuid, bigint, boolean, text)"
    )
    op.execute("DROP FUNCTION v4_get_job(uuid)")
    op.execute("DROP FUNCTION v4_poison_ingestion_job(uuid, uuid, bigint, text)")
    op.execute(
        "DROP FUNCTION v4_requeue_ingestion_job(uuid, uuid, bigint, timestamptz)"
    )
    op.execute(
        "DROP FUNCTION v4_commit_ingestion_job(uuid, uuid, bigint, integer, jsonb)"
    )
    op.execute(
        "DROP FUNCTION v4_update_ingestion_progress("
        "uuid, uuid, bigint, text, integer, integer)"
    )
    op.execute("DROP FUNCTION v4_heartbeat_ingestion_job(uuid, uuid, bigint, integer)")
    op.execute("DROP FUNCTION v4_claim_ingestion_job(text, integer)")
    op.execute(
        "DROP FUNCTION v4_heartbeat_service_lease(text, text, uuid, bigint, integer)"
    )
    op.execute("DROP FUNCTION v4_claim_service_lease(text, text, integer)")
    op.execute("DROP FUNCTION v4_admin_apply_acl(uuid, text)")
    op.execute("DROP FUNCTION v4_admin_preview_acl(jsonb)")
    op.execute("DROP FUNCTION v4_admin_access_context(uuid)")
    op.execute("DROP FUNCTION v4_acl_impact(jsonb)")
    op.execute("DROP FUNCTION v4_document_team_recipients(uuid[])")
    op.execute("DROP FUNCTION v4_account_active_teams()")
    op.execute("DROP FUNCTION v4_admin_create_team(text, text)")
    op.execute("DROP FUNCTION v4_admin_set_user(uuid, text, text)")
    op.execute("DROP FUNCTION v4_admin_reset_user(uuid, text, timestamptz)")
    op.execute(
        "DROP FUNCTION v4_admin_create_user(text, text, text, text, timestamptz)"
    )
    op.execute(
        "DROP FUNCTION v4_change_password("
        "text, bigint, text, text, text, timestamptz, timestamptz)"
    )
    op.execute("DROP FUNCTION v4_password_change_lookup(text)")
    op.execute(
        "DROP FUNCTION v4_consume_activation("
        "text, text, text, text, timestamptz, timestamptz)"
    )
    op.execute("DROP FUNCTION v4_logout(text)")
    op.execute(
        "DROP FUNCTION v4_issue_login_session("
        "uuid, bigint, text, text, timestamptz, timestamptz)"
    )
    op.execute("DROP FUNCTION v4_clear_login_failures(text)")
    op.execute("DROP FUNCTION v4_record_login_failure(text)")
    op.execute("DROP FUNCTION v4_refresh_session(text, text, timestamptz)")
    op.execute("DROP FUNCTION v4_session_view(text)")
    op.execute("DROP FUNCTION v4_auth_lookup(text, text)")
    op.execute("DROP FUNCTION v4_login_blocked_until(text)")
    op.execute("DROP FUNCTION v4_append_audit(text, text, uuid, jsonb)")
    op.execute("DROP FUNCTION v4_require_admin()")
    op.execute("DROP FUNCTION v4_schema_revision()")
    op.execute("DROP FUNCTION v4_runtime_identity(text)")
    op.execute(
        "DROP FUNCTION v4_upload_metadata_digest("
        "text, text, text, text, bigint, text, text, text)"
    )
    op.execute("DROP FUNCTION v4_enforce_turn_source_immutability()")
    op.execute("DROP FUNCTION v4_bootstrap_admin(text, text, text)")
    op.execute("DROP FUNCTION v4_rebuild_effective_document_access()")
    op.execute("DROP FUNCTION v4_can_view_library_node(uuid)")
    op.execute("DROP FUNCTION v4_can_read_folder(uuid)")
    op.execute("DROP FUNCTION v4_can_read_document(uuid)")
    op.execute("DROP FUNCTION v4_activate_actor(text)")
    op.execute("DROP FUNCTION v4_current_actor_is_admin()")
    op.execute("DROP FUNCTION v4_current_actor_id()")

    for table_name in (
        "upload_reservations",
        "turn_citations",
        "turn_sources",
        "chat_turns",
        "chat_scopes",
        "access_grants",
        "team_members",
        "sessions",
        "pre_auth_challenges",
        "library_nodes",
        "ingestion_jobs",
        "effective_document_access",
        "chunks",
        "chats",
        "audit_events",
        "acl_previews",
        "users",
        "teams",
        "security_epochs",
        "object_deletions",
        "login_throttles",
        "backup_runs",
        "service_leases",
        "documents",
    ):
        op.drop_table(table_name)

    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE rag_owner "
        "GRANT EXECUTE ON FUNCTIONS TO PUBLIC"
    )
    op.execute("GRANT CREATE ON SCHEMA public TO rag_migrator")
