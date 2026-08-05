import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db.models import Document
from app.main import create_app
from app.services.identity import document_uuid
from app.services.object_lifecycle import canonical_object_key
from app.services.object_storage import ObjectStoreError
from tests.integration.test_phase6_end_to_end import (
    _assert_dedicated_object_store,
)

pytestmark = pytest.mark.integration
_CONFIRMATION = "PHASE6-DEDICATED-ONLY"


def _identity(database_url: str) -> tuple[str, int, str]:
    parsed = make_url(database_url)
    host = (parsed.host or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return host, parsed.port or 5432, parsed.database or ""


def _environment() -> dict[str, str]:
    names = (
        "TEST_DATABASE_URL",
        "PHASE6_TEST_DATABASE_NAME",
        "PHASE6_DEDICATED_DATABASE_CONFIRM",
        "PHASE6_COMPOSE_PROJECT",
        "OCR_PYTHON_EXECUTABLE",
        "PHASE6_TEST_DEPLOYMENT_ID",
        "PHASE6_TEST_OBJECT_STORAGE_ENDPOINT_URL",
        "PHASE6_TEST_OBJECT_STORAGE_BUCKET",
    )
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(f"Phase 6 restart environment is missing: {', '.join(missing)}")
    if values["PHASE6_DEDICATED_DATABASE_CONFIRM"] != _CONFIRMATION:
        pytest.fail(f"restart gate requires confirmation {_CONFIRMATION!r}")
    if (
        re.fullmatch(
            r"rag-phase6-test-[A-Za-z0-9_-]+", values["PHASE6_COMPOSE_PROJECT"]
        )
        is None
    ):
        pytest.fail("restart gate refuses an unsafe Compose project name")
    test_identity = _identity(values["TEST_DATABASE_URL"])
    primary_identity = _identity(Settings().database_url)
    if test_identity == primary_identity or test_identity[2] == primary_identity[2]:
        pytest.fail("restart gate refuses the primary database")
    if test_identity[2] != values["PHASE6_TEST_DATABASE_NAME"]:
        pytest.fail("restart database name does not match the explicit guard")
    settings = Settings()
    if settings.deployment_id != values["PHASE6_TEST_DEPLOYMENT_ID"]:
        pytest.fail("restart deployment identity does not match Settings")
    if (
        str(settings.object_storage_endpoint_url).rstrip("/")
        != values["PHASE6_TEST_OBJECT_STORAGE_ENDPOINT_URL"].rstrip("/")
        or settings.object_storage_bucket != values["PHASE6_TEST_OBJECT_STORAGE_BUCKET"]
    ):
        pytest.fail("restart object-storage identity does not match Settings")
    _assert_dedicated_object_store(values["PHASE6_COMPOSE_PROJECT"], settings)
    return values


def _digital_pdf() -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        (
            "BT /F1 12 Tf 72 720 Td "
            "(Durable restart reference. The amber recovery code is 4815. "
            "This controlled document verifies PostgreSQL and RustFS restart "
            "persistence with exact page citations.) Tj ET"
        ).encode("ascii")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


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


def _wait_for_job(
    client: TestClient, job_id: str, timeout_seconds: float = 90
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"completed", "failed", "interrupted"}:
            return job
        time.sleep(0.1)
    pytest.fail(f"job {job_id} did not finish")


def _restart_durable_services(project: str) -> None:
    repository = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "restart",
            "postgres",
            "rustfs",
        ],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    ready = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "up",
            "-d",
            "--wait",
            "postgres",
            "rustfs",
        ],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert ready.returncode == 0, ready.stderr


def test_postgres_rustfs_job_library_chat_and_preview_survive_restart(
    tmp_path: Path,
) -> None:
    if os.environ.get("RUN_PHASE6_RESTART_E2E") != "1":
        pytest.skip("RUN_PHASE6_RESTART_E2E is not enabled")
    environment = _environment()
    pdf = _digital_pdf()
    checksum = hashlib.sha256(pdf).hexdigest()
    document_id = document_uuid(checksum)
    object_key = canonical_object_key(checksum)
    settings = Settings(
        database_url=environment["TEST_DATABASE_URL"],
        data_root=tmp_path / "data",
        ocr_python_executable=Path(environment["OCR_PYTHON_EXECUTABLE"]),
        worker_poll_seconds=0.05,
    )

    async def stale() -> bool:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as connection:
                return (
                    await connection.scalar(
                        select(Document.id).where(Document.id == document_id)
                    )
                    is not None
                )
        finally:
            await engine.dispose()

    if asyncio.run(
        stale(),
        loop_factory=(asyncio.SelectorEventLoop if os.name == "nt" else None),
    ):
        pytest.fail("restart gate found stale controlled document")

    accepted: dict[str, str] | None = None
    folder_id: str | None = None
    chat_id: str | None = None
    try:
        restarted_app = create_app(settings)
        with TestClient(restarted_app) as client:
            upload = client.post(
                "/api/documents",
                files={"file": ("restart.pdf", pdf, "application/pdf")},
            )
            assert upload.status_code == 202, upload.text
            accepted = upload.json()
            assert _wait_for_job(client, accepted["job_id"])["status"] == "completed"

            folder = client.post(
                "/api/library/folders",
                json={"name": "Restart Evidence", "parent_id": None},
            )
            assert folder.status_code == 201, folder.text
            folder_id = folder.json()["node_id"]
            moved = client.patch(
                f"/api/library/nodes/{accepted['node_id']}",
                json={"name": "restart-evidence.pdf", "parent_id": folder_id},
            )
            assert moved.status_code == 200, moved.text

            chat = client.post("/api/chats", json={})
            assert chat.status_code == 201, chat.text
            chat_id = chat.json()["chat_id"]
            scope = client.put(
                f"/api/chats/{chat_id}/scope",
                json={"mode": "selected", "node_ids": [accepted["node_id"]]},
            )
            assert scope.status_code == 200, scope.text
            answer = client.post(
                f"/api/chats/{chat_id}/messages/stream",
                headers={"Accept": "text/event-stream"},
                json={"question": "What is the amber recovery code?"},
            )
            assert answer.status_code == 200, answer.text
            events = _events(answer.text)
            assert events[-1][0] == "final"
            assert "4815" in str(events[-1][1]["answer"])
            assert events[-1][1]["citations"][0]["page_start"] == 1

            preview = client.get(
                f"/api/documents/{accepted['document_id']}/content",
                headers={"Range": "bytes=0-31"},
            )
            assert preview.status_code == 206
            assert preview.content == pdf[:32]

        _restart_durable_services(environment["PHASE6_COMPOSE_PROJECT"])

        with TestClient(create_app(settings)) as client:
            document = next(
                item
                for item in client.get("/api/documents").json()
                if item["document_id"] == accepted["document_id"]
            )
            assert document["state"] == "ready"
            assert document["logical_path"] == (
                "/Restart Evidence/restart-evidence.pdf"
            )
            assert (
                client.get(f"/api/jobs/{accepted['job_id']}").json()["status"]
                == "completed"
            )
            browse = client.get(
                "/api/library/browse", params={"parent_id": folder_id}
            ).json()
            assert browse["children"][0]["document_id"] == accepted["document_id"]

            detail = client.get(f"/api/chats/{chat_id}").json()
            assert detail["turns"][0]["status"] == "complete"
            assert detail["turns"][0]["citation_ranks"] == [1]
            assert detail["turns"][0]["sources"][0]["page_start"] == 1

            preview = client.get(
                f"/api/documents/{accepted['document_id']}/content",
                headers={"Range": "bytes=32-63"},
            )
            assert preview.status_code == 206
            assert preview.content == pdf[32:64]
            assert client.portal is not None
            metadata = client.portal.call(
                restarted_app.state.container.object_store.head, object_key
            )
            assert metadata.sha256 == checksum
            assert metadata.size == len(pdf)

            deleted = client.delete(f"/api/documents/{accepted['document_id']}")
            assert deleted.status_code == 204
            unavailable = client.get(f"/api/chats/{chat_id}").json()
            assert unavailable["turns"][0]["sources"][0]["source_available"] is False
            assert client.delete(f"/api/chats/{chat_id}").status_code == 204
            assert client.delete(f"/api/library/folders/{folder_id}").status_code == 204

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    client.portal.call(
                        restarted_app.state.container.object_store.head,
                        object_key,
                    )
                except ObjectStoreError as exc:
                    if exc.not_found:
                        break
                    raise
                time.sleep(0.1)
            else:
                pytest.fail("restart gate object deletion did not complete")
    finally:
        if accepted is not None or chat_id is not None or folder_id is not None:
            with TestClient(create_app(settings)) as client:
                if accepted is not None:
                    client.delete(f"/api/documents/{accepted['document_id']}")
                if chat_id is not None:
                    client.delete(f"/api/chats/{chat_id}")
                if folder_id is not None:
                    client.delete(f"/api/library/folders/{folder_id}")
