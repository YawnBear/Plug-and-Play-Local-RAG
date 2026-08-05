"""Add immutable V5 citation highlight evidence."""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_v5_citation_highlights"
down_revision: str | None = "0001_v4_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL ROLE rag_owner")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.documents)
               OR EXISTS (SELECT 1 FROM public.chunks)
               OR EXISTS (SELECT 1 FROM public.turn_sources) THEN
                RAISE EXCEPTION
                    'V5 citation anchors require an empty document/source state'
                    USING ERRCODE = '55000';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.v5_is_valid_highlight_anchor(
            p_anchor jsonb,
            p_page_start integer,
            p_page_end integer
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_page jsonb;
            v_region jsonb;
            v_page_number integer;
            v_exact text;
            v_prefix text;
            v_suffix text;
            v_x double precision;
            v_y double precision;
            v_width double precision;
            v_height double precision;
            v_seen integer[] := ARRAY[]::integer[];
        BEGIN
            IF jsonb_typeof(p_anchor) <> 'object'
               OR p_anchor->>'version' <> '1'
               OR p_anchor->>'normalization' <> 'citation-highlight-v1'
               OR jsonb_typeof(p_anchor->'pages') <> 'array'
               OR jsonb_array_length(p_anchor->'pages') NOT BETWEEN 1 AND 32
               OR p_page_start < 1 OR p_page_end < p_page_start THEN
                RETURN false;
            END IF;
            FOR v_page IN SELECT value FROM jsonb_array_elements(p_anchor->'pages')
            LOOP
                IF jsonb_typeof(v_page) <> 'object'
                   OR coalesce(v_page->>'page', '') !~ '^[0-9]+$' THEN
                    RETURN false;
                END IF;
                v_page_number := (v_page->>'page')::integer;
                IF v_page_number < p_page_start OR v_page_number > p_page_end
                   OR v_page_number = ANY(v_seen) THEN
                    RETURN false;
                END IF;
                v_seen := array_append(v_seen, v_page_number);
                IF v_page->>'kind' = 'text_quote' THEN
                    IF jsonb_typeof(v_page->'selector') <> 'object'
                       OR v_page ? 'regions' THEN
                        RETURN false;
                    END IF;
                    v_exact := v_page->'selector'->>'exact';
                    v_prefix := v_page->'selector'->>'prefix';
                    v_suffix := v_page->'selector'->>'suffix';
                    IF coalesce(char_length(v_exact), 0) NOT BETWEEN 1 AND 16000
                       OR coalesce(char_length(v_prefix), -1) NOT BETWEEN 0 AND 256
                       OR coalesce(char_length(v_suffix), -1) NOT BETWEEN 0 AND 256
                       OR coalesce(v_page->'selector'->>'sha256', '')
                            !~ '^[0-9a-f]{64}$'
                       OR encode(
                            public.digest(convert_to(v_exact, 'UTF8'), 'sha256'),
                            'hex'
                          ) <> v_page->'selector'->>'sha256' THEN
                        RETURN false;
                    END IF;
                ELSIF v_page->>'kind' = 'ocr_regions' THEN
                    IF v_page ? 'selector'
                       OR jsonb_typeof(v_page->'regions') <> 'array'
                       OR jsonb_array_length(v_page->'regions')
                            NOT BETWEEN 1 AND 256 THEN
                        RETURN false;
                    END IF;
                    FOR v_region IN
                        SELECT value FROM jsonb_array_elements(v_page->'regions')
                    LOOP
                        IF jsonb_typeof(v_region) <> 'object'
                           OR jsonb_typeof(v_region->'x') <> 'number'
                           OR jsonb_typeof(v_region->'y') <> 'number'
                           OR jsonb_typeof(v_region->'width') <> 'number'
                           OR jsonb_typeof(v_region->'height') <> 'number' THEN
                            RETURN false;
                        END IF;
                        v_x := (v_region->>'x')::double precision;
                        v_y := (v_region->>'y')::double precision;
                        v_width := (v_region->>'width')::double precision;
                        v_height := (v_region->>'height')::double precision;
                        IF v_x < 0 OR v_y < 0 OR v_width <= 0 OR v_height <= 0
                           OR v_x + v_width > 1 OR v_y + v_height > 1 THEN
                            RETURN false;
                        END IF;
                    END LOOP;
                ELSE
                    RETURN false;
                END IF;
            END LOOP;
            RETURN true;
        EXCEPTION WHEN OTHERS THEN
            RETURN false;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE public.chunks
            ADD COLUMN highlight_anchor jsonb NOT NULL,
            ADD CONSTRAINT ck_chunks_highlight_anchor
            CHECK (
                public.v5_is_valid_highlight_anchor(
                    highlight_anchor, page_start, page_end
                )
            );
        ALTER TABLE public.turn_sources
            ADD COLUMN highlight_anchor jsonb NOT NULL,
            ADD CONSTRAINT ck_turn_sources_highlight_anchor
            CHECK (
                public.v5_is_valid_highlight_anchor(
                    highlight_anchor, page_start, page_end
                )
            );
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.v4_enforce_turn_source_immutability()
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
                NEW.snapshot_text, NEW.highlight_anchor,
                NEW.token_count, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.turn_id, OLD.rank, OLD.label,
                OLD.document_id_snapshot, OLD.chunk_id_snapshot,
                OLD.original_filename, OLD.display_name, OLD.logical_path,
                OLD.page_start, OLD.page_end, OLD.section,
                OLD.source_sha256, OLD.text_sha256,
                OLD.retrieval_distance, OLD.rerank_score,
                OLD.snapshot_text, OLD.highlight_anchor,
                OLD.token_count, OLD.created_at
            ) THEN
                RAISE EXCEPTION 'turn source snapshots are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.document_id IS DISTINCT FROM OLD.document_id AND NOT (
                OLD.document_id IS NOT NULL AND NEW.document_id IS NULL
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
        $$;
        """
    )
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
        CREATE OR REPLACE FUNCTION public.v4_store_turn_sources(
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
            FROM public.security_epochs WHERE singleton FOR SHARE;
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
                snapshot_text, highlight_anchor, token_count
            )
            SELECT p_turn_id, requested.rank, 'S' || requested.rank::text,
                   document.id, chunk.id, document.id, chunk.id,
                   document.original_filename, node.name,
                   location.logical_path, chunk.page_start, chunk.page_end,
                   chunk.section, chunk.source_sha256, chunk.text_sha256,
                   requested.retrieval_distance, requested.rerank_score,
                   chunk.text, chunk.highlight_anchor, chunk.token_count
            FROM jsonb_to_recordset(p_sources) AS requested(
                rank smallint, chunk_id uuid,
                retrieval_distance double precision,
                rerank_score double precision
            )
            JOIN public.chunks AS chunk ON chunk.id = requested.chunk_id
            JOIN public.documents AS document ON document.id = chunk.document_id
            JOIN public.library_nodes AS node ON node.document_id = document.id
            CROSS JOIN LATERAL (
                WITH RECURSIVE ancestry AS (
                    SELECT current.id, current.parent_id, current.name, 0 AS depth
                    FROM public.library_nodes AS current WHERE current.id = node.id
                    UNION ALL
                    SELECT parent.id, parent.parent_id, parent.name, child.depth + 1
                    FROM public.library_nodes AS parent
                    JOIN ancestry AS child ON parent.id = child.parent_id
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
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.v5_citation_evidence(
            p_chat_id uuid,
            p_turn_id uuid,
            p_source_rank smallint
        )
        RETURNS TABLE (
            label text,
            rank smallint,
            document_id uuid,
            display_name text,
            logical_path text,
            page_start integer,
            page_end integer,
            section text,
            parse_method text,
            snapshot_text text,
            highlight_anchor jsonb,
            source_sha256 text,
            text_sha256 text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT source.label::text, source.rank, source.document_id,
                   source.display_name::text, source.logical_path,
                   source.page_start, source.page_end, source.section,
                   chunk.parse_method::text, source.snapshot_text,
                   source.highlight_anchor, source.source_sha256::text,
                   source.text_sha256::text
            FROM public.turn_sources AS source
            JOIN public.turn_citations AS citation
              ON citation.turn_id = source.turn_id
             AND citation.source_rank = source.rank
            JOIN public.chat_turns AS turn_row ON turn_row.id = source.turn_id
            JOIN public.chats AS chat ON chat.id = turn_row.chat_id
            JOIN public.documents AS document ON document.id = source.document_id
            JOIN public.chunks AS chunk ON chunk.id = source.chunk_id
            WHERE chat.id = p_chat_id
              AND chat.owner_user_id = public.v4_current_actor_id()
              AND turn_row.id = p_turn_id
              AND turn_row.status = 'complete'
              AND source.rank = p_source_rank
              AND source.document_id = source.document_id_snapshot
              AND source.chunk_id = source.chunk_id_snapshot
              AND chunk.document_id = document.id
              AND chunk.source_sha256 = source.source_sha256
              AND chunk.text_sha256 = source.text_sha256
              AND document.state = 'ready'
              AND public.v4_can_read_document(document.id)
            LIMIT 1
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
        AS $$ SELECT '0002_v5_citation_highlights'::text $$;

        CREATE FUNCTION public.v5_readiness()
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
            SELECT '0002_v5_citation_highlights'::text,
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
                       WHERE version_num = '0002_v5_citation_highlights'
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
    op.execute(
        """
        REVOKE ALL ON FUNCTION
            public.v5_is_valid_highlight_anchor(jsonb,integer,integer),
            public.v5_citation_evidence(uuid,uuid,smallint),
            public.v5_readiness()
        FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
            public.v5_citation_evidence(uuid,uuid,smallint)
        TO rag_api;
        GRANT EXECUTE ON FUNCTION public.v5_readiness()
        TO rag_api, rag_maintenance;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "V5 citation evidence is forward-only; restore a paired V4 backup instead"
    )
