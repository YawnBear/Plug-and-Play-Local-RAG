# ruff: noqa: E501
"""Allow restart-scoped runtime changes without backup evidence."""

from alembic import op

revision: str = "0014_restart_without_backup"
down_revision: str | None = "0013_effective_runtime_identity"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.runtime_configuration_changes
            ALTER COLUMN backup_run_id DROP NOT NULL;

        CREATE OR REPLACE FUNCTION public.v9_admin_apply_runtime_configuration(
            p_preview_id uuid, p_impact_digest text, p_grant_token_hash text,
            p_controller_nonce_hash text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        DECLARE
            actor_id uuid;
            current_session_id uuid;
            preview public.runtime_configuration_previews%ROWTYPE;
            grant_row public.system_reauthentication_grants%ROWTYPE;
            change_id uuid := public.gen_random_uuid();
            revision_value text := 'v8d-' || replace(change_id::text, '-', '');
            current_revision text;
        BEGIN
            PERFORM public.v4_require_admin();
            actor_id := public.v4_current_actor_id();
            SELECT session.id INTO STRICT current_session_id
            FROM public.sessions AS session
            WHERE session.token_hash = current_setting('rag.session_token_hash', true)
              AND session.revoked_at IS NULL;
            SELECT * INTO preview FROM public.runtime_configuration_previews
            WHERE id = p_preview_id FOR UPDATE;
            SELECT revision_id INTO STRICT current_revision
            FROM public.runtime_configuration_revisions WHERE effective FOR UPDATE;
            IF preview.id IS NULL OR preview.actor_user_id <> actor_id
               OR preview.session_id <> current_session_id
               OR preview.consumed_at IS NOT NULL
               OR preview.expires_at <= statement_timestamp()
               OR preview.impact_digest <> p_impact_digest
               OR preview.base_revision_id <> current_revision
               OR preview.operation_class <> 'restart_scoped' THEN
                RAISE EXCEPTION 'runtime configuration preview is stale'
                    USING ERRCODE = '40001';
            END IF;
            SELECT * INTO grant_row FROM public.system_reauthentication_grants
            WHERE token_hash = p_grant_token_hash FOR UPDATE;
            IF grant_row.id IS NULL OR grant_row.actor_user_id <> actor_id
               OR grant_row.session_id <> current_session_id
               OR grant_row.action <> 'apply_runtime_configuration'
               OR grant_row.impact_digest <> p_impact_digest
               OR grant_row.consumed_at IS NOT NULL
               OR grant_row.expires_at <= statement_timestamp() THEN
                RAISE EXCEPTION 'reauthentication grant is invalid or expired'
                    USING ERRCODE = '28000';
            END IF;
            IF p_controller_nonce_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'controller nonce is invalid' USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.runtime_configuration_revisions (
                revision_id, generation_profile_id, reranker_profile_id, ocr_mode,
                ocr_profile_id, ocr_preset_id, created_by
            ) VALUES (
                revision_value, preview.generation_profile_id,
                preview.reranker_profile_id, preview.ocr_mode,
                preview.ocr_profile_id, preview.ocr_preset_id, actor_id
            );
            INSERT INTO public.runtime_configuration_changes (
                id, actor_user_id, prior_revision_id, desired_revision_id,
                preview_id, backup_run_id, impact_digest, operation_class,
                controller_nonce_hash
            ) VALUES (
                change_id, actor_id, current_revision, revision_value,
                preview.id, NULL, p_impact_digest,
                preview.operation_class, p_controller_nonce_hash
            );
            UPDATE public.runtime_configuration_previews
            SET consumed_at = statement_timestamp() WHERE id = preview.id;
            UPDATE public.system_reauthentication_grants
            SET consumed_at = statement_timestamp() WHERE id = grant_row.id;
            INSERT INTO public.audit_events (
                id, actor_user_id, event_type, target_type, target_id, details
            ) VALUES (
                public.gen_random_uuid(), actor_id, 'runtime_configuration_requested',
                'runtime_configuration', change_id,
                jsonb_build_object('impact_digest', p_impact_digest,
                                   'operation_class', preview.operation_class,
                                   'backup_required', false,
                                   'prior_revision', current_revision,
                                   'desired_revision', revision_value)
            );
            RETURN change_id;
        EXCEPTION WHEN unique_violation THEN
            RAISE EXCEPTION 'a runtime configuration change is already active'
                USING ERRCODE = '40001';
        END;
        $$;

        CREATE OR REPLACE FUNCTION public.v4_schema_revision()
        RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ SELECT '0014_restart_without_backup'::text $$;

        CREATE OR REPLACE FUNCTION public.v5_readiness()
        RETURNS TABLE (
            schema_revision text, vector_extension boolean,
            bootstrap_required boolean, catalog_integrity boolean
        ) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
            SELECT '0014_restart_without_backup'::text,
                EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'vector'),
                NOT EXISTS (SELECT 1 FROM public.users WHERE role = 'admin'
                            AND status = 'active' AND deleted_at IS NULL),
                EXISTS (SELECT 1 FROM public.alembic_version
                        WHERE version_num = '0014_restart_without_backup')
                AND (SELECT count(*) = 8 FROM pg_catalog.pg_class AS relation
                     WHERE relation.oid IN (
                        'public.ingestion_version_configuration'::regclass,
                        'public.reprocessing_previews'::regclass,
                        'public.reprocessing_operations'::regclass,
                        'public.document_generations'::regclass,
                        'public.embedding_generations'::regclass,
                        'public.embedding_generation_state'::regclass,
                        'public.chunk_embeddings'::regclass,
                        'public.reindex_tasks'::regclass)
                       AND relation.relrowsecurity AND relation.relforcerowsecurity)
                AND (SELECT count(*) = 1 FROM public.embedding_generation_state)
                AND (SELECT count(*) = 1 FROM public.embedding_generations
                     WHERE state = 'active')
                AND NOT has_table_privilege('rag_api',
                    'public.chunk_embeddings','SELECT')
                AND has_table_privilege('rag_backup',
                    'public.chunk_embeddings','SELECT')
                AND has_function_privilege('rag_api',
                    'public.v10_admin_version_inventory()','EXECUTE')
                AND has_function_privilege('rag_api',
                    'public.v10_effective_ingestion_version()','EXECUTE')
                AND has_function_privilege('rag_api',
                    'public.v11_admin_personal_backup_history(integer)','EXECUTE')
                AND has_function_privilege('rag_api',
                    'public.v13_admin_effective_runtime_configuration()','EXECUTE')
                AND has_function_privilege('rag_worker',
                    'public.v10_claim_reindex_tasks(text,integer,integer)','EXECUTE')
                AND NOT has_function_privilege('public',
                    'public.v10_admin_version_inventory()','EXECUTE')
                AND NOT has_function_privilege('public',
                    'public.v11_admin_personal_backup_history(integer)','EXECUTE')
                AND NOT has_function_privilege('public',
                    'public.v13_admin_effective_runtime_configuration()','EXECUTE')
        $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Restart-without-backup policy is forward-only; restore a paired backup"
    )
