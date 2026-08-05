"""Add V8E versioned parser reingestion and shadow embedding generations.

This migration is forward-only because query selection, ingestion commits, and
the reprocessing worker advance atomically with this schema contract.
"""

# ruff: noqa: E501 -- SQL contracts remain line-oriented for source audits.

from collections.abc import Sequence

from alembic import op

revision: str = "0010_versioned_reprocessing"
down_revision: str | None = "0009_runtime_configuration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL ROLE rag_owner")
    _create_generation_schema()
    _replace_ingestion_contracts()
    _create_admin_contracts()
    _create_worker_contracts()
    _secure_contracts()


def _create_generation_schema() -> None:
    op.execute(
        """
        ALTER TABLE public.system_reauthentication_grants
            DROP CONSTRAINT ck_system_reauth_action;
        ALTER TABLE public.system_reauthentication_grants
            ADD CONSTRAINT ck_system_reauth_action CHECK (
                action IN ('apply_runtime_configuration', 'start_reprocessing')
            );

        CREATE TABLE public.ingestion_version_configuration (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            revision_id text NOT NULL UNIQUE,
            parser_profile_id text NOT NULL,
            parser_version text NOT NULL,
            chunking_version text NOT NULL,
            updated_by uuid REFERENCES public.users(id),
            updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            CONSTRAINT ck_ingestion_version_profile CHECK (
                (parser_profile_id = 'parser.paddleocr-vl-1.6.adaptive-v2'
                 AND parser_version = 'pypdf+paddleocr-vl-v1.6-adaptive-v2'
                 AND chunking_version = 'fragment-paragraph-sentence-v2')
                OR
                (parser_profile_id = 'parser.paddleocr-vl-1.6.legacy-v1'
                 AND parser_version = 'pypdf+paddleocr-vl-v1.6'
                 AND chunking_version = 'paragraph-sentence-v1')
            )
        );
        INSERT INTO public.ingestion_version_configuration (
            singleton, revision_id, parser_profile_id, parser_version,
            chunking_version
        ) VALUES (
            true, 'v8e-ingestion-initial',
            'parser.paddleocr-vl-1.6.adaptive-v2',
            'pypdf+paddleocr-vl-v1.6-adaptive-v2',
            'fragment-paragraph-sentence-v2'
        );

        CREATE TABLE public.reprocessing_previews (
            id uuid PRIMARY KEY DEFAULT public.gen_random_uuid(),
            actor_user_id uuid NOT NULL REFERENCES public.users(id),
            session_id uuid NOT NULL REFERENCES public.sessions(id),
            operation_type text NOT NULL CHECK (
                operation_type IN ('reindex', 'reingestion')
            ),
            target_profile_id text NOT NULL,
            source_parser_version text,
            target_parser_version text,
            target_chunking_version text,
            target_embedding_version text,
            target_dimension integer,
            document_count integer NOT NULL CHECK (document_count >= 0),
            chunk_count integer NOT NULL CHECK (chunk_count >= 0),
            estimated_bytes bigint NOT NULL CHECK (estimated_bytes >= 0),
            impact_digest text NOT NULL CHECK (impact_digest ~ '^[0-9a-f]{64}$'),
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            CONSTRAINT ck_reprocessing_preview_expiry CHECK (expires_at > created_at),
            CONSTRAINT ck_reprocessing_preview_target CHECK (
                (operation_type = 'reindex'
                 AND target_parser_version IS NULL
                 AND target_chunking_version IS NULL
                 AND target_embedding_version IS NOT NULL
                 AND target_dimension BETWEEN 1 AND 4096)
                OR
                (operation_type = 'reingestion'
                 AND target_parser_version IS NOT NULL
                 AND target_chunking_version IS NOT NULL
                 AND target_embedding_version IS NULL
                 AND target_dimension IS NULL)
            )
        );

        CREATE TABLE public.reprocessing_operations (
            id uuid PRIMARY KEY DEFAULT public.gen_random_uuid(),
            preview_id uuid NOT NULL UNIQUE REFERENCES public.reprocessing_previews(id),
            actor_user_id uuid NOT NULL REFERENCES public.users(id),
            backup_run_id uuid NOT NULL REFERENCES public.backup_runs(id),
            operation_type text NOT NULL CHECK (
                operation_type IN ('reindex', 'reingestion')
            ),
            state text NOT NULL DEFAULT 'running' CHECK (
                state IN ('running','paused','qualifying','succeeded',
                          'failed','cancelled')
            ),
            stage text NOT NULL DEFAULT 'processing' CHECK (
                stage IN ('queued','processing','paused','qualifying','cutover',
                          'succeeded','failed','cancelled')
            ),
            target_profile_id text NOT NULL,
            source_parser_version text,
            target_parser_version text,
            target_chunking_version text,
            target_embedding_version text,
            target_dimension integer,
            impact_digest text NOT NULL CHECK (impact_digest ~ '^[0-9a-f]{64}$'),
            operation_generation_id uuid,
            total_documents integer NOT NULL CHECK (total_documents >= 0),
            completed_documents integer NOT NULL DEFAULT 0 CHECK (
                completed_documents >= 0 AND completed_documents <= total_documents
            ),
            failed_documents integer NOT NULL DEFAULT 0 CHECK (
                failed_documents >= 0 AND failed_documents <= total_documents
            ),
            total_chunks integer NOT NULL CHECK (total_chunks >= 0),
            completed_chunks integer NOT NULL DEFAULT 0 CHECK (completed_chunks >= 0),
            reason_code text,
            qualification jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
                jsonb_typeof(qualification) = 'object'
            ),
            lease_owner text,
            lease_token uuid,
            lease_expires_at timestamptz,
            fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            started_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            finished_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            CONSTRAINT ck_reprocessing_operation_terminal CHECK (
                (state IN ('succeeded','failed','cancelled') AND finished_at IS NOT NULL)
                OR (state NOT IN ('succeeded','failed','cancelled') AND finished_at IS NULL)
            )
        );
        CREATE UNIQUE INDEX uq_reprocessing_active_operation
            ON public.reprocessing_operations ((true))
            WHERE state IN ('running','paused','qualifying');
        CREATE INDEX ix_reprocessing_operations_created
            ON public.reprocessing_operations (created_at DESC, id DESC);

        CREATE TABLE public.document_generations (
            id uuid PRIMARY KEY DEFAULT public.gen_random_uuid(),
            document_id uuid NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
            operation_id uuid REFERENCES public.reprocessing_operations(id),
            parser_version text NOT NULL,
            chunking_version text NOT NULL,
            state text NOT NULL CHECK (
                state IN ('building','ready','failed','retained','abandoned')
            ),
            page_count integer CHECK (page_count IS NULL OR page_count >= 0),
            chunk_count integer NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
            error text,
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            ready_at timestamptz,
            retired_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            UNIQUE (id, document_id),
            CONSTRAINT ck_document_generation_result CHECK (
                (state = 'ready' AND ready_at IS NOT NULL AND error IS NULL)
                OR (state = 'retained' AND ready_at IS NOT NULL
                    AND retired_at IS NOT NULL AND error IS NULL)
                OR (state = 'failed' AND error IS NOT NULL)
                OR (state IN ('building','abandoned'))
            )
        );
        CREATE INDEX ix_document_generations_document
            ON public.document_generations (document_id, created_at DESC);
        CREATE INDEX ix_document_generations_operation
            ON public.document_generations (operation_id) WHERE operation_id IS NOT NULL;

        ALTER TABLE public.documents ADD COLUMN active_generation_id uuid;
        INSERT INTO public.document_generations (
            document_id, parser_version, chunking_version, state,
            page_count, chunk_count, error, ready_at
        )
        SELECT document.id, document.parser_version, document.chunking_version,
               CASE WHEN document.state = 'ready' THEN 'ready'
                    WHEN document.state = 'failed' THEN 'failed'
                    ELSE 'building' END,
               document.page_count, document.chunk_count, document.error,
               CASE WHEN document.state = 'ready' THEN document.updated_at END
        FROM public.documents AS document;
        UPDATE public.documents AS document
        SET active_generation_id = generation.id
        FROM public.document_generations AS generation
        WHERE generation.document_id = document.id;
        ALTER TABLE public.documents ALTER COLUMN active_generation_id SET NOT NULL;
        ALTER TABLE public.documents ADD CONSTRAINT fk_documents_active_generation
            FOREIGN KEY (active_generation_id, id)
            REFERENCES public.document_generations(id, document_id)
            DEFERRABLE INITIALLY DEFERRED;

        ALTER TABLE public.chunks ADD COLUMN document_generation_id uuid;
        UPDATE public.chunks AS chunk
        SET document_generation_id = document.active_generation_id
        FROM public.documents AS document
        WHERE document.id = chunk.document_id;
        ALTER TABLE public.chunks ALTER COLUMN document_generation_id SET NOT NULL;
        DROP INDEX public.uq_chunks_document_ordinal;
        CREATE UNIQUE INDEX uq_chunks_generation_ordinal
            ON public.chunks (document_generation_id, ordinal);
        CREATE INDEX ix_chunks_document_generation
            ON public.chunks (document_generation_id);
        ALTER TABLE public.chunks ADD CONSTRAINT fk_chunks_document_generation
            FOREIGN KEY (document_generation_id, document_id)
            REFERENCES public.document_generations(id, document_id)
            ON DELETE CASCADE;
        ALTER TABLE public.chunks ALTER COLUMN embedding DROP NOT NULL;

        ALTER TABLE public.ingestion_jobs ADD COLUMN target_generation_id uuid;
        ALTER TABLE public.ingestion_jobs ADD COLUMN reprocessing_operation_id uuid
            REFERENCES public.reprocessing_operations(id);
        UPDATE public.ingestion_jobs AS job
        SET target_generation_id = document.active_generation_id
        FROM public.documents AS document
        WHERE document.id = job.document_id;
        ALTER TABLE public.ingestion_jobs ALTER COLUMN target_generation_id SET NOT NULL;
        ALTER TABLE public.ingestion_jobs ADD CONSTRAINT fk_ingestion_target_generation
            FOREIGN KEY (target_generation_id, document_id)
            REFERENCES public.document_generations(id, document_id)
            ON DELETE CASCADE;
        CREATE INDEX ix_ingestion_jobs_reprocessing
            ON public.ingestion_jobs (reprocessing_operation_id)
            WHERE reprocessing_operation_id IS NOT NULL;

        CREATE TABLE public.embedding_generations (
            id uuid PRIMARY KEY DEFAULT public.gen_random_uuid(),
            operation_id uuid UNIQUE REFERENCES public.reprocessing_operations(id),
            profile_id text NOT NULL,
            embedding_version text NOT NULL,
            dimension integer NOT NULL CHECK (dimension BETWEEN 1 AND 4096),
            state text NOT NULL CHECK (
                state IN ('building','qualified','active','retained','abandoned')
            ),
            source_generation_id uuid REFERENCES public.embedding_generations(id),
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            qualified_at timestamptz,
            activated_at timestamptz,
            retired_at timestamptz,
            qualification jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
                jsonb_typeof(qualification) = 'object'
            ),
            CONSTRAINT ck_embedding_generation_state CHECK (
                (state = 'active' AND activated_at IS NOT NULL)
                OR (state = 'qualified' AND qualified_at IS NOT NULL)
                OR (state = 'retained' AND activated_at IS NOT NULL
                    AND retired_at IS NOT NULL)
                OR state IN ('building','abandoned')
            )
        );
        CREATE UNIQUE INDEX uq_embedding_generation_active
            ON public.embedding_generations ((true)) WHERE state = 'active';

        CREATE TABLE public.embedding_generation_state (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            active_generation_id uuid NOT NULL
                REFERENCES public.embedding_generations(id)
        );
        WITH baseline AS (
            INSERT INTO public.embedding_generations (
                profile_id, embedding_version, dimension, state,
                qualified_at, activated_at,
                qualification
            ) VALUES (
                'embedding.qwen3-0.6b-1024.ollama.windows-x64',
                'qwen3-embedding-0.6b-1024', 1024, 'active',
                statement_timestamp(), statement_timestamp(),
                jsonb_build_object('migration_backfill', true)
            ) RETURNING id
        )
        INSERT INTO public.embedding_generation_state (singleton, active_generation_id)
        SELECT true, id FROM baseline;

        CREATE TABLE public.chunk_embeddings (
            embedding_generation_id uuid NOT NULL
                REFERENCES public.embedding_generations(id) ON DELETE CASCADE,
            chunk_id uuid NOT NULL REFERENCES public.chunks(id) ON DELETE CASCADE,
            document_generation_id uuid NOT NULL
                REFERENCES public.document_generations(id) ON DELETE CASCADE,
            dimension integer NOT NULL CHECK (dimension BETWEEN 1 AND 4096),
            embedding public.vector NOT NULL,
            text_sha256 text NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
            provenance_sha256 text NOT NULL CHECK (
                provenance_sha256 ~ '^[0-9a-f]{64}$'
            ),
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            PRIMARY KEY (embedding_generation_id, chunk_id),
            CONSTRAINT ck_chunk_embedding_dimension CHECK (
                public.vector_dims(embedding) = dimension
            )
        );
        CREATE INDEX ix_chunk_embeddings_chunk ON public.chunk_embeddings (chunk_id);
        CREATE INDEX ix_chunk_embeddings_document_generation
            ON public.chunk_embeddings (document_generation_id);
        CREATE INDEX ix_chunk_embeddings_1024_hnsw
            ON public.chunk_embeddings
            USING hnsw ((embedding::public.vector(1024)) public.vector_cosine_ops)
            WHERE dimension = 1024;
        INSERT INTO public.chunk_embeddings (
            embedding_generation_id, chunk_id, document_generation_id,
            dimension, embedding, text_sha256, provenance_sha256
        )
        SELECT state.active_generation_id, chunk.id,
               chunk.document_generation_id, 1024, chunk.embedding::public.vector,
               chunk.text_sha256,
               encode(public.digest(concat_ws('|', chunk.id::text,
                   chunk.text_sha256, chunk.document_generation_id::text,
                   chunk.embedding_version), 'sha256'), 'hex')
        FROM public.chunks AS chunk
        CROSS JOIN public.embedding_generation_state AS state;

        CREATE TABLE public.reindex_tasks (
            operation_id uuid NOT NULL REFERENCES public.reprocessing_operations(id)
                ON DELETE CASCADE,
            generation_id uuid NOT NULL REFERENCES public.embedding_generations(id)
                ON DELETE CASCADE,
            chunk_id uuid NOT NULL REFERENCES public.chunks(id) ON DELETE CASCADE,
            state text NOT NULL DEFAULT 'queued' CHECK (
                state IN ('queued','running','completed','failed')
            ),
            attempt integer NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 3),
            lease_owner text,
            lease_token uuid,
            lease_expires_at timestamptz,
            fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
            error text,
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            PRIMARY KEY (operation_id, chunk_id)
        );
        CREATE INDEX ix_reindex_tasks_queue
            ON public.reindex_tasks (state, created_at);

        ALTER TABLE public.chat_turns ADD COLUMN embedding_generation_id uuid;
        UPDATE public.chat_turns
        SET embedding_generation_id = state.active_generation_id
        FROM public.embedding_generation_state AS state;
        ALTER TABLE public.chat_turns ALTER COLUMN embedding_generation_id SET NOT NULL;
        ALTER TABLE public.chat_turns ADD CONSTRAINT fk_chat_turn_embedding_generation
            FOREIGN KEY (embedding_generation_id)
            REFERENCES public.embedding_generations(id);

        CREATE FUNCTION public.v10_bind_turn_embedding_generation()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            SELECT state.active_generation_id INTO NEW.embedding_generation_id
            FROM public.embedding_generation_state AS state WHERE state.singleton;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_chat_turn_embedding_generation
            BEFORE INSERT ON public.chat_turns
            FOR EACH ROW EXECUTE FUNCTION public.v10_bind_turn_embedding_generation();

        CREATE FUNCTION public.v10_prepare_document_generation()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            IF NEW.active_generation_id IS NULL THEN
                NEW.active_generation_id := public.gen_random_uuid();
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_prepare_document_generation
            BEFORE INSERT ON public.documents
            FOR EACH ROW EXECUTE FUNCTION public.v10_prepare_document_generation();

        CREATE FUNCTION public.v10_create_document_generation()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            INSERT INTO public.document_generations (
                id, document_id, parser_version, chunking_version, state,
                page_count, chunk_count, error, ready_at
            ) VALUES (
                NEW.active_generation_id, NEW.id, NEW.parser_version,
                NEW.chunking_version,
                CASE WHEN NEW.state = 'ready' THEN 'ready'
                     WHEN NEW.state = 'failed' THEN 'failed'
                     ELSE 'building' END,
                NEW.page_count, NEW.chunk_count, NEW.error,
                CASE WHEN NEW.state = 'ready' THEN NEW.updated_at END
            );
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_create_document_generation
            AFTER INSERT ON public.documents
            FOR EACH ROW EXECUTE FUNCTION public.v10_create_document_generation();

        CREATE FUNCTION public.v10_bind_ingestion_target_generation()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            IF NEW.target_generation_id IS NULL THEN
                SELECT document.active_generation_id
                INTO STRICT NEW.target_generation_id
                FROM public.documents AS document
                WHERE document.id = NEW.document_id;
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_bind_ingestion_target_generation
            BEFORE INSERT ON public.ingestion_jobs
            FOR EACH ROW EXECUTE FUNCTION public.v10_bind_ingestion_target_generation();

        CREATE FUNCTION public.v10_effective_ingestion_version()
        RETURNS TABLE (
            revision_id text, parser_profile_id text, parser_version text,
            chunking_version text, embedding_profile_id text,
            embedding_version text, embedding_dimension integer
        ) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
            SELECT config.revision_id, config.parser_profile_id,
                   config.parser_version, config.chunking_version,
                   active.profile_id, active.embedding_version, active.dimension
            FROM public.ingestion_version_configuration AS config
            CROSS JOIN public.embedding_generation_state AS state
            JOIN public.embedding_generations AS active
              ON active.id = state.active_generation_id
            WHERE config.singleton
        $$;

        CREATE FUNCTION public.v10_retrieve_active_chunks(
            p_query public.vector, p_limit integer, p_document_ids uuid[]
        ) RETURNS TABLE (chunk_id uuid, distance double precision)
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE active_dimension integer;
        BEGIN
            IF p_limit NOT BETWEEN 1 AND 100 THEN
                RAISE EXCEPTION 'invalid retrieval limit' USING ERRCODE = '22023';
            END IF;
            SELECT generation.dimension INTO STRICT active_dimension
            FROM public.embedding_generation_state AS state
            JOIN public.embedding_generations AS generation
              ON generation.id = state.active_generation_id
            WHERE state.singleton;
            IF public.vector_dims(p_query) <> active_dimension THEN
                RAISE EXCEPTION 'query embedding dimension does not match active generation'
                    USING ERRCODE = '22023';
            END IF;
            IF active_dimension <> 1024 THEN
                RAISE EXCEPTION 'active embedding dimension is not release-qualified'
                    USING ERRCODE = '55000';
            END IF;
            RETURN QUERY
            SELECT chunk.id,
                   (item.embedding::public.vector(1024)
                        OPERATOR(public.<=>)
                        p_query::public.vector(1024))::double precision
            FROM public.embedding_generation_state AS state
            JOIN public.chunk_embeddings AS item
              ON item.embedding_generation_id = state.active_generation_id
             AND item.dimension = active_dimension
            JOIN public.chunks AS chunk ON chunk.id = item.chunk_id
            JOIN public.documents AS document
              ON document.id = chunk.document_id
             AND document.active_generation_id = chunk.document_generation_id
            WHERE document.state = 'ready'
              AND public.v4_can_read_document(document.id)
              AND (p_document_ids IS NULL OR document.id = ANY(p_document_ids))
            ORDER BY item.embedding::public.vector(1024)
                         OPERATOR(public.<=>)
                         p_query::public.vector(1024), chunk.id
            LIMIT p_limit;
        END;
        $$;
        """
    )


def _replace_ingestion_contracts() -> None:
    op.execute(
        """
        DROP FUNCTION public.v4_claim_ingestion_job(text, integer);
        CREATE FUNCTION public.v4_claim_ingestion_job(
            p_owner_id text, p_lease_seconds integer
        ) RETURNS TABLE (
            job_id uuid, document_id uuid, object_key text,
            original_filename text, sha256 text, byte_size bigint,
            attempt integer, lease_token uuid, fencing_token bigint,
            parser_version text, chunking_version text, embedding_version text
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
            IF p_lease_seconds NOT BETWEEN 5 AND 3600 THEN
                RAISE EXCEPTION 'invalid ingestion lease duration'
                    USING ERRCODE = '22023';
            END IF;
            RETURN QUERY
            WITH candidate AS (
                SELECT queued.id
                FROM public.ingestion_jobs AS queued
                LEFT JOIN public.reprocessing_operations AS operation
                  ON operation.id = queued.reprocessing_operation_id
                WHERE (
                    (queued.status = 'queued'
                     AND queued.available_at <= statement_timestamp())
                    OR
                    (queued.status = 'running'
                     AND queued.lease_expires_at <= statement_timestamp())
                )
                  AND (queued.reprocessing_operation_id IS NULL
                       OR operation.state = 'running')
                ORDER BY queued.available_at, queued.created_at, queued.id
                FOR UPDATE OF queued SKIP LOCKED LIMIT 1
            )
            UPDATE public.ingestion_jobs AS job
            SET status = 'running', attempt = job.attempt + 1,
                stage = 'uploaded', completed_units = 0, total_units = NULL,
                started_at = COALESCE(job.started_at, statement_timestamp()),
                heartbeat_at = statement_timestamp(), lease_owner = p_owner_id,
                lease_token = public.gen_random_uuid(),
                lease_expires_at = statement_timestamp()
                    + make_interval(secs => p_lease_seconds),
                fencing_token = job.fencing_token + 1,
                updated_at = statement_timestamp()
            FROM candidate, public.documents AS document,
                 public.document_generations AS generation,
                 public.embedding_generation_state AS embedding_state,
                 public.embedding_generations AS embedding_generation
            WHERE job.id = candidate.id AND document.id = job.document_id
              AND generation.id = job.target_generation_id
              AND generation.document_id = document.id
              AND embedding_generation.id = embedding_state.active_generation_id
            RETURNING job.id, document.id, document.object_key::text,
                document.original_filename::text, document.sha256::text,
                document.byte_size, job.attempt, job.lease_token,
                job.fencing_token, generation.parser_version::text,
                generation.chunking_version::text,
                embedding_generation.embedding_version::text;
        END;
        $$;

        CREATE OR REPLACE FUNCTION public.v4_update_ingestion_progress(
            p_job_id uuid, p_lease_token uuid, p_fencing_token bigint,
            p_stage text, p_completed_units integer, p_total_units integer
        ) RETURNS text
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE
            v_job public.ingestion_jobs%ROWTYPE;
            v_current integer;
            v_requested integer;
        BEGIN
            IF p_stage NOT IN ('parsing','chunking','embedding','indexing')
               OR p_total_units NOT BETWEEN 1 AND 1000000
               OR p_completed_units NOT BETWEEN 0 AND p_total_units THEN
                RAISE EXCEPTION 'invalid ingestion progress' USING ERRCODE = '22023';
            END IF;
            SELECT * INTO v_job FROM public.ingestion_jobs
            WHERE id = p_job_id FOR UPDATE;
            IF NOT FOUND OR v_job.status <> 'running'
               OR v_job.lease_token <> p_lease_token
               OR v_job.fencing_token <> p_fencing_token
               OR v_job.lease_expires_at <= statement_timestamp()
               OR (v_job.reprocessing_operation_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.reprocessing_operations AS operation
                    WHERE operation.id = v_job.reprocessing_operation_id
                      AND operation.state = 'running'
               )) THEN RETURN 'stale'; END IF;
            v_current := CASE v_job.stage WHEN 'uploaded' THEN 0 WHEN 'parsing' THEN 1
                WHEN 'chunking' THEN 2 WHEN 'embedding' THEN 3
                WHEN 'indexing' THEN 4 ELSE 5 END;
            v_requested := CASE p_stage WHEN 'parsing' THEN 1 WHEN 'chunking' THEN 2
                WHEN 'embedding' THEN 3 WHEN 'indexing' THEN 4 END;
            IF v_requested < v_current OR (v_requested = v_current AND
               (p_completed_units < v_job.completed_units OR
                (v_job.total_units IS NOT NULL
                 AND p_total_units < v_job.total_units))) THEN
                RETURN 'stale';
            END IF;
            UPDATE public.ingestion_jobs SET stage = p_stage,
                completed_units = p_completed_units, total_units = p_total_units,
                heartbeat_at = statement_timestamp(), updated_at = statement_timestamp()
            WHERE id = p_job_id;
            UPDATE public.documents SET state = p_stage, stage = p_stage,
                error = NULL, updated_at = statement_timestamp()
            WHERE id = v_job.document_id
              AND active_generation_id = v_job.target_generation_id
              AND state <> 'ready';
            RETURN 'accepted';
        END;
        $$;

        CREATE OR REPLACE FUNCTION public.v4_commit_ingestion_job(
            p_job_id uuid, p_lease_token uuid, p_fencing_token bigint,
            p_page_count integer, p_chunks jsonb
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE
            v_job public.ingestion_jobs%ROWTYPE;
            v_generation public.document_generations%ROWTYPE;
            v_embedding public.embedding_generations%ROWTYPE;
            v_previous uuid;
            v_chunk_count integer;
        BEGIN
            SELECT * INTO v_job FROM public.ingestion_jobs
            WHERE id = p_job_id AND status = 'running'
              AND lease_token = p_lease_token AND fencing_token = p_fencing_token
              AND lease_expires_at > statement_timestamp() FOR UPDATE;
            IF NOT FOUND OR p_page_count < 0 OR jsonb_typeof(p_chunks) <> 'array'
               OR (v_job.reprocessing_operation_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.reprocessing_operations AS operation
                    WHERE operation.id = v_job.reprocessing_operation_id
                      AND operation.state = 'running'
               )) THEN RETURN false; END IF;
            SELECT * INTO STRICT v_generation FROM public.document_generations
            WHERE id = v_job.target_generation_id
              AND document_id = v_job.document_id FOR UPDATE;
            SELECT generation.* INTO STRICT v_embedding
            FROM public.embedding_generation_state AS state
            JOIN public.embedding_generations AS generation
              ON generation.id = state.active_generation_id FOR SHARE;
            IF EXISTS (
                SELECT 1 FROM jsonb_to_recordset(p_chunks) AS source(
                    parser_version text, chunking_version text,
                    embedding_version text, embedding text
                ) WHERE source.parser_version <> v_generation.parser_version
                   OR source.chunking_version <> v_generation.chunking_version
                   OR source.embedding_version <> v_embedding.embedding_version
                   OR public.vector_dims(source.embedding::public.vector)
                        <> v_embedding.dimension
            ) THEN
                RAISE EXCEPTION 'chunk ingestion version does not match target generation'
                    USING ERRCODE = '22023';
            END IF;
            DELETE FROM public.chunks
            WHERE document_generation_id = v_generation.id;
            INSERT INTO public.chunks (
                id, document_id, document_generation_id, ordinal, filename,
                page_start, page_end, section, text, token_count, text_sha256,
                source_sha256, parse_method, parser_version, chunking_version,
                embedding_version, schema_version, citation_label,
                highlight_anchor, embedding
            )
            SELECT source.id, v_job.document_id, v_generation.id, source.ordinal,
                source.filename, source.page_start, source.page_end, source.section,
                source.text, source.token_count, source.text_sha256,
                source.source_sha256, source.parse_method, source.parser_version,
                source.chunking_version, source.embedding_version,
                source.schema_version, source.citation_label,
                source.highlight_anchor,
                CASE WHEN v_embedding.dimension = 1024
                     THEN source.embedding::public.vector END
            FROM jsonb_to_recordset(p_chunks) AS source(
                id uuid, ordinal integer, filename text, page_start integer,
                page_end integer, section text, text text, token_count integer,
                text_sha256 text, source_sha256 text, parse_method text,
                parser_version text, chunking_version text,
                embedding_version text, schema_version text,
                citation_label text, highlight_anchor jsonb, embedding text
            );
            GET DIAGNOSTICS v_chunk_count = ROW_COUNT;
            IF v_chunk_count <> jsonb_array_length(p_chunks) THEN
                RAISE EXCEPTION 'chunk payload count changed' USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.chunk_embeddings (
                embedding_generation_id, chunk_id, document_generation_id,
                dimension, embedding, text_sha256, provenance_sha256
            )
            SELECT v_embedding.id, source.id, v_generation.id,
                v_embedding.dimension, source.embedding::public.vector,
                source.text_sha256,
                encode(public.digest(concat_ws('|', source.id::text,
                    source.text_sha256, v_generation.id::text,
                    v_embedding.embedding_version), 'sha256'), 'hex')
            FROM jsonb_to_recordset(p_chunks) AS source(
                id uuid, text_sha256 text, embedding text
            );
            SELECT active_generation_id INTO STRICT v_previous
            FROM public.documents WHERE id = v_job.document_id FOR UPDATE;
            IF v_previous <> v_generation.id THEN
                UPDATE public.document_generations SET state = 'retained',
                    retired_at = statement_timestamp(), updated_at = statement_timestamp()
                WHERE id = v_previous AND state = 'ready';
            END IF;
            UPDATE public.document_generations SET state = 'ready', error = NULL,
                page_count = p_page_count, chunk_count = v_chunk_count,
                ready_at = statement_timestamp(), retired_at = NULL,
                updated_at = statement_timestamp()
            WHERE id = v_generation.id;
            UPDATE public.documents SET active_generation_id = v_generation.id,
                parser_version = v_generation.parser_version,
                chunking_version = v_generation.chunking_version,
                embedding_version = v_embedding.embedding_version,
                state = 'ready', stage = 'ready', error = NULL,
                page_count = p_page_count, chunk_count = v_chunk_count,
                updated_at = statement_timestamp()
            WHERE id = v_job.document_id;
            UPDATE public.ingestion_jobs SET status = 'completed', stage = 'ready',
                completed_units = v_chunk_count, total_units = v_chunk_count,
                error = NULL, lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, heartbeat_at = statement_timestamp(),
                updated_at = statement_timestamp() WHERE id = p_job_id;
            IF v_job.reprocessing_operation_id IS NOT NULL THEN
                UPDATE public.reprocessing_operations AS operation
                SET completed_documents = completed_documents + 1,
                    completed_chunks = completed_chunks + v_chunk_count,
                    state = CASE WHEN completed_documents + 1 = total_documents
                                 THEN 'succeeded' ELSE state END,
                    stage = CASE WHEN completed_documents + 1 = total_documents
                                 THEN 'succeeded' ELSE stage END,
                    finished_at = CASE WHEN completed_documents + 1 = total_documents
                                       THEN statement_timestamp() ELSE NULL END,
                    updated_at = statement_timestamp()
                WHERE id = v_job.reprocessing_operation_id;
            END IF;
            RETURN true;
        END;
        $$;

        CREATE OR REPLACE FUNCTION public.v4_poison_ingestion_job(
            p_job_id uuid, p_lease_token uuid, p_fencing_token bigint, p_error text
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_job public.ingestion_jobs%ROWTYPE;
        BEGIN
            UPDATE public.ingestion_jobs SET status = 'failed', stage = 'failed',
                error = left(btrim(p_error),500), lease_owner = NULL,
                lease_token = NULL, lease_expires_at = NULL,
                heartbeat_at = statement_timestamp(), updated_at = statement_timestamp()
            WHERE id = p_job_id AND status = 'running'
              AND lease_token = p_lease_token AND fencing_token = p_fencing_token
              AND lease_expires_at > statement_timestamp()
              AND char_length(btrim(p_error)) BETWEEN 1 AND 500
            RETURNING * INTO v_job;
            IF v_job.id IS NULL THEN RETURN false; END IF;
            UPDATE public.document_generations SET state = 'failed',
                error = left(btrim(p_error),500), updated_at = statement_timestamp()
            WHERE id = v_job.target_generation_id;
            UPDATE public.documents SET state = 'failed', stage = 'failed',
                error = left(btrim(p_error),500), updated_at = statement_timestamp()
            WHERE id = v_job.document_id
              AND active_generation_id = v_job.target_generation_id
              AND state <> 'ready';
            IF v_job.reprocessing_operation_id IS NOT NULL THEN
                UPDATE public.reprocessing_operations SET state = 'failed',
                    stage = 'failed', failed_documents = failed_documents + 1,
                    reason_code = 'document_reingestion_failed',
                    finished_at = statement_timestamp(),
                    updated_at = statement_timestamp()
                WHERE id = v_job.reprocessing_operation_id;
            END IF;
            RETURN true;
        END;
        $$;
        """
    )


def _create_admin_contracts() -> None:
    op.execute(
        """
        CREATE FUNCTION public.v10_admin_version_inventory()
        RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE result jsonb;
        BEGIN
            PERFORM public.v4_require_admin();
            SELECT jsonb_build_object(
                'ingestion', jsonb_build_object(
                    'revision_id', config.revision_id,
                    'parser_profile_id', config.parser_profile_id,
                    'parser_version', config.parser_version,
                    'chunking_version', config.chunking_version,
                    'document_versions', COALESCE((
                        SELECT jsonb_agg(jsonb_build_object(
                            'parser_version', grouped.parser_version,
                            'document_count', grouped.document_count
                        ) ORDER BY grouped.parser_version)
                        FROM (SELECT document.parser_version,
                                     count(*)::integer AS document_count
                              FROM public.documents AS document
                              WHERE document.state = 'ready'
                              GROUP BY document.parser_version) AS grouped
                    ), '[]'::jsonb),
                    'generations', COALESCE((
                        SELECT jsonb_agg(jsonb_build_object(
                            'generation_id', generation.id,
                            'document_id', generation.document_id,
                            'filename', document.original_filename,
                            'parser_version', generation.parser_version,
                            'chunking_version', generation.chunking_version,
                            'state', generation.state,
                            'chunk_count', generation.chunk_count,
                            'created_at', generation.created_at,
                            'retired_at', generation.retired_at,
                            'cleanup_available', generation.operation_id IS NOT NULL
                                AND generation.state IN (
                                    'retained','abandoned','failed'
                                )
                                AND document.active_generation_id <> generation.id
                        ) ORDER BY generation.created_at DESC)
                        FROM public.document_generations AS generation
                        JOIN public.documents AS document
                          ON document.id = generation.document_id
                        WHERE generation.operation_id IS NOT NULL
                    ), '[]'::jsonb)
                ),
                'embedding', jsonb_build_object(
                    'active_generation_id', active.id,
                    'profile_id', active.profile_id,
                    'embedding_version', active.embedding_version,
                    'dimension', active.dimension,
                    'generations', COALESCE((
                        SELECT jsonb_agg(jsonb_build_object(
                            'generation_id', generation.id,
                            'profile_id', generation.profile_id,
                            'embedding_version', generation.embedding_version,
                            'dimension', generation.dimension,
                            'state', generation.state,
                            'chunk_count', (SELECT count(*) FROM public.chunk_embeddings AS item
                                            WHERE item.embedding_generation_id = generation.id),
                            'created_at', generation.created_at,
                            'activated_at', generation.activated_at,
                            'retired_at', generation.retired_at,
                            'cleanup_available', generation.operation_id IS NOT NULL
                                AND generation.state IN ('retained','abandoned')
                        ) ORDER BY generation.created_at DESC)
                        FROM public.embedding_generations AS generation
                    ), '[]'::jsonb)
                )
            ) INTO result
            FROM public.ingestion_version_configuration AS config
            CROSS JOIN public.embedding_generation_state AS state
            JOIN public.embedding_generations AS active
              ON active.id = state.active_generation_id;
            RETURN result;
        END;
        $$;

        CREATE FUNCTION public.v10_admin_set_ingestion_profile(
            p_base_revision text, p_profile_id text
        ) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE current_row public.ingestion_version_configuration%ROWTYPE;
                revision_value text;
        BEGIN
            PERFORM public.v4_require_admin();
            SELECT * INTO STRICT current_row
            FROM public.ingestion_version_configuration WHERE singleton FOR UPDATE;
            IF p_base_revision <> current_row.revision_id THEN
                RAISE EXCEPTION 'ingestion version selection is stale'
                    USING ERRCODE = '40001';
            END IF;
            IF p_profile_id = current_row.parser_profile_id THEN
                RAISE EXCEPTION 'ingestion version selection has no changes'
                    USING ERRCODE = '22023';
            END IF;
            IF p_profile_id NOT IN (
                'parser.paddleocr-vl-1.6.adaptive-v2',
                'parser.paddleocr-vl-1.6.legacy-v1'
            ) THEN
                RAISE EXCEPTION 'parser profile is unavailable' USING ERRCODE = '22023';
            END IF;
            revision_value := 'v8e-ingestion-' ||
                replace(public.gen_random_uuid()::text, '-', '');
            UPDATE public.ingestion_version_configuration SET
                revision_id = revision_value, parser_profile_id = p_profile_id,
                parser_version = CASE p_profile_id
                    WHEN 'parser.paddleocr-vl-1.6.adaptive-v2'
                        THEN 'pypdf+paddleocr-vl-v1.6-adaptive-v2'
                    WHEN 'parser.paddleocr-vl-1.6.legacy-v1'
                        THEN 'pypdf+paddleocr-vl-v1.6'
                    ELSE NULL END,
                chunking_version = CASE p_profile_id
                    WHEN 'parser.paddleocr-vl-1.6.adaptive-v2'
                        THEN 'fragment-paragraph-sentence-v2'
                    WHEN 'parser.paddleocr-vl-1.6.legacy-v1'
                        THEN 'paragraph-sentence-v1'
                    ELSE NULL END,
                updated_by = public.v4_current_actor_id(),
                updated_at = statement_timestamp()
            WHERE singleton;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'parser profile is unavailable' USING ERRCODE = '22023';
            END IF;
            PERFORM public.v4_append_audit('ingestion_profile_selected',
                'ingestion_configuration', NULL,
                jsonb_build_object('prior_revision', p_base_revision,
                                   'revision', revision_value,
                                   'profile_id', p_profile_id));
            RETURN revision_value;
        END;
        $$;

        CREATE FUNCTION public.v10_admin_preview_reprocessing(
            p_operation_type text, p_target_profile_id text,
            p_source_parser_version text
        ) RETURNS TABLE (
            preview_id uuid, impact_digest text, expires_at timestamptz,
            document_count integer, chunk_count integer,
            estimated_bytes bigint, backup_verified boolean
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE actor_id uuid := public.v4_current_actor_id();
                session_id_value uuid;
                preview_uuid uuid := public.gen_random_uuid();
                expiry timestamptz := statement_timestamp() + interval '5 minutes';
                docs integer; chunks integer; bytes bigint;
                target_parser text; target_chunking text;
                target_embedding text; dimension_value integer;
                digest_value text; backup_ok boolean;
        BEGIN
            PERFORM public.v4_require_admin();
            SELECT session.id INTO STRICT session_id_value FROM public.sessions AS session
            WHERE session.token_hash = current_setting('rag.session_token_hash', true)
              AND session.revoked_at IS NULL;
            IF EXISTS (SELECT 1 FROM public.reprocessing_operations
                       WHERE state IN ('running','paused','qualifying')) THEN
                RAISE EXCEPTION 'a reprocessing operation is already active'
                    USING ERRCODE = '40001';
            END IF;
            IF p_operation_type = 'reindex' THEN
                IF p_target_profile_id <>
                   'embedding.qwen3-0.6b-1024.ollama.windows-x64'
                   OR p_source_parser_version IS NOT NULL THEN
                    RAISE EXCEPTION 'embedding profile is unavailable'
                        USING ERRCODE = '22023';
                END IF;
                target_embedding := 'qwen3-embedding-0.6b-1024';
                dimension_value := 1024;
                SELECT count(DISTINCT document.id)::integer, count(chunk.id)::integer,
                       count(chunk.id)::bigint * dimension_value * 4
                INTO docs, chunks, bytes
                FROM public.documents AS document
                JOIN public.chunks AS chunk
                  ON chunk.document_generation_id = document.active_generation_id
                WHERE document.state = 'ready';
            ELSIF p_operation_type = 'reingestion' THEN
                IF p_target_profile_id = 'parser.paddleocr-vl-1.6.adaptive-v2' THEN
                    target_parser := 'pypdf+paddleocr-vl-v1.6-adaptive-v2';
                    target_chunking := 'fragment-paragraph-sentence-v2';
                ELSIF p_target_profile_id = 'parser.paddleocr-vl-1.6.legacy-v1' THEN
                    target_parser := 'pypdf+paddleocr-vl-v1.6';
                    target_chunking := 'paragraph-sentence-v1';
                ELSE
                    RAISE EXCEPTION 'parser profile is unavailable'
                        USING ERRCODE = '22023';
                END IF;
                SELECT count(*)::integer, COALESCE(sum(document.chunk_count),0)::integer,
                       COALESCE(sum(document.byte_size),0)::bigint
                INTO docs, chunks, bytes FROM public.documents AS document
                WHERE document.state = 'ready'
                  AND (p_source_parser_version IS NULL
                       OR document.parser_version = p_source_parser_version)
                  AND document.parser_version <> target_parser;
            ELSE
                RAISE EXCEPTION 'invalid reprocessing operation'
                    USING ERRCODE = '22023';
            END IF;
            IF docs = 0 OR chunks = 0 THEN
                RAISE EXCEPTION 'no ready content matches this operation'
                    USING ERRCODE = '22023';
            END IF;
            digest_value := encode(public.digest(concat_ws('|',
                p_operation_type, p_target_profile_id,
                COALESCE(p_source_parser_version,''), COALESCE(target_parser,''),
                COALESCE(target_chunking,''), COALESCE(target_embedding,''),
                COALESCE(dimension_value::text,''), docs::text, chunks::text,
                bytes::text), 'sha256'), 'hex');
            SELECT EXISTS (
                SELECT 1 FROM public.backup_restore_verifications AS verification
                JOIN public.backup_runs AS run ON run.id = verification.backup_run_id
                WHERE run.status = 'succeeded'
                  AND verification.verified_at >= statement_timestamp() - interval '30 days'
            ) INTO backup_ok;
            INSERT INTO public.reprocessing_previews (
                id, actor_user_id, session_id, operation_type, target_profile_id,
                source_parser_version, target_parser_version,
                target_chunking_version, target_embedding_version,
                target_dimension, document_count, chunk_count, estimated_bytes,
                impact_digest, expires_at
            ) VALUES (
                preview_uuid, actor_id, session_id_value, p_operation_type,
                p_target_profile_id, p_source_parser_version, target_parser,
                target_chunking, target_embedding, dimension_value,
                docs, chunks, bytes, digest_value, expiry
            );
            RETURN QUERY SELECT preview_uuid, digest_value, expiry, docs, chunks,
                                bytes, backup_ok;
        END;
        $$;

        CREATE FUNCTION public.v10_admin_issue_reprocessing_grant(
            p_preview_id uuid, p_impact_digest text, p_token_hash text
        ) RETURNS timestamptz LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE actor_id uuid := public.v4_current_actor_id(); session_id_value uuid;
                expiry timestamptz := statement_timestamp() + interval '5 minutes';
        BEGIN
            PERFORM public.v4_require_admin();
            SELECT session.id INTO STRICT session_id_value FROM public.sessions AS session
            WHERE session.token_hash = current_setting('rag.session_token_hash', true)
              AND session.revoked_at IS NULL;
            IF p_impact_digest !~ '^[0-9a-f]{64}$'
               OR p_token_hash !~ '^[0-9a-f]{64}$'
               OR NOT EXISTS (
                    SELECT 1 FROM public.reprocessing_previews AS preview
                    WHERE preview.id = p_preview_id
                      AND preview.actor_user_id = actor_id
                      AND preview.session_id = session_id_value
                      AND preview.impact_digest = p_impact_digest
                      AND preview.consumed_at IS NULL
                      AND preview.expires_at > statement_timestamp()
               ) THEN
                RAISE EXCEPTION 'reprocessing preview is stale' USING ERRCODE = '40001';
            END IF;
            INSERT INTO public.system_reauthentication_grants (
                actor_user_id, session_id, action, impact_digest,
                token_hash, expires_at
            ) VALUES (actor_id, session_id_value, 'start_reprocessing',
                      p_impact_digest, p_token_hash, expiry);
            UPDATE public.sessions SET recent_reauthenticated_at = statement_timestamp(),
                last_seen_at = statement_timestamp() WHERE id = session_id_value;
            RETURN expiry;
        END;
        $$;
        """
    )
    _create_admin_start_and_control_contracts()


def _create_admin_start_and_control_contracts() -> None:
    op.execute(
        """
        CREATE FUNCTION public.v10_admin_start_reprocessing(
            p_preview_id uuid, p_impact_digest text, p_grant_token_hash text
        ) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE actor_id uuid := public.v4_current_actor_id(); session_id_value uuid;
                preview public.reprocessing_previews%ROWTYPE;
                grant_row public.system_reauthentication_grants%ROWTYPE;
                backup_id uuid; operation_uuid uuid := public.gen_random_uuid();
                generation_uuid uuid; active_embedding uuid;
        BEGIN
            PERFORM public.v4_require_admin();
            SELECT session.id INTO STRICT session_id_value FROM public.sessions AS session
            WHERE session.token_hash = current_setting('rag.session_token_hash', true)
              AND session.revoked_at IS NULL;
            SELECT * INTO preview FROM public.reprocessing_previews
            WHERE id = p_preview_id FOR UPDATE;
            SELECT * INTO grant_row FROM public.system_reauthentication_grants
            WHERE token_hash = p_grant_token_hash FOR UPDATE;
            IF preview.id IS NULL OR preview.actor_user_id <> actor_id
               OR preview.session_id <> session_id_value
               OR preview.impact_digest <> p_impact_digest
               OR preview.consumed_at IS NOT NULL
               OR preview.expires_at <= statement_timestamp()
               OR grant_row.id IS NULL OR grant_row.actor_user_id <> actor_id
               OR grant_row.session_id <> session_id_value
               OR grant_row.action <> 'start_reprocessing'
               OR grant_row.impact_digest <> p_impact_digest
               OR grant_row.consumed_at IS NOT NULL
               OR grant_row.expires_at <= statement_timestamp() THEN
                RAISE EXCEPTION 'reprocessing authorization is invalid or stale'
                    USING ERRCODE = '28000';
            END IF;
            SELECT verification.backup_run_id INTO backup_id
            FROM public.backup_restore_verifications AS verification
            JOIN public.backup_runs AS run ON run.id = verification.backup_run_id
            WHERE run.status = 'succeeded'
              AND verification.verified_at >= statement_timestamp() - interval '30 days'
            ORDER BY verification.verified_at DESC LIMIT 1;
            IF backup_id IS NULL THEN
                RAISE EXCEPTION 'a current restore-verified backup is required'
                    USING ERRCODE = '55000';
            END IF;
            INSERT INTO public.reprocessing_operations (
                id, preview_id, actor_user_id, backup_run_id, operation_type,
                target_profile_id, source_parser_version, target_parser_version,
                target_chunking_version, target_embedding_version,
                target_dimension, impact_digest, total_documents, total_chunks
            ) VALUES (
                operation_uuid, preview.id, actor_id, backup_id,
                preview.operation_type, preview.target_profile_id,
                preview.source_parser_version, preview.target_parser_version,
                preview.target_chunking_version, preview.target_embedding_version,
                preview.target_dimension, preview.impact_digest,
                preview.document_count, preview.chunk_count
            );
            IF preview.operation_type = 'reindex' THEN
                SELECT active_generation_id INTO STRICT active_embedding
                FROM public.embedding_generation_state WHERE singleton FOR UPDATE;
                generation_uuid := public.gen_random_uuid();
                INSERT INTO public.embedding_generations (
                    id, operation_id, profile_id, embedding_version,
                    dimension, state, source_generation_id
                ) VALUES (
                    generation_uuid, operation_uuid, preview.target_profile_id,
                    preview.target_embedding_version, preview.target_dimension,
                    'building', active_embedding
                );
                UPDATE public.reprocessing_operations
                SET operation_generation_id = generation_uuid
                WHERE id = operation_uuid;
                INSERT INTO public.reindex_tasks (
                    operation_id, generation_id, chunk_id
                )
                SELECT operation_uuid, generation_uuid, chunk.id
                FROM public.documents AS document
                JOIN public.chunks AS chunk
                  ON chunk.document_generation_id = document.active_generation_id
                WHERE document.state = 'ready';
            ELSE
                WITH targets AS (
                    SELECT document.id AS document_id, public.gen_random_uuid() AS generation_id,
                           public.gen_random_uuid() AS job_id
                    FROM public.documents AS document
                    WHERE document.state = 'ready'
                      AND (preview.source_parser_version IS NULL
                           OR document.parser_version = preview.source_parser_version)
                      AND document.parser_version <> preview.target_parser_version
                    FOR UPDATE OF document
                ), inserted_generations AS (
                    INSERT INTO public.document_generations (
                        id, document_id, operation_id, parser_version,
                        chunking_version, state
                    ) SELECT generation_id, document_id, operation_uuid,
                             preview.target_parser_version,
                             preview.target_chunking_version, 'building'
                      FROM targets RETURNING id, document_id
                )
                INSERT INTO public.ingestion_jobs (
                    id, document_id, target_generation_id,
                    reprocessing_operation_id, status, stage, attempt,
                    completed_units, total_units
                )
                SELECT target.job_id, target.document_id, target.generation_id,
                       operation_uuid, 'queued', 'uploaded', 0, 0, NULL
                FROM targets AS target
                JOIN inserted_generations AS generation
                  ON generation.id = target.generation_id;
            END IF;
            UPDATE public.reprocessing_previews SET consumed_at = statement_timestamp()
            WHERE id = preview.id;
            UPDATE public.system_reauthentication_grants
            SET consumed_at = statement_timestamp() WHERE id = grant_row.id;
            PERFORM public.v4_append_audit('reprocessing_started',
                'reprocessing_operation', operation_uuid,
                jsonb_build_object('operation_type', preview.operation_type,
                    'impact_digest', preview.impact_digest,
                    'documents', preview.document_count,
                    'chunks', preview.chunk_count,
                    'backup_run_id', backup_id));
            RETURN operation_uuid;
        EXCEPTION WHEN unique_violation THEN
            RAISE EXCEPTION 'a reprocessing operation is already active'
                USING ERRCODE = '40001';
        END;
        $$;

        CREATE FUNCTION public.v10_admin_reprocessing_operations(p_limit integer)
        RETURNS TABLE (
            operation_id uuid, operation_type text, state text, stage text,
            target_profile_id text, source_parser_version text,
            target_parser_version text, target_embedding_version text,
            target_dimension integer, impact_digest text,
            total_documents integer, completed_documents integer,
            failed_documents integer, total_chunks integer,
            completed_chunks integer, reason_code text, qualification jsonb,
            operation_generation_id uuid, created_at timestamptz,
            finished_at timestamptz
        ) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            IF p_limit NOT BETWEEN 1 AND 100 THEN
                RAISE EXCEPTION 'invalid operation limit' USING ERRCODE = '22023';
            END IF;
            RETURN QUERY SELECT operation.id, operation.operation_type::text,
                operation.state::text, operation.stage::text,
                operation.target_profile_id::text,
                operation.source_parser_version::text,
                operation.target_parser_version::text,
                operation.target_embedding_version::text,
                operation.target_dimension, operation.impact_digest::text,
                operation.total_documents, operation.completed_documents,
                operation.failed_documents, operation.total_chunks,
                operation.completed_chunks, operation.reason_code::text,
                operation.qualification, operation.operation_generation_id,
                operation.created_at, operation.finished_at
            FROM public.reprocessing_operations AS operation
            ORDER BY operation.created_at DESC, operation.id DESC LIMIT p_limit;
        END;
        $$;

        CREATE FUNCTION public.v10_admin_pause_reprocessing(p_operation_id uuid)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            IF EXISTS (SELECT 1 FROM public.reindex_tasks WHERE operation_id = p_operation_id
                       AND state = 'running') OR EXISTS (
                SELECT 1 FROM public.ingestion_jobs WHERE reprocessing_operation_id = p_operation_id
                AND status = 'running') THEN
                RAISE EXCEPTION 'operation is between safe pause boundaries'
                    USING ERRCODE = '40001';
            END IF;
            UPDATE public.reprocessing_operations SET state = 'paused', stage = 'paused',
                updated_at = statement_timestamp()
            WHERE id = p_operation_id AND state = 'running';
            RETURN FOUND;
        END;
        $$;

        CREATE FUNCTION public.v10_admin_resume_reprocessing(p_operation_id uuid)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            UPDATE public.reprocessing_operations SET state = 'running',
                stage = 'processing', updated_at = statement_timestamp()
            WHERE id = p_operation_id AND state = 'paused';
            RETURN FOUND;
        END;
        $$;

        CREATE FUNCTION public.v10_admin_cancel_reprocessing(p_operation_id uuid)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE operation_row public.reprocessing_operations%ROWTYPE;
        BEGIN
            PERFORM public.v4_require_admin();
            SELECT * INTO operation_row FROM public.reprocessing_operations
            WHERE id = p_operation_id FOR UPDATE;
            IF operation_row.id IS NULL OR operation_row.state NOT IN ('paused','failed')
               OR EXISTS (SELECT 1 FROM public.reindex_tasks
                          WHERE operation_id = p_operation_id AND state = 'running')
               OR EXISTS (SELECT 1 FROM public.ingestion_jobs
                          WHERE reprocessing_operation_id = p_operation_id
                            AND status = 'running') THEN RETURN false; END IF;
            DELETE FROM public.ingestion_jobs
            WHERE reprocessing_operation_id = p_operation_id
              AND status IN ('queued','failed','interrupted');
            UPDATE public.document_generations SET state = 'abandoned',
                retired_at = statement_timestamp(), updated_at = statement_timestamp()
            WHERE operation_id = p_operation_id AND state IN ('building','failed');
            UPDATE public.embedding_generations SET state = 'abandoned',
                retired_at = statement_timestamp()
            WHERE operation_id = p_operation_id AND state IN ('building','qualified');
            UPDATE public.reprocessing_operations SET state = 'cancelled',
                stage = 'cancelled', reason_code = 'cancelled_by_admin',
                finished_at = statement_timestamp(), updated_at = statement_timestamp()
            WHERE id = p_operation_id;
            RETURN true;
        END;
        $$;

        CREATE FUNCTION public.v10_admin_retry_reprocessing(p_operation_id uuid)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE operation_row public.reprocessing_operations%ROWTYPE;
        BEGIN
            PERFORM public.v4_require_admin();
            SELECT * INTO operation_row FROM public.reprocessing_operations
            WHERE id = p_operation_id FOR UPDATE;
            IF operation_row.id IS NULL OR operation_row.state <> 'failed' THEN
                RETURN false;
            END IF;
            IF operation_row.operation_type = 'reindex' THEN
                UPDATE public.reindex_tasks SET state = 'queued', error = NULL,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    updated_at = statement_timestamp()
                WHERE operation_id = p_operation_id AND state = 'failed' AND attempt < 3;
                UPDATE public.embedding_generations SET state = 'building',
                    retired_at = NULL WHERE operation_id = p_operation_id;
            ELSE
                UPDATE public.ingestion_jobs SET status = 'queued', stage = 'uploaded',
                    error = NULL, available_at = statement_timestamp(),
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    updated_at = statement_timestamp()
                WHERE reprocessing_operation_id = p_operation_id AND status = 'failed';
                UPDATE public.document_generations SET state = 'building', error = NULL,
                    retired_at = NULL, updated_at = statement_timestamp()
                WHERE operation_id = p_operation_id AND state = 'failed';
            END IF;
            UPDATE public.reprocessing_operations SET state = 'running',
                stage = 'processing', reason_code = NULL, failed_documents = 0,
                finished_at = NULL, updated_at = statement_timestamp()
            WHERE id = p_operation_id;
            RETURN true;
        END;
        $$;

        CREATE FUNCTION public.v10_admin_rollback_embedding_generation(
            p_generation_id uuid
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE current_id uuid;
        BEGIN
            PERFORM public.v4_require_admin();
            SELECT active_generation_id INTO STRICT current_id
            FROM public.embedding_generation_state WHERE singleton FOR UPDATE;
            IF NOT EXISTS (
                SELECT 1 FROM public.embedding_generations
                WHERE id = p_generation_id AND state = 'retained'
                  AND retired_at >= statement_timestamp() - interval '30 days'
            ) THEN RETURN false; END IF;
            UPDATE public.embedding_generations SET state = 'retained',
                retired_at = statement_timestamp() WHERE id = current_id;
            UPDATE public.embedding_generations SET state = 'active',
                retired_at = NULL, activated_at = statement_timestamp()
            WHERE id = p_generation_id;
            UPDATE public.embedding_generation_state
            SET active_generation_id = p_generation_id WHERE singleton;
            PERFORM public.v4_append_audit('embedding_generation_rolled_back',
                'embedding_generation', p_generation_id,
                jsonb_build_object('prior_generation_id', current_id));
            RETURN true;
        END;
        $$;

        CREATE FUNCTION public.v10_admin_cleanup_generation(
            p_generation_type text, p_generation_id uuid
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            IF p_generation_type = 'embedding' THEN
                DELETE FROM public.embedding_generations
                WHERE id = p_generation_id AND operation_id IS NOT NULL
                  AND state IN ('retained','abandoned')
                  AND id <> (SELECT active_generation_id
                             FROM public.embedding_generation_state WHERE singleton);
            ELSIF p_generation_type = 'document' THEN
                DELETE FROM public.document_generations AS generation
                WHERE generation.id = p_generation_id
                  AND generation.operation_id IS NOT NULL
                  AND generation.state IN ('retained','abandoned','failed')
                  AND NOT EXISTS (SELECT 1 FROM public.documents AS document
                                  WHERE document.active_generation_id = generation.id);
            ELSE
                RAISE EXCEPTION 'invalid generation type' USING ERRCODE = '22023';
            END IF;
            RETURN FOUND;
        END;
        $$;
        """
    )


def _create_worker_contracts() -> None:
    op.execute(
        """
        CREATE FUNCTION public.v10_claim_reindex_tasks(
            p_owner_id text, p_lease_seconds integer, p_limit integer
        ) RETURNS TABLE (
            operation_id uuid, generation_id uuid, chunk_id uuid,
            chunk_text text, text_sha256 text, dimension integer,
            embedding_version text, lease_token uuid, fencing_token bigint,
            attempt integer
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
            IF char_length(p_owner_id) NOT BETWEEN 1 AND 255
               OR p_lease_seconds NOT BETWEEN 5 AND 3600
               OR p_limit NOT BETWEEN 1 AND 32 THEN
                RAISE EXCEPTION 'invalid reindex claim' USING ERRCODE = '22023';
            END IF;
            RETURN QUERY
            WITH candidates AS (
                SELECT task.operation_id, task.chunk_id
                FROM public.reindex_tasks AS task
                JOIN public.reprocessing_operations AS operation
                  ON operation.id = task.operation_id
                WHERE operation.state = 'running'
                  AND ((task.state = 'queued') OR
                       (task.state = 'running'
                        AND task.lease_expires_at <= statement_timestamp()))
                ORDER BY task.created_at, task.chunk_id
                FOR UPDATE OF task SKIP LOCKED LIMIT p_limit
            ), claimed AS (
                UPDATE public.reindex_tasks AS task SET state = 'running',
                    attempt = task.attempt + 1, lease_owner = p_owner_id,
                    lease_token = public.gen_random_uuid(),
                    lease_expires_at = statement_timestamp()
                        + make_interval(secs => p_lease_seconds),
                    fencing_token = task.fencing_token + 1,
                    updated_at = statement_timestamp()
                FROM candidates
                WHERE task.operation_id = candidates.operation_id
                  AND task.chunk_id = candidates.chunk_id
                RETURNING task.*
            )
            SELECT claimed.operation_id, claimed.generation_id, claimed.chunk_id,
                   chunk.text, chunk.text_sha256::text, generation.dimension,
                   generation.embedding_version::text, claimed.lease_token,
                   claimed.fencing_token, claimed.attempt
            FROM claimed
            JOIN public.chunks AS chunk ON chunk.id = claimed.chunk_id
            JOIN public.embedding_generations AS generation
              ON generation.id = claimed.generation_id;
        END;
        $$;

        CREATE FUNCTION public.v10_commit_reindex_task(
            p_operation_id uuid, p_chunk_id uuid, p_lease_token uuid,
            p_fencing_token bigint, p_embedding text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE task_row public.reindex_tasks%ROWTYPE;
                generation public.embedding_generations%ROWTYPE;
                source public.chunks%ROWTYPE; finished integer; total integer;
        BEGIN
            SELECT * INTO task_row FROM public.reindex_tasks
            WHERE operation_id = p_operation_id AND chunk_id = p_chunk_id
              AND state = 'running' AND lease_token = p_lease_token
              AND fencing_token = p_fencing_token
              AND lease_expires_at > statement_timestamp() FOR UPDATE;
            IF task_row.operation_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM public.reprocessing_operations
                WHERE id = p_operation_id AND state = 'running'
            ) THEN RETURN false; END IF;
            SELECT * INTO STRICT generation FROM public.embedding_generations
            WHERE id = task_row.generation_id AND state = 'building';
            SELECT * INTO STRICT source FROM public.chunks WHERE id = p_chunk_id;
            IF public.vector_dims(p_embedding::public.vector) <> generation.dimension THEN
                RAISE EXCEPTION 'candidate embedding dimension mismatch'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.chunk_embeddings (
                embedding_generation_id, chunk_id, document_generation_id,
                dimension, embedding, text_sha256, provenance_sha256
            ) VALUES (
                generation.id, source.id, source.document_generation_id,
                generation.dimension, p_embedding::public.vector, source.text_sha256,
                encode(public.digest(concat_ws('|', source.id::text,
                    source.text_sha256, source.document_generation_id::text,
                    generation.embedding_version), 'sha256'), 'hex')
            ) ON CONFLICT (embedding_generation_id, chunk_id) DO UPDATE SET
                embedding = EXCLUDED.embedding,
                dimension = EXCLUDED.dimension,
                text_sha256 = EXCLUDED.text_sha256,
                provenance_sha256 = EXCLUDED.provenance_sha256,
                created_at = statement_timestamp();
            UPDATE public.reindex_tasks SET state = 'completed', error = NULL,
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                updated_at = statement_timestamp()
            WHERE operation_id = p_operation_id AND chunk_id = p_chunk_id;
            SELECT count(*) FILTER (WHERE state = 'completed'), count(*)
            INTO finished, total FROM public.reindex_tasks
            WHERE operation_id = p_operation_id;
            UPDATE public.reprocessing_operations SET completed_chunks = finished,
                state = CASE WHEN finished = total THEN 'qualifying' ELSE state END,
                stage = CASE WHEN finished = total THEN 'qualifying' ELSE stage END,
                updated_at = statement_timestamp()
            WHERE id = p_operation_id;
            RETURN true;
        END;
        $$;

        CREATE FUNCTION public.v10_fail_reindex_task(
            p_operation_id uuid, p_chunk_id uuid, p_lease_token uuid,
            p_fencing_token bigint, p_error text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE attempt_value integer;
        BEGIN
            UPDATE public.reindex_tasks SET
                state = CASE WHEN attempt < 3 THEN 'queued' ELSE 'failed' END,
                error = left(btrim(p_error),500), lease_owner = NULL,
                lease_token = NULL, lease_expires_at = NULL,
                updated_at = statement_timestamp()
            WHERE operation_id = p_operation_id AND chunk_id = p_chunk_id
              AND state = 'running' AND lease_token = p_lease_token
              AND fencing_token = p_fencing_token
            RETURNING attempt INTO attempt_value;
            IF attempt_value IS NULL THEN RETURN false; END IF;
            IF attempt_value >= 3 THEN
                UPDATE public.embedding_generations SET state = 'abandoned',
                    retired_at = statement_timestamp()
                WHERE operation_id = p_operation_id;
                UPDATE public.reprocessing_operations SET state = 'failed',
                    stage = 'failed', reason_code = 'embedding_task_failed',
                    finished_at = statement_timestamp(), updated_at = statement_timestamp()
                WHERE id = p_operation_id;
            END IF;
            RETURN true;
        END;
        $$;

        CREATE FUNCTION public.v10_claim_reindex_qualification(
            p_owner_id text, p_lease_seconds integer
        ) RETURNS TABLE (
            operation_id uuid, generation_id uuid, dimension integer,
            lease_token uuid, fencing_token bigint
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
            IF char_length(p_owner_id) NOT BETWEEN 1 AND 255
               OR p_lease_seconds NOT BETWEEN 5 AND 3600 THEN
                RAISE EXCEPTION 'invalid qualification claim' USING ERRCODE = '22023';
            END IF;
            RETURN QUERY WITH candidate AS (
                SELECT operation.id FROM public.reprocessing_operations AS operation
                WHERE operation.operation_type = 'reindex'
                  AND operation.state = 'qualifying'
                  AND (operation.lease_token IS NULL
                       OR operation.lease_expires_at <= statement_timestamp())
                ORDER BY operation.created_at FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE public.reprocessing_operations AS operation SET
                lease_owner = p_owner_id, lease_token = public.gen_random_uuid(),
                lease_expires_at = statement_timestamp()
                    + make_interval(secs => p_lease_seconds),
                fencing_token = operation.fencing_token + 1,
                updated_at = statement_timestamp()
            FROM candidate, public.embedding_generations AS generation
            WHERE operation.id = candidate.id
              AND generation.id = operation.operation_generation_id
            RETURNING operation.id, generation.id, generation.dimension,
                operation.lease_token, operation.fencing_token;
        END;
        $$;

        CREATE FUNCTION public.v10_reindex_qualification_sample(p_operation_id uuid)
        RETURNS TABLE (
            chunk_id uuid, chunk_text text, filename text, page_start integer,
            page_end integer, citation_label text, text_sha256 text
        ) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
            SELECT chunk.id, chunk.text, chunk.filename::text, chunk.page_start,
                   chunk.page_end, chunk.citation_label::text, chunk.text_sha256::text
            FROM public.reprocessing_operations AS operation
            JOIN public.reindex_tasks AS task ON task.operation_id = operation.id
            JOIN public.chunks AS chunk ON chunk.id = task.chunk_id
            WHERE operation.id = p_operation_id AND operation.state = 'qualifying'
              AND task.state = 'completed'
            ORDER BY chunk.id LIMIT 2
        $$;

        CREATE FUNCTION public.v10_candidate_retrieve(
            p_operation_id uuid, p_query_vector text, p_limit integer
        ) RETURNS TABLE (chunk_id uuid, distance double precision)
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE generation public.embedding_generations%ROWTYPE;
        BEGIN
            SELECT embedding_generation.* INTO STRICT generation
            FROM public.reprocessing_operations AS operation
            JOIN public.embedding_generations AS embedding_generation
              ON embedding_generation.id = operation.operation_generation_id
            WHERE operation.id = p_operation_id AND operation.state = 'qualifying';
            IF p_limit NOT BETWEEN 1 AND 20
               OR public.vector_dims(p_query_vector::public.vector) <> generation.dimension
            THEN RAISE EXCEPTION 'invalid candidate retrieval request'
                    USING ERRCODE = '22023'; END IF;
            RETURN QUERY SELECT item.chunk_id,
                (item.embedding OPERATOR(public.<=>)
                    p_query_vector::public.vector)::double precision
            FROM public.chunk_embeddings AS item
            WHERE item.embedding_generation_id = generation.id
            ORDER BY item.embedding OPERATOR(public.<=>)
                p_query_vector::public.vector LIMIT p_limit;
        END;
        $$;

        CREATE FUNCTION public.v10_complete_reindex_qualification(
            p_operation_id uuid, p_lease_token uuid, p_fencing_token bigint,
            p_retrieval_passed boolean, p_rerank_passed boolean,
            p_citation_passed boolean, p_insufficient_context_passed boolean,
            p_reason_code text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE operation_row public.reprocessing_operations%ROWTYPE;
                generation public.embedding_generations%ROWTYPE; current_id uuid;
                source_changed boolean; candidate_count integer; provenance_ok boolean;
        BEGIN
            SELECT * INTO operation_row FROM public.reprocessing_operations
            WHERE id = p_operation_id AND state = 'qualifying'
              AND lease_token = p_lease_token AND fencing_token = p_fencing_token
              AND lease_expires_at > statement_timestamp() FOR UPDATE;
            IF operation_row.id IS NULL THEN RETURN false; END IF;
            SELECT * INTO STRICT generation FROM public.embedding_generations
            WHERE id = operation_row.operation_generation_id FOR UPDATE;
            SELECT EXISTS (
                (SELECT task.chunk_id FROM public.reindex_tasks AS task
                 WHERE task.operation_id = p_operation_id)
                EXCEPT
                (SELECT chunk.id FROM public.documents AS document
                 JOIN public.chunks AS chunk
                   ON chunk.document_generation_id = document.active_generation_id
                 WHERE document.state = 'ready')
            ) OR EXISTS (
                (SELECT chunk.id FROM public.documents AS document
                 JOIN public.chunks AS chunk
                   ON chunk.document_generation_id = document.active_generation_id
                 WHERE document.state = 'ready')
                EXCEPT
                (SELECT task.chunk_id FROM public.reindex_tasks AS task
                 WHERE task.operation_id = p_operation_id)
            ) INTO source_changed;
            SELECT count(*), bool_and(item.dimension = generation.dimension
                AND public.vector_dims(item.embedding) = generation.dimension
                AND item.text_sha256 = chunk.text_sha256
                AND item.document_generation_id = chunk.document_generation_id
                AND item.provenance_sha256 = encode(public.digest(concat_ws('|',
                    chunk.id::text, chunk.text_sha256,
                    chunk.document_generation_id::text,
                    generation.embedding_version), 'sha256'), 'hex'))
            INTO candidate_count, provenance_ok
            FROM public.chunk_embeddings AS item
            JOIN public.chunks AS chunk ON chunk.id = item.chunk_id
            WHERE item.embedding_generation_id = generation.id;
            IF source_changed OR candidate_count <> operation_row.total_chunks
               OR provenance_ok IS NOT TRUE OR NOT p_retrieval_passed
               OR NOT p_rerank_passed OR NOT p_citation_passed
               OR NOT p_insufficient_context_passed THEN
                UPDATE public.embedding_generations SET state = 'abandoned',
                    retired_at = statement_timestamp(),
                    qualification = jsonb_build_object(
                        'source_unchanged', NOT source_changed,
                        'count_parity', candidate_count = operation_row.total_chunks,
                        'provenance', COALESCE(provenance_ok,false),
                        'retrieval', p_retrieval_passed, 'rerank', p_rerank_passed,
                        'citation', p_citation_passed,
                        'insufficient_context', p_insufficient_context_passed)
                WHERE id = generation.id;
                UPDATE public.reprocessing_operations SET state = 'failed',
                    stage = 'failed', reason_code = left(COALESCE(NULLIF(p_reason_code,''),
                        'candidate_qualification_failed'),128),
                    qualification = (SELECT qualification
                                     FROM public.embedding_generations
                                     WHERE id = generation.id),
                    finished_at = statement_timestamp(), lease_owner = NULL,
                    lease_token = NULL, lease_expires_at = NULL,
                    updated_at = statement_timestamp()
                WHERE id = p_operation_id;
                RETURN true;
            END IF;
            SELECT active_generation_id INTO STRICT current_id
            FROM public.embedding_generation_state WHERE singleton FOR UPDATE;
            UPDATE public.embedding_generations SET state = 'retained',
                retired_at = statement_timestamp() WHERE id = current_id;
            UPDATE public.embedding_generations SET state = 'active',
                qualified_at = statement_timestamp(),
                activated_at = statement_timestamp(), retired_at = NULL,
                qualification = jsonb_build_object(
                    'source_unchanged', true, 'count_parity', true,
                    'provenance', true, 'dimensions', true,
                    'retrieval', true, 'rerank', true, 'citation', true,
                    'insufficient_context', true)
            WHERE id = generation.id;
            UPDATE public.embedding_generation_state
            SET active_generation_id = generation.id WHERE singleton;
            UPDATE public.documents SET embedding_version = generation.embedding_version,
                updated_at = statement_timestamp() WHERE state = 'ready';
            UPDATE public.reprocessing_operations SET state = 'succeeded',
                stage = 'succeeded', reason_code = NULL,
                qualification = (SELECT qualification
                                 FROM public.embedding_generations
                                 WHERE id = generation.id),
                completed_documents = total_documents,
                completed_chunks = total_chunks,
                finished_at = statement_timestamp(), lease_owner = NULL,
                lease_token = NULL, lease_expires_at = NULL,
                updated_at = statement_timestamp()
            WHERE id = p_operation_id;
            PERFORM public.v4_append_audit('embedding_generation_cutover',
                'embedding_generation', generation.id,
                jsonb_build_object('prior_generation_id', current_id,
                                   'operation_id', p_operation_id,
                                   'dimension', generation.dimension));
            RETURN true;
        END;
        $$;
        """
    )


def _secure_contracts() -> None:
    op.execute(
        """
        ALTER TABLE public.ingestion_version_configuration OWNER TO rag_owner;
        ALTER TABLE public.reprocessing_previews OWNER TO rag_owner;
        ALTER TABLE public.reprocessing_operations OWNER TO rag_owner;
        ALTER TABLE public.document_generations OWNER TO rag_owner;
        ALTER TABLE public.embedding_generations OWNER TO rag_owner;
        ALTER TABLE public.embedding_generation_state OWNER TO rag_owner;
        ALTER TABLE public.chunk_embeddings OWNER TO rag_owner;
        ALTER TABLE public.reindex_tasks OWNER TO rag_owner;

        ALTER TABLE public.ingestion_version_configuration ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.ingestion_version_configuration FORCE ROW LEVEL SECURITY;
        ALTER TABLE public.reprocessing_previews ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.reprocessing_previews FORCE ROW LEVEL SECURITY;
        ALTER TABLE public.reprocessing_operations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.reprocessing_operations FORCE ROW LEVEL SECURITY;
        ALTER TABLE public.document_generations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.document_generations FORCE ROW LEVEL SECURITY;
        ALTER TABLE public.embedding_generations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.embedding_generations FORCE ROW LEVEL SECURITY;
        ALTER TABLE public.embedding_generation_state ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.embedding_generation_state FORCE ROW LEVEL SECURITY;
        ALTER TABLE public.chunk_embeddings ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.chunk_embeddings FORCE ROW LEVEL SECURITY;
        ALTER TABLE public.reindex_tasks ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.reindex_tasks FORCE ROW LEVEL SECURITY;

        REVOKE ALL ON TABLE public.ingestion_version_configuration,
            public.reprocessing_previews, public.reprocessing_operations,
            public.document_generations, public.embedding_generations,
            public.embedding_generation_state, public.chunk_embeddings,
            public.reindex_tasks FROM PUBLIC, rag_api, rag_worker,
            rag_maintenance, rag_backup, rag_migrator;
        GRANT SELECT ON TABLE public.ingestion_version_configuration,
            public.reprocessing_previews, public.reprocessing_operations,
            public.document_generations, public.embedding_generations,
            public.embedding_generation_state, public.chunk_embeddings,
            public.reindex_tasks TO rag_backup;

        REVOKE ALL ON FUNCTION
            public.v10_prepare_document_generation(),
            public.v10_create_document_generation(),
            public.v10_bind_ingestion_target_generation(),
            public.v10_bind_turn_embedding_generation()
            FROM PUBLIC, rag_api, rag_worker, rag_maintenance, rag_backup,
                 rag_migrator;

        REVOKE ALL ON FUNCTION public.v10_effective_ingestion_version()
            FROM PUBLIC, rag_worker, rag_maintenance, rag_backup, rag_migrator;
        GRANT EXECUTE ON FUNCTION public.v10_effective_ingestion_version()
            TO rag_api;
        REVOKE ALL ON FUNCTION
            public.v10_retrieve_active_chunks(public.vector,integer,uuid[])
            FROM PUBLIC, rag_worker, rag_maintenance, rag_backup, rag_migrator;
        GRANT EXECUTE ON FUNCTION
            public.v10_retrieve_active_chunks(public.vector,integer,uuid[])
            TO rag_api;

        REVOKE ALL ON FUNCTION
            public.v10_admin_version_inventory(),
            public.v10_admin_set_ingestion_profile(text,text),
            public.v10_admin_preview_reprocessing(text,text,text),
            public.v10_admin_issue_reprocessing_grant(uuid,text,text),
            public.v10_admin_start_reprocessing(uuid,text,text),
            public.v10_admin_reprocessing_operations(integer),
            public.v10_admin_pause_reprocessing(uuid),
            public.v10_admin_resume_reprocessing(uuid),
            public.v10_admin_cancel_reprocessing(uuid),
            public.v10_admin_retry_reprocessing(uuid),
            public.v10_admin_rollback_embedding_generation(uuid),
            public.v10_admin_cleanup_generation(text,uuid)
            FROM PUBLIC, rag_worker, rag_maintenance, rag_backup, rag_migrator;
        GRANT EXECUTE ON FUNCTION
            public.v10_admin_version_inventory(),
            public.v10_admin_set_ingestion_profile(text,text),
            public.v10_admin_preview_reprocessing(text,text,text),
            public.v10_admin_issue_reprocessing_grant(uuid,text,text),
            public.v10_admin_start_reprocessing(uuid,text,text),
            public.v10_admin_reprocessing_operations(integer),
            public.v10_admin_pause_reprocessing(uuid),
            public.v10_admin_resume_reprocessing(uuid),
            public.v10_admin_cancel_reprocessing(uuid),
            public.v10_admin_retry_reprocessing(uuid),
            public.v10_admin_rollback_embedding_generation(uuid),
            public.v10_admin_cleanup_generation(text,uuid)
            TO rag_api;

        REVOKE ALL ON FUNCTION
            public.v10_claim_reindex_tasks(text,integer,integer),
            public.v10_commit_reindex_task(uuid,uuid,uuid,bigint,text),
            public.v10_fail_reindex_task(uuid,uuid,uuid,bigint,text),
            public.v10_claim_reindex_qualification(text,integer),
            public.v10_reindex_qualification_sample(uuid),
            public.v10_candidate_retrieve(uuid,text,integer),
            public.v10_complete_reindex_qualification(uuid,uuid,bigint,
                boolean,boolean,boolean,boolean,text)
            FROM PUBLIC, rag_api, rag_maintenance, rag_backup, rag_migrator;
        GRANT EXECUTE ON FUNCTION
            public.v10_claim_reindex_tasks(text,integer,integer),
            public.v10_commit_reindex_task(uuid,uuid,uuid,bigint,text),
            public.v10_fail_reindex_task(uuid,uuid,uuid,bigint,text),
            public.v10_claim_reindex_qualification(text,integer),
            public.v10_reindex_qualification_sample(uuid),
            public.v10_candidate_retrieve(uuid,text,integer),
            public.v10_complete_reindex_qualification(uuid,uuid,bigint,
                boolean,boolean,boolean,boolean,text)
            TO rag_worker;

        REVOKE ALL ON FUNCTION public.v4_claim_ingestion_job(text,integer)
            FROM PUBLIC, rag_api, rag_maintenance, rag_backup, rag_migrator;
        GRANT EXECUTE ON FUNCTION public.v4_claim_ingestion_job(text,integer)
            TO rag_worker;

        CREATE OR REPLACE FUNCTION public.v4_schema_revision()
        RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ SELECT '0010_versioned_reprocessing'::text $$;

        CREATE OR REPLACE FUNCTION public.v5_readiness()
        RETURNS TABLE (
            schema_revision text, vector_extension boolean,
            bootstrap_required boolean, catalog_integrity boolean
        ) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
            SELECT '0010_versioned_reprocessing'::text,
                EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'vector'),
                NOT EXISTS (SELECT 1 FROM public.users WHERE role = 'admin'
                            AND status = 'active' AND deleted_at IS NULL),
                EXISTS (SELECT 1 FROM public.alembic_version
                        WHERE version_num = '0010_versioned_reprocessing')
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
                AND has_function_privilege('rag_worker',
                    'public.v10_claim_reindex_tasks(text,integer,integer)','EXECUTE')
                AND NOT has_function_privilege('public',
                    'public.v10_admin_version_inventory()','EXECUTE')
        $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "V8E generation selection is forward-only; restore a paired pre-V8E backup"
    )
