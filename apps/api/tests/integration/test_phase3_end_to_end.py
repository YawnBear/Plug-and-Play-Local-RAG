import hashlib
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.models import Chunk, Document, LibraryNode, ObjectDeletion
from app.main import create_app
from app.services.identity import document_uuid
from app.services.library import acquire_library_lock
from app.services.object_lifecycle import acquire_checksum_lock, canonical_object_key
from app.services.object_storage import ObjectStoreError, S3ObjectStore
from tests.integration.phase3_safety import (
    dedicated_phase3_environment,
    run_selector,
)

pytestmark = pytest.mark.integration


def _refuse_stale_checksum(database_url: str, checksum: str) -> None:
    async def exists() -> bool:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                return (
                    await connection.scalar(
                        select(Document.id).where(Document.sha256 == checksum)
                    )
                    is not None
                )
        finally:
            await engine.dispose()

    if run_selector(exists()):
        pytest.fail(
            f"dedicated test database contains stale checksum {checksum}; "
            "reset the isolated test database"
        )


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
            "(Zephyr protocol reference. The cobalt access code is 7391. "
            "This controlled document verifies deterministic local retrieval "
            "with filename and page provenance.) Tj ET"
        ).encode("ascii")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    from io import BytesIO

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


async def _object_snapshot(
    object_store: S3ObjectStore, object_key: str
) -> tuple[object, bytes, tuple[tuple[str, int, str | None], ...]]:
    metadata = await object_store.head(object_key)
    object_read = await object_store.get(object_key)
    try:
        content = await object_read.body.read()
    finally:
        await object_read.body.close()
    inventory = tuple(
        [
            (item.key, item.size, item.etag)
            async for item in object_store.list_prefix(object_key)
        ]
    )
    return metadata, content, inventory


async def _cleanup_controlled_state(
    settings: Settings,
    checksum: str,
    folder_id: uuid.UUID | None,
) -> None:
    object_key = canonical_object_key(checksum)
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            await acquire_checksum_lock(session, checksum)
            await acquire_library_lock(session)
            await session.execute(delete(Document).where(Document.sha256 == checksum))
            await session.execute(
                delete(ObjectDeletion).where(ObjectDeletion.object_key == object_key)
            )
            if folder_id is not None:
                await session.execute(
                    delete(LibraryNode).where(LibraryNode.id == folder_id)
                )
    finally:
        await engine.dispose()

    object_store = S3ObjectStore.from_settings(settings)
    await object_store.delete(object_key)
    try:
        await object_store.head(object_key)
    except ObjectStoreError as exc:
        assert exc.not_found, exc
    else:
        pytest.fail(f"controlled object still exists after cleanup: {object_key}")


def test_real_upload_worker_duplicate_restart_retrieval_delete(
    tmp_path: Path,
) -> None:
    if os.environ.get("RUN_PHASE3_E2E") != "1":
        pytest.skip("RUN_PHASE3_E2E is not enabled")
    environment = dedicated_phase3_environment("OCR_PYTHON_EXECUTABLE")
    database_url = environment["TEST_DATABASE_URL"]
    settings = Settings(
        database_url=database_url,
        data_root=tmp_path / "data",
        ocr_python_executable=Path(environment["OCR_PYTHON_EXECUTABLE"]),
        worker_poll_seconds=0.05,
    )
    pdf = _digital_pdf()
    checksum = hashlib.sha256(pdf).hexdigest()
    expected_document_id = document_uuid(checksum)
    object_key = canonical_object_key(checksum)
    _refuse_stale_checksum(database_url, checksum)
    controlled_folder_id: uuid.UUID | None = None

    try:
        first_app = create_app(settings)
        with TestClient(first_app) as client:
            response = client.post(
                "/api/documents",
                files={"file": ("zephyr.pdf", pdf, "application/pdf")},
            )
            assert response.status_code == 202
            accepted = response.json()
            assert accepted["document_id"] == str(expected_document_id)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                job = client.get(f"/api/jobs/{accepted['job_id']}").json()
                if job["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.1)
            assert job["status"] == "completed", job
            assert job["stage"] == "ready"

            duplicate = client.post(
                "/api/documents",
                files={"file": ("duplicate-name.pdf", pdf, "application/pdf")},
            )
            assert duplicate.status_code == 202
            duplicate_body = duplicate.json()
            assert duplicate_body["document_id"] == accepted["document_id"]
            assert duplicate_body["job_id"] == accepted["job_id"]
            assert duplicate_body["duplicate_of"] == accepted["document_id"]
            assert duplicate_body["location_reused"] is True

            assert client.portal is not None
            object_before = client.portal.call(
                _object_snapshot,
                first_app.state.container.object_store,
                object_key,
            )
            assert object_before[0].sha256 == checksum
            assert object_before[1] == pdf
            assert [item[0] for item in object_before[2]] == [object_key]

            async def provenance_snapshot() -> tuple[object, ...]:
                session_factory = first_app.state.container.database.session_factory
                async with session_factory() as session:
                    document = await session.scalar(
                        select(Document).where(Document.id == expected_document_id)
                    )
                    chunks = list(
                        await session.scalars(
                            select(Chunk)
                            .where(Chunk.document_id == expected_document_id)
                            .order_by(Chunk.ordinal)
                        )
                    )
                    assert document is not None
                    return (
                        document.object_key,
                        document.original_filename,
                        tuple(
                            (chunk.filename, chunk.source_sha256) for chunk in chunks
                        ),
                    )

            provenance_before = client.portal.call(provenance_snapshot)
            assert provenance_before[0] == object_key
            assert provenance_before[1] == "zephyr.pdf"
            assert provenance_before[2]

            folder = client.post(
                "/api/library/folders",
                json={"name": "Controlled", "parent_id": None},
            )
            assert folder.status_code == 201, folder.text
            controlled_folder_id = uuid.UUID(folder.json()["node_id"])
            moved = client.patch(
                f"/api/library/nodes/{accepted['node_id']}",
                json={
                    "name": "current-zephyr.pdf",
                    "parent_id": str(controlled_folder_id),
                },
            )
            assert moved.status_code == 200, moved.text
            assert moved.json()["logical_path"] == "/Controlled/current-zephyr.pdf"

            provenance_after = client.portal.call(provenance_snapshot)
            object_after = client.portal.call(
                _object_snapshot,
                first_app.state.container.object_store,
                object_key,
            )
            assert provenance_after == provenance_before
            assert object_after == object_before

            results = client.portal.call(
                lambda: first_app.state.container.retrieval.retrieve(
                    "What is the cobalt access code?", limit=20
                )
            )
            assert results
            assert str(results[0].chunk.document_id) == accepted["document_id"]
            assert results[0].chunk.filename == "zephyr.pdf"
            assert results[0].chunk.page_start == 1
            assert "7391" in results[0].chunk.text

        restarted_app = create_app(settings)
        with TestClient(restarted_app) as client:
            documents = client.get("/api/documents").json()
            persisted = next(
                document
                for document in documents
                if document["document_id"] == accepted["document_id"]
            )
            assert persisted["state"] == "ready"
            assert persisted["logical_path"] == "/Controlled/current-zephyr.pdf"
            persisted_job = client.get(f"/api/jobs/{accepted['job_id']}")
            assert persisted_job.status_code == 200
            assert persisted_job.json()["status"] == "completed"
            deleted = client.delete(f"/api/documents/{accepted['document_id']}")
            assert deleted.status_code == 204
            assert all(
                document["document_id"] != accepted["document_id"]
                for document in client.get("/api/documents").json()
            )

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    client.portal.call(
                        restarted_app.state.container.object_store.head, object_key
                    )
                except ObjectStoreError as exc:
                    if exc.not_found:
                        break
                    raise
                time.sleep(0.1)
            else:
                pytest.fail("object deletion worker did not remove controlled object")
    finally:
        run_selector(
            _cleanup_controlled_state(settings, checksum, controlled_folder_id)
        )
