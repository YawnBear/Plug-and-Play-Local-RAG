from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = API_ROOT / "alembic" / "versions" / "0001_v4_baseline.py"


def _migration() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_fresh_v4_baseline_is_the_only_schema_root() -> None:
    versions = sorted(
        path.name
        for path in (API_ROOT / "alembic" / "versions").glob("*.py")
        if path.name != "__init__.py"
    )

    assert versions == [
        "0001_v4_baseline.py",
        "0002_v5_citation_highlights.py",
        "0003_v6_ingestion_version_guard.py",
        "0004_create_children_capability.py",
        "0005_document_reingest_action.py",
        "0006_versioned_claim.py",
        "0007_owner_setup.py",
        "0008_system_visibility.py",
        "0009_runtime_configuration.py",
        "0010_versioned_reprocessing.py",
        "0011_v8f_release_maintenance.py",
        "0012_dynamic_ocr_tuning.py",
        "0013_effective_runtime_identity.py",
        "0014_restart_without_backup.py",
    ]
    assert 'revision: str = "0001_v4_baseline"' in _migration()
    assert "v3_" not in _migration()


def test_file_uploader_constraint_and_effective_access_are_database_enforced() -> None:
    source = _migration()

    assert "uploader_user_id UUID" in source
    assert "ck_library_nodes_kind_uploader" in source
    assert "node.uploader_user_id =" in source
    assert "public.v4_current_actor_id()" in source
    assert "node.uploader_user_id AS user_id" in source
    assert "uploader_user_id) REFERENCES users (id) ON DELETE RESTRICT" in source


def test_upload_contract_keeps_hidden_duplicates_opaque_and_grants_atomically() -> None:
    source = _migration()

    assert source.count("'duplicate_forbidden'::text") == 2
    assert "p_selected_team_ids uuid[]" in source
    assert "invalid upload team selection" in source
    assert "members must upload into a folder" in source
    assert source.count("AND public.v4_can_read_folder(id)") == 2
    assert "AND public.v4_can_view_library_node(id)" not in source
    assert "p_name_key, p_document_id, v_actor" in source
    assert "public.gen_random_uuid(), p_node_id, v_actor, NULL" in source
    assert "FROM unnest(p_selected_team_ids) AS selected(team_id)" in source


def test_folder_write_capability_is_not_descendant_navigation_visibility() -> None:
    source = _migration()
    read_folder = source[
        source.index("CREATE FUNCTION v4_can_read_folder") : source.index(
            "CREATE FUNCTION v4_can_view_library_node"
        )
    ]
    preview = source[
        source.index("CREATE FUNCTION v4_admin_preview_acl") : source.index(
            "CREATE FUNCTION v4_admin_apply_acl"
        )
    ]
    apply_acl = source[
        source.index("CREATE FUNCTION v4_admin_apply_acl") : source.index(
            "CREATE FUNCTION v4_claim_service_lease"
        )
    ]

    assert "public.v4_current_actor_is_admin()" in read_folder
    assert "WITH RECURSIVE ancestry AS" in read_folder
    assert "WHERE NOT child.boundary_seen" in read_folder
    assert "grant_row.node_id = ancestry.id" in read_folder
    assert "descendants" not in read_folder
    assert "v4_can_read_folder(target.id)" in preview
    assert "v4_can_read_folder(target.id)" in apply_acl
    assert "v4_can_view_library_node" not in preview
    assert "v4_can_view_library_node" not in apply_acl
    assert source.count("v4_can_read_folder(target.id)") == 2
    assert source.count("AND public.v4_can_read_folder(id)") == 2


def test_member_management_and_authoritative_admin_contracts_are_controlled() -> None:
    source = _migration()

    assert source.count("node.uploader_user_id = v_actor") >= 3
    assert "CREATE FUNCTION v4_account_active_teams()" in source
    assert "CREATE FUNCTION v4_document_team_recipients" in source
    assert "CREATE FUNCTION v4_admin_access_context" in source
    assert "'user_count', jsonb_array_length(v_principals)" in source
    assert "'node_count', jsonb_array_length(v_nodes)" in source
    assert "'document_count', jsonb_array_length(v_documents)" in source
    assert "array_agg(member.user_id ORDER BY member.user_id)" in source
    assert "count(member.user_id)::bigint AS member_count" in source
