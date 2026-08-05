"""V8F backup history and release-maintenance database contract."""

from alembic import op

revision: str = "0011_v8f_release_maintenance"
down_revision: str | None = "0010_versioned_reprocessing"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.v11_admin_personal_backup_history(p_limit integer)
        RETURNS TABLE (
            backup_run_id uuid, state text, stage text, reason_code text,
            created_at timestamptz, finished_at timestamptz,
            restore_verified boolean, manifest_sha256 text
        ) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            IF p_limit < 1 OR p_limit > 100 THEN
                RAISE EXCEPTION 'backup history limit is invalid'
                    USING ERRCODE = '22023';
            END IF;
            RETURN QUERY
            SELECT operation.backup_run_id, operation.state::text,
                   operation.stage::text, operation.reason_code::text,
                   operation.created_at, operation.finished_at,
                   verification.backup_run_id IS NOT NULL,
                   operation.manifest_sha256::text
            FROM public.personal_backup_operations AS operation
            LEFT JOIN public.backup_restore_verifications AS verification
              ON verification.backup_run_id = operation.backup_run_id
            ORDER BY operation.created_at DESC, operation.backup_run_id DESC
            LIMIT p_limit;
        END;
        $$;

        REVOKE ALL ON FUNCTION public.v11_admin_personal_backup_history(integer)
            FROM PUBLIC, rag_worker, rag_maintenance, rag_backup, rag_migrator;
        GRANT EXECUTE ON FUNCTION public.v11_admin_personal_backup_history(integer)
            TO rag_api;

        CREATE OR REPLACE FUNCTION public.v4_schema_revision()
        RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ SELECT '0011_v8f_release_maintenance'::text $$;

        CREATE OR REPLACE FUNCTION public.v5_readiness()
        RETURNS TABLE (
            schema_revision text, vector_extension boolean,
            bootstrap_required boolean, catalog_integrity boolean
        ) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
            SELECT '0011_v8f_release_maintenance'::text,
                EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'vector'),
                NOT EXISTS (SELECT 1 FROM public.users WHERE role = 'admin'
                            AND status = 'active' AND deleted_at IS NULL),
                EXISTS (SELECT 1 FROM public.alembic_version
                        WHERE version_num = '0011_v8f_release_maintenance')
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
        "V8F release maintenance is forward-only; restore a paired pre-update backup"
    )
