import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db.models import Chunk, Document
from app.main import create_app
from app.services.identity import document_uuid

pytestmark = pytest.mark.integration
_DEDICATED_CONFIRMATION = "PHASE6-DEDICATED-ONLY"


def _events(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        events.append(
            (
                lines[0].removeprefix("event: "),
                json.loads(lines[1].removeprefix("data: ")),
            )
        )
    return events


def _wait_for_terminal_job(
    client: TestClient, job_id: str, timeout_seconds: float
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"completed", "failed", "interrupted"}:
            return job
        time.sleep(0.25)
    pytest.fail(f"job {job_id} did not finish within {timeout_seconds} seconds")


def _required_environment(*names: str) -> dict[str, str]:
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(f"Phase 6 environment is missing: {', '.join(missing)}")
    return values


def _database_identity(database_url: str) -> tuple[str, int, str]:
    parsed = make_url(database_url)
    host = (parsed.host or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return (
        host,
        parsed.port or 5432,
        parsed.database or "",
    )


def _dedicated_environment(*extra_names: str) -> dict[str, str]:
    environment = _required_environment(
        "TEST_DATABASE_URL",
        "PHASE6_TEST_DATABASE_NAME",
        "PHASE6_DEDICATED_DATABASE_CONFIRM",
        "PHASE6_TEST_DEPLOYMENT_ID",
        "PHASE6_TEST_OBJECT_STORAGE_ENDPOINT_URL",
        "PHASE6_TEST_OBJECT_STORAGE_BUCKET",
        "PHASE6_COMPOSE_PROJECT",
        *extra_names,
    )
    if environment["PHASE6_DEDICATED_DATABASE_CONFIRM"] != (_DEDICATED_CONFIRMATION):
        pytest.fail(
            "refusing Phase 6 integration without explicit dedicated-database "
            f"confirmation {_DEDICATED_CONFIRMATION!r}"
        )
    test_identity = _database_identity(environment["TEST_DATABASE_URL"])
    primary_identity = _database_identity(Settings().database_url)
    if test_identity == primary_identity or test_identity[2] == primary_identity[2]:
        pytest.fail("refusing to run Phase 6 integration against DATABASE_URL")
    if test_identity[2] != environment["PHASE6_TEST_DATABASE_NAME"]:
        pytest.fail(
            "TEST_DATABASE_URL database does not match PHASE6_TEST_DATABASE_NAME"
        )
    settings = Settings()
    if settings.deployment_id != environment["PHASE6_TEST_DEPLOYMENT_ID"]:
        pytest.fail("dedicated deployment identity does not match Settings")
    if (
        str(settings.object_storage_endpoint_url).rstrip("/")
        != environment["PHASE6_TEST_OBJECT_STORAGE_ENDPOINT_URL"].rstrip("/")
        or settings.object_storage_bucket
        != environment["PHASE6_TEST_OBJECT_STORAGE_BUCKET"]
    ):
        pytest.fail("dedicated object-storage identity does not match Settings")
    _assert_dedicated_object_store(environment["PHASE6_COMPOSE_PROJECT"], settings)
    return environment


def _assert_dedicated_object_store(project: str, settings: Settings) -> None:
    if (
        re.fullmatch(r"(?:rag-phase6-test|rag-restore-drill)-[A-Za-z0-9_-]+", project)
        is None
    ):
        pytest.fail("unsafe dedicated Compose project")
    containers = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            "label=com.docker.compose.service=rustfs",
            "--format",
            "{{.ID}}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    identifiers = containers.stdout.split()
    if containers.returncode != 0 or len(identifiers) != 1:
        pytest.fail("dedicated RustFS container identity is not unique")
    inspected = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{(index (index .NetworkSettings.Ports "9000/tcp") 0).HostPort}}',
            identifiers[0],
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    endpoint = settings.object_storage_endpoint_url
    if (
        inspected.returncode != 0
        or endpoint.host not in {"localhost", "127.0.0.1", "::1"}
        or endpoint.port != int(inspected.stdout.strip())
    ):
        pytest.fail("object-storage endpoint is not bound to dedicated RustFS")


def _refuse_stale_document(database_url: str, document_id: uuid.UUID) -> None:
    async def check() -> bool:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                return bool(
                    await connection.scalar(
                        select(func.count())
                        .select_from(Document)
                        .where(Document.id == document_id)
                    )
                )
        finally:
            await engine.dispose()

    if asyncio.run(check()):
        pytest.fail(
            f"dedicated test database contains stale document {document_id}; "
            "refusing automatic deletion—reset the isolated test database"
        )


def test_scanned_pdf_ocr_to_grounded_answer(tmp_path: Path) -> None:
    if os.environ.get("RUN_PHASE6_SCAN_E2E") != "1":
        pytest.skip("RUN_PHASE6_SCAN_E2E is not enabled")
    environment = _dedicated_environment(
        "OCR_PYTHON_EXECUTABLE",
        "PHASE6_SCAN_PDF",
        "PHASE6_SCAN_QUESTION",
        "PHASE6_SCAN_EXPECTED_TOKEN",
    )
    scan_path = Path(environment["PHASE6_SCAN_PDF"]).resolve()
    assert scan_path.is_file(), f"controlled scan is missing: {scan_path}"
    checksum = hashlib.sha256(scan_path.read_bytes()).hexdigest()
    _refuse_stale_document(environment["TEST_DATABASE_URL"], document_uuid(checksum))
    expected_token = environment["PHASE6_SCAN_EXPECTED_TOKEN"]
    timeout_seconds = float(os.environ.get("PHASE6_SCAN_TIMEOUT_SECONDS", "1800"))
    settings = Settings(
        database_url=environment["TEST_DATABASE_URL"],
        data_root=tmp_path / "data",
        ocr_python_executable=Path(environment["OCR_PYTHON_EXECUTABLE"]),
        worker_poll_seconds=0.05,
    )
    application = create_app(settings)
    accepted: dict[str, str] | None = None
    created_document_id: str | None = None
    with TestClient(application) as client:
        try:
            with scan_path.open("rb") as scan:
                upload = client.post(
                    "/api/documents",
                    files={"file": (scan_path.name, scan, "application/pdf")},
                )
            assert upload.status_code == 202, upload.text
            accepted = upload.json()
            assert accepted["duplicate_of"] is None
            created_document_id = accepted["document_id"]
            job = _wait_for_terminal_job(client, accepted["job_id"], timeout_seconds)
            assert job["status"] == "completed", job
            documents = client.get("/api/documents").json()
            document = next(
                item
                for item in documents
                if item["document_id"] == accepted["document_id"]
            )
            assert document["state"] == "ready"
            assert document["page_count"] == 1
            assert document["chunk_count"] > 0

            assert client.portal is not None

            async def stored_chunks() -> list[Chunk]:
                session_factory = application.state.container.database.session_factory
                async with session_factory() as session:
                    result = await session.scalars(
                        select(Chunk)
                        .where(Chunk.document_id == uuid.UUID(accepted["document_id"]))
                        .order_by(Chunk.ordinal)
                    )
                    return list(result)

            chunks = client.portal.call(stored_chunks)
            assert chunks
            assert all(chunk.filename == scan_path.name for chunk in chunks)
            assert all(chunk.page_start >= 1 for chunk in chunks)
            assert all(chunk.id for chunk in chunks)
            assert all(
                chunk.section is None or isinstance(chunk.section, str)
                for chunk in chunks
            )
            assert any(chunk.parse_method == "ocr" for chunk in chunks)
            expected_chunks = [
                chunk
                for chunk in chunks
                if expected_token.lower() in chunk.text.lower()
            ]
            assert expected_chunks
            assert all(chunk.page_start == 1 for chunk in expected_chunks)
            assert all(chunk.parse_method == "ocr" for chunk in expected_chunks)

            retrieved = client.portal.call(
                lambda: application.state.container.retrieval.retrieve(
                    environment["PHASE6_SCAN_QUESTION"],
                    limit=20,
                    document_ids=[uuid.UUID(accepted["document_id"])],
                )
            )
            assert retrieved
            assert any(
                expected_token.lower() in candidate.chunk.text.lower()
                for candidate in retrieved
            )
            reranked = client.portal.call(
                lambda: application.state.container.reranker.rerank(
                    environment["PHASE6_SCAN_QUESTION"], retrieved, limit=6
                )
            )
            assert expected_token.lower() in reranked[0].candidate.chunk.text.lower()
            assert reranked[0].candidate.chunk.page_start == 1

            answer = client.post(
                "/api/query/stream",
                headers={"Accept": "text/event-stream"},
                json={
                    "question": environment["PHASE6_SCAN_QUESTION"],
                    "document_ids": [accepted["document_id"]],
                },
            )
            assert answer.status_code == 200, answer.text
            events = _events(answer.text)
            assert events[0][0] == "sources"
            assert any(name == "token" for name, _payload in events[1:-1])
            assert events[-1][0] == "final"
            final = events[-1][1]
            assert final["insufficient_context"] is False, final
            assert expected_token.lower() in str(final["answer"]).lower()
            stored_by_id = {str(chunk.id): chunk for chunk in chunks}
            assert final["citations"]
            for citation in final["citations"]:
                chunk = stored_by_id[citation["chunk_id"]]
                assert citation["filename"] == scan_path.name
                assert citation["page_start"] == 1
                assert citation["page_start"] == chunk.page_start
                assert citation["page_end"] == chunk.page_end

            configured_ollama = os.environ.get("OLLAMA_EXECUTABLE")
            ollama = (
                str(Path(configured_ollama).resolve())
                if configured_ollama
                else shutil.which("ollama")
            )
            assert ollama and Path(ollama).is_file(), (
                "set OLLAMA_EXECUTABLE to ollama.exe to verify GPU use"
            )
            process = subprocess.run(
                [ollama, "ps"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            assert process.returncode == 0, process.stderr
            for model in (settings.generation_model, settings.embedding_model):
                row = next(
                    (line for line in process.stdout.splitlines() if model in line),
                    None,
                )
                assert row is not None, (
                    f"{model} is absent from ollama ps:\n{process.stdout}"
                )
                assert "GPU" in row.upper(), row
        finally:
            if created_document_id is not None:
                deleted = client.delete(f"/api/documents/{created_document_id}")
                assert deleted.status_code == 204


def test_corrupt_pdf_records_failed_job_and_zero_chunks(tmp_path: Path) -> None:
    if os.environ.get("RUN_PHASE6_FAILURE_INTEGRATION") != "1":
        pytest.skip("RUN_PHASE6_FAILURE_INTEGRATION is not enabled")
    environment = _dedicated_environment("OCR_PYTHON_EXECUTABLE")
    settings = Settings(
        database_url=environment["TEST_DATABASE_URL"],
        data_root=tmp_path / "data",
        ocr_python_executable=Path(environment["OCR_PYTHON_EXECUTABLE"]),
        worker_poll_seconds=0.05,
    )
    application = create_app(settings)
    accepted: dict[str, str] | None = None
    created_document_id: str | None = None
    corrupt = f"%PDF-1.7\ncorrupt-{uuid.uuid4()}".encode()
    with TestClient(application) as client:
        try:
            upload = client.post(
                "/api/documents",
                files={"file": ("corrupt.pdf", corrupt, "application/pdf")},
            )
            assert upload.status_code == 202
            accepted = upload.json()
            assert accepted["duplicate_of"] is None
            created_document_id = accepted["document_id"]
            job = _wait_for_terminal_job(client, accepted["job_id"], 15)
            assert job["status"] == "failed", job
            assert job["error"]
            document = next(
                item
                for item in client.get("/api/documents").json()
                if item["document_id"] == accepted["document_id"]
            )
            assert document["state"] == "failed"
            assert document["chunk_count"] == 0

            assert client.portal is not None

            async def chunk_count() -> int:
                session_factory = application.state.container.database.session_factory
                async with session_factory() as session:
                    return int(
                        await session.scalar(
                            select(func.count())
                            .select_from(Chunk)
                            .where(
                                Chunk.document_id == uuid.UUID(accepted["document_id"])
                            )
                        )
                        or 0
                    )

            assert client.portal.call(chunk_count) == 0
        finally:
            if created_document_id is not None:
                deleted = client.delete(f"/api/documents/{created_document_id}")
                assert deleted.status_code == 204
