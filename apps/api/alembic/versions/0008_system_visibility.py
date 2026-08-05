# ruff: noqa: E501
"""Add durable, sanitized V8C System operation and validation evidence."""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_system_visibility"
down_revision: str | None = "0007_owner_setup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL ROLE rag_owner")
    op.execute(
        """
        CREATE TABLE public.system_operations (
            id uuid PRIMARY KEY DEFAULT public.gen_random_uuid(),
            actor_user_id uuid NOT NULL REFERENCES public.users(id),
            operation_type varchar(32) COLLATE "C" NOT NULL,
            profile_id varchar(96) COLLATE "C" NOT NULL,
            profile_revision integer NOT NULL DEFAULT 1,
            state varchar(24) COLLATE "C" NOT NULL DEFAULT 'running',
            stage varchar(24) COLLATE "C" NOT NULL DEFAULT 'preflight',
            reason_code varchar(64) COLLATE "C",
            metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            completion_token_hash varchar(64) COLLATE "C" NOT NULL,
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            finished_at timestamptz,
            CONSTRAINT ck_system_operations_type CHECK (
                operation_type IN ('profile_validation', 'profile_benchmark')
            ),
            CONSTRAINT ck_system_operations_profile CHECK (
                profile_id IN (
                    'generation.qwen3-8b.ollama.windows-x64',
                    'embedding.qwen3-0.6b-1024.ollama.windows-x64',
                    'reranking.bge-v2-m3.cpu.windows-x64',
                    'ocr.paddleocr-vl-1.6.cpu.windows-x64'
                )
            ),
            CONSTRAINT ck_system_operations_revision CHECK (profile_revision = 1),
            CONSTRAINT ck_system_operations_state CHECK (
                state IN ('running', 'effective', 'failed')
            ),
            CONSTRAINT ck_system_operations_stage CHECK (
                stage IN ('preflight', 'validating', 'benchmarking', 'effective', 'failed')
            ),
            CONSTRAINT ck_system_operations_reason CHECK (
                reason_code IS NULL OR reason_code ~ '^[a-z0-9][a-z0-9._-]{1,63}$'
            ),
            CONSTRAINT ck_system_operations_metrics CHECK (
                jsonb_typeof(metrics) = 'object' AND pg_column_size(metrics) <= 8192
            ),
            CONSTRAINT ck_system_operations_token CHECK (
                completion_token_hash ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_system_operations_terminal CHECK (
                (state = 'running' AND finished_at IS NULL)
                OR (state <> 'running' AND finished_at IS NOT NULL)
            )
        );
        ALTER TABLE public.system_operations OWNER TO rag_owner;
        ALTER TABLE public.system_operations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.system_operations FORCE ROW LEVEL SECURITY;
        CREATE UNIQUE INDEX uq_system_operations_active_profile
            ON public.system_operations (profile_id)
            WHERE state = 'running';
        CREATE INDEX ix_system_operations_created
            ON public.system_operations (created_at DESC, id DESC);

        CREATE TABLE public.system_profile_evidence (
            profile_id varchar(96) COLLATE "C" PRIMARY KEY,
            profile_revision integer NOT NULL,
            validation_state varchar(24) COLLATE "C" NOT NULL,
            reason_code varchar(64) COLLATE "C" NOT NULL,
            fixture_id varchar(96) COLLATE "C" NOT NULL,
            metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            operation_id uuid NOT NULL REFERENCES public.system_operations(id),
            evidence_at timestamptz NOT NULL,
            CONSTRAINT ck_system_profile_evidence_revision CHECK (profile_revision = 1),
            CONSTRAINT ck_system_profile_evidence_state CHECK (
                validation_state IN ('locally_validated', 'failed')
            ),
            CONSTRAINT ck_system_profile_evidence_reason CHECK (
                reason_code ~ '^[a-z0-9][a-z0-9._-]{1,63}$'
            ),
            CONSTRAINT ck_system_profile_evidence_fixture CHECK (
                fixture_id ~ '^[a-z0-9][a-z0-9._-]{2,95}$'
            ),
            CONSTRAINT ck_system_profile_evidence_metrics CHECK (
                jsonb_typeof(metrics) = 'object' AND pg_column_size(metrics) <= 8192
            )
        );
        ALTER TABLE public.system_profile_evidence OWNER TO rag_owner;
        ALTER TABLE public.system_profile_evidence ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.system_profile_evidence FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON TABLE public.system_operations, public.system_profile_evidence
            FROM PUBLIC, rag_api, rag_worker, rag_backup, rag_migrator,
                 rag_maintenance;
        GRANT SELECT ON TABLE public.system_operations, public.system_profile_evidence
            TO rag_backup;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.v8_admin_start_system_operation(
            p_operation_type text,
            p_profile_id text,
            p_completion_token_hash text
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            operation_id uuid := public.gen_random_uuid();
            actor_id uuid;
        BEGIN
            PERFORM public.v4_require_admin();
            actor_id := public.v4_current_actor_id();
            IF p_operation_type NOT IN ('profile_validation', 'profile_benchmark')
               OR p_profile_id NOT IN (
                    'generation.qwen3-8b.ollama.windows-x64',
                    'embedding.qwen3-0.6b-1024.ollama.windows-x64',
                    'reranking.bge-v2-m3.cpu.windows-x64',
                    'ocr.paddleocr-vl-1.6.cpu.windows-x64'
               )
               OR p_completion_token_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'invalid bounded System operation'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.system_operations (
                id, actor_user_id, operation_type, profile_id,
                completion_token_hash
            ) VALUES (
                operation_id, actor_id, p_operation_type, p_profile_id,
                p_completion_token_hash
            );
            INSERT INTO public.audit_events (
                id, actor_user_id, event_type, target_type, target_id, details
            ) VALUES (
                public.gen_random_uuid(), actor_id, 'system_operation_started',
                'system_profile', operation_id,
                jsonb_build_object(
                    'operation_type', p_operation_type,
                    'profile_id', p_profile_id
                )
            );
            RETURN operation_id;
        EXCEPTION
            WHEN unique_violation THEN
                RAISE EXCEPTION 'a profile operation is already running'
                    USING ERRCODE = '40001';
        END;
        $$;

        CREATE FUNCTION public.v8_complete_system_operation(
            p_operation_id uuid,
            p_completion_token_hash text,
            p_succeeded boolean,
            p_reason_code text,
            p_fixture_id text,
            p_metrics jsonb
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            operation_row public.system_operations%ROWTYPE;
            next_state text;
            next_stage text;
        BEGIN
            SELECT * INTO operation_row
            FROM public.system_operations
            WHERE id = p_operation_id FOR UPDATE;
            IF operation_row.id IS NULL
               OR operation_row.state <> 'running'
               OR operation_row.completion_token_hash <> p_completion_token_hash THEN
                RAISE EXCEPTION 'System operation completion is unavailable'
                    USING ERRCODE = '55000';
            END IF;
            IF p_reason_code !~ '^[a-z0-9][a-z0-9._-]{1,63}$'
               OR p_fixture_id !~ '^[a-z0-9][a-z0-9._-]{2,95}$'
               OR jsonb_typeof(p_metrics) <> 'object'
               OR pg_column_size(p_metrics) > 8192
               OR EXISTS (
                    SELECT 1 FROM jsonb_object_keys(p_metrics) AS metric(key)
                    WHERE metric.key NOT IN (
                        'duration_seconds', 'peak_working_set_bytes', 'pages',
                        'samples', 'fixture_sha256', 'result_sha256',
                        'embedding_dimension', 'relevant_score',
                        'irrelevant_score', 'processor'
                    )
               ) THEN
                RAISE EXCEPTION 'invalid sanitized System operation result'
                    USING ERRCODE = '22023';
            END IF;
            next_state := CASE WHEN p_succeeded THEN 'effective' ELSE 'failed' END;
            next_stage := next_state;
            UPDATE public.system_operations
            SET state = next_state,
                stage = next_stage,
                reason_code = p_reason_code,
                metrics = p_metrics,
                completion_token_hash = repeat('0', 64),
                finished_at = statement_timestamp()
            WHERE id = p_operation_id;

            IF operation_row.operation_type = 'profile_validation' THEN
                INSERT INTO public.system_profile_evidence (
                    profile_id, profile_revision, validation_state, reason_code,
                    fixture_id, metrics, operation_id, evidence_at
                ) VALUES (
                    operation_row.profile_id, operation_row.profile_revision,
                    CASE WHEN p_succeeded THEN 'locally_validated' ELSE 'failed' END,
                    p_reason_code, p_fixture_id, p_metrics, p_operation_id,
                    statement_timestamp()
                )
                ON CONFLICT (profile_id) DO UPDATE SET
                    profile_revision = EXCLUDED.profile_revision,
                    validation_state = EXCLUDED.validation_state,
                    reason_code = EXCLUDED.reason_code,
                    fixture_id = EXCLUDED.fixture_id,
                    metrics = EXCLUDED.metrics,
                    operation_id = EXCLUDED.operation_id,
                    evidence_at = EXCLUDED.evidence_at;
            END IF;
            INSERT INTO public.audit_events (
                id, actor_user_id, event_type, target_type, target_id, details
            ) VALUES (
                public.gen_random_uuid(), operation_row.actor_user_id,
                'system_operation_finished', 'system_profile', p_operation_id,
                jsonb_build_object(
                    'profile_id', operation_row.profile_id,
                    'state', next_state,
                    'reason_code', p_reason_code
                )
            );
        END;
        $$;

        CREATE FUNCTION public.v8_advance_system_operation(
            p_operation_id uuid,
            p_completion_token_hash text,
            p_stage text
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            IF p_stage NOT IN ('validating', 'benchmarking') THEN
                RAISE EXCEPTION 'invalid System operation stage'
                    USING ERRCODE = '22023';
            END IF;
            UPDATE public.system_operations
            SET stage = p_stage
            WHERE id = p_operation_id
              AND state = 'running'
              AND completion_token_hash = p_completion_token_hash;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'System operation advance is unavailable'
                    USING ERRCODE = '55000';
            END IF;
        END;
        $$;

        CREATE FUNCTION public.v8_admin_system_operations(p_limit integer)
        RETURNS TABLE (
            operation_id uuid,
            operation_type text,
            profile_id text,
            state text,
            stage text,
            reason_code text,
            metrics jsonb,
            created_at timestamptz,
            finished_at timestamptz
        )
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            IF p_limit NOT BETWEEN 1 AND 100 THEN
                RAISE EXCEPTION 'invalid System operation limit'
                    USING ERRCODE = '22023';
            END IF;
            RETURN QUERY
            SELECT operation.id, operation.operation_type::text,
                   operation.profile_id::text, operation.state::text,
                   operation.stage::text, operation.reason_code::text,
                   operation.metrics, operation.created_at, operation.finished_at
            FROM public.system_operations AS operation
            ORDER BY operation.created_at DESC, operation.id DESC
            LIMIT p_limit;
        END;
        $$;

        CREATE FUNCTION public.v8_admin_system_evidence()
        RETURNS TABLE (
            profile_id text,
            profile_revision integer,
            validation_state text,
            reason_code text,
            fixture_id text,
            metrics jsonb,
            operation_id uuid,
            evidence_at timestamptz
        )
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            RETURN QUERY
            SELECT evidence.profile_id::text, evidence.profile_revision,
                   evidence.validation_state::text, evidence.reason_code::text,
                   evidence.fixture_id::text, evidence.metrics,
                   evidence.operation_id, evidence.evidence_at
            FROM public.system_profile_evidence AS evidence
            ORDER BY evidence.profile_id;
        END;
        $$;

        CREATE FUNCTION public.v8_admin_system_counts()
        RETURNS jsonb
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            RETURN jsonb_build_object(
                'documents', jsonb_build_object(
                    'ready', (SELECT count(*) FROM public.documents WHERE state = 'ready'),
                    'processing', (SELECT count(*) FROM public.documents
                        WHERE state IN ('uploaded','parsing','chunking','embedding','indexing')),
                    'failed', (SELECT count(*) FROM public.documents WHERE state = 'failed')
                ),
                'jobs', jsonb_build_object(
                    'active', (SELECT count(*) FROM public.ingestion_jobs WHERE status = 'running'),
                    'queued', (SELECT count(*) FROM public.ingestion_jobs WHERE status = 'queued')
                ),
                'service_leases', COALESCE((
                    SELECT jsonb_object_agg(
                        lease.service_name,
                        lease.lease_expires_at > statement_timestamp()
                    ) FROM public.service_leases AS lease
                ), '{}'::jsonb)
            );
        END;
        $$;

        REVOKE ALL ON FUNCTION public.v8_admin_start_system_operation(text,text,text)
            FROM PUBLIC, rag_worker, rag_backup, rag_migrator, rag_maintenance;
        REVOKE ALL ON FUNCTION public.v8_complete_system_operation(uuid,text,boolean,text,text,jsonb)
            FROM PUBLIC, rag_worker, rag_backup, rag_migrator, rag_maintenance;
        REVOKE ALL ON FUNCTION public.v8_advance_system_operation(uuid,text,text)
            FROM PUBLIC, rag_worker, rag_backup, rag_migrator, rag_maintenance;
        REVOKE ALL ON FUNCTION public.v8_admin_system_operations(integer)
            FROM PUBLIC, rag_worker, rag_backup, rag_migrator, rag_maintenance;
        REVOKE ALL ON FUNCTION public.v8_admin_system_evidence()
            FROM PUBLIC, rag_worker, rag_backup, rag_migrator, rag_maintenance;
        REVOKE ALL ON FUNCTION public.v8_admin_system_counts()
            FROM PUBLIC, rag_worker, rag_backup, rag_migrator, rag_maintenance;
        GRANT EXECUTE ON FUNCTION public.v8_admin_start_system_operation(text,text,text)
            TO rag_api;
        GRANT EXECUTE ON FUNCTION public.v8_complete_system_operation(uuid,text,boolean,text,text,jsonb)
            TO rag_api;
        GRANT EXECUTE ON FUNCTION public.v8_advance_system_operation(uuid,text,text)
            TO rag_api;
        GRANT EXECUTE ON FUNCTION public.v8_admin_system_operations(integer)
            TO rag_api;
        GRANT EXECUTE ON FUNCTION public.v8_admin_system_evidence() TO rag_api;
        GRANT EXECUTE ON FUNCTION public.v8_admin_system_counts() TO rag_api;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.v4_schema_revision()
        RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ SELECT '0008_system_visibility'::text $$;

        CREATE OR REPLACE FUNCTION public.v5_readiness()
        RETURNS TABLE (
            schema_revision text,
            vector_extension boolean,
            bootstrap_required boolean,
            catalog_integrity boolean
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT '0008_system_visibility'::text,
                   EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'vector'),
                   NOT EXISTS (SELECT 1 FROM public.users
                       WHERE role = 'admin' AND status = 'active' AND deleted_at IS NULL),
                   EXISTS (SELECT 1 FROM public.alembic_version
                       WHERE version_num = '0008_system_visibility')
                   AND EXISTS (
                       SELECT 1 FROM pg_catalog.pg_class AS relation
                       JOIN pg_catalog.pg_roles AS owner ON owner.oid = relation.relowner
                       WHERE relation.oid = 'public.owner_setup'::regclass
                         AND owner.rolname = 'rag_owner'
                         AND relation.relrowsecurity AND relation.relforcerowsecurity
                   )
                   AND (
                       SELECT count(*) = 4
                       FROM pg_catalog.pg_proc AS routine
                       JOIN pg_catalog.pg_roles AS owner ON owner.oid = routine.proowner
                       WHERE routine.oid IN (
                           'public.v8_setup_status()'::regprocedure,
                           'public.v8_issue_setup_code(text,timestamptz)'::regprocedure,
                           'public.v8_verify_setup_code(text,text,timestamptz)'::regprocedure,
                           'public.v8_complete_owner_setup(text,text,text,text)'::regprocedure
                       )
                         AND owner.rolname = 'rag_owner'
                         AND routine.prosecdef
                         AND routine.proconfig = ARRAY['search_path=pg_catalog']::text[]
                   )
                   AND has_function_privilege(
                       'rag_api', 'public.v8_setup_status()', 'EXECUTE'
                   )
                   AND has_function_privilege(
                       'rag_api',
                       'public.v8_verify_setup_code(text,text,timestamptz)',
                       'EXECUTE'
                   )
                   AND has_function_privilege(
                       'rag_api',
                       'public.v8_complete_owner_setup(text,text,text,text)',
                       'EXECUTE'
                   )
                   AND has_function_privilege(
                       'rag_maintenance',
                       'public.v8_issue_setup_code(text,timestamptz)',
                       'EXECUTE'
                   )
                   AND NOT has_table_privilege(
                       'rag_api', 'public.owner_setup', 'SELECT'
                   )
                   AND NOT has_function_privilege(
                       'public', 'public.v8_setup_status()', 'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'public',
                       'public.v8_verify_setup_code(text,text,timestamptz)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'public',
                       'public.v8_complete_owner_setup(text,text,text,text)',
                       'EXECUTE'
                   )
                   AND NOT has_function_privilege(
                       'public',
                       'public.v8_issue_setup_code(text,timestamptz)',
                       'EXECUTE'
                   )
                   AND EXISTS (
                       SELECT 1 FROM pg_catalog.pg_class AS relation
                       JOIN pg_catalog.pg_roles AS owner ON owner.oid = relation.relowner
                       WHERE relation.oid = 'public.system_operations'::regclass
                         AND owner.rolname = 'rag_owner'
                         AND relation.relrowsecurity AND relation.relforcerowsecurity
                   )
                   AND EXISTS (
                       SELECT 1 FROM pg_catalog.pg_class AS relation
                       JOIN pg_catalog.pg_roles AS owner ON owner.oid = relation.relowner
                       WHERE relation.oid = 'public.system_profile_evidence'::regclass
                         AND owner.rolname = 'rag_owner'
                         AND relation.relrowsecurity AND relation.relforcerowsecurity
                   )
                   AND NOT has_table_privilege('rag_api', 'public.system_operations', 'SELECT')
                   AND NOT has_table_privilege('rag_api', 'public.system_profile_evidence', 'SELECT')
                   AND has_function_privilege('rag_api',
                       'public.v8_admin_system_operations(integer)', 'EXECUTE')
                   AND has_function_privilege('rag_api',
                       'public.v8_admin_system_evidence()', 'EXECUTE')
                   AND has_function_privilege('rag_api',
                       'public.v8_admin_system_counts()', 'EXECUTE')
                   AND has_function_privilege('rag_api',
                       'public.v8_admin_start_system_operation(text,text,text)',
                       'EXECUTE')
                   AND has_function_privilege('rag_api',
                       'public.v8_complete_system_operation(uuid,text,boolean,text,text,jsonb)',
                       'EXECUTE')
                   AND has_function_privilege('rag_api',
                       'public.v8_advance_system_operation(uuid,text,text)',
                       'EXECUTE')
                   AND NOT has_function_privilege('public',
                       'public.v8_admin_system_operations(integer)', 'EXECUTE')
                   AND NOT has_function_privilege('public',
                       'public.v8_admin_system_evidence()', 'EXECUTE')
                   AND NOT has_function_privilege('public',
                       'public.v8_admin_system_counts()', 'EXECUTE')
                   AND NOT has_function_privilege('public',
                       'public.v8_admin_start_system_operation(text,text,text)',
                       'EXECUTE')
                   AND NOT has_function_privilege('public',
                       'public.v8_complete_system_operation(uuid,text,boolean,text,text,jsonb)',
                       'EXECUTE')
                   AND NOT has_function_privilege('public',
                       'public.v8_advance_system_operation(uuid,text,text)',
                       'EXECUTE')
        $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("0008_system_visibility is forward-only")
