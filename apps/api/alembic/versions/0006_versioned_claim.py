"""Return the stored document ingestion identity with each worker claim.

This migration is forward-only because the worker claim contract and release
readiness revision advance atomically.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_versioned_claim"
down_revision: str | None = "0005_document_reingest_action"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL ROLE rag_owner")
    op.execute(
        """
        DROP FUNCTION public.v4_claim_ingestion_job(text, integer);

        CREATE FUNCTION public.v4_claim_ingestion_job(
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
            fencing_token bigint,
            parser_version text,
            chunking_version text,
            embedding_version text
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
                stage = 'uploaded',
                completed_units = 0,
                total_units = NULL,
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
                job.fencing_token, document.parser_version::text,
                document.chunking_version::text,
                document.embedding_version::text;
        END;
        $$;

        REVOKE ALL ON FUNCTION
            public.v4_claim_ingestion_job(text, integer)
            FROM PUBLIC, rag_api, rag_maintenance, rag_backup, rag_migrator;
        GRANT EXECUTE ON FUNCTION
            public.v4_claim_ingestion_job(text, integer) TO rag_worker;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.v4_schema_revision()
        RETURNS text
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ SELECT '0006_versioned_claim'::text $$;

        CREATE OR REPLACE FUNCTION public.v5_readiness()
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
            SELECT '0006_versioned_claim'::text,
                   EXISTS (
                       SELECT 1 FROM pg_catalog.pg_extension
                       WHERE extname = 'vector'
                   ),
                   NOT EXISTS (
                       SELECT 1 FROM public.users
                       WHERE role = 'admin' AND status = 'active'
                         AND deleted_at IS NULL
                   ),
                   EXISTS (
                       SELECT 1 FROM public.alembic_version
                       WHERE version_num =
                             '0006_versioned_claim'
                   )
                   AND EXISTS (
                       SELECT 1
                       FROM pg_catalog.pg_class AS relation
                       WHERE relation.oid =
                             'public.folder_create_grants'::regclass
                         AND relation.relrowsecurity
                         AND relation.relforcerowsecurity
                   )
                   AND EXISTS (
                       SELECT 1 FROM pg_catalog.pg_attribute
                       WHERE attrelid = 'public.chunks'::regclass
                         AND attname = 'highlight_anchor'
                         AND attnotnull AND NOT attisdropped
                   )
                   AND EXISTS (
                       SELECT 1 FROM pg_catalog.pg_attribute
                       WHERE attrelid = 'public.turn_sources'::regclass
                         AND attname = 'highlight_anchor'
                         AND attnotnull AND NOT attisdropped
                   )
                   AND (
                       SELECT count(*) = 2
                       FROM pg_catalog.pg_constraint
                       WHERE conname IN (
                           'ck_chunks_highlight_anchor',
                           'ck_turn_sources_highlight_anchor'
                       )
                         AND convalidated
                   )
                   AND to_regprocedure(
                       'public.v5_citation_evidence(uuid,uuid,smallint)'
                   ) IS NOT NULL
                   AND (
                       SELECT count(*) = 12
                       FROM pg_catalog.pg_proc AS routine
                       JOIN pg_catalog.pg_roles AS owner
                         ON owner.oid = routine.proowner
                       WHERE routine.oid IN (
                           'public.v4_claim_ingestion_job(text,integer)'::regprocedure,
                           'public.v4_commit_ingestion_job(uuid,uuid,bigint,integer,jsonb)'::regprocedure,
                           'public.v4_store_turn_sources(uuid,uuid,jsonb)'::regprocedure,
                           'public.v4_enforce_turn_source_immutability()'::regprocedure,
                           'public.v5_is_valid_highlight_anchor(jsonb,integer,integer)'::regprocedure,
                           'public.v5_citation_evidence(uuid,uuid,smallint)'::regprocedure,
                           'public.v4_can_create_children(uuid)'::regprocedure,
                           'public.v4_create_folder(uuid,uuid,text,text)'::regprocedure,
                           'public.v4_require_folder_create_grant_target()'::regprocedure,
                           'public.v4_prepare_document_reingest(uuid)'::regprocedure,
                           'public.v4_commit_document_reingest(uuid,text,uuid)'::regprocedure,
                           'public.v5_readiness()'::regprocedure
                       )
                         AND owner.rolname = 'rag_owner'
                   )
                   AND (
                       SELECT count(*) = 3
                       FROM pg_catalog.pg_proc AS routine
                       WHERE routine.oid IN (
                           'public.v4_claim_ingestion_job(text,integer)'::regprocedure,
                           'public.v4_prepare_document_reingest(uuid)'::regprocedure,
                           'public.v4_commit_document_reingest(uuid,text,uuid)'::regprocedure
                       )
                         AND routine.prosecdef
                         AND routine.proconfig =
                             ARRAY['search_path=pg_catalog']::text[]
                   )
                   AND has_function_privilege(
                       'rag_worker',
                       'public.v4_claim_ingestion_job(text,integer)',
                       'EXECUTE'
                   )
                   AND has_function_privilege(
                       'rag_api',
                       'public.v4_prepare_document_reingest(uuid)',
                       'EXECUTE'
                   )
                   AND has_function_privilege(
                       'rag_api',
                       'public.v4_commit_document_reingest(uuid,text,uuid)',
                       'EXECUTE'
                   )
                   AND has_function_privilege(
                       'rag_api',
                       'public.v5_citation_evidence(uuid,uuid,smallint)',
                       'EXECUTE'
                   )
                   AND has_function_privilege(
                       'rag_api',
                       'public.v4_can_create_children(uuid)',
                       'EXECUTE'
                   )
                   AND has_function_privilege(
                       'rag_api',
                       'public.v4_create_folder(uuid,uuid,text,text)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'public',
                       'public.v4_claim_ingestion_job(text,integer)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'rag_api',
                       'public.v4_claim_ingestion_job(text,integer)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'rag_maintenance',
                       'public.v4_claim_ingestion_job(text,integer)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'rag_backup',
                       'public.v4_claim_ingestion_job(text,integer)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'rag_migrator',
                       'public.v4_claim_ingestion_job(text,integer)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'public',
                       'public.v4_prepare_document_reingest(uuid)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'public',
                       'public.v4_commit_document_reingest(uuid,text,uuid)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'rag_worker',
                       'public.v4_prepare_document_reingest(uuid)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'rag_worker',
                       'public.v4_commit_document_reingest(uuid,text,uuid)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'rag_maintenance',
                       'public.v4_prepare_document_reingest(uuid)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'rag_maintenance',
                       'public.v4_commit_document_reingest(uuid,text,uuid)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'public',
                       'public.v5_citation_evidence(uuid,uuid,smallint)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'public',
                       'public.v4_can_create_children(uuid)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'public',
                       'public.v4_create_folder(uuid,uuid,text,text)',
                       'EXECUTE'
                   )
                   AND NOT has_table_privilege(
                       'rag_api', 'public.folder_create_grants', 'SELECT'
                   )
                   AND NOT has_table_privilege(
                       'rag_api', 'public.folder_create_grants', 'INSERT'
                   )
                   AND NOT has_table_privilege(
                       'rag_api', 'public.folder_create_grants', 'UPDATE'
                   )
                   AND NOT has_table_privilege(
                       'rag_api', 'public.folder_create_grants', 'DELETE'
                   )
                   AND has_table_privilege(
                       'rag_backup', 'public.folder_create_grants', 'SELECT'
                   )
                   AND has_function_privilege(
                       'rag_api', 'public.v5_readiness()', 'EXECUTE'
                   )
                   AND has_function_privilege(
                       'rag_maintenance', 'public.v5_readiness()', 'EXECUTE'
                   )
        $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("0006_versioned_claim is forward-only")
