import asyncio
import hashlib
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


async def _activate(connection: AsyncConnection, token: str) -> None:
    await connection.execute(text("SET LOCAL ROLE rag_api"))
    row = (
        await connection.execute(
            text("SELECT * FROM v4_activate_actor(:token)"),
            {"token": token},
        )
    ).one()
    assert row.user_id is not None


async def _prepare(
    connection: AsyncConnection, document_id: uuid.UUID
) -> object:
    return (
        await connection.execute(
            text("SELECT * FROM v4_prepare_document_reingest(:document_id)"),
            {"document_id": document_id},
        )
    ).one()


async def _expect_execute_denied(
    connection: AsyncConnection, role: str, document_id: uuid.UUID
) -> None:
    await connection.execute(text("RESET ROLE"))
    await connection.execute(text(f"SET LOCAL ROLE {role}"))
    calls = (
        (
            "SELECT * FROM v4_prepare_document_reingest(:document_id)",
            {"document_id": document_id},
        ),
        (
            "SELECT * FROM v4_commit_document_reingest("
            ":document_id, :snapshot, :job_id)",
            {
                "document_id": document_id,
                "snapshot": "0" * 64,
                "job_id": uuid.uuid4(),
            },
        ),
    )
    for statement, parameters in calls:
        with pytest.raises(DBAPIError, match="permission denied") as error:
            async with connection.begin_nested():
                await connection.execute(text(statement), parameters)
        assert getattr(error.value.orig, "sqlstate", None) == "42501"


def test_failed_document_reingest_functions_enforce_live_contract() -> None:
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
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
            )
            counts = (
                await connection.execute(
                    text(
                        "SELECT 'users', count(*) FROM users "
                        "UNION ALL SELECT 'documents', count(*) FROM documents "
                        "UNION ALL SELECT 'audit_events', count(*) FROM audit_events"
                    )
                )
            ).all()
            if any(count for _, count in counts):
                pytest.fail(
                    "refusing reused V4 security database: "
                    + ", ".join(f"{table}={count}" for table, count in counts)
                )

            admin_id = uuid.uuid4()
            uploader_id = uuid.uuid4()
            reader_id = uuid.uuid4()
            team_id = uuid.uuid4()
            document_id = uuid.uuid4()
            node_id = uuid.uuid4()
            previous_job_id = uuid.uuid4()
            queued_job_id = uuid.uuid4()
            admin_token = "a" * 64
            uploader_token = "b" * 64
            reader_token = "c" * 64
            checksum = "d" * 64
            residual_text = "residual"
            residual_sha256 = hashlib.sha256(
                residual_text.encode("utf-8")
            ).hexdigest()
            anchor = {
                "version": 1,
                "normalization": "citation-highlight-v1",
                "pages": [
                    {
                        "page": 1,
                        "kind": "text_quote",
                        "selector": {
                            "exact": residual_text,
                            "prefix": "",
                            "suffix": "",
                            "sha256": residual_sha256,
                        },
                    }
                ],
            }

            await connection.execute(text("SET LOCAL ROLE rag_owner"))
            await connection.execute(
                text(
                    "INSERT INTO users ("
                    "id, username, display_name, role, status, password_hash"
                    ") VALUES "
                    "(:admin, 'reingest.admin', 'Reingest Admin', 'admin', "
                    "'active', '$argon2id$test'), "
                    "(:uploader, 'reingest.uploader', 'Uploader', 'member', "
                    "'active', '$argon2id$test'), "
                    "(:reader, 'reingest.reader', 'Reader', 'member', "
                    "'active', '$argon2id$test')"
                ),
                {
                    "admin": admin_id,
                    "uploader": uploader_id,
                    "reader": reader_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO teams (id, name, name_key, is_active) "
                    "VALUES (:team, 'Reingest Readers', 'reingest readers', true)"
                ),
                {"team": team_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO team_members (team_id, user_id) "
                    "VALUES (:team, :reader)"
                ),
                {"team": team_id, "reader": reader_id},
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
                    "(:admin, :admin_token, 'e'), "
                    "(:uploader, :uploader_token, 'f'), "
                    "(:reader, :reader_token, '0')"
                    ") AS seed(user_id, token_hash, csrf_marker) "
                    "CROSS JOIN security_epochs AS epoch WHERE epoch.singleton"
                ),
                {
                    "admin": admin_id,
                    "admin_token": admin_token,
                    "uploader": uploader_id,
                    "uploader_token": uploader_token,
                    "reader": reader_id,
                    "reader_token": reader_token,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO documents ("
                    "id, sha256, original_filename, mime_type, byte_size, "
                    "object_key, state, stage, error, parser_version, "
                    "chunking_version, embedding_version, page_count, chunk_count"
                    ") VALUES ("
                    ":document, :sha256, 'failed.pdf', 'application/pdf', 10, "
                    ":object_key, 'failed', 'failed', 'controlled failure', "
                    "'stored-parser', 'stored-chunker', 'stored-embedding', 1, 1)"
                ),
                {
                    "document": document_id,
                    "sha256": checksum,
                    "object_key": f"originals/{checksum[:2]}/{checksum}.pdf",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO library_nodes ("
                    "id, parent_id, kind, name, name_key, document_id, "
                    "uploader_user_id"
                    ") VALUES ("
                    ":node, NULL, 'file', 'failed.pdf', 'failed.pdf', "
                    ":document, :uploader)"
                ),
                {
                    "node": node_id,
                    "document": document_id,
                    "uploader": uploader_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO access_grants ("
                    "id, node_id, user_id, team_id"
                    ") VALUES "
                    "(gen_random_uuid(), :node, :uploader, NULL), "
                    "(gen_random_uuid(), :node, NULL, :team)"
                ),
                {
                    "node": node_id,
                    "uploader": uploader_id,
                    "team": team_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO ingestion_jobs ("
                    "id, document_id, status, stage, attempt, completed_units, "
                    "total_units, error"
                    ") VALUES ("
                    ":job, :document, 'failed', 'failed', 2, 1, 1, "
                    "'controlled failure')"
                ),
                {"job": previous_job_id, "document": document_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO chunks ("
                    "id, document_id, ordinal, filename, page_start, page_end, "
                    "section, text, token_count, text_sha256, source_sha256, "
                    "parse_method, parser_version, chunking_version, "
                    "embedding_version, schema_version, citation_label, "
                    "embedding, highlight_anchor"
                    ") VALUES ("
                    "gen_random_uuid(), :document, 0, 'failed.pdf', 1, 1, "
                    "NULL, :chunk_text, 1, :text_sha256, :source_sha256, "
                    "'direct', 'stored-parser', 'stored-chunker', "
                    "'stored-embedding', 'chunk-v1', 'S1', "
                    "CAST(:embedding AS vector), CAST(:anchor AS jsonb))"
                ),
                {
                    "document": document_id,
                    "chunk_text": residual_text,
                    "text_sha256": residual_sha256,
                    "source_sha256": checksum,
                    "embedding": f"[{','.join(['0'] * 1024)}]",
                    "anchor": json.dumps(anchor),
                },
            )
            await connection.execute(
                text("SELECT v4_rebuild_effective_document_access()")
            )

            await _activate(connection, reader_token)
            denied = await _prepare(connection, document_id)
            assert denied.result_status == "not_found"
            assert denied.document_id is None

            await connection.execute(text("RESET ROLE"))
            await _activate(connection, admin_token)
            admin_prepared = await _prepare(connection, document_id)
            assert admin_prepared.result_status == "prepared"
            assert admin_prepared.previous_job_id == previous_job_id

            await connection.execute(text("RESET ROLE"))
            await _activate(connection, uploader_token)
            prepared = await _prepare(connection, document_id)
            assert prepared.result_status == "prepared"
            assert prepared.parser_version == "stored-parser"
            assert prepared.chunking_version == "stored-chunker"
            assert prepared.embedding_version == "stored-embedding"
            committed = (
                await connection.execute(
                    text(
                        "SELECT * FROM v4_commit_document_reingest("
                        ":document, :snapshot, :job)"
                    ),
                    {
                        "document": document_id,
                        "snapshot": prepared.snapshot_token,
                        "job": queued_job_id,
                    },
                )
            ).one()
            assert committed.result_status == "created"
            assert committed.job_id == queued_job_id

            polled = (
                await connection.execute(
                    text("SELECT * FROM v4_get_job(:job)"),
                    {"job": queued_job_id},
                )
            ).one()
            assert polled.document_id == document_id
            assert polled.status == "queued"
            assert polled.stage == "uploaded"

            repeated_job_id = uuid.uuid4()
            repeated = (
                await connection.execute(
                    text(
                        "SELECT * FROM v4_commit_document_reingest("
                        ":document, :snapshot, :job)"
                    ),
                    {
                        "document": document_id,
                        "snapshot": prepared.snapshot_token,
                        "job": repeated_job_id,
                    },
                )
            ).one()
            assert repeated.result_status == "not_retryable"

            await connection.execute(text("RESET ROLE"))
            await connection.execute(text("SET LOCAL ROLE rag_owner"))
            document = (
                await connection.execute(
                    text(
                        "SELECT state, stage, error, page_count, chunk_count, "
                        "parser_version, chunking_version, embedding_version "
                        "FROM documents WHERE id = :document"
                    ),
                    {"document": document_id},
                )
            ).one()
            assert tuple(document) == (
                "uploaded",
                "uploaded",
                None,
                None,
                0,
                "stored-parser",
                "stored-chunker",
                "stored-embedding",
            )
            assert not await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM chunks "
                    "WHERE document_id = :document)"
                ),
                {"document": document_id},
            )
            queued = (
                await connection.execute(
                    text(
                        "SELECT status, stage, attempt, completed_units, "
                        "total_units FROM ingestion_jobs WHERE id = :job"
                    ),
                    {"job": queued_job_id},
                )
            ).one()
            assert tuple(queued) == ("queued", "uploaded", 0, 0, None)
            assert not await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM ingestion_jobs WHERE id = :job)"
                ),
                {"job": repeated_job_id},
            )
            audit = (
                await connection.execute(
                    text(
                        "SELECT actor_user_id, details FROM audit_events "
                        "WHERE event_type = 'document_reingest_queued' "
                        "AND target_id = :document"
                    ),
                    {"document": document_id},
                )
            ).one()
            assert audit.actor_user_id == uploader_id
            assert audit.details == {
                "job_id": str(queued_job_id),
                "job_status": "queued",
                "previous_job_id": str(previous_job_id),
                "previous_job_status": "failed",
            }
            assert not set(audit.details).intersection(
                {"document_text", "prompt", "answer", "snapshot_text"}
            )

            await _expect_execute_denied(
                connection, "rag_worker", document_id
            )
            await _expect_execute_denied(
                connection, "rag_maintenance", document_id
            )
        finally:
            if transaction.is_active:
                await transaction.rollback()
            await connection.close()
            await engine.dispose()

    _run(exercise())
