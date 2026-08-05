"""Require committed chunks to match their document ingestion identity."""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_v6_ingestion_version_guard"
down_revision: str | None = "0002_v5_citation_highlights"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL ROLE rag_owner")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.v4_commit_ingestion_job(
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
            IF v_document_id IS NULL OR p_page_count < 0
               OR jsonb_typeof(p_chunks) <> 'array' THEN
                RETURN false;
            END IF;
            IF EXISTS (
                SELECT 1
                FROM jsonb_to_recordset(p_chunks) AS source(
                    parser_version text,
                    chunking_version text,
                    embedding_version text
                )
                JOIN public.documents AS document
                  ON document.id = v_document_id
                WHERE source.parser_version IS DISTINCT FROM
                          document.parser_version
                   OR source.chunking_version IS DISTINCT FROM
                          document.chunking_version
                   OR source.embedding_version IS DISTINCT FROM
                          document.embedding_version
            ) THEN
                RAISE EXCEPTION
                    'chunk ingestion version does not match document version'
                    USING ERRCODE = '22023';
            END IF;
            DELETE FROM public.chunks WHERE document_id = v_document_id;
            INSERT INTO public.chunks (
                id, document_id, ordinal, filename, page_start, page_end,
                section, text, token_count, text_sha256, source_sha256,
                parse_method, parser_version, chunking_version,
                embedding_version, schema_version, citation_label,
                highlight_anchor, embedding
            )
            SELECT source.id, v_document_id, source.ordinal,
                   source.filename, source.page_start, source.page_end,
                   source.section, source.text, source.token_count,
                   source.text_sha256, source.source_sha256,
                   source.parse_method, source.parser_version,
                   source.chunking_version, source.embedding_version,
                   source.schema_version, source.citation_label,
                   source.highlight_anchor, source.embedding::public.vector
            FROM jsonb_to_recordset(p_chunks) AS source(
                id uuid, ordinal integer, filename text,
                page_start integer, page_end integer, section text,
                text text, token_count integer, text_sha256 text,
                source_sha256 text, parse_method text,
                parser_version text, chunking_version text,
                embedding_version text, schema_version text,
                citation_label text, highlight_anchor jsonb, embedding text
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
        $$;
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
        AS $$ SELECT '0003_v6_ingestion_version_guard'::text $$;

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
            SELECT '0003_v6_ingestion_version_guard'::text,
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
                       WHERE version_num = '0003_v6_ingestion_version_guard'
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
                       SELECT count(*) = 6
                       FROM pg_catalog.pg_proc AS routine
                       JOIN pg_catalog.pg_roles AS owner
                         ON owner.oid = routine.proowner
                       WHERE routine.oid IN (
                           'public.v4_commit_ingestion_job(uuid,uuid,bigint,integer,jsonb)'::regprocedure,
                           'public.v4_store_turn_sources(uuid,uuid,jsonb)'::regprocedure,
                           'public.v4_enforce_turn_source_immutability()'::regprocedure,
                           'public.v5_is_valid_highlight_anchor(jsonb,integer,integer)'::regprocedure,
                           'public.v5_citation_evidence(uuid,uuid,smallint)'::regprocedure,
                           'public.v5_readiness()'::regprocedure
                       )
                         AND owner.rolname = 'rag_owner'
                   )
                   AND has_function_privilege(
                       'rag_api',
                       'public.v5_citation_evidence(uuid,uuid,smallint)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'public',
                       'public.v5_citation_evidence(uuid,uuid,smallint)',
                       'EXECUTE'
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
    raise RuntimeError(
        "V6 ingestion version identity is forward-only; "
        "restore a paired pre-V6 backup instead"
    )
