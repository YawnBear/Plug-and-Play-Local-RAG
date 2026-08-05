# ruff: noqa: E501
"""Allow bounded dynamic OCR device profiles and manual tuning presets."""

from alembic import op

revision: str = "0012_dynamic_ocr_tuning"
down_revision: str | None = "0011_v8f_release_maintenance"
branch_labels: str | None = None
depends_on: str | None = None

_OCR_PROFILE_PATTERN = r"^ocr\.[a-z0-9][a-z0-9._-]{2,91}$"
_OCR_PRESET_PATTERN = (
    r"^(balanced|manual-t([1-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-6])"
    r"-p([1-9]|1[0-6]))$"
)


def upgrade() -> None:
    op.execute("SET LOCAL ROLE rag_owner")
    op.execute(
        f"""
        ALTER TABLE public.runtime_configuration_revisions
            DROP CONSTRAINT ck_runtime_ocr_profile,
            DROP CONSTRAINT ck_runtime_ocr_preset,
            ADD CONSTRAINT ck_runtime_ocr_profile CHECK (
                ocr_profile_id ~ '{_OCR_PROFILE_PATTERN}'
            ),
            ADD CONSTRAINT ck_runtime_ocr_preset CHECK (
                ocr_preset_id ~ '{_OCR_PRESET_PATTERN}'
            );

        ALTER TABLE public.runtime_configuration_previews
            DROP CONSTRAINT ck_runtime_preview_profiles,
            ADD CONSTRAINT ck_runtime_preview_profiles CHECK (
                generation_profile_id = 'generation.qwen3-8b.ollama.windows-x64'
                AND reranker_profile_id = 'reranking.bge-v2-m3.cpu.windows-x64'
                AND ocr_profile_id ~ '{_OCR_PROFILE_PATTERN}'
                AND ocr_preset_id ~ '{_OCR_PRESET_PATTERN}'
                AND ocr_mode IN ('auto','explicit')
            );

        CREATE OR REPLACE FUNCTION public.v9_admin_preview_runtime_configuration(
            p_base_revision text, p_generation_profile_id text,
            p_reranker_profile_id text, p_ocr_mode text,
            p_ocr_profile_id text, p_ocr_preset_id text
        )
        RETURNS TABLE (preview_id uuid, impact_digest text, expires_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        DECLARE
            actor_id uuid;
            current_session_id uuid;
            current_row public.runtime_configuration_revisions%ROWTYPE;
            digest_value text;
            preview_uuid uuid := public.gen_random_uuid();
            expiry timestamptz := statement_timestamp() + interval '5 minutes';
        BEGIN
            PERFORM public.v4_require_admin();
            actor_id := public.v4_current_actor_id();
            SELECT session.id INTO STRICT current_session_id
            FROM public.sessions AS session
            WHERE session.token_hash = current_setting('rag.session_token_hash', true);
            SELECT * INTO STRICT current_row
            FROM public.runtime_configuration_revisions WHERE effective FOR SHARE;
            IF p_base_revision <> current_row.revision_id THEN
                RAISE EXCEPTION 'runtime configuration preview is stale'
                    USING ERRCODE = '40001';
            END IF;
            IF p_generation_profile_id <> 'generation.qwen3-8b.ollama.windows-x64'
               OR p_reranker_profile_id <> 'reranking.bge-v2-m3.cpu.windows-x64'
               OR p_ocr_profile_id !~ '{_OCR_PROFILE_PATTERN}'
               OR p_ocr_preset_id !~ '{_OCR_PRESET_PATTERN}'
               OR p_ocr_mode NOT IN ('auto','explicit') THEN
                RAISE EXCEPTION 'runtime configuration contains an unavailable profile'
                    USING ERRCODE = '22023';
            END IF;
            IF p_generation_profile_id = current_row.generation_profile_id
               AND p_reranker_profile_id = current_row.reranker_profile_id
               AND p_ocr_profile_id = current_row.ocr_profile_id
               AND p_ocr_preset_id = current_row.ocr_preset_id
               AND p_ocr_mode = current_row.ocr_mode THEN
                RAISE EXCEPTION 'runtime configuration has no changes'
                    USING ERRCODE = '22023';
            END IF;
            digest_value := encode(public.digest(concat_ws('|',
                p_base_revision, p_generation_profile_id, p_reranker_profile_id,
                p_ocr_mode, p_ocr_profile_id, p_ocr_preset_id,
                'restart_scoped'), 'sha256'), 'hex');
            INSERT INTO public.runtime_configuration_previews (
                id, actor_user_id, session_id, base_revision_id,
                generation_profile_id, reranker_profile_id, ocr_mode,
                ocr_profile_id, ocr_preset_id, impact_digest,
                operation_class, expires_at
            ) VALUES (
                preview_uuid, actor_id, current_session_id, p_base_revision,
                p_generation_profile_id, p_reranker_profile_id, p_ocr_mode,
                p_ocr_profile_id, p_ocr_preset_id, digest_value,
                'restart_scoped', expiry
            );
            RETURN QUERY SELECT preview_uuid, digest_value, expiry;
        END;
        $$;

        CREATE OR REPLACE FUNCTION public.v4_schema_revision()
        RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ SELECT '0012_dynamic_ocr_tuning'::text $$;

        CREATE OR REPLACE FUNCTION public.v5_readiness()
        RETURNS TABLE (
            schema_revision text, vector_extension boolean,
            bootstrap_required boolean, catalog_integrity boolean
        ) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
            SELECT '0012_dynamic_ocr_tuning'::text,
                EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'vector'),
                NOT EXISTS (SELECT 1 FROM public.users WHERE role = 'admin'
                            AND status = 'active' AND deleted_at IS NULL),
                EXISTS (SELECT 1 FROM public.alembic_version
                        WHERE version_num = '0012_dynamic_ocr_tuning')
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
                AND has_function_privilege('rag_worker',
                    'public.v10_claim_reindex_tasks(text,integer,integer)','EXECUTE')
                AND NOT has_function_privilege('public',
                    'public.v10_admin_version_inventory()','EXECUTE')
                AND NOT has_function_privilege('public',
                    'public.v11_admin_personal_backup_history(integer)','EXECUTE')
        $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Dynamic OCR tuning is forward-only; restore a paired pre-update backup"
    )
