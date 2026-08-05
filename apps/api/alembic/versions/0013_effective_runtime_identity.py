"""Expose the actual effective runtime identity separately from pending changes."""

from alembic import op

revision: str = "0013_effective_runtime_identity"
down_revision: str | None = "0012_dynamic_ocr_tuning"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.v13_admin_effective_runtime_configuration()
        RETURNS TABLE (
            effective_revision text, generation_profile_id text,
            reranker_profile_id text, ocr_mode text,
            ocr_profile_id text, ocr_preset_id text
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            RETURN QUERY
            SELECT revision.revision_id::text,
                   revision.generation_profile_id::text,
                   revision.reranker_profile_id::text,
                   revision.ocr_mode::text,
                   revision.ocr_profile_id::text,
                   revision.ocr_preset_id::text
            FROM public.runtime_configuration_revisions AS revision
            WHERE revision.effective;
        END;
        $$;

        REVOKE ALL ON FUNCTION public.v13_admin_effective_runtime_configuration()
            FROM PUBLIC, rag_worker, rag_maintenance, rag_backup, rag_migrator;
        GRANT EXECUTE ON FUNCTION public.v13_admin_effective_runtime_configuration()
            TO rag_api;

        CREATE OR REPLACE FUNCTION public.v4_schema_revision()
        RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ SELECT '0013_effective_runtime_identity'::text $$;

        CREATE OR REPLACE FUNCTION public.v5_readiness()
        RETURNS TABLE (
            schema_revision text, vector_extension boolean,
            bootstrap_required boolean, catalog_integrity boolean
        ) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
            SELECT '0013_effective_runtime_identity'::text,
                EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'vector'),
                NOT EXISTS (SELECT 1 FROM public.users WHERE role = 'admin'
                            AND status = 'active' AND deleted_at IS NULL),
                EXISTS (SELECT 1 FROM public.alembic_version
                        WHERE version_num = '0013_effective_runtime_identity')
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
        "Effective runtime identity is forward-only; restore a paired backup"
    )
