"""Add the independently granted create-subfolders capability."""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_create_children_capability"
down_revision: str | None = "0003_v6_ingestion_version_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL ROLE rag_owner")
    op.execute(
        """
        CREATE TABLE public.folder_create_grants (
            id uuid PRIMARY KEY,
            folder_id uuid NOT NULL
                REFERENCES public.library_nodes(id) ON DELETE CASCADE,
            user_id uuid REFERENCES public.users(id) ON DELETE CASCADE,
            team_id uuid REFERENCES public.teams(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
            CONSTRAINT ck_folder_create_grants_one_principal CHECK (
                (user_id IS NOT NULL AND team_id IS NULL)
                OR (user_id IS NULL AND team_id IS NOT NULL)
            ),
            CONSTRAINT uq_folder_create_grants_folder_principal
                UNIQUE NULLS NOT DISTINCT (folder_id, user_id, team_id)
        );
        CREATE INDEX ix_folder_create_grants_user_id
            ON public.folder_create_grants(user_id);
        CREATE INDEX ix_folder_create_grants_team_id
            ON public.folder_create_grants(team_id);

        CREATE FUNCTION public.v4_require_folder_create_grant_target()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM public.library_nodes
                WHERE id = NEW.folder_id AND kind = 'folder'
            ) THEN
                RAISE EXCEPTION 'create-children grant target is not a folder'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_folder_create_grants_folder_target
        BEFORE INSERT OR UPDATE OF folder_id
        ON public.folder_create_grants
        FOR EACH ROW EXECUTE FUNCTION
            public.v4_require_folder_create_grant_target();

        ALTER TABLE public.folder_create_grants ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.folder_create_grants FORCE ROW LEVEL SECURITY;
        CREATE POLICY folder_create_grants_admin_inspection
        ON public.folder_create_grants FOR SELECT
        USING (public.v4_current_actor_is_admin());

        REVOKE ALL ON TABLE public.folder_create_grants FROM PUBLIC;
        REVOKE ALL ON TABLE public.folder_create_grants
            FROM rag_api, rag_worker, rag_maintenance, rag_backup, rag_migrator;
        GRANT SELECT ON TABLE public.folder_create_grants TO rag_backup;
        REVOKE ALL ON FUNCTION
            public.v4_require_folder_create_grant_target() FROM PUBLIC;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.v4_can_create_children(p_folder_id uuid)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.library_nodes
                WHERE id = p_folder_id AND kind = 'folder'
            ) AND (
                public.v4_current_actor_is_admin()
                OR (
                    public.v4_can_read_folder(p_folder_id)
                    AND EXISTS (
                        WITH RECURSIVE ancestry AS (
                            SELECT node.id, node.parent_id,
                                   node.access_boundary AS boundary_seen
                            FROM public.library_nodes AS node
                            WHERE node.id = p_folder_id
                              AND node.kind = 'folder'
                            UNION ALL
                            SELECT parent.id, parent.parent_id,
                                   child.boundary_seen
                                       OR parent.access_boundary
                            FROM public.library_nodes AS parent
                            JOIN ancestry AS child
                              ON parent.id = child.parent_id
                            WHERE NOT child.boundary_seen
                        )
                        SELECT 1
                        FROM ancestry
                        JOIN public.folder_create_grants AS grant_row
                          ON grant_row.folder_id = ancestry.id
                        LEFT JOIN public.team_members AS membership
                          ON membership.team_id = grant_row.team_id
                         AND membership.user_id =
                             public.v4_current_actor_id()
                        LEFT JOIN public.teams AS team
                          ON team.id = grant_row.team_id
                         AND team.is_active
                        WHERE grant_row.user_id =
                                  public.v4_current_actor_id()
                           OR membership.user_id IS NOT NULL
                              AND team.id IS NOT NULL
                        LIMIT 1
                    )
                )
            )
        $$;

        CREATE OR REPLACE FUNCTION public.v4_can_view_library_node(
            p_node_id uuid
        )
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT public.v4_current_actor_is_admin()
                OR EXISTS (
                    SELECT 1 FROM public.library_nodes AS target
                    WHERE target.id = p_node_id
                      AND target.kind = 'folder'
                      AND public.v4_can_read_folder(target.id)
                )
                OR EXISTS (
                    WITH RECURSIVE descendants AS (
                        SELECT n.id, n.document_id
                        FROM public.library_nodes AS n
                        WHERE n.id = p_node_id
                        UNION ALL
                        SELECT child.id, child.document_id
                        FROM public.library_nodes AS child
                        JOIN descendants AS parent
                          ON child.parent_id = parent.id
                    )
                    SELECT 1
                    FROM descendants AS node
                    JOIN public.effective_document_access AS access
                      ON access.document_id = node.document_id
                    JOIN public.security_epochs AS epoch ON epoch.singleton
                    WHERE access.user_id = public.v4_current_actor_id()
                      AND access.authorization_version =
                          epoch.authorization_version
                    UNION ALL
                    SELECT 1
                    FROM descendants AS granted_descendant
                    JOIN public.access_grants AS grant_row
                      ON grant_row.node_id = granted_descendant.id
                    LEFT JOIN public.team_members AS membership
                      ON membership.team_id = grant_row.team_id
                    LEFT JOIN public.teams AS team
                      ON team.id = grant_row.team_id
                    WHERE grant_row.user_id = public.v4_current_actor_id()
                       OR membership.user_id = public.v4_current_actor_id()
                          AND team.is_active
                    LIMIT 1
                )
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.v4_create_folder(
            p_node_id uuid,
            p_parent_id uuid,
            p_name text,
            p_name_key text
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_actor uuid := public.v4_current_actor_id();
            v_is_admin boolean := public.v4_current_actor_is_admin();
            v_parent_depth integer;
        BEGIN
            IF v_actor IS NULL THEN
                RAISE EXCEPTION 'authentication required'
                    USING ERRCODE = '28000';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-library-acl', 0)
            );
            IF p_node_id IS NULL
               OR p_name IS NULL
               OR p_name <> btrim(p_name)
               OR char_length(p_name) NOT BETWEEN 1 AND 255
               OR p_name_key IS NULL
               OR char_length(p_name_key) = 0
               OR octet_length(p_name_key) > 1024 THEN
                RAISE EXCEPTION 'invalid library folder'
                    USING ERRCODE = '22023';
            END IF;
            IF NOT v_is_admin AND (
                p_parent_id IS NULL
                OR NOT public.v4_can_create_children(p_parent_id)
            ) THEN
                RAISE EXCEPTION 'library node not found'
                    USING ERRCODE = '42501';
            END IF;
            IF p_parent_id IS NOT NULL THEN
                WITH RECURSIVE ancestry AS (
                    SELECT id, parent_id, 1 AS depth
                    FROM public.library_nodes WHERE id = p_parent_id
                    UNION ALL
                    SELECT parent.id, parent.parent_id, child.depth + 1
                    FROM public.library_nodes AS parent
                    JOIN ancestry AS child ON parent.id = child.parent_id
                    WHERE child.depth < 257
                )
                SELECT max(depth) INTO v_parent_depth FROM ancestry;
                IF v_parent_depth IS NULL
                   OR NOT EXISTS (
                       SELECT 1 FROM public.library_nodes
                       WHERE id = p_parent_id AND kind = 'folder'
                   ) THEN
                    IF v_is_admin THEN
                        RAISE EXCEPTION 'invalid library folder'
                            USING ERRCODE = '22023';
                    END IF;
                    RAISE EXCEPTION 'library node not found'
                        USING ERRCODE = '42501';
                END IF;
                IF v_parent_depth >= 256 THEN
                    RAISE EXCEPTION 'library exceeds maximum depth'
                        USING ERRCODE = '22023';
                END IF;
            END IF;
            IF (SELECT count(*) FROM public.library_nodes) >= 10000 THEN
                RAISE EXCEPTION 'library exceeds maximum node count'
                    USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.library_nodes (
                id, parent_id, kind, name, name_key, document_id
            ) VALUES (
                p_node_id, p_parent_id, 'folder', p_name, p_name_key, NULL
            );
            PERFORM public.v4_append_audit(
                'folder_created', 'library_node', p_node_id, '{}'::jsonb
            );
            RETURN p_node_id;
        END;
        $$;
        REVOKE ALL ON FUNCTION
            public.v4_create_folder(uuid, uuid, text, text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
            public.v4_create_folder(uuid, uuid, text, text) TO rag_api;
        REVOKE ALL ON FUNCTION
            public.v4_can_create_children(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
            public.v4_can_create_children(uuid) TO rag_api;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.v4_admin_access_context(
            p_node_id uuid
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE v_result jsonb;
        BEGIN
            PERFORM public.v4_require_admin();
            IF NOT EXISTS (
                SELECT 1 FROM public.library_nodes WHERE id = p_node_id
            ) THEN
                RAISE EXCEPTION 'library node not found'
                    USING ERRCODE = 'P0002';
            END IF;
            WITH RECURSIVE ancestry AS (
                SELECT node.id, node.parent_id, node.access_boundary, 0 AS depth
                FROM public.library_nodes AS node WHERE node.id = p_node_id
                UNION ALL
                SELECT parent.id, parent.parent_id, parent.access_boundary,
                       child.depth + 1
                FROM public.library_nodes AS parent
                JOIN ancestry AS child ON parent.id = child.parent_id
                WHERE NOT child.access_boundary
            )
            SELECT jsonb_build_object(
                'node_id', p_node_id,
                'nearest_boundary_node_id', (
                    SELECT id FROM ancestry WHERE access_boundary
                    ORDER BY depth LIMIT 1
                ),
                'direct_grants', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'id', g.id, 'node_id', g.node_id,
                        'user_id', g.user_id, 'team_id', g.team_id
                    ) ORDER BY g.id)
                    FROM public.access_grants AS g
                    WHERE g.node_id = p_node_id
                ), '[]'::jsonb),
                'inherited_grants', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'source_node_id', g.node_id,
                        'user_id', g.user_id, 'team_id', g.team_id
                    ) ORDER BY g.node_id, g.user_id, g.team_id)
                    FROM ancestry JOIN public.access_grants AS g
                      ON g.node_id = ancestry.id
                    WHERE ancestry.depth > 0
                ), '[]'::jsonb),
                'direct_create_grants', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'id', g.id, 'node_id', g.folder_id,
                        'user_id', g.user_id, 'team_id', g.team_id
                    ) ORDER BY g.id)
                    FROM public.folder_create_grants AS g
                    WHERE g.folder_id = p_node_id
                ), '[]'::jsonb),
                'inherited_create_grants', COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'source_node_id', g.folder_id,
                        'user_id', g.user_id, 'team_id', g.team_id
                    ) ORDER BY g.folder_id, g.user_id, g.team_id)
                    FROM ancestry JOIN public.folder_create_grants AS g
                      ON g.folder_id = ancestry.id
                    WHERE ancestry.depth > 0
                ), '[]'::jsonb)
            ) INTO v_result;
            RETURN v_result;
        END;
        $$;
        """
    )
    op.execute(
        """
        ALTER FUNCTION public.v4_acl_impact(jsonb)
            RENAME TO v4_acl_impact_without_create_children;
        ALTER FUNCTION public.v4_admin_apply_acl(uuid, text)
            RENAME TO v4_admin_apply_acl_without_create_children;
        REVOKE ALL ON FUNCTION
            public.v4_acl_impact_without_create_children(jsonb)
            FROM PUBLIC, rag_api;
        REVOKE ALL ON FUNCTION
            public.v4_admin_apply_acl_without_create_children(uuid, text)
            FROM PUBLIC, rag_api;

        CREATE FUNCTION public.v4_acl_impact(p_operation jsonb)
        RETURNS jsonb
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_kind text := p_operation->>'kind';
            v_read jsonb;
            v_users jsonb;
            v_nodes jsonb;
            v_documents jsonb;
        BEGIN
            IF v_kind = 'set_create_children_grant' THEN
                IF p_operation IS NULL
                   OR jsonb_typeof(p_operation) <> 'object'
                   OR (SELECT count(*) FROM jsonb_object_keys(p_operation)) <> 4
                   OR NOT p_operation ? 'kind'
                   OR NOT p_operation ? 'folder_id'
                   OR NOT p_operation ? 'present'
                   OR (p_operation ? 'user_id') =
                      (p_operation ? 'team_id')
                   OR jsonb_typeof(p_operation->'kind') <> 'string'
                   OR jsonb_typeof(p_operation->'folder_id') <> 'string'
                   OR (p_operation->>'folder_id') !~
                      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                   OR jsonb_typeof(p_operation->'present') <> 'boolean'
                   OR (
                       p_operation ? 'user_id'
                       AND (
                           jsonb_typeof(p_operation->'user_id') <> 'string'
                           OR (p_operation->>'user_id') !~
                              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                       )
                   )
                   OR (
                       p_operation ? 'team_id'
                       AND (
                           jsonb_typeof(p_operation->'team_id') <> 'string'
                           OR (p_operation->>'team_id') !~
                              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                       )
                   )
                   OR NOT EXISTS (
                       SELECT 1 FROM public.library_nodes
                       WHERE id = (p_operation->>'folder_id')::uuid
                         AND kind = 'folder'
                   ) THEN
                    RAISE EXCEPTION 'invalid create-children grant operation'
                        USING ERRCODE = '22023';
                END IF;
                v_read := jsonb_build_object(
                    'user_ids', '[]'::jsonb, 'node_ids', '[]'::jsonb,
                    'document_ids', '[]'::jsonb
                );
            ELSE
                v_read :=
                    public.v4_acl_impact_without_create_children(p_operation);
            END IF;

            WITH RECURSIVE
            nodes_after AS (
                SELECT node.id,
                       CASE WHEN v_kind = 'move_node'
                              AND node.id = (p_operation->>'node_id')::uuid
                            THEN NULLIF(p_operation->>'parent_id', '')::uuid
                            ELSE node.parent_id END AS parent_id,
                       node.kind,
                       CASE WHEN v_kind = 'set_boundary'
                              AND node.id = (p_operation->>'node_id')::uuid
                            THEN (p_operation->>'enabled')::boolean
                            ELSE node.access_boundary END AS access_boundary
                FROM public.library_nodes AS node
            ),
            read_grants_after AS (
                SELECT node_id, user_id, team_id
                FROM public.access_grants AS grant_row
                WHERE NOT (
                    v_kind = 'set_grant'
                    AND node_id = (p_operation->>'node_id')::uuid
                    AND user_id IS NOT DISTINCT FROM
                        NULLIF(p_operation->>'user_id', '')::uuid
                    AND team_id IS NOT DISTINCT FROM
                        NULLIF(p_operation->>'team_id', '')::uuid
                )
                UNION
                SELECT (p_operation->>'node_id')::uuid,
                       NULLIF(p_operation->>'user_id', '')::uuid,
                       NULLIF(p_operation->>'team_id', '')::uuid
                WHERE v_kind = 'set_grant'
                  AND (p_operation->>'present')::boolean
            ),
            create_grants_after AS (
                SELECT folder_id, user_id, team_id
                FROM public.folder_create_grants AS grant_row
                WHERE NOT (
                    v_kind = 'set_create_children_grant'
                    AND folder_id = (p_operation->>'folder_id')::uuid
                    AND user_id IS NOT DISTINCT FROM
                        NULLIF(p_operation->>'user_id', '')::uuid
                    AND team_id IS NOT DISTINCT FROM
                        NULLIF(p_operation->>'team_id', '')::uuid
                )
                UNION
                SELECT (p_operation->>'folder_id')::uuid,
                       NULLIF(p_operation->>'user_id', '')::uuid,
                       NULLIF(p_operation->>'team_id', '')::uuid
                WHERE v_kind = 'set_create_children_grant'
                  AND (p_operation->>'present')::boolean
            ),
            memberships_after AS (
                SELECT team_id, user_id FROM public.team_members AS membership
                WHERE NOT (
                    v_kind = 'set_membership'
                    AND team_id = (p_operation->>'team_id')::uuid
                    AND user_id = (p_operation->>'user_id')::uuid
                )
                UNION
                SELECT (p_operation->>'team_id')::uuid,
                       (p_operation->>'user_id')::uuid
                WHERE v_kind = 'set_membership'
                  AND (p_operation->>'present')::boolean
            ),
            teams_after AS (
                SELECT id, CASE WHEN v_kind = 'set_team_active'
                                      AND id = (p_operation->>'team_id')::uuid
                                    THEN (p_operation->>'active')::boolean
                                    ELSE is_active END AS is_active
                FROM public.teams
            ),
            paths_before AS (
                SELECT node.id AS folder_id, node.id AS ancestor_id,
                       node.parent_id, node.access_boundary AS boundary_seen
                FROM public.library_nodes AS node WHERE node.kind = 'folder'
                UNION ALL
                SELECT path.folder_id, parent.id, parent.parent_id,
                       path.boundary_seen OR parent.access_boundary
                FROM paths_before AS path
                JOIN public.library_nodes AS parent
                  ON parent.id = path.parent_id
                WHERE NOT path.boundary_seen
            ),
            paths_after AS (
                SELECT node.id AS folder_id, node.id AS ancestor_id,
                       node.parent_id, node.access_boundary AS boundary_seen
                FROM nodes_after AS node WHERE node.kind = 'folder'
                UNION ALL
                SELECT path.folder_id, parent.id, parent.parent_id,
                       path.boundary_seen OR parent.access_boundary
                FROM paths_after AS path
                JOIN nodes_after AS parent ON parent.id = path.parent_id
                WHERE NOT path.boundary_seen
            ),
            read_before AS (
                SELECT path.folder_id, grant_row.user_id AS user_id
                FROM paths_before AS path
                JOIN public.access_grants AS grant_row
                  ON grant_row.node_id = path.ancestor_id
                WHERE grant_row.user_id IS NOT NULL
                UNION
                SELECT path.folder_id, membership.user_id
                FROM paths_before AS path
                JOIN public.access_grants AS grant_row
                  ON grant_row.node_id = path.ancestor_id
                JOIN public.team_members AS membership
                  ON membership.team_id = grant_row.team_id
                JOIN public.teams AS team
                  ON team.id = membership.team_id AND team.is_active
            ),
            read_after AS (
                SELECT path.folder_id, grant_row.user_id AS user_id
                FROM paths_after AS path
                JOIN read_grants_after AS grant_row
                  ON grant_row.node_id = path.ancestor_id
                WHERE grant_row.user_id IS NOT NULL
                UNION
                SELECT path.folder_id, membership.user_id
                FROM paths_after AS path
                JOIN read_grants_after AS grant_row
                  ON grant_row.node_id = path.ancestor_id
                JOIN memberships_after AS membership
                  ON membership.team_id = grant_row.team_id
                JOIN teams_after AS team
                  ON team.id = membership.team_id AND team.is_active
            ),
            create_before AS (
                SELECT path.folder_id, grant_row.user_id AS user_id
                FROM paths_before AS path
                JOIN public.folder_create_grants AS grant_row
                  ON grant_row.folder_id = path.ancestor_id
                WHERE grant_row.user_id IS NOT NULL
                UNION
                SELECT path.folder_id, membership.user_id
                FROM paths_before AS path
                JOIN public.folder_create_grants AS grant_row
                  ON grant_row.folder_id = path.ancestor_id
                JOIN public.team_members AS membership
                  ON membership.team_id = grant_row.team_id
                JOIN public.teams AS team
                  ON team.id = membership.team_id AND team.is_active
            ),
            create_after AS (
                SELECT path.folder_id, grant_row.user_id AS user_id
                FROM paths_after AS path
                JOIN create_grants_after AS grant_row
                  ON grant_row.folder_id = path.ancestor_id
                WHERE grant_row.user_id IS NOT NULL
                UNION
                SELECT path.folder_id, membership.user_id
                FROM paths_after AS path
                JOIN create_grants_after AS grant_row
                  ON grant_row.folder_id = path.ancestor_id
                JOIN memberships_after AS membership
                  ON membership.team_id = grant_row.team_id
                JOIN teams_after AS team
                  ON team.id = membership.team_id AND team.is_active
            ),
            effective_before AS (
                SELECT DISTINCT read_row.user_id, read_row.folder_id
                FROM read_before AS read_row
                JOIN create_before AS create_row
                  USING (user_id, folder_id)
                JOIN public.users AS account ON account.id = read_row.user_id
                WHERE account.role = 'member' AND account.status = 'active'
                  AND account.deleted_at IS NULL
            ),
            effective_after AS (
                SELECT DISTINCT read_row.user_id, read_row.folder_id
                FROM read_after AS read_row
                JOIN create_after AS create_row
                  USING (user_id, folder_id)
                JOIN public.users AS account ON account.id = read_row.user_id
                WHERE account.role = 'member' AND account.status = 'active'
                  AND account.deleted_at IS NULL
            ),
            changed AS (
                (SELECT * FROM effective_before EXCEPT
                 SELECT * FROM effective_after)
                UNION
                (SELECT * FROM effective_after EXCEPT
                 SELECT * FROM effective_before)
            ),
            merged_users AS (
                SELECT value::uuid AS id
                FROM jsonb_array_elements_text(v_read->'user_ids')
                UNION SELECT user_id FROM changed
            ),
            merged_nodes AS (
                SELECT value::uuid AS id
                FROM jsonb_array_elements_text(v_read->'node_ids')
                UNION SELECT folder_id FROM changed
            )
            SELECT COALESCE((SELECT jsonb_agg(id ORDER BY id)
                             FROM merged_users), '[]'::jsonb),
                   COALESCE((SELECT jsonb_agg(id ORDER BY id)
                             FROM merged_nodes), '[]'::jsonb),
                   COALESCE(v_read->'document_ids', '[]'::jsonb)
            INTO v_users, v_nodes, v_documents;
            RETURN jsonb_build_object(
                'operation', p_operation, 'user_ids', v_users,
                'node_ids', v_nodes, 'document_ids', v_documents,
                'user_count', jsonb_array_length(v_users),
                'node_count', jsonb_array_length(v_nodes),
                'document_count', jsonb_array_length(v_documents)
            );
        END;
        $$;

        CREATE FUNCTION public.v4_admin_apply_acl(
            p_preview_id uuid, p_impact_digest text
        )
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_actor uuid := public.v4_current_actor_id();
            v_preview public.acl_previews%ROWTYPE;
            v_version bigint;
        BEGIN
            IF v_actor IS NULL THEN
                RAISE EXCEPTION 'authentication required'
                    USING ERRCODE = '28000';
            END IF;
            PERFORM public.v4_require_admin();
            SELECT * INTO STRICT v_preview
            FROM public.acl_previews WHERE id = p_preview_id;
            IF v_preview.operation->>'kind' <>
               'set_create_children_grant' THEN
                RETURN public.v4_admin_apply_acl_without_create_children(
                    p_preview_id, p_impact_digest
                );
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-library-acl', 0)
            );
            SELECT * INTO STRICT v_preview
            FROM public.acl_previews WHERE id = p_preview_id FOR UPDATE;
            SELECT authorization_version INTO STRICT v_version
            FROM public.security_epochs WHERE singleton FOR UPDATE;
            IF v_preview.actor_user_id <> v_actor
               OR v_preview.impact_digest <> p_impact_digest
               OR v_preview.authorization_version <> v_version
               OR v_preview.expires_at <= statement_timestamp()
               OR v_preview.consumed_at IS NOT NULL
               OR encode(public.digest(convert_to(
                    public.v4_acl_impact(v_preview.operation)::text, 'UTF8'
               ), 'sha256'), 'hex') <> p_impact_digest THEN
                RAISE EXCEPTION 'stale or invalid ACL preview'
                    USING ERRCODE = '40001';
            END IF;
            IF (v_preview.operation->>'present')::boolean THEN
                INSERT INTO public.folder_create_grants(
                    id, folder_id, user_id, team_id
                ) VALUES (
                    public.gen_random_uuid(),
                    (v_preview.operation->>'folder_id')::uuid,
                    NULLIF(v_preview.operation->>'user_id', '')::uuid,
                    NULLIF(v_preview.operation->>'team_id', '')::uuid
                ) ON CONFLICT DO NOTHING;
            ELSE
                DELETE FROM public.folder_create_grants
                WHERE folder_id =
                      (v_preview.operation->>'folder_id')::uuid
                  AND user_id IS NOT DISTINCT FROM
                      NULLIF(v_preview.operation->>'user_id', '')::uuid
                  AND team_id IS NOT DISTINCT FROM
                      NULLIF(v_preview.operation->>'team_id', '')::uuid;
            END IF;
            UPDATE public.security_epochs
            SET authorization_version = authorization_version + 1,
                updated_at = statement_timestamp()
            WHERE singleton RETURNING authorization_version INTO v_version;
            UPDATE public.acl_previews
            SET consumed_at = statement_timestamp()
            WHERE id = p_preview_id;
            PERFORM public.v4_append_audit(
                'acl_operation_applied', 'acl_preview', p_preview_id,
                jsonb_build_object(
                    'kind', 'set_create_children_grant',
                    'folder_id', v_preview.operation->>'folder_id',
                    'user_id', v_preview.operation->'user_id',
                    'team_id', v_preview.operation->'team_id',
                    'present', v_preview.operation->'present'
                )
            );
            RETURN v_version;
        END;
        $$;
        REVOKE ALL ON FUNCTION public.v4_acl_impact(jsonb) FROM PUBLIC;
        REVOKE ALL ON FUNCTION
            public.v4_admin_apply_acl(uuid, text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
            public.v4_admin_apply_acl(uuid, text) TO rag_api;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.v4_admin_set_user(
            p_user_id uuid, p_role text, p_status text
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE v_current public.users%ROWTYPE;
        BEGIN
            PERFORM public.v4_require_admin();
            SELECT * INTO v_current FROM public.users
            WHERE id = p_user_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'user state changed or no longer exists'
                    USING ERRCODE = 'RAG02';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('rag-v4-final-admin', 0)
            );
            IF p_role NOT IN ('admin', 'member')
               OR p_status NOT IN ('active', 'disabled', 'deleted') THEN
                RAISE EXCEPTION 'invalid user state' USING ERRCODE = '22023';
            END IF;
            IF v_current.status = 'deleted' THEN
                RAISE EXCEPTION 'deleted users are irreversible'
                    USING ERRCODE = 'RAG04';
            END IF;
            IF v_current.role = 'admin'
               AND v_current.status = 'active'
               AND (p_role <> 'admin' OR p_status <> 'active')
               AND (SELECT count(*) FROM public.users
                    WHERE role = 'admin' AND status = 'active'
                      AND deleted_at IS NULL) <= 1 THEN
                RAISE EXCEPTION 'cannot remove the final enabled administrator'
                    USING ERRCODE = 'RAG03';
            END IF;
            UPDATE public.users SET role = p_role, status = p_status,
                password_hash = CASE WHEN p_status = 'deleted'
                    THEN NULL ELSE password_hash END,
                deleted_at = CASE WHEN p_status = 'deleted'
                    THEN statement_timestamp() ELSE NULL END,
                authentication_version = authentication_version + 1,
                updated_at = statement_timestamp()
            WHERE id = p_user_id;
            UPDATE public.sessions SET revoked_at = statement_timestamp()
            WHERE user_id = p_user_id AND revoked_at IS NULL;
            IF p_status <> 'active' THEN
                DELETE FROM public.team_members WHERE user_id = p_user_id;
                DELETE FROM public.access_grants WHERE user_id = p_user_id;
                DELETE FROM public.folder_create_grants
                WHERE user_id = p_user_id;
            END IF;
            PERFORM public.v4_rebuild_effective_document_access();
            PERFORM public.v4_append_audit(
                'user_state_changed', 'user', p_user_id,
                jsonb_build_object('role', p_role, 'status', p_status)
            );
        END;
        $$;

        CREATE OR REPLACE FUNCTION public.v4_schema_revision()
        RETURNS text
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ SELECT '0004_create_children_capability'::text $$;

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
            SELECT '0004_create_children_capability'::text,
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
                       WHERE version_num = '0004_create_children_capability'
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
                       SELECT count(*) = 9
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
                           'public.v5_readiness()'::regprocedure
                       )
                         AND owner.rolname = 'rag_owner'
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
    raise RuntimeError("0004_create_children_capability is forward-only")
