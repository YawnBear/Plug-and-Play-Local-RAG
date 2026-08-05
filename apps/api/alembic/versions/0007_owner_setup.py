"""Add the bounded one-time Personal owner setup contract.

The setup code and browser challenge are stored only as SHA-256 digests. The
database serializes issuance, verification, and owner creation, and the
existing v4_bootstrap_admin function remains the sole account bootstrap
authority.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_owner_setup"
down_revision: str | None = "0006_versioned_claim"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL ROLE rag_owner")
    op.execute(
        """
        CREATE TABLE public.owner_setup (
            singleton boolean PRIMARY KEY DEFAULT true,
            code_hash varchar(64) COLLATE "C",
            code_issued_at timestamptz,
            code_expires_at timestamptz,
            failed_attempts smallint NOT NULL DEFAULT 0,
            max_attempts smallint NOT NULL DEFAULT 5,
            challenge_hash varchar(64) COLLATE "C",
            challenge_expires_at timestamptz,
            consumed_at timestamptz,
            CONSTRAINT ck_owner_setup_singleton CHECK (singleton),
            CONSTRAINT ck_owner_setup_attempts CHECK (
                failed_attempts BETWEEN 0 AND max_attempts AND max_attempts = 5
            ),
            CONSTRAINT ck_owner_setup_code_hash CHECK (
                code_hash IS NULL OR code_hash ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_owner_setup_challenge_hash CHECK (
                challenge_hash IS NULL OR challenge_hash ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_owner_setup_code_window CHECK (
                (code_hash IS NULL AND code_issued_at IS NULL
                 AND code_expires_at IS NULL)
                OR
                (code_hash IS NOT NULL AND code_issued_at IS NOT NULL
                 AND code_expires_at > code_issued_at)
            ),
            CONSTRAINT ck_owner_setup_challenge_window CHECK (
                (challenge_hash IS NULL AND challenge_expires_at IS NULL)
                OR
                (challenge_hash IS NOT NULL AND challenge_expires_at IS NOT NULL)
            )
        );
        ALTER TABLE public.owner_setup OWNER TO rag_owner;
        ALTER TABLE public.owner_setup ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.owner_setup FORCE ROW LEVEL SECURITY;
        INSERT INTO public.owner_setup (singleton) VALUES (true);
        REVOKE ALL ON TABLE public.owner_setup FROM PUBLIC;
        GRANT SELECT ON TABLE public.owner_setup TO rag_backup;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.v8_setup_status()
        RETURNS TABLE (
            setup_state text,
            code_expires_at timestamptz,
            attempts_remaining integer
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT
                CASE WHEN EXISTS (
                    SELECT 1 FROM public.users
                    WHERE role = 'admin' AND status = 'active'
                      AND deleted_at IS NULL
                ) THEN 'setup_complete' ELSE 'setup_required' END,
                CASE WHEN setup_row.consumed_at IS NULL
                     THEN setup_row.code_expires_at ELSE NULL END,
                CASE WHEN setup_row.consumed_at IS NULL
                     THEN greatest(
                         setup_row.max_attempts - setup_row.failed_attempts, 0
                     )::integer
                     ELSE 0 END
            FROM public.owner_setup AS setup_row
            WHERE setup_row.singleton
        $$;

        CREATE FUNCTION public.v8_issue_setup_code(
            p_code_hash text,
            p_expires_at timestamptz
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v8-owner-setup', 0)
            );
            IF EXISTS (SELECT 1 FROM public.users) THEN
                RAISE EXCEPTION 'owner setup is unavailable'
                    USING ERRCODE = '55000';
            END IF;
            IF p_code_hash !~ '^[0-9a-f]{64}$'
               OR p_expires_at <= statement_timestamp()
               OR p_expires_at > statement_timestamp() + interval '15 minutes' THEN
                RAISE EXCEPTION 'invalid owner setup code contract'
                    USING ERRCODE = '22023';
            END IF;
            UPDATE public.owner_setup
            SET code_hash = p_code_hash,
                code_issued_at = statement_timestamp(),
                code_expires_at = p_expires_at,
                failed_attempts = 0,
                max_attempts = 5,
                challenge_hash = NULL,
                challenge_expires_at = NULL,
                consumed_at = NULL
            WHERE singleton;
        END;
        $$;

        CREATE FUNCTION public.v8_verify_setup_code(
            p_code_hash text,
            p_challenge_hash text,
            p_challenge_expires_at timestamptz
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            setup_row public.owner_setup%ROWTYPE;
            next_failures integer;
        BEGIN
            IF p_code_hash !~ '^[0-9a-f]{64}$'
               OR p_challenge_hash !~ '^[0-9a-f]{64}$'
               OR p_challenge_expires_at <= statement_timestamp()
               OR p_challenge_expires_at >
                    statement_timestamp() + interval '10 minutes' THEN
                RETURN 'rejected';
            END IF;
            SELECT * INTO setup_row
            FROM public.owner_setup WHERE singleton FOR UPDATE;
            IF EXISTS (SELECT 1 FROM public.users)
               OR setup_row.consumed_at IS NOT NULL THEN
                RETURN 'unavailable';
            END IF;
            IF setup_row.code_hash IS NULL OR setup_row.code_expires_at IS NULL
               OR setup_row.code_expires_at <= statement_timestamp() THEN
                RETURN 'expired';
            END IF;
            IF setup_row.failed_attempts >= setup_row.max_attempts THEN
                RETURN 'locked';
            END IF;
            IF setup_row.code_hash <> p_code_hash THEN
                next_failures := least(
                    setup_row.failed_attempts + 1, setup_row.max_attempts
                );
                UPDATE public.owner_setup
                SET failed_attempts = next_failures,
                    challenge_hash = NULL,
                    challenge_expires_at = NULL
                WHERE singleton;
                IF next_failures >= setup_row.max_attempts THEN
                    RETURN 'locked';
                END IF;
                RETURN 'rejected';
            END IF;
            UPDATE public.owner_setup
            SET challenge_hash = p_challenge_hash,
                challenge_expires_at = p_challenge_expires_at
            WHERE singleton;
            RETURN 'accepted';
        END;
        $$;

        CREATE FUNCTION public.v8_complete_owner_setup(
            p_challenge_hash text,
            p_username text,
            p_display_name text,
            p_password_hash text
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            setup_row public.owner_setup%ROWTYPE;
            owner_id uuid;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v8-owner-setup', 0)
            );
            SELECT * INTO setup_row
            FROM public.owner_setup WHERE singleton FOR UPDATE;
            IF setup_row.consumed_at IS NOT NULL
               OR setup_row.code_hash IS NULL
               OR setup_row.code_expires_at <= statement_timestamp()
               OR setup_row.challenge_hash IS NULL
               OR setup_row.challenge_expires_at <= statement_timestamp()
               OR setup_row.challenge_hash <> p_challenge_hash
               OR setup_row.failed_attempts >= setup_row.max_attempts
               OR EXISTS (SELECT 1 FROM public.users) THEN
                RAISE EXCEPTION 'owner setup is unavailable'
                    USING ERRCODE = '55000';
            END IF;
            owner_id := public.v4_bootstrap_admin(
                p_username, p_display_name, p_password_hash
            );
            UPDATE public.owner_setup
            SET code_hash = NULL,
                code_issued_at = NULL,
                code_expires_at = NULL,
                challenge_hash = NULL,
                challenge_expires_at = NULL,
                consumed_at = statement_timestamp()
            WHERE singleton;
            RETURN owner_id;
        END;
        $$;

        REVOKE ALL ON FUNCTION public.v8_setup_status()
            FROM PUBLIC, rag_worker, rag_backup, rag_migrator;
        REVOKE ALL ON FUNCTION public.v8_issue_setup_code(text, timestamptz)
            FROM PUBLIC, rag_api, rag_worker, rag_backup, rag_migrator;
        REVOKE ALL ON FUNCTION public.v8_verify_setup_code(text, text, timestamptz)
            FROM PUBLIC, rag_worker, rag_maintenance, rag_backup, rag_migrator;
        REVOKE ALL ON FUNCTION public.v8_complete_owner_setup(text, text, text, text)
            FROM PUBLIC, rag_worker, rag_maintenance, rag_backup, rag_migrator;
        GRANT EXECUTE ON FUNCTION public.v8_setup_status() TO rag_api, rag_maintenance;
        GRANT EXECUTE ON FUNCTION public.v8_issue_setup_code(text, timestamptz)
            TO rag_maintenance;
        GRANT EXECUTE ON FUNCTION public.v8_verify_setup_code(text, text, timestamptz)
            TO rag_api;
        GRANT EXECUTE ON FUNCTION
            public.v8_complete_owner_setup(text, text, text, text) TO rag_api;
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
        AS $$ SELECT '0007_owner_setup'::text $$;

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
            SELECT '0007_owner_setup'::text,
                   EXISTS (
                       SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'vector'
                   ),
                   NOT EXISTS (
                       SELECT 1 FROM public.users
                       WHERE role = 'admin' AND status = 'active'
                         AND deleted_at IS NULL
                   ),
                   EXISTS (
                       SELECT 1 FROM public.alembic_version
                       WHERE version_num = '0007_owner_setup'
                   )
                   AND EXISTS (
                       SELECT 1
                       FROM pg_catalog.pg_class AS relation
                       JOIN pg_catalog.pg_roles AS owner
                         ON owner.oid = relation.relowner
                       WHERE relation.oid = 'public.owner_setup'::regclass
                         AND owner.rolname = 'rag_owner'
                         AND relation.relrowsecurity
                         AND relation.relforcerowsecurity
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
        $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("0007_owner_setup is forward-only")
