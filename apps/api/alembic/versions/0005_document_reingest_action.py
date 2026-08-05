"""Add the authenticated failed-document reingest action.

This migration is forward-only because the API and release verifier advance
atomically with its database contract.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_document_reingest_action"
down_revision: str | None = "0004_create_children_capability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL ROLE rag_owner")
    op.execute(
        """
        CREATE FUNCTION public.v4_prepare_document_reingest(
            p_document_id uuid
        )
        RETURNS TABLE (
            result_status text,
            document_id uuid,
            sha256 text,
            byte_size bigint,
            object_key text,
            parser_version text,
            chunking_version text,
            embedding_version text,
            previous_job_id uuid,
            previous_job_status text,
            snapshot_token text
        )
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_actor uuid := public.v4_current_actor_id();
            v_authorized record;
            v_document public.documents%ROWTYPE;
            v_uploader_user_id uuid;
            v_latest_job public.ingestion_jobs%ROWTYPE;
            v_snapshot_token text;
        BEGIN
            IF v_actor IS NULL THEN
                RETURN QUERY SELECT
                    'not_found'::text, NULL::uuid, NULL::text, NULL::bigint,
                    NULL::text, NULL::text, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text;
                RETURN;
            END IF;
            SELECT document AS document_row,
                   node.uploader_user_id AS uploader_user_id
            INTO v_authorized
            FROM public.documents AS document
            JOIN public.library_nodes AS node
              ON node.document_id = document.id
             AND node.kind = 'file'
            WHERE document.id = p_document_id
              AND (
                  public.v4_current_actor_is_admin()
                  OR node.uploader_user_id = v_actor
              );
            IF NOT FOUND THEN
                RETURN QUERY SELECT
                    'not_found'::text, NULL::uuid, NULL::text, NULL::bigint,
                    NULL::text, NULL::text, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text;
                RETURN;
            END IF;
            v_document := v_authorized.document_row;
            v_uploader_user_id := v_authorized.uploader_user_id;
            IF EXISTS (
                SELECT 1
                FROM public.ingestion_jobs AS active_job
                WHERE active_job.document_id = p_document_id
                  AND active_job.status IN ('queued', 'running')
            ) THEN
                RETURN QUERY SELECT
                    'active_job'::text, v_document.id,
                    NULL::text, NULL::bigint, NULL::text, NULL::text,
                    NULL::text, NULL::text, NULL::uuid, NULL::text,
                    NULL::text;
                RETURN;
            END IF;
            SELECT job.*
            INTO v_latest_job
            FROM public.ingestion_jobs AS job
            WHERE job.document_id = p_document_id
            ORDER BY job.created_at DESC, job.id DESC
            LIMIT 1;
            IF v_document.state <> 'failed'
               OR v_latest_job.id IS NULL
               OR v_latest_job.status NOT IN ('failed', 'interrupted') THEN
                RETURN QUERY SELECT
                    'not_retryable'::text, v_document.id,
                    NULL::text, NULL::bigint, NULL::text, NULL::text,
                    NULL::text, NULL::text, v_latest_job.id,
                    v_latest_job.status::text, NULL::text;
                RETURN;
            END IF;
            v_snapshot_token := encode(
                public.digest(
                    convert_to(
                        jsonb_build_array(
                            v_document.id, v_document.state, v_document.stage,
                            v_document.error, v_document.sha256,
                            v_document.byte_size, v_document.object_key,
                            v_document.parser_version,
                            v_document.chunking_version,
                            v_document.embedding_version,
                            v_document.page_count, v_document.chunk_count,
                            v_document.updated_at, v_uploader_user_id,
                            v_latest_job.id, v_latest_job.status,
                            v_latest_job.stage, v_latest_job.error,
                            v_latest_job.attempt,
                            v_latest_job.completed_units,
                            v_latest_job.total_units,
                            v_latest_job.updated_at
                        )::text,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            );
            RETURN QUERY SELECT
                'prepared'::text, v_document.id, v_document.sha256::text,
                v_document.byte_size, v_document.object_key::text,
                v_document.parser_version::text,
                v_document.chunking_version::text,
                v_document.embedding_version::text,
                v_latest_job.id, v_latest_job.status::text, v_snapshot_token;
        END;
        $$;

        CREATE FUNCTION public.v4_commit_document_reingest(
            p_document_id uuid,
            p_snapshot_token text,
            p_job_id uuid
        )
        RETURNS TABLE (
            result_status text,
            document_id uuid,
            job_id uuid
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_actor uuid := public.v4_current_actor_id();
            v_authorized record;
            v_document public.documents%ROWTYPE;
            v_uploader_user_id uuid;
            v_latest_job public.ingestion_jobs%ROWTYPE;
            v_current_snapshot_token text;
        BEGIN
            IF p_document_id IS NULL
               OR p_job_id IS NULL
               OR p_snapshot_token IS NULL
               OR p_snapshot_token !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'invalid document reingest request'
                    USING ERRCODE = '22023';
            END IF;
            IF v_actor IS NULL THEN
                RETURN QUERY SELECT
                    'not_found'::text, NULL::uuid, NULL::uuid;
                RETURN;
            END IF;
            SELECT document AS document_row,
                   node.uploader_user_id AS uploader_user_id
            INTO v_authorized
            FROM public.documents AS document
            JOIN public.library_nodes AS node
              ON node.document_id = document.id
             AND node.kind = 'file'
            WHERE document.id = p_document_id
              AND (
                  public.v4_current_actor_is_admin()
                  OR node.uploader_user_id = v_actor
              )
            FOR UPDATE OF document, node;
            IF NOT FOUND THEN
                RETURN QUERY SELECT
                    'not_found'::text, NULL::uuid, NULL::uuid;
                RETURN;
            END IF;
            v_document := v_authorized.document_row;
            v_uploader_user_id := v_authorized.uploader_user_id;
            IF v_document.state <> 'failed' THEN
                RETURN QUERY SELECT
                    'not_retryable'::text, v_document.id, NULL::uuid;
                RETURN;
            END IF;
            PERFORM job.id
            FROM public.ingestion_jobs AS job
            WHERE job.document_id = p_document_id
            ORDER BY job.created_at, job.id
            FOR UPDATE OF job;
            SELECT job.*
            INTO v_latest_job
            FROM public.ingestion_jobs AS job
            WHERE job.document_id = p_document_id
            ORDER BY job.created_at DESC, job.id DESC
            LIMIT 1;
            IF EXISTS (
                SELECT 1
                FROM public.ingestion_jobs AS active_job
                WHERE active_job.document_id = p_document_id
                  AND active_job.status IN ('queued', 'running')
            ) THEN
                RETURN QUERY SELECT
                    'active_job'::text, v_document.id, NULL::uuid;
                RETURN;
            END IF;
            IF v_latest_job.id IS NULL
               OR v_latest_job.status NOT IN ('failed', 'interrupted') THEN
                RETURN QUERY SELECT
                    'not_retryable'::text, v_document.id, NULL::uuid;
                RETURN;
            END IF;
            v_current_snapshot_token := encode(
                public.digest(
                    convert_to(
                        jsonb_build_array(
                            v_document.id, v_document.state, v_document.stage,
                            v_document.error, v_document.sha256,
                            v_document.byte_size, v_document.object_key,
                            v_document.parser_version,
                            v_document.chunking_version,
                            v_document.embedding_version,
                            v_document.page_count, v_document.chunk_count,
                            v_document.updated_at, v_uploader_user_id,
                            v_latest_job.id, v_latest_job.status,
                            v_latest_job.stage, v_latest_job.error,
                            v_latest_job.attempt,
                            v_latest_job.completed_units,
                            v_latest_job.total_units,
                            v_latest_job.updated_at
                        )::text,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            );
            IF v_current_snapshot_token <> p_snapshot_token THEN
                RETURN QUERY SELECT
                    'stale'::text, v_document.id, NULL::uuid;
                RETURN;
            END IF;
            DELETE FROM public.chunks
            WHERE chunks.document_id = p_document_id;
            UPDATE public.documents
            SET state = 'uploaded', stage = 'uploaded', error = NULL,
                page_count = NULL, chunk_count = 0,
                updated_at = statement_timestamp()
            WHERE documents.id = p_document_id;
            INSERT INTO public.ingestion_jobs (
                id, document_id, status, stage, attempt,
                completed_units, total_units
            ) VALUES (
                p_job_id, p_document_id, 'queued', 'uploaded', 0, 0, NULL
            );
            PERFORM public.v4_append_audit(
                'document_reingest_queued',
                'document',
                p_document_id,
                jsonb_build_object(
                    'job_id', p_job_id,
                    'job_status', 'queued',
                    'previous_job_id', v_latest_job.id,
                    'previous_job_status', v_latest_job.status
                )
            );
            RETURN QUERY SELECT
                'created'::text, p_document_id, p_job_id;
        END;
        $$;

        REVOKE ALL ON FUNCTION
            public.v4_prepare_document_reingest(uuid)
            FROM PUBLIC, rag_worker, rag_maintenance, rag_backup, rag_migrator;
        REVOKE ALL ON FUNCTION
            public.v4_commit_document_reingest(uuid, text, uuid)
            FROM PUBLIC, rag_worker, rag_maintenance, rag_backup, rag_migrator;
        GRANT EXECUTE ON FUNCTION
            public.v4_prepare_document_reingest(uuid) TO rag_api;
        GRANT EXECUTE ON FUNCTION
            public.v4_commit_document_reingest(uuid, text, uuid) TO rag_api;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.v4_get_job(p_job_id uuid)
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
              AND (
                  public.v4_current_actor_is_admin()
                  OR EXISTS (
                      SELECT 1
                      FROM public.library_nodes AS node
                      WHERE node.document_id = job.document_id
                        AND node.kind = 'file'
                        AND node.uploader_user_id =
                            public.v4_current_actor_id()
                  )
              )
        $$;

        CREATE OR REPLACE FUNCTION public.v4_schema_revision()
        RETURNS text
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ SELECT '0005_document_reingest_action'::text $$;

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
            SELECT '0005_document_reingest_action'::text,
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
                       WHERE version_num = '0005_document_reingest_action'
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
                       SELECT count(*) = 11
                       FROM pg_catalog.pg_proc AS routine
                       JOIN pg_catalog.pg_roles AS owner
                         ON owner.oid = routine.proowner
                       WHERE routine.oid IN (
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
                       SELECT count(*) = 2
                       FROM pg_catalog.pg_proc AS routine
                       WHERE routine.oid IN (
                           'public.v4_prepare_document_reingest(uuid)'::regprocedure,
                           'public.v4_commit_document_reingest(uuid,text,uuid)'::regprocedure
                       )
                         AND routine.prosecdef
                         AND routine.proconfig =
                             ARRAY['search_path=pg_catalog']::text[]
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
    raise RuntimeError("0005_document_reingest_action is forward-only")
