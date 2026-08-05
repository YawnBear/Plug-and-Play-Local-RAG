import asyncio
import hashlib
import json
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.services.highlight_anchors import text_quote_anchor
from app.versions import (
    ADAPTIVE_PARSER_VERSION,
    EMBEDDING_VERSION,
    FRAGMENT_CHUNKING_VERSION,
    PARSER_VERSION,
)

pytestmark = pytest.mark.integration


def _url(role: str, password: str) -> str:
    admin_url = os.environ.get("V8E_TEST_DATABASE_URL", "")
    if not admin_url:
        pytest.skip("V8E_TEST_DATABASE_URL is required")
    parsed = make_url(admin_url)
    if parsed.database != "rag_v8e" or parsed.port != 54321:
        pytest.fail("V8E test refuses any database except rag_v8e on port 54321")
    return parsed.set(username=role, password=password).render_as_string(
        hide_password=False
    )


async def _activate(connection: object, token_hash: str) -> None:
    await connection.execute(
        text("SELECT * FROM v4_activate_actor(:token_hash)"),
        {"token_hash": token_hash},
    )


def test_v8e_generation_cutover_rollback_and_failed_reingestion() -> None:
    asyncio.run(_exercise(), loop_factory=asyncio.SelectorEventLoop)


async def _exercise() -> None:
    admin_url = os.environ.get("V8E_TEST_DATABASE_URL", "")
    if not admin_url:
        pytest.skip("V8E_TEST_DATABASE_URL is required")
    parsed = make_url(admin_url)
    if parsed.database != "rag_v8e" or parsed.port != 54321:
        pytest.fail("V8E test refuses any database except rag_v8e on port 54321")

    admin = create_async_engine(admin_url)
    api = create_async_engine(_url("rag_api", "V8eApi_2026"))
    worker = create_async_engine(_url("rag_worker", "V8eWorker_2026"))
    actor_id = uuid.uuid4()
    session_id = uuid.uuid4()
    document_id = uuid.uuid4()
    job_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    backup_id = uuid.uuid4()
    token_hash = "a" * 64
    vector = [0.001] * 1024
    chunk_text = "Versioned retrieval qualification fixture."
    chunk_sha256 = hashlib.sha256(chunk_text.encode()).hexdigest()
    source_sha256 = "b" * 64
    anchor = text_quote_anchor(page=1, page_text=chunk_text, chunk_text=chunk_text)
    chunk_payload = [
        {
            "id": str(chunk_id),
            "ordinal": 0,
            "filename": "fixture.pdf",
            "page_start": 1,
            "page_end": 1,
            "section": "Fixture",
            "text": chunk_text,
            "token_count": 5,
            "text_sha256": chunk_sha256,
            "source_sha256": source_sha256,
            "parse_method": "direct",
            "parser_version": ADAPTIVE_PARSER_VERSION,
            "chunking_version": FRAGMENT_CHUNKING_VERSION,
            "embedding_version": EMBEDDING_VERSION,
            "schema_version": "v5",
            "citation_label": "fixture.pdf p.1",
            "highlight_anchor": anchor,
            "embedding": json.dumps(vector, separators=(",", ":")),
        }
    ]

    try:
        async with admin.begin() as connection:
            revision = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            assert revision == "0014_restart_without_backup"
            await connection.execute(
                text(
                    "INSERT INTO users (id, username, display_name, role, status, "
                    "password_hash) VALUES (:id, 'v8e-admin', 'V8E Admin', "
                    "'admin', 'active', '$argon2id$v=19$m=8,t=1,p=1$c2FsdA$hash')"
                ),
                {"id": actor_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO sessions (id, user_id, token_hash, csrf_token_hash, "
                    "issued_authentication_version, issued_authentication_epoch, "
                    "issued_session_epoch, idle_expires_at, absolute_expires_at) "
                    "SELECT :session_id, :actor_id, :token_hash, :csrf_hash, 1, "
                    "authentication_version, session_epoch, "
                    "statement_timestamp() + interval '1 hour', "
                    "statement_timestamp() + interval '1 hour' "
                    "FROM security_epochs WHERE singleton"
                ),
                {
                    "session_id": session_id,
                    "actor_id": actor_id,
                    "token_hash": token_hash,
                    "csrf_hash": "c" * 64,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO backup_runs (id, status, destination_id, "
                    "database_sha256, storage_manifest_sha256, database_bytes, "
                    "storage_bytes, finished_at) VALUES (:id, 'succeeded', "
                    "'v8e-disposable', :sha, :sha, 1, 1, statement_timestamp())"
                ),
                {"id": backup_id, "sha": "d" * 64},
            )
            await connection.execute(
                text(
                    "INSERT INTO backup_restore_verifications (backup_run_id, "
                    "manifest_sha256, verification_profile) VALUES "
                    "(:id, :sha, 'personal.isolated-restore.v1')"
                ),
                {"id": backup_id, "sha": "d" * 64},
            )
            await connection.execute(
                text(
                    "INSERT INTO documents (id, sha256, original_filename, "
                    "mime_type, byte_size, object_key, state, stage, parser_version, "
                    "chunking_version, embedding_version, chunk_count) VALUES "
                    "(:id, :sha, 'fixture.pdf', 'application/pdf', 12, :key, "
                    "'uploaded', 'uploaded', :parser, :chunking, :embedding, 0)"
                ),
                {
                    "id": document_id,
                    "sha": source_sha256,
                    "key": f"originals/{source_sha256[:2]}/{source_sha256}.pdf",
                    "parser": ADAPTIVE_PARSER_VERSION,
                    "chunking": FRAGMENT_CHUNKING_VERSION,
                    "embedding": EMBEDDING_VERSION,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO ingestion_jobs (id, document_id, status, stage, "
                    "attempt, completed_units) VALUES "
                    "(:id, :document_id, 'queued', 'uploaded', 0, 0)"
                ),
                {"id": job_id, "document_id": document_id},
            )
            bound = (
                await connection.execute(
                    text(
                        "SELECT document.active_generation_id, "
                        "job.target_generation_id "
                        "FROM documents AS document JOIN ingestion_jobs AS job "
                        "ON job.document_id = document.id WHERE document.id = :id"
                    ),
                    {"id": document_id},
                )
            ).one()
            assert bound.active_generation_id == bound.target_generation_id

        async with worker.begin() as connection:
            claim = (
                await connection.execute(
                    text("SELECT * FROM v4_claim_ingestion_job('v8e-worker', 60)")
                )
            ).one()
            assert claim.parser_version == ADAPTIVE_PARSER_VERSION
            assert await connection.scalar(
                text(
                    "SELECT v4_commit_ingestion_job(:job_id, :lease_token, "
                    ":fencing_token, 1, CAST(:chunks AS jsonb))"
                ),
                {
                    "job_id": claim.job_id,
                    "lease_token": claim.lease_token,
                    "fencing_token": claim.fencing_token,
                    "chunks": json.dumps(chunk_payload),
                },
            )

        async with api.begin() as connection:
            await _activate(connection, token_hash)
            inventory = await connection.scalar(
                text("SELECT v10_admin_version_inventory()")
            )
            baseline_generation = inventory["embedding"]["active_generation_id"]
            preview = (
                await connection.execute(
                    text(
                        "SELECT * FROM v10_admin_preview_reprocessing("
                        "'reindex', :profile, NULL)"
                    ),
                    {"profile": inventory["embedding"]["profile_id"]},
                )
            ).one()
            assert preview.backup_verified
            grant_hash = "e" * 64
            await connection.scalar(
                text(
                    "SELECT v10_admin_issue_reprocessing_grant("
                    ":preview_id, :digest, :grant_hash)"
                ),
                {
                    "preview_id": preview.preview_id,
                    "digest": preview.impact_digest,
                    "grant_hash": grant_hash,
                },
            )
            reindex_operation = await connection.scalar(
                text(
                    "SELECT v10_admin_start_reprocessing("
                    ":preview_id, :digest, :grant_hash)"
                ),
                {
                    "preview_id": preview.preview_id,
                    "digest": preview.impact_digest,
                    "grant_hash": grant_hash,
                },
            )

        async with worker.begin() as connection:
            task = (
                await connection.execute(
                    text("SELECT * FROM v10_claim_reindex_tasks('v8e-worker', 60, 8)")
                )
            ).one()
            assert task.chunk_id == chunk_id
            assert await connection.scalar(
                text(
                    "SELECT v10_commit_reindex_task(:operation_id, :chunk_id, "
                    ":lease_token, :fencing_token, :embedding)"
                ),
                {
                    "operation_id": task.operation_id,
                    "chunk_id": task.chunk_id,
                    "lease_token": task.lease_token,
                    "fencing_token": task.fencing_token,
                    "embedding": json.dumps(vector),
                },
            )
            qualification = (
                await connection.execute(
                    text(
                        "SELECT * FROM v10_claim_reindex_qualification("
                        "'v8e-worker', 60)"
                    )
                )
            ).one()
            assert await connection.scalar(
                text(
                    "SELECT v10_complete_reindex_qualification("
                    ":operation_id, :lease_token, :fencing_token, true, true, "
                    "true, true, 'qualification_passed')"
                ),
                {
                    "operation_id": qualification.operation_id,
                    "lease_token": qualification.lease_token,
                    "fencing_token": qualification.fencing_token,
                },
            )

        async with api.begin() as connection:
            await _activate(connection, token_hash)
            inventory_after_cutover = await connection.scalar(
                text("SELECT v10_admin_version_inventory()")
            )
            active_generation = inventory_after_cutover["embedding"][
                "active_generation_id"
            ]
            assert str(active_generation) != baseline_generation
            result = (
                await connection.execute(
                    text(
                        "SELECT * FROM v10_retrieve_active_chunks("
                        "CAST(:embedding AS vector), 5, NULL)"
                    ),
                    {"embedding": json.dumps(vector)},
                )
            ).one()
            assert result.chunk_id == chunk_id
            assert await connection.scalar(
                text("SELECT v10_admin_rollback_embedding_generation(:generation_id)"),
                {"generation_id": baseline_generation},
            )
            assert await connection.scalar(
                text(
                    "SELECT v10_admin_cleanup_generation('embedding', :generation_id)"
                ),
                {"generation_id": active_generation},
            )
            ingestion_revision = await connection.scalar(
                text(
                    "SELECT v10_admin_set_ingestion_profile("
                    "'v8e-ingestion-initial', "
                    "'parser.paddleocr-vl-1.6.legacy-v1')"
                )
            )
            assert str(ingestion_revision).startswith("v8e-ingestion-")
            prior_document_generation = await connection.scalar(
                text("SELECT active_generation_id FROM documents WHERE id = :id"),
                {"id": document_id},
            )
            parser_preview = (
                await connection.execute(
                    text(
                        "SELECT * FROM v10_admin_preview_reprocessing("
                        "'reingestion', "
                        "'parser.paddleocr-vl-1.6.legacy-v1', :source)"
                    ),
                    {"source": ADAPTIVE_PARSER_VERSION},
                )
            ).one()
            parser_grant = "f" * 64
            await connection.scalar(
                text(
                    "SELECT v10_admin_issue_reprocessing_grant("
                    ":preview_id, :digest, :grant_hash)"
                ),
                {
                    "preview_id": parser_preview.preview_id,
                    "digest": parser_preview.impact_digest,
                    "grant_hash": parser_grant,
                },
            )
            parser_operation = await connection.scalar(
                text(
                    "SELECT v10_admin_start_reprocessing("
                    ":preview_id, :digest, :grant_hash)"
                ),
                {
                    "preview_id": parser_preview.preview_id,
                    "digest": parser_preview.impact_digest,
                    "grant_hash": parser_grant,
                },
            )

        async with worker.begin() as connection:
            failed_claim = (
                await connection.execute(
                    text("SELECT * FROM v4_claim_ingestion_job('v8e-worker', 60)")
                )
            ).one()
            assert failed_claim.parser_version == PARSER_VERSION
            assert await connection.scalar(
                text(
                    "SELECT v4_poison_ingestion_job(:job_id, :lease_token, "
                    ":fencing_token, 'forced qualification failure')"
                ),
                {
                    "job_id": failed_claim.job_id,
                    "lease_token": failed_claim.lease_token,
                    "fencing_token": failed_claim.fencing_token,
                },
            )

        async with api.begin() as connection:
            await _activate(connection, token_hash)
            preserved = (
                await connection.execute(
                    text(
                        "SELECT state, active_generation_id, parser_version "
                        "FROM documents WHERE id = :id"
                    ),
                    {"id": document_id},
                )
            ).one()
            assert preserved.state == "ready"
            assert preserved.active_generation_id == prior_document_generation
            assert preserved.parser_version == ADAPTIVE_PARSER_VERSION
            operation_rows = (
                await connection.execute(
                    text("SELECT * FROM v10_admin_reprocessing_operations(100)")
                )
            ).all()
            operation_states = {row.operation_id: row.state for row in operation_rows}
            assert operation_states[parser_operation] == "failed"
            inventory = await connection.scalar(
                text("SELECT v10_admin_version_inventory()")
            )
            failed_copy = next(
                generation
                for generation in inventory["ingestion"]["generations"]
                if generation["state"] == "failed"
            )
            assert failed_copy["filename"] == "fixture.pdf"
            assert failed_copy["cleanup_available"] is True
            assert await connection.scalar(
                text("SELECT v10_admin_cleanup_generation('document', :generation_id)"),
                {"generation_id": failed_copy["generation_id"]},
            )
            assert operation_states[reindex_operation] == "succeeded"
    finally:
        await worker.dispose()
        await api.dispose()
        await admin.dispose()
