# ruff: noqa: E501
"""Add V8D guarded runtime configuration and controller ledgers."""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_runtime_configuration"
down_revision: str | None = "0008_system_visibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL ROLE rag_owner")
    op.execute(
        """
        CREATE TABLE public.runtime_configuration_revisions (
            revision_id varchar(64) COLLATE "C" PRIMARY KEY,
            generation_profile_id varchar(96) COLLATE "C" NOT NULL,
            reranker_profile_id varchar(96) COLLATE "C" NOT NULL,
            ocr_mode varchar(16) COLLATE "C" NOT NULL,
            ocr_profile_id varchar(96) COLLATE "C" NOT NULL,
            ocr_preset_id varchar(32) COLLATE "C" NOT NULL,
            created_by uuid REFERENCES public.users(id),
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            effective boolean NOT NULL DEFAULT false,
            CONSTRAINT ck_runtime_revision_id CHECK (
                revision_id ~ '^[a-z0-9][a-z0-9._-]{7,63}$'
            ),
            CONSTRAINT ck_runtime_generation_profile CHECK (
                generation_profile_id = 'generation.qwen3-8b.ollama.windows-x64'
            ),
            CONSTRAINT ck_runtime_reranker_profile CHECK (
                reranker_profile_id = 'reranking.bge-v2-m3.cpu.windows-x64'
            ),
            CONSTRAINT ck_runtime_ocr_mode CHECK (ocr_mode IN ('auto','explicit')),
            CONSTRAINT ck_runtime_ocr_profile CHECK (
                ocr_profile_id = 'ocr.paddleocr-vl-1.6.cpu.windows-x64'
            ),
            CONSTRAINT ck_runtime_ocr_preset CHECK (ocr_preset_id = 'balanced')
        );
        ALTER TABLE public.runtime_configuration_revisions OWNER TO rag_owner;
        ALTER TABLE public.runtime_configuration_revisions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.runtime_configuration_revisions FORCE ROW LEVEL SECURITY;
        CREATE UNIQUE INDEX uq_runtime_configuration_effective
            ON public.runtime_configuration_revisions (effective) WHERE effective;

        CREATE TABLE public.runtime_configuration_previews (
            id uuid PRIMARY KEY DEFAULT public.gen_random_uuid(),
            actor_user_id uuid NOT NULL REFERENCES public.users(id),
            session_id uuid NOT NULL REFERENCES public.sessions(id),
            base_revision_id varchar(64) COLLATE "C" NOT NULL
                REFERENCES public.runtime_configuration_revisions(revision_id),
            generation_profile_id varchar(96) COLLATE "C" NOT NULL,
            reranker_profile_id varchar(96) COLLATE "C" NOT NULL,
            ocr_mode varchar(16) COLLATE "C" NOT NULL,
            ocr_profile_id varchar(96) COLLATE "C" NOT NULL,
            ocr_preset_id varchar(32) COLLATE "C" NOT NULL,
            impact_digest varchar(64) COLLATE "C" NOT NULL,
            operation_class varchar(24) COLLATE "C" NOT NULL,
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            CONSTRAINT ck_runtime_preview_profiles CHECK (
                generation_profile_id = 'generation.qwen3-8b.ollama.windows-x64'
                AND reranker_profile_id = 'reranking.bge-v2-m3.cpu.windows-x64'
                AND ocr_profile_id = 'ocr.paddleocr-vl-1.6.cpu.windows-x64'
                AND ocr_preset_id = 'balanced'
                AND ocr_mode IN ('auto','explicit')
            ),
            CONSTRAINT ck_runtime_preview_digest CHECK (
                impact_digest ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_runtime_preview_class CHECK (
                operation_class = 'restart_scoped'
            ),
            CONSTRAINT ck_runtime_preview_expiry CHECK (expires_at > created_at)
        );
        ALTER TABLE public.runtime_configuration_previews OWNER TO rag_owner;
        ALTER TABLE public.runtime_configuration_previews ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.runtime_configuration_previews FORCE ROW LEVEL SECURITY;

        CREATE TABLE public.system_reauthentication_grants (
            id uuid PRIMARY KEY DEFAULT public.gen_random_uuid(),
            actor_user_id uuid NOT NULL REFERENCES public.users(id),
            session_id uuid NOT NULL REFERENCES public.sessions(id),
            action varchar(32) COLLATE "C" NOT NULL,
            impact_digest varchar(64) COLLATE "C" NOT NULL,
            token_hash varchar(64) COLLATE "C" NOT NULL UNIQUE,
            issued_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            CONSTRAINT ck_system_reauth_action CHECK (
                action = 'apply_runtime_configuration'
            ),
            CONSTRAINT ck_system_reauth_digests CHECK (
                impact_digest ~ '^[0-9a-f]{64}$'
                AND token_hash ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_system_reauth_expiry CHECK (expires_at > issued_at)
        );
        ALTER TABLE public.system_reauthentication_grants OWNER TO rag_owner;
        ALTER TABLE public.system_reauthentication_grants ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.system_reauthentication_grants FORCE ROW LEVEL SECURITY;

        CREATE TABLE public.backup_restore_verifications (
            backup_run_id uuid PRIMARY KEY REFERENCES public.backup_runs(id),
            manifest_sha256 varchar(64) COLLATE "C" NOT NULL,
            verification_profile varchar(64) COLLATE "C" NOT NULL,
            verified_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            CONSTRAINT ck_backup_restore_manifest CHECK (
                manifest_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_backup_restore_profile CHECK (
                verification_profile = 'personal.isolated-restore.v1'
            )
        );
        ALTER TABLE public.backup_restore_verifications OWNER TO rag_owner;
        ALTER TABLE public.backup_restore_verifications ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.backup_restore_verifications FORCE ROW LEVEL SECURITY;

        CREATE TABLE public.personal_backup_operations (
            backup_run_id uuid PRIMARY KEY REFERENCES public.backup_runs(id),
            actor_user_id uuid NOT NULL REFERENCES public.users(id),
            state varchar(16) COLLATE "C" NOT NULL DEFAULT 'pending',
            stage varchar(24) COLLATE "C" NOT NULL DEFAULT 'queued',
            controller_nonce_hash varchar(64) COLLATE "C" NOT NULL,
            manifest_sha256 varchar(64) COLLATE "C",
            reason_code varchar(64) COLLATE "C",
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            finished_at timestamptz,
            CONSTRAINT ck_personal_backup_state CHECK (
                state IN ('pending','running','succeeded','failed')
            ),
            CONSTRAINT ck_personal_backup_stage CHECK (
                stage IN ('queued','draining','exporting','verifying','succeeded','failed')
            ),
            CONSTRAINT ck_personal_backup_nonce CHECK (
                controller_nonce_hash ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_personal_backup_manifest CHECK (
                manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_personal_backup_reason CHECK (
                reason_code IS NULL OR reason_code ~ '^[a-z0-9][a-z0-9._-]{1,63}$'
            ),
            CONSTRAINT ck_personal_backup_terminal CHECK (
                (state IN ('pending','running') AND finished_at IS NULL)
                OR (state IN ('succeeded','failed') AND finished_at IS NOT NULL)
            )
        );
        ALTER TABLE public.personal_backup_operations OWNER TO rag_owner;
        ALTER TABLE public.personal_backup_operations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.personal_backup_operations FORCE ROW LEVEL SECURITY;
        CREATE UNIQUE INDEX uq_personal_backup_active
            ON public.personal_backup_operations ((true))
            WHERE state IN ('pending','running');
        CREATE INDEX ix_personal_backup_created
            ON public.personal_backup_operations (created_at DESC, backup_run_id DESC);

        CREATE TABLE public.runtime_configuration_changes (
            id uuid PRIMARY KEY DEFAULT public.gen_random_uuid(),
            actor_user_id uuid NOT NULL REFERENCES public.users(id),
            prior_revision_id varchar(64) COLLATE "C" NOT NULL
                REFERENCES public.runtime_configuration_revisions(revision_id),
            desired_revision_id varchar(64) COLLATE "C" NOT NULL
                REFERENCES public.runtime_configuration_revisions(revision_id),
            preview_id uuid NOT NULL UNIQUE REFERENCES public.runtime_configuration_previews(id),
            backup_run_id uuid NOT NULL REFERENCES public.backup_restore_verifications(backup_run_id),
            impact_digest varchar(64) COLLATE "C" NOT NULL,
            operation_class varchar(24) COLLATE "C" NOT NULL,
            state varchar(24) COLLATE "C" NOT NULL DEFAULT 'pending',
            stage varchar(24) COLLATE "C" NOT NULL DEFAULT 'queued',
            controller_nonce_hash varchar(64) COLLATE "C" NOT NULL,
            reason_code varchar(64) COLLATE "C",
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            finished_at timestamptz,
            CONSTRAINT ck_runtime_change_distinct CHECK (
                prior_revision_id <> desired_revision_id
            ),
            CONSTRAINT ck_runtime_change_digest CHECK (
                impact_digest ~ '^[0-9a-f]{64}$'
                AND controller_nonce_hash ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_runtime_change_class CHECK (
                operation_class = 'restart_scoped'
            ),
            CONSTRAINT ck_runtime_change_state CHECK (
                state IN ('pending','applying','effective','failed','rolled_back','cancelled')
            ),
            CONSTRAINT ck_runtime_change_stage CHECK (
                stage IN ('queued','preflight','backing_up','draining','applying',
                          'restarting','validating','effective','failed',
                          'rolling_back','rolled_back','cancelled')
            ),
            CONSTRAINT ck_runtime_change_reason CHECK (
                reason_code IS NULL OR reason_code ~ '^[a-z0-9][a-z0-9._-]{1,63}$'
            ),
            CONSTRAINT ck_runtime_change_terminal CHECK (
                (state IN ('pending','applying') AND finished_at IS NULL)
                OR (state NOT IN ('pending','applying') AND finished_at IS NOT NULL)
            )
        );
        ALTER TABLE public.runtime_configuration_changes OWNER TO rag_owner;
        ALTER TABLE public.runtime_configuration_changes ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.runtime_configuration_changes FORCE ROW LEVEL SECURITY;
        CREATE UNIQUE INDEX uq_runtime_configuration_change_active
            ON public.runtime_configuration_changes ((true))
            WHERE state IN ('pending','applying');
        CREATE INDEX ix_runtime_configuration_changes_created
            ON public.runtime_configuration_changes (created_at DESC, id DESC);

        REVOKE ALL ON TABLE public.runtime_configuration_revisions,
            public.runtime_configuration_previews,
            public.system_reauthentication_grants,
            public.backup_restore_verifications,
            public.personal_backup_operations,
            public.runtime_configuration_changes
            FROM PUBLIC, rag_api, rag_worker, rag_backup, rag_migrator, rag_maintenance;
        GRANT SELECT ON TABLE public.runtime_configuration_revisions,
            public.backup_restore_verifications,
            public.personal_backup_operations,
            public.runtime_configuration_changes TO rag_backup;

        INSERT INTO public.runtime_configuration_revisions (
            revision_id, generation_profile_id, reranker_profile_id, ocr_mode,
            ocr_profile_id, ocr_preset_id, effective
        ) VALUES (
            'v8d-baseline-0001',
            'generation.qwen3-8b.ollama.windows-x64',
            'reranking.bge-v2-m3.cpu.windows-x64',
            'auto',
            'ocr.paddleocr-vl-1.6.cpu.windows-x64',
            'balanced', true
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.v9_admin_start_personal_backup(
            p_controller_nonce_hash text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        DECLARE actor_id uuid; backup_id uuid;
        BEGIN
            PERFORM public.v4_require_admin();
            actor_id := public.v4_current_actor_id();
            IF p_controller_nonce_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'controller nonce is invalid' USING ERRCODE = '22023';
            END IF;
            backup_id := public.v4_begin_backup_run('personal.attended');
            INSERT INTO public.personal_backup_operations (
                backup_run_id, actor_user_id, controller_nonce_hash
            ) VALUES (backup_id, actor_id, p_controller_nonce_hash);
            INSERT INTO public.audit_events (
                id, actor_user_id, event_type, target_type, target_id, details
            ) VALUES (
                public.gen_random_uuid(), actor_id, 'personal_backup_started',
                'backup_run', backup_id,
                jsonb_build_object('destination', 'attended_local_folder')
            );
            RETURN backup_id;
        END;
        $$;

        CREATE FUNCTION public.v9_admin_personal_backup_status()
        RETURNS TABLE (
            backup_run_id uuid, state text, stage text, reason_code text,
            created_at timestamptz, finished_at timestamptz,
            restore_verified boolean, manifest_sha256 text
        ) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM public.v4_require_admin();
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
            LIMIT 1;
        END;
        $$;

        CREATE FUNCTION public.v9_admin_fail_personal_backup_delivery(
            p_backup_run_id uuid, p_controller_nonce_hash text
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            PERFORM public.v4_finish_backup_run(
                p_backup_run_id, false, NULL, NULL, NULL, NULL,
                'controller_unavailable'
            );
            UPDATE public.personal_backup_operations
            SET state = 'failed', stage = 'failed',
                reason_code = 'controller_unavailable',
                controller_nonce_hash = repeat('0', 64),
                finished_at = statement_timestamp()
            WHERE backup_run_id = p_backup_run_id AND state = 'pending'
              AND controller_nonce_hash = p_controller_nonce_hash;
        END;
        $$;

        CREATE FUNCTION public.v9_controller_claim_personal_backup(
            p_backup_run_id uuid, p_controller_nonce_hash text
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            UPDATE public.personal_backup_operations
            SET state = 'running', stage = 'draining'
            WHERE backup_run_id = p_backup_run_id AND state = 'pending'
              AND controller_nonce_hash = p_controller_nonce_hash;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'backup operation is unavailable' USING ERRCODE = '55000';
            END IF;
        END;
        $$;

        CREATE FUNCTION public.v9_controller_advance_personal_backup(
            p_backup_run_id uuid, p_controller_nonce_hash text, p_stage text
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            IF p_stage NOT IN ('draining','exporting','verifying') THEN
                RAISE EXCEPTION 'invalid backup stage' USING ERRCODE = '22023';
            END IF;
            UPDATE public.personal_backup_operations SET stage = p_stage
            WHERE backup_run_id = p_backup_run_id AND state = 'running'
              AND controller_nonce_hash = p_controller_nonce_hash;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'backup operation is unavailable' USING ERRCODE = '55000';
            END IF;
        END;
        $$;

        CREATE FUNCTION public.v9_controller_finish_personal_backup_export(
            p_backup_run_id uuid, p_controller_nonce_hash text,
            p_database_sha256 text, p_manifest_sha256 text,
            p_database_bytes bigint, p_storage_bytes bigint
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            IF NOT public.v4_finish_backup_run(
                p_backup_run_id, true, p_database_sha256, p_manifest_sha256,
                p_database_bytes, p_storage_bytes, NULL
            ) THEN
                RAISE EXCEPTION 'backup run is unavailable' USING ERRCODE = '55000';
            END IF;
            UPDATE public.personal_backup_operations
            SET stage = 'verifying', manifest_sha256 = p_manifest_sha256
            WHERE backup_run_id = p_backup_run_id AND state = 'running'
              AND controller_nonce_hash = p_controller_nonce_hash;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'backup operation is unavailable' USING ERRCODE = '55000';
            END IF;
        END;
        $$;

        CREATE FUNCTION public.v9_controller_fail_personal_backup(
            p_backup_run_id uuid, p_controller_nonce_hash text, p_reason_code text
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            IF p_reason_code !~ '^[a-z0-9][a-z0-9._-]{1,63}$' THEN
                RAISE EXCEPTION 'invalid backup failure' USING ERRCODE = '22023';
            END IF;
            PERFORM public.v4_finish_backup_run(
                p_backup_run_id, false, NULL, NULL, NULL, NULL, p_reason_code
            );
            UPDATE public.personal_backup_operations
            SET state = 'failed', stage = 'failed', reason_code = p_reason_code,
                controller_nonce_hash = repeat('0', 64),
                finished_at = statement_timestamp()
            WHERE backup_run_id = p_backup_run_id AND state = 'running'
              AND controller_nonce_hash = p_controller_nonce_hash;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'backup operation is unavailable' USING ERRCODE = '55000';
            END IF;
        END;
        $$;

        CREATE FUNCTION public.v9_admin_runtime_configuration()
        RETURNS TABLE (
            effective_revision text, desired_revision text, state text,
            generation_profile_id text, reranker_profile_id text,
            ocr_mode text, ocr_profile_id text, ocr_preset_id text,
            impact_digest text, operation_class text, prior_revision text,
            actor_user_id uuid, proposed_at timestamptz, reason_code text,
            backup_verified boolean, backup_verified_at timestamptz
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            RETURN QUERY
            WITH effective AS (
                SELECT * FROM public.runtime_configuration_revisions WHERE effective
            ), active AS (
                SELECT change.* FROM public.runtime_configuration_changes AS change
                WHERE change.state IN ('pending','applying')
                ORDER BY change.created_at DESC LIMIT 1
            ), selected AS (
                SELECT COALESCE(active.desired_revision_id, effective.revision_id) AS revision_id
                FROM effective LEFT JOIN active ON true
            ), backup AS (
                SELECT verification.verified_at
                FROM public.backup_restore_verifications AS verification
                JOIN public.backup_runs AS run ON run.id = verification.backup_run_id
                WHERE run.status = 'succeeded'
                ORDER BY verification.verified_at DESC LIMIT 1
            )
            SELECT effective.revision_id::text, selected.revision_id::text,
                   COALESCE(active.state, 'effective')::text,
                   desired.generation_profile_id::text,
                   desired.reranker_profile_id::text, desired.ocr_mode::text,
                   desired.ocr_profile_id::text, desired.ocr_preset_id::text,
                   active.impact_digest::text, active.operation_class::text,
                   active.prior_revision_id::text, active.actor_user_id,
                   active.created_at, active.reason_code::text,
                   (backup.verified_at IS NOT NULL), backup.verified_at
            FROM effective
            JOIN selected ON true
            JOIN public.runtime_configuration_revisions AS desired
              ON desired.revision_id = selected.revision_id
            LEFT JOIN active ON true
            LEFT JOIN backup ON true;
        END;
        $$;

        CREATE FUNCTION public.v9_admin_preview_runtime_configuration(
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
               OR p_ocr_profile_id <> 'ocr.paddleocr-vl-1.6.cpu.windows-x64'
               OR p_ocr_preset_id <> 'balanced'
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

        CREATE FUNCTION public.v9_admin_issue_reauthentication_grant(
            p_preview_id uuid, p_action text, p_impact_digest text, p_token_hash text
        ) RETURNS timestamptz
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        DECLARE
            actor_id uuid;
            current_session_id uuid;
            expiry timestamptz := statement_timestamp() + interval '5 minutes';
        BEGIN
            PERFORM public.v4_require_admin();
            actor_id := public.v4_current_actor_id();
            SELECT session.id INTO STRICT current_session_id
            FROM public.sessions AS session
            WHERE session.token_hash = current_setting('rag.session_token_hash', true)
              AND session.revoked_at IS NULL;
            IF p_action <> 'apply_runtime_configuration'
               OR p_impact_digest !~ '^[0-9a-f]{64}$'
               OR p_token_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'invalid reauthentication grant'
                    USING ERRCODE = '22023';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.runtime_configuration_previews AS preview
                WHERE preview.id = p_preview_id
                  AND preview.actor_user_id = actor_id
                  AND preview.session_id = current_session_id
                  AND preview.impact_digest = p_impact_digest
                  AND preview.consumed_at IS NULL
                  AND preview.expires_at > statement_timestamp()
            ) THEN
                RAISE EXCEPTION 'runtime configuration preview is stale'
                    USING ERRCODE = '40001';
            END IF;
            INSERT INTO public.system_reauthentication_grants (
                actor_user_id, session_id, action, impact_digest,
                token_hash, expires_at
            ) VALUES (
                actor_id, current_session_id, p_action, p_impact_digest,
                p_token_hash, expiry
            );
            UPDATE public.sessions
            SET recent_reauthenticated_at = statement_timestamp(),
                last_seen_at = statement_timestamp()
            WHERE id = current_session_id;
            RETURN expiry;
        END;
        $$;

        CREATE FUNCTION public.v9_admin_apply_runtime_configuration(
            p_preview_id uuid, p_impact_digest text, p_grant_token_hash text,
            p_controller_nonce_hash text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        DECLARE
            actor_id uuid;
            current_session_id uuid;
            preview public.runtime_configuration_previews%ROWTYPE;
            grant_row public.system_reauthentication_grants%ROWTYPE;
            backup_id uuid;
            change_id uuid := public.gen_random_uuid();
            revision_value text := 'v8d-' || replace(change_id::text, '-', '');
            current_revision text;
        BEGIN
            PERFORM public.v4_require_admin();
            actor_id := public.v4_current_actor_id();
            SELECT session.id INTO STRICT current_session_id
            FROM public.sessions AS session
            WHERE session.token_hash = current_setting('rag.session_token_hash', true)
              AND session.revoked_at IS NULL;
            SELECT * INTO preview FROM public.runtime_configuration_previews
            WHERE id = p_preview_id FOR UPDATE;
            SELECT revision_id INTO STRICT current_revision
            FROM public.runtime_configuration_revisions WHERE effective FOR UPDATE;
            IF preview.id IS NULL OR preview.actor_user_id <> actor_id
               OR preview.session_id <> current_session_id
               OR preview.consumed_at IS NOT NULL
               OR preview.expires_at <= statement_timestamp()
               OR preview.impact_digest <> p_impact_digest
               OR preview.base_revision_id <> current_revision THEN
                RAISE EXCEPTION 'runtime configuration preview is stale'
                    USING ERRCODE = '40001';
            END IF;
            SELECT * INTO grant_row FROM public.system_reauthentication_grants
            WHERE token_hash = p_grant_token_hash FOR UPDATE;
            IF grant_row.id IS NULL OR grant_row.actor_user_id <> actor_id
               OR grant_row.session_id <> current_session_id
               OR grant_row.action <> 'apply_runtime_configuration'
               OR grant_row.impact_digest <> p_impact_digest
               OR grant_row.consumed_at IS NOT NULL
               OR grant_row.expires_at <= statement_timestamp() THEN
                RAISE EXCEPTION 'reauthentication grant is invalid or expired'
                    USING ERRCODE = '28000';
            END IF;
            IF p_controller_nonce_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'controller nonce is invalid' USING ERRCODE = '22023';
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
            INSERT INTO public.runtime_configuration_revisions (
                revision_id, generation_profile_id, reranker_profile_id, ocr_mode,
                ocr_profile_id, ocr_preset_id, created_by
            ) VALUES (
                revision_value, preview.generation_profile_id,
                preview.reranker_profile_id, preview.ocr_mode,
                preview.ocr_profile_id, preview.ocr_preset_id, actor_id
            );
            INSERT INTO public.runtime_configuration_changes (
                id, actor_user_id, prior_revision_id, desired_revision_id,
                preview_id, backup_run_id, impact_digest, operation_class,
                controller_nonce_hash
            ) VALUES (
                change_id, actor_id, current_revision, revision_value,
                preview.id, backup_id, p_impact_digest,
                preview.operation_class, p_controller_nonce_hash
            );
            UPDATE public.runtime_configuration_previews
            SET consumed_at = statement_timestamp() WHERE id = preview.id;
            UPDATE public.system_reauthentication_grants
            SET consumed_at = statement_timestamp() WHERE id = grant_row.id;
            INSERT INTO public.audit_events (
                id, actor_user_id, event_type, target_type, target_id, details
            ) VALUES (
                public.gen_random_uuid(), actor_id, 'runtime_configuration_requested',
                'runtime_configuration', change_id,
                jsonb_build_object('impact_digest', p_impact_digest,
                                   'operation_class', preview.operation_class,
                                   'prior_revision', current_revision,
                                   'desired_revision', revision_value)
            );
            RETURN change_id;
        EXCEPTION WHEN unique_violation THEN
            RAISE EXCEPTION 'a runtime configuration change is already active'
                USING ERRCODE = '40001';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.v9_admin_runtime_configuration_changes(p_limit integer)
        RETURNS TABLE (
            change_id uuid, actor_user_id uuid, prior_revision text,
            desired_revision text, impact_digest text, operation_class text,
            state text, stage text, reason_code text, created_at timestamptz,
            finished_at timestamptz
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            PERFORM public.v4_require_admin();
            IF p_limit NOT BETWEEN 1 AND 100 THEN
                RAISE EXCEPTION 'invalid runtime change limit' USING ERRCODE = '22023';
            END IF;
            RETURN QUERY SELECT change.id, change.actor_user_id,
                change.prior_revision_id::text, change.desired_revision_id::text,
                change.impact_digest::text, change.operation_class::text,
                change.state::text, change.stage::text, change.reason_code::text,
                change.created_at, change.finished_at
            FROM public.runtime_configuration_changes AS change
            ORDER BY change.created_at DESC, change.id DESC LIMIT p_limit;
        END;
        $$;

        CREATE FUNCTION public.v9_admin_fail_runtime_configuration_delivery(
            p_change_id uuid, p_controller_nonce_hash text
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        DECLARE actor_id uuid := public.v4_current_actor_id();
        BEGIN
            PERFORM public.v4_require_admin();
            UPDATE public.runtime_configuration_changes
            SET state = 'failed', stage = 'failed',
                reason_code = 'controller_unavailable',
                controller_nonce_hash = repeat('0', 64),
                finished_at = statement_timestamp()
            WHERE id = p_change_id AND actor_user_id = actor_id
              AND state = 'pending'
              AND controller_nonce_hash = p_controller_nonce_hash;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'runtime configuration delivery is unavailable'
                    USING ERRCODE = '55000';
            END IF;
            INSERT INTO public.audit_events (
                id, actor_user_id, event_type, target_type, target_id, details
            ) VALUES (
                public.gen_random_uuid(), actor_id,
                'runtime_configuration_delivery_failed',
                'runtime_configuration', p_change_id,
                jsonb_build_object('result', 'failed',
                                   'reason_code', 'controller_unavailable')
            );
        END;
        $$;

        CREATE FUNCTION public.v9_controller_runtime_configuration_change(
            p_change_id uuid, p_controller_nonce_hash text
        ) RETURNS TABLE (prior_configuration jsonb, desired_configuration jsonb)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            UPDATE public.runtime_configuration_changes
            SET state = 'applying', stage = 'preflight'
            WHERE id = p_change_id AND state = 'pending'
              AND controller_nonce_hash = p_controller_nonce_hash;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'controller change is unavailable' USING ERRCODE = '55000';
            END IF;
            RETURN QUERY
            SELECT jsonb_build_object(
                       'generation_profile_id', prior.generation_profile_id,
                       'reranker_profile_id', prior.reranker_profile_id,
                       'ocr_mode', prior.ocr_mode,
                       'ocr_profile_id', prior.ocr_profile_id,
                       'ocr_preset_id', prior.ocr_preset_id),
                   jsonb_build_object(
                       'generation_profile_id', desired.generation_profile_id,
                       'reranker_profile_id', desired.reranker_profile_id,
                       'ocr_mode', desired.ocr_mode,
                       'ocr_profile_id', desired.ocr_profile_id,
                       'ocr_preset_id', desired.ocr_preset_id)
            FROM public.runtime_configuration_changes AS change
            JOIN public.runtime_configuration_revisions AS prior
              ON prior.revision_id = change.prior_revision_id
            JOIN public.runtime_configuration_revisions AS desired
              ON desired.revision_id = change.desired_revision_id
            WHERE change.id = p_change_id;
        END;
        $$;

        CREATE FUNCTION public.v9_controller_advance_runtime_configuration(
            p_change_id uuid, p_controller_nonce_hash text, p_stage text
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            IF p_stage NOT IN ('backing_up','draining','applying','restarting',
                               'validating','rolling_back') THEN
                RAISE EXCEPTION 'invalid controller stage' USING ERRCODE = '22023';
            END IF;
            UPDATE public.runtime_configuration_changes SET stage = p_stage
            WHERE id = p_change_id AND state = 'applying'
              AND controller_nonce_hash = p_controller_nonce_hash;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'controller change is unavailable' USING ERRCODE = '55000';
            END IF;
        END;
        $$;

        CREATE FUNCTION public.v9_controller_finish_runtime_configuration(
            p_change_id uuid, p_controller_nonce_hash text,
            p_result text, p_reason_code text
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        DECLARE change_row public.runtime_configuration_changes%ROWTYPE;
        BEGIN
            SELECT * INTO change_row FROM public.runtime_configuration_changes
            WHERE id = p_change_id AND state = 'applying'
              AND controller_nonce_hash = p_controller_nonce_hash FOR UPDATE;
            IF change_row.id IS NULL
               OR p_result NOT IN ('effective','failed','rolled_back')
               OR p_reason_code !~ '^[a-z0-9][a-z0-9._-]{1,63}$' THEN
                RAISE EXCEPTION 'invalid controller result' USING ERRCODE = '22023';
            END IF;
            IF p_result = 'effective' THEN
                UPDATE public.runtime_configuration_revisions SET effective = false
                WHERE revision_id = change_row.prior_revision_id;
                UPDATE public.runtime_configuration_revisions SET effective = true
                WHERE revision_id = change_row.desired_revision_id;
            END IF;
            UPDATE public.runtime_configuration_changes
            SET state = p_result, stage = p_result, reason_code = p_reason_code,
                controller_nonce_hash = repeat('0', 64),
                finished_at = statement_timestamp()
            WHERE id = p_change_id;
            INSERT INTO public.audit_events (
                id, actor_user_id, event_type, target_type, target_id, details
            ) VALUES (
                public.gen_random_uuid(), change_row.actor_user_id,
                'runtime_configuration_finished', 'runtime_configuration', p_change_id,
                jsonb_build_object('impact_digest', change_row.impact_digest,
                                   'result', p_result,
                                   'effective_revision', CASE WHEN p_result = 'effective'
                                       THEN change_row.desired_revision_id
                                       ELSE change_row.prior_revision_id END,
                                   'reason_code', p_reason_code)
            );
        END;
        $$;

        CREATE FUNCTION public.v9_controller_record_restore_verification(
            p_backup_run_id uuid, p_controller_nonce_hash text,
            p_manifest_sha256 text, p_verification_profile text
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
        AS $$
        BEGIN
            IF p_manifest_sha256 !~ '^[0-9a-f]{64}$'
               OR p_verification_profile <> 'personal.isolated-restore.v1'
               OR NOT EXISTS (SELECT 1 FROM public.backup_runs
                              WHERE id = p_backup_run_id AND status = 'succeeded')
               OR NOT EXISTS (
                    SELECT 1 FROM public.personal_backup_operations
                    WHERE backup_run_id = p_backup_run_id AND state = 'running'
                      AND stage = 'verifying'
                      AND controller_nonce_hash = p_controller_nonce_hash
               ) THEN
                RAISE EXCEPTION 'invalid restore verification evidence'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.backup_restore_verifications (
                backup_run_id, manifest_sha256, verification_profile
            ) VALUES (p_backup_run_id, p_manifest_sha256, p_verification_profile);
            UPDATE public.personal_backup_operations
            SET state = 'succeeded', stage = 'succeeded',
                controller_nonce_hash = repeat('0', 64),
                finished_at = statement_timestamp()
            WHERE backup_run_id = p_backup_run_id;
            INSERT INTO public.audit_events (
                id, actor_user_id, event_type, target_type, target_id, details
            )
            SELECT public.gen_random_uuid(), operation.actor_user_id,
                   'personal_backup_restore_verified', 'backup_run', p_backup_run_id,
                   jsonb_build_object('manifest_sha256', p_manifest_sha256,
                                      'verification_profile', p_verification_profile)
            FROM public.personal_backup_operations AS operation
            WHERE operation.backup_run_id = p_backup_run_id;
        END;
        $$;
        """
    )
    op.execute(
        """
        REVOKE ALL ON FUNCTION public.v9_admin_runtime_configuration(),
            public.v9_admin_start_personal_backup(text),
            public.v9_admin_personal_backup_status(),
            public.v9_admin_fail_personal_backup_delivery(uuid,text),
            public.v9_admin_preview_runtime_configuration(text,text,text,text,text,text),
            public.v9_admin_issue_reauthentication_grant(uuid,text,text,text),
            public.v9_admin_apply_runtime_configuration(uuid,text,text,text),
            public.v9_admin_runtime_configuration_changes(integer),
            public.v9_admin_fail_runtime_configuration_delivery(uuid,text),
            public.v9_controller_runtime_configuration_change(uuid,text),
            public.v9_controller_advance_runtime_configuration(uuid,text,text),
            public.v9_controller_finish_runtime_configuration(uuid,text,text,text),
            public.v9_controller_claim_personal_backup(uuid,text),
            public.v9_controller_advance_personal_backup(uuid,text,text),
            public.v9_controller_finish_personal_backup_export(uuid,text,text,text,bigint,bigint),
            public.v9_controller_fail_personal_backup(uuid,text,text),
            public.v9_controller_record_restore_verification(uuid,text,text,text)
            FROM PUBLIC, rag_worker, rag_backup, rag_migrator, rag_maintenance;
        GRANT EXECUTE ON FUNCTION public.v9_admin_runtime_configuration(),
            public.v9_admin_start_personal_backup(text),
            public.v9_admin_personal_backup_status(),
            public.v9_admin_fail_personal_backup_delivery(uuid,text),
            public.v9_admin_preview_runtime_configuration(text,text,text,text,text,text),
            public.v9_admin_issue_reauthentication_grant(uuid,text,text,text),
            public.v9_admin_apply_runtime_configuration(uuid,text,text,text),
            public.v9_admin_runtime_configuration_changes(integer),
            public.v9_admin_fail_runtime_configuration_delivery(uuid,text),
            public.v9_controller_runtime_configuration_change(uuid,text),
            public.v9_controller_advance_runtime_configuration(uuid,text,text),
            public.v9_controller_finish_runtime_configuration(uuid,text,text,text),
            public.v9_controller_claim_personal_backup(uuid,text),
            public.v9_controller_advance_personal_backup(uuid,text,text),
            public.v9_controller_finish_personal_backup_export(uuid,text,text,text,bigint,bigint),
            public.v9_controller_fail_personal_backup(uuid,text,text),
            public.v9_controller_record_restore_verification(uuid,text,text,text)
            TO rag_api;

        CREATE FUNCTION public.v9_runtime_configuration_integrity()
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT (SELECT count(*) = 6 FROM pg_catalog.pg_class AS relation
                    JOIN pg_catalog.pg_roles AS owner ON owner.oid = relation.relowner
                    WHERE relation.oid IN (
                        'public.runtime_configuration_revisions'::regclass,
                        'public.runtime_configuration_previews'::regclass,
                        'public.system_reauthentication_grants'::regclass,
                        'public.backup_restore_verifications'::regclass,
                        'public.personal_backup_operations'::regclass,
                        'public.runtime_configuration_changes'::regclass)
                      AND owner.rolname = 'rag_owner'
                      AND relation.relrowsecurity AND relation.relforcerowsecurity)
               AND NOT has_table_privilege('rag_api',
                    'public.runtime_configuration_changes', 'SELECT')
               AND NOT has_table_privilege('rag_api',
                    'public.system_reauthentication_grants', 'SELECT')
               AND has_function_privilege('rag_api',
                    'public.v9_admin_runtime_configuration()', 'EXECUTE')
               AND has_function_privilege('rag_api',
                    'public.v9_controller_runtime_configuration_change(uuid,text)',
                    'EXECUTE')
               AND NOT has_function_privilege('public',
                    'public.v9_admin_apply_runtime_configuration(uuid,text,text,text)',
                    'EXECUTE')
               AND (SELECT count(*) = 1
                    FROM public.runtime_configuration_revisions WHERE effective)
        $$;
        REVOKE ALL ON FUNCTION public.v9_runtime_configuration_integrity()
            FROM PUBLIC, rag_worker, rag_backup, rag_migrator, rag_maintenance;
        GRANT EXECUTE ON FUNCTION public.v9_runtime_configuration_integrity() TO rag_api;

        ALTER FUNCTION public.v5_readiness() RENAME TO v8_readiness_base;
        REVOKE ALL ON FUNCTION public.v8_readiness_base() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.v8_readiness_base() TO rag_api;
        CREATE FUNCTION public.v5_readiness()
        RETURNS TABLE (
            schema_revision text, vector_extension boolean,
            bootstrap_required boolean, catalog_integrity boolean
        ) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
        AS $$
            SELECT '0009_runtime_configuration'::text,
                   base.vector_extension, base.bootstrap_required,
                   base.catalog_integrity
                       AND public.v9_runtime_configuration_integrity()
            FROM public.v8_readiness_base() AS base
        $$;
        REVOKE ALL ON FUNCTION public.v5_readiness() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.v5_readiness() TO rag_api;

        CREATE OR REPLACE FUNCTION public.v4_schema_revision()
        RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ SELECT '0009_runtime_configuration'::text $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("0009_runtime_configuration is forward-only")
