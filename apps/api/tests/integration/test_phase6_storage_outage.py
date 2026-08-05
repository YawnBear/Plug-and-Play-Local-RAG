import hashlib
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.config import Settings
from app.main import create_app
from app.services.object_lifecycle import canonical_object_key
from app.services.object_storage import ObjectStoreError
from tests.integration.test_phase6_backup_restore import (
    _compose_environment,
    _run_compose,
)
from tests.integration.test_phase6_restart_persistence import (
    _digital_pdf,
    _environment,
    _events,
    _wait_for_job,
)

pytestmark = pytest.mark.integration


def test_rustfs_outage_preserves_postgres_and_indexed_chat(tmp_path: Path) -> None:
    if os.environ.get("RUN_PHASE6_STORAGE_OUTAGE_E2E") != "1":
        pytest.skip("RUN_PHASE6_STORAGE_OUTAGE_E2E is not enabled")
    environment = _environment()
    repository = Path(__file__).resolve().parents[4]
    database_url = environment["TEST_DATABASE_URL"]
    database = make_url(database_url)
    settings = Settings(
        database_url=database_url,
        data_root=tmp_path / "data",
        ocr_python_executable=Path(environment["OCR_PYTHON_EXECUTABLE"]),
        worker_poll_seconds=0.05,
        object_storage_connect_timeout_seconds=1,
        object_storage_read_timeout_seconds=2,
        object_storage_max_attempts=1,
        object_storage_operation_max_attempts=1,
    )
    compose_environment = _compose_environment(
        database_url,
        settings,
        postgres_port=database.port or 5432,
        rustfs_api_port=59000,
        rustfs_console_port=59001,
    )
    pdf = _digital_pdf()
    object_key = canonical_object_key(hashlib.sha256(pdf).hexdigest())
    application = create_app(settings)
    accepted: dict[str, str] | None = None
    chat_id: str | None = None
    storage_stopped = False
    with TestClient(application) as client:
        try:
            upload = client.post(
                "/api/documents",
                files={"file": ("outage-source.pdf", pdf, "application/pdf")},
            )
            assert upload.status_code == 202, upload.text
            accepted = upload.json()
            assert _wait_for_job(client, accepted["job_id"])["status"] == "completed"
            chat = client.post("/api/chats", json={})
            assert chat.status_code == 201, chat.text
            chat_id = chat.json()["chat_id"]
            scope = client.put(
                f"/api/chats/{chat_id}/scope",
                json={"mode": "selected", "node_ids": [accepted["node_id"]]},
            )
            assert scope.status_code == 200, scope.text
            first_answer = client.post(
                f"/api/chats/{chat_id}/messages/stream",
                headers={"Accept": "text/event-stream"},
                json={"question": "What is the amber recovery code?"},
            )
            assert first_answer.status_code == 200, first_answer.text
            assert "4815" in str(_events(first_answer.text)[-1][1]["answer"])
            document_count = len(client.get("/api/documents").json())

            _run_compose(
                repository,
                environment["PHASE6_COMPOSE_PROJECT"],
                compose_environment,
                "stop",
                "rustfs",
            )
            storage_stopped = True

            ready = client.get("/ready")
            assert ready.status_code == 503
            readiness = ready.json()
            assert readiness["database"] is True
            assert readiness["object_storage_endpoint"] is False
            assert readiness["object_storage_bucket"] is False
            assert readiness["detail"]

            documents = client.get("/api/documents")
            assert documents.status_code == 200
            assert len(documents.json()) == document_count
            persisted = client.get(f"/api/chats/{chat_id}")
            assert persisted.status_code == 200
            assert persisted.json()["turns"][0]["status"] == "complete"
            assert persisted.json()["turns"][0]["sources"][0]["page_start"] == 1

            preview = client.get(
                f"/api/documents/{accepted['document_id']}/content",
                headers={"Range": "bytes=0-31"},
            )
            assert preview.status_code == 503
            preview_detail = preview.json()["detail"].lower()
            assert "object storage" in preview_detail
            assert "timed out" in preview_detail or "unavailable" in preview_detail

            unavailable_upload = client.post(
                "/api/documents",
                files={
                    "file": (
                        "outage-new.pdf",
                        f"%PDF-1.7\noutage-{uuid.uuid4()}".encode(),
                        "application/pdf",
                    )
                },
            )
            assert unavailable_upload.status_code == 503
            assert "object storage unavailable" in (
                unavailable_upload.json()["detail"].lower()
            )
            assert len(client.get("/api/documents").json()) == document_count

            second_answer = client.post(
                f"/api/chats/{chat_id}/messages/stream",
                headers={"Accept": "text/event-stream"},
                json={"question": "Repeat the amber recovery code."},
            )
            assert second_answer.status_code == 200, second_answer.text
            final = _events(second_answer.text)[-1]
            assert final[0] == "final"
            assert "4815" in str(final[1]["answer"])
            assert final[1]["citations"][0]["page_start"] == 1
        finally:
            if storage_stopped:
                _run_compose(
                    repository,
                    environment["PHASE6_COMPOSE_PROJECT"],
                    compose_environment,
                    "up",
                    "-d",
                    "--wait",
                    "rustfs",
                )
            if accepted is not None:
                recovered = client.get(
                    f"/api/documents/{accepted['document_id']}/content",
                    headers={"Range": "bytes=0-31"},
                )
                assert recovered.status_code == 206
                assert recovered.content == pdf[:32]
                assert (
                    client.delete(
                        f"/api/documents/{accepted['document_id']}"
                    ).status_code
                    == 204
                )
            if chat_id is not None:
                assert client.delete(f"/api/chats/{chat_id}").status_code == 204
            deadline = time.monotonic() + 15
            while accepted is not None and time.monotonic() < deadline:
                try:
                    assert client.portal is not None
                    client.portal.call(
                        application.state.container.object_store.head,
                        object_key,
                    )
                except ObjectStoreError as exc:
                    if exc.not_found:
                        break
                    raise
                time.sleep(0.1)
            else:
                if accepted is not None:
                    pytest.fail("outage test object cleanup did not complete")
