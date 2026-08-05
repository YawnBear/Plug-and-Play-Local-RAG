import asyncio
import json
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import Settings

pytestmark = pytest.mark.integration

_CONFIRMATION = "V4-SECURITY-DEDICATED-ONLY"
_LOCK_KEY = 7_418_114_725_462_311_904


def _run(coroutine: object) -> object:
    return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)


def _environment() -> tuple[str, str]:
    if os.environ.get("RUN_V4_SECURITY_E2E") != "1":
        pytest.skip("RUN_V4_SECURITY_E2E is not enabled")
    url = os.environ.get("V4_SECURITY_TEST_DATABASE_ADMIN_URL", "")
    name = os.environ.get("V4_SECURITY_TEST_DATABASE_NAME", "")
    confirmation = os.environ.get("V4_SECURITY_DEDICATED_DATABASE_CONFIRM", "")
    if not url or not name:
        pytest.skip("the V4 security test database is not configured")
    if confirmation != _CONFIRMATION:
        pytest.fail(f"expected dedicated-database confirmation {_CONFIRMATION!r}")
    parsed = make_url(url)
    if parsed.database != name or not name.startswith("rag_v4_security_"):
        pytest.fail("V4 security URL must target the named disposable database")
    if make_url(Settings().database_url).database == name:
        pytest.fail("refusing to run V4 security integration against DATABASE_URL")
    return url, name


async def _expect_database_error(
    connection: AsyncConnection,
    statement: str,
    parameters: dict[str, object],
    *,
    message: str,
    sqlstate: str,
) -> None:
    with pytest.raises(DBAPIError, match=message) as error:
        async with connection.begin_nested():
            await connection.execute(text(statement), parameters)
    assert getattr(error.value.orig, "sqlstate", None) == sqlstate


async def _preview_and_apply_acl(
    connection: AsyncConnection, operation: dict[str, object]
) -> dict[str, object]:
    preview = (
        await connection.execute(
            text(
                "SELECT preview_id, impact_digest, impact "
                "FROM v4_admin_preview_acl(CAST(:operation AS jsonb))"
            ),
            {"operation": json.dumps(operation)},
        )
    ).one()
    await connection.execute(
        text("SELECT v4_admin_apply_acl(:preview_id, :impact_digest)"),
        {
            "preview_id": preview.preview_id,
            "impact_digest": preview.impact_digest,
        },
    )
    return preview.impact


def test_folder_write_authorization_and_move_cycle_are_database_enforced() -> None:
    async def exercise() -> None:
        url, database_name = _environment()
        engine = create_async_engine(url)
        connection = await engine.connect()
        transaction = await connection.begin()
        try:
            assert await connection.scalar(text("SELECT current_database()")) == (
                database_name
            )
            assert (
                await connection.scalar(text("SELECT current_setting('is_superuser')"))
                == "on"
            )
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0006_versioned_claim"
            )
            readiness = (
                await connection.execute(
                    text(
                        "SELECT schema_revision, catalog_integrity "
                        "FROM v5_readiness()"
                    )
                )
            ).one()
            assert readiness.schema_revision == "0006_versioned_claim"
            assert readiness.catalog_integrity
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": _LOCK_KEY},
            )
            counts = (
                await connection.execute(
                    text(
                        "SELECT 'users', count(*) FROM users "
                        "UNION ALL SELECT 'documents', count(*) FROM documents "
                        "UNION ALL SELECT 'library_nodes', count(*) "
                        "FROM library_nodes"
                    )
                )
            ).all()
            if any(count for _, count in counts):
                pytest.fail(
                    "refusing reused V4 security database: "
                    + ", ".join(f"{table}={count}" for table, count in counts)
                )

            admin_id = uuid.uuid4()
            member_id = uuid.uuid4()
            uploader_id = uuid.uuid4()
            team_id = uuid.uuid4()
            ancestor_id = uuid.uuid4()
            descendant_id = uuid.uuid4()
            member_created_id = uuid.uuid4()
            source_id = uuid.uuid4()
            visible_document_id = uuid.uuid4()
            visible_file_id = uuid.uuid4()
            owned_document_id = uuid.uuid4()
            owned_file_id = uuid.uuid4()
            admin_token = "a" * 64
            member_token = "b" * 64

            await connection.execute(text("SET LOCAL ROLE rag_owner"))
            await connection.execute(
                text(
                    "INSERT INTO users ("
                    "id, username, display_name, role, status, password_hash"
                    ") VALUES "
                    "(:admin_id, 'security.admin', 'Security Admin', 'admin', "
                    "'active', '$argon2id$test'), "
                    "(:member_id, 'security.member', 'Security Member', 'member', "
                    "'active', '$argon2id$test'), "
                    "(:uploader_id, 'security.uploader', 'Other Uploader', 'member', "
                    "'active', '$argon2id$test')"
                ),
                {
                    "admin_id": admin_id,
                    "member_id": member_id,
                    "uploader_id": uploader_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO teams (id, name, name_key, is_active) "
                    "VALUES (:team_id, 'Security Team', 'security team', true)"
                ),
                {"team_id": team_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO team_members (team_id, user_id) "
                    "VALUES (:team_id, :member_id)"
                ),
                {"team_id": team_id, "member_id": member_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO sessions ("
                    "id, user_id, token_hash, csrf_token_hash, "
                    "issued_authentication_version, "
                    "issued_authentication_epoch, issued_session_epoch, "
                    "issued_at, last_seen_at, idle_expires_at, "
                    "absolute_expires_at"
                    ") "
                    "SELECT gen_random_uuid(), seed.user_id, seed.token_hash, "
                    "repeat(seed.csrf_marker, 64), 1, "
                    "epoch.authentication_version, epoch.session_epoch, "
                    "statement_timestamp(), statement_timestamp(), "
                    "statement_timestamp() + interval '30 minutes', "
                    "statement_timestamp() + interval '30 minutes' "
                    "FROM (VALUES "
                    "(:admin_id, :admin_token, 'c'), "
                    "(:member_id, :member_token, 'd')"
                    ") AS seed(user_id, token_hash, csrf_marker) "
                    "CROSS JOIN security_epochs AS epoch "
                    "WHERE epoch.singleton"
                ),
                {
                    "admin_id": admin_id,
                    "admin_token": admin_token,
                    "member_id": member_id,
                    "member_token": member_token,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO library_nodes ("
                    "id, parent_id, kind, name, name_key, document_id, "
                    "uploader_user_id"
                    ") VALUES "
                    "(:ancestor, NULL, 'folder', 'Ancestor', 'ancestor', NULL, NULL), "
                    "(:descendant, :ancestor, 'folder', 'Descendant', "
                    "'descendant', NULL, NULL), "
                    "(:source, NULL, 'folder', 'Source', 'source', NULL, NULL)"
                ),
                {
                    "ancestor": ancestor_id,
                    "descendant": descendant_id,
                    "source": source_id,
                },
            )
            for document_id, checksum, filename in (
                (visible_document_id, "1" * 64, "visible.pdf"),
                (owned_document_id, "2" * 64, "owned.pdf"),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO documents ("
                        "id, sha256, original_filename, mime_type, byte_size, "
                        "object_key, state, stage, parser_version, "
                        "chunking_version, embedding_version, chunk_count"
                        ") VALUES ("
                        ":id, :sha256, :filename, 'application/pdf', 1, "
                        ":object_key, 'uploaded', 'uploaded', 'test', 'test', "
                        "'test', 0)"
                    ),
                    {
                        "id": document_id,
                        "sha256": checksum,
                        "filename": filename,
                        "object_key": (f"originals/{checksum[:2]}/{checksum}.pdf"),
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO library_nodes ("
                    "id, parent_id, kind, name, name_key, document_id, "
                    "uploader_user_id"
                    ") VALUES "
                    "(:visible_file, :descendant, 'file', 'visible.pdf', "
                    "'visible.pdf', :visible_document, :uploader), "
                    "(:owned_file, :source, 'file', 'owned.pdf', 'owned.pdf', "
                    ":owned_document, :member)"
                ),
                {
                    "visible_file": visible_file_id,
                    "descendant": descendant_id,
                    "visible_document": visible_document_id,
                    "uploader": uploader_id,
                    "owned_file": owned_file_id,
                    "source": source_id,
                    "owned_document": owned_document_id,
                    "member": member_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO access_grants (id, node_id, user_id, team_id) "
                    "VALUES (gen_random_uuid(), :node_id, :user_id, NULL)"
                ),
                {"node_id": visible_file_id, "user_id": member_id},
            )
            await connection.execute(
                text("SELECT v4_rebuild_effective_document_access()")
            )

            await connection.execute(text("SET LOCAL ROLE rag_api"))
            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": member_token},
            )
            assert await connection.scalar(
                text("SELECT v4_can_view_library_node(:node_id)"),
                {"node_id": ancestor_id},
            )
            assert not await connection.scalar(
                text("SELECT v4_can_read_folder(:node_id)"),
                {"node_id": ancestor_id},
            )
            assert not await connection.scalar(
                text("SELECT v4_can_create_children(:node_id)"),
                {"node_id": ancestor_id},
            )
            await _expect_database_error(
                connection,
                "INSERT INTO folder_create_grants ("
                "id, folder_id, user_id, team_id"
                ") VALUES (gen_random_uuid(), :folder_id, :user_id, NULL)",
                {"folder_id": ancestor_id, "user_id": member_id},
                message="permission denied",
                sqlstate="42501",
            )
            await _expect_database_error(
                connection,
                "SELECT v4_create_folder("
                ":node_id, NULL, 'Member root', 'member root')",
                {"node_id": uuid.uuid4()},
                message="library node not found",
                sqlstate="42501",
            )

            denied_upload = await connection.scalar(
                text(
                    "SELECT result_status FROM v4_admin_upload_preflight("
                        ":sha256, :object_key, 'denied.pdf', 'denied.pdf', "
                        "'denied.pdf', 'application/pdf', 1, 'test', 'test', "
                        "'test', :parent_id, ARRAY[:team_id]::uuid[])"
                    ),
                    {
                        "sha256": "3" * 64,
                        "object_key": f"originals/33/{'3' * 64}.pdf",
                        "parent_id": ancestor_id,
                        "team_id": team_id,
                },
            )
            assert denied_upload == "parent_not_found"
            await _expect_database_error(
                connection,
                "SELECT * FROM v4_admin_preview_acl(CAST(:operation AS jsonb))",
                {
                    "operation": json.dumps(
                        {
                            "kind": "move_node",
                            "node_id": str(owned_file_id),
                            "parent_id": str(ancestor_id),
                        }
                    )
                },
                message="capability denied",
                sqlstate="42501",
            )

            await connection.execute(text("RESET ROLE"))
            await connection.execute(text("SET LOCAL ROLE rag_owner"))
            await connection.execute(
                text(
                    "INSERT INTO access_grants (id, node_id, user_id, team_id) "
                    "VALUES (gen_random_uuid(), :node_id, :user_id, NULL)"
                ),
                {"node_id": ancestor_id, "user_id": member_id},
            )
            await connection.execute(
                text("SELECT v4_rebuild_effective_document_access()")
            )
            await connection.execute(text("SET LOCAL ROLE rag_api"))
            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": member_token},
            )
            assert await connection.scalar(
                text("SELECT v4_can_read_folder(:node_id)"),
                {"node_id": descendant_id},
            )
            assert not await connection.scalar(
                text("SELECT v4_can_create_children(:node_id)"),
                {"node_id": descendant_id},
            )
            await _expect_database_error(
                connection,
                "SELECT v4_create_folder("
                ":node_id, :parent_id, 'Denied child', 'denied child')",
                {"node_id": uuid.uuid4(), "parent_id": descendant_id},
                message="library node not found",
                sqlstate="42501",
            )
            allowed_upload = await connection.scalar(
                text(
                    "SELECT result_status FROM v4_admin_upload_preflight("
                        ":sha256, :object_key, 'allowed.pdf', 'allowed.pdf', "
                        "'allowed.pdf', 'application/pdf', 1, 'test', 'test', "
                        "'test', :parent_id, ARRAY[:team_id]::uuid[])"
                    ),
                    {
                        "sha256": "4" * 64,
                        "object_key": f"originals/44/{'4' * 64}.pdf",
                        "parent_id": descendant_id,
                        "team_id": team_id,
                },
            )
            assert allowed_upload == "upload_required"
            move_preview = (
                await connection.execute(
                    text(
                        "SELECT preview_id, impact_digest "
                        "FROM v4_admin_preview_acl(CAST(:operation AS jsonb))"
                    ),
                    {
                        "operation": json.dumps(
                            {
                                "kind": "move_node",
                                "node_id": str(owned_file_id),
                                "parent_id": str(descendant_id),
                            }
                        )
                    },
                )
            ).one()
            assert move_preview.preview_id is not None
            assert len(move_preview.impact_digest) == 64

            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": admin_token},
            )
            create_preview = (
                await connection.execute(
                    text(
                        "SELECT preview_id, impact_digest, impact "
                        "FROM v4_admin_preview_acl(CAST(:operation AS jsonb))"
                    ),
                    {
                        "operation": json.dumps(
                            {
                                "kind": "set_create_children_grant",
                                "folder_id": str(ancestor_id),
                                "user_id": str(member_id),
                                "present": True,
                            }
                        )
                    },
                )
            ).one()
            assert str(member_id) in {
                str(user_id) for user_id in create_preview.impact["user_ids"]
            }
            assert str(descendant_id) in {
                str(node_id) for node_id in create_preview.impact["node_ids"]
            }
            await connection.execute(
                text("SELECT v4_admin_apply_acl(:preview_id, :impact_digest)"),
                {
                    "preview_id": create_preview.preview_id,
                    "impact_digest": create_preview.impact_digest,
                },
            )

            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": member_token},
            )
            assert await connection.scalar(
                text("SELECT v4_can_create_children(:node_id)"),
                {"node_id": descendant_id},
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT v4_create_folder("
                        ":node_id, :parent_id, 'Member child', 'member child')"
                    ),
                    {"node_id": member_created_id, "parent_id": descendant_id},
                )
                == member_created_id
            )
            assert await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM library_nodes "
                    "WHERE id = :node_id)"
                ),
                {"node_id": member_created_id},
            )
            assert await connection.scalar(
                text("SELECT v4_can_create_children(:node_id)"),
                {"node_id": member_created_id},
            )

            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": admin_token},
            )
            capability_move_preview = (
                await connection.execute(
                    text(
                        "SELECT impact "
                        "FROM v4_admin_preview_acl(CAST(:operation AS jsonb))"
                    ),
                    {
                        "operation": json.dumps(
                            {
                                "kind": "move_node",
                                "node_id": str(descendant_id),
                                "parent_id": str(source_id),
                            }
                        )
                    },
                )
            ).one()
            assert str(member_id) in {
                str(user_id)
                for user_id in capability_move_preview.impact["user_ids"]
            }
            assert str(descendant_id) in {
                str(node_id)
                for node_id in capability_move_preview.impact["node_ids"]
            }

            await _preview_and_apply_acl(
                connection,
                {
                    "kind": "set_create_children_grant",
                    "folder_id": str(ancestor_id),
                    "user_id": str(member_id),
                    "present": False,
                },
            )
            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": member_token},
            )
            assert not await connection.scalar(
                text("SELECT v4_can_create_children(:node_id)"),
                {"node_id": descendant_id},
            )

            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": admin_token},
            )
            team_grant_impact = await _preview_and_apply_acl(
                connection,
                {
                    "kind": "set_create_children_grant",
                    "folder_id": str(ancestor_id),
                    "team_id": str(team_id),
                    "present": True,
                },
            )
            assert str(member_id) in {
                str(user_id) for user_id in team_grant_impact["user_ids"]
            }
            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": member_token},
            )
            assert await connection.scalar(
                text("SELECT v4_can_create_children(:node_id)"),
                {"node_id": descendant_id},
            )

            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": admin_token},
            )
            await _preview_and_apply_acl(
                connection,
                {
                    "kind": "set_team_active",
                    "team_id": str(team_id),
                    "active": False,
                },
            )
            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": member_token},
            )
            assert not await connection.scalar(
                text("SELECT v4_can_create_children(:node_id)"),
                {"node_id": descendant_id},
            )

            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": admin_token},
            )
            await _preview_and_apply_acl(
                connection,
                {
                    "kind": "set_team_active",
                    "team_id": str(team_id),
                    "active": True,
                },
            )
            await _preview_and_apply_acl(
                connection,
                {
                    "kind": "set_membership",
                    "team_id": str(team_id),
                    "user_id": str(member_id),
                    "present": False,
                },
            )
            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": member_token},
            )
            assert not await connection.scalar(
                text("SELECT v4_can_create_children(:node_id)"),
                {"node_id": descendant_id},
            )

            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": admin_token},
            )
            await _preview_and_apply_acl(
                connection,
                {
                    "kind": "set_membership",
                    "team_id": str(team_id),
                    "user_id": str(member_id),
                    "present": True,
                },
            )
            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": member_token},
            )
            assert await connection.scalar(
                text("SELECT v4_can_create_children(:node_id)"),
                {"node_id": descendant_id},
            )

            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": admin_token},
            )
            direct_read_preview = (
                await connection.execute(
                    text(
                        "SELECT preview_id, impact_digest "
                        "FROM v4_admin_preview_acl(CAST(:operation AS jsonb))"
                    ),
                    {
                        "operation": json.dumps(
                            {
                                "kind": "set_grant",
                                "node_id": str(descendant_id),
                                "user_id": str(member_id),
                                "present": True,
                            }
                        )
                    },
                )
            ).one()
            await connection.execute(
                text("SELECT v4_admin_apply_acl(:preview_id, :impact_digest)"),
                {
                    "preview_id": direct_read_preview.preview_id,
                    "impact_digest": direct_read_preview.impact_digest,
                },
            )
            boundary_preview = (
                await connection.execute(
                    text(
                        "SELECT preview_id, impact_digest, impact "
                        "FROM v4_admin_preview_acl(CAST(:operation AS jsonb))"
                    ),
                    {
                        "operation": json.dumps(
                            {
                                "kind": "set_boundary",
                                "node_id": str(descendant_id),
                                "enabled": True,
                            }
                        )
                    },
                )
            ).one()
            assert str(member_id) in {
                str(user_id) for user_id in boundary_preview.impact["user_ids"]
            }
            assert str(descendant_id) in {
                str(node_id) for node_id in boundary_preview.impact["node_ids"]
            }
            await connection.execute(
                text("SELECT v4_admin_apply_acl(:preview_id, :impact_digest)"),
                {
                    "preview_id": boundary_preview.preview_id,
                    "impact_digest": boundary_preview.impact_digest,
                },
            )
            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": member_token},
            )
            assert await connection.scalar(
                text("SELECT v4_can_read_folder(:node_id)"),
                {"node_id": descendant_id},
            )
            assert not await connection.scalar(
                text("SELECT v4_can_create_children(:node_id)"),
                {"node_id": descendant_id},
            )

            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": admin_token},
            )
            await _preview_and_apply_acl(
                connection,
                {
                    "kind": "set_create_children_grant",
                    "folder_id": str(descendant_id),
                    "user_id": str(member_id),
                    "present": True,
                },
            )
            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": member_token},
            )
            assert await connection.scalar(
                text("SELECT v4_can_create_children(:node_id)"),
                {"node_id": descendant_id},
            )

            await connection.execute(
                text("SELECT * FROM v4_activate_actor(:token)"),
                {"token": admin_token},
            )
            for target_id in (ancestor_id, descendant_id):
                await _expect_database_error(
                    connection,
                    "SELECT * FROM v4_admin_preview_acl(CAST(:operation AS jsonb))",
                    {
                        "operation": json.dumps(
                            {
                                "kind": "move_node",
                                "node_id": str(ancestor_id),
                                "parent_id": str(target_id),
                            }
                        )
                    },
                    message="move target creates a cycle",
                    sqlstate="22023",
                )
        finally:
            await transaction.rollback()
            await connection.close()
            await engine.dispose()

    _run(exercise())
