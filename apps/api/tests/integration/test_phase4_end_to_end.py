import json
import os
import time
import uuid
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.main import create_app
from tests.integration.phase4_safety import (
    assert_phase4_database,
    dedicated_phase4_environment,
    run_selector,
)

pytestmark = pytest.mark.integration


def _digital_pdf(marker: str) -> bytes:
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
            "(Zephyr protocol reference. The cobalt access code is 7391. "
            "This controlled document verifies grounded answers with filename "
            f"and page citations. Test marker {marker}.) Tj ET"
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


def test_real_persistent_scoped_chat_survives_restart(tmp_path: Path) -> None:
    if os.environ.get("RUN_PHASE4_E2E") != "1":
        pytest.skip("RUN_PHASE4_E2E is not enabled")
    environment = dedicated_phase4_environment("OCR_PYTHON_EXECUTABLE")
    database_url = environment["TEST_DATABASE_URL"]

    async def assert_database() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                await assert_phase4_database(
                    connection, environment["PHASE4_TEST_DATABASE_NAME"]
                )
        finally:
            await engine.dispose()

    run_selector(assert_database())

    settings = Settings(
        database_url=database_url,
        data_root=tmp_path / "data",
        ocr_python_executable=Path(environment["OCR_PYTHON_EXECUTABLE"]),
        worker_poll_seconds=0.05,
    )
    accepted: dict[str, str] | None = None
    chat_id: str | None = None
    marker = uuid.uuid4().hex
    question = "What is the cobalt access code?"
    try:
        with TestClient(create_app(settings)) as client:
            upload = client.post(
                "/api/documents",
                files={
                    "file": (
                        f"zephyr-{marker}.pdf",
                        _digital_pdf(marker),
                        "application/pdf",
                    )
                },
            )
            assert upload.status_code == 202
            accepted = upload.json()
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                job = client.get(f"/api/jobs/{accepted['job_id']}").json()
                if job["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.1)
            assert job["status"] == "completed", job

            created = client.post("/api/chats", json={})
            assert created.status_code == 201
            chat_id = created.json()["chat_id"]
            scope = client.put(
                f"/api/chats/{chat_id}/scope",
                json={"mode": "selected", "node_ids": [accepted["node_id"]]},
            )
            assert scope.status_code == 200
            assert scope.json()["scope_node_ids"] == [accepted["node_id"]]

            answer_response = client.post(
                f"/api/chats/{chat_id}/messages/stream",
                headers={"Accept": "text/event-stream"},
                json={"question": question},
            )
            assert answer_response.status_code == 200
            answer_events = _events(answer_response.text)
            event_names = [event for event, _ in answer_events]
            assert event_names[0] == "sources"
            assert "token" in event_names[1:-1]
            assert event_names[-1] == "final"
            final = answer_events[-1][1]
            assert final["insufficient_context"] is False, final
            assert "7391" in str(final["answer"])
            assert final["chat_id"] == chat_id
            assert final["turn_id"] == answer_events[0][1]["turn_id"]
            assert final["citations"][0]["label"] == "S1"
            assert (
                answer_events[0][1]["sources"][0]["document_id"]
                == (accepted["document_id"])
            )

            decline_response = client.post(
                "/api/query/stream",
                headers={"Accept": "text/event-stream"},
                json={
                    "question": "What is the lunar launch date?",
                    "document_ids": [str(uuid.uuid4())],
                },
            )
            decline_events = _events(decline_response.text)
            assert [event for event, _ in decline_events] == ["sources", "final"]
            assert decline_events[-1][1]["insufficient_context"] is True
            assert decline_events[-1][1]["citations"] == []

        with TestClient(create_app(settings)) as client:
            recovered = client.get(f"/api/chats/{chat_id}")
            assert recovered.status_code == 200
            detail = recovered.json()
            assert detail["title"] == question
            assert detail["turns"][0]["status"] == "complete"
            assert detail["turns"][0]["citation_ranks"] == [1]
            source = detail["turns"][0]["sources"][0]
            assert source["source_available"] is True
            assert source["document_id"] == accepted["document_id"]

            deleted = client.delete(f"/api/documents/{accepted['document_id']}")
            assert deleted.status_code == 204
            unavailable = client.get(f"/api/chats/{chat_id}").json()
            historical_source = unavailable["turns"][0]["sources"][0]
            assert historical_source["source_available"] is False
            assert historical_source["document_id"] is None
            assert (
                historical_source["document_id_snapshot"] == (accepted["document_id"])
            )
            assert client.delete(f"/api/chats/{chat_id}").status_code == 204
    finally:
        if accepted is not None or chat_id is not None:
            with TestClient(create_app(settings)) as client:
                if chat_id is not None:
                    client.delete(f"/api/chats/{chat_id}")
                if accepted is not None:
                    client.delete(f"/api/documents/{accepted['document_id']}")
