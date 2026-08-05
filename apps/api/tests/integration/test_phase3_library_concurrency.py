import asyncio
import os
import uuid
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.datastructures import Headers, UploadFile

from app.config import Settings
from app.db.models import Document, LibraryNode, ObjectDeletion
from app.services.documents import DocumentService
from app.services.library import (
    LibraryConflict,
    LibraryNotEmpty,
    LibraryNotFound,
    LibraryService,
    acquire_library_lock,
)
from app.services.object_storage import ObjectMetadata
from tests.integration.phase3_safety import (
    assert_phase3_database,
    dedicated_phase3_environment,
    run_selector,
)

pytestmark = pytest.mark.integration


class _ObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(
        self,
        key: str,
        source: Path,
        *,
        sha256: str,
        byte_size: int,
        content_type: str = "application/pdf",
    ) -> None:
        content = source.read_bytes()
        assert len(content) == byte_size
        self.objects[key] = (content, sha256)

    async def head(self, key: str) -> ObjectMetadata:
        content, sha256 = self.objects[key]
        return ObjectMetadata(
            key, len(content), sha256, len(content), "application/pdf", None
        )


def _upload(content: bytes, filename: str) -> UploadFile:
    return UploadFile(
        BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": "application/pdf"}),
    )


def test_concurrent_casefolded_root_create_has_one_winner() -> None:
    if os.environ.get("RUN_PHASE3_LIBRARY_E2E") != "1":
        pytest.skip("RUN_PHASE3_LIBRARY_E2E is not enabled")
    environment = dedicated_phase3_environment()
    database_url = environment["TEST_DATABASE_URL"]

    async def exercise() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        revision_name = f"phase3-{uuid.uuid4().hex}"
        service = LibraryService(factory)
        validated = False
        try:
            async with engine.connect() as connection:
                await assert_phase3_database(
                    connection, environment["PHASE3_TEST_DATABASE_NAME"]
                )
            validated = True

            results = await asyncio.gather(
                service.create_folder(revision_name, None),
                service.create_folder(revision_name.upper(), None),
                return_exceptions=True,
            )
            successes = [
                result for result in results if not isinstance(result, Exception)
            ]
            conflicts = [
                result for result in results if isinstance(result, LibraryConflict)
            ]
            assert len(successes) == 1
            assert len(conflicts) == 1
        finally:
            if validated:
                async with factory() as session, session.begin():
                    await acquire_library_lock(session)
                    await session.execute(
                        delete(LibraryNode).where(
                            LibraryNode.name_key == revision_name.casefold(),
                            LibraryNode.parent_id.is_(None),
                        )
                    )
            await engine.dispose()

    run_selector(exercise())


def test_concurrent_upload_suffix_delete_cascade_and_structural_serialization(
    tmp_path: Path,
) -> None:
    if os.environ.get("RUN_PHASE3_LIBRARY_E2E") != "1":
        pytest.skip("RUN_PHASE3_LIBRARY_E2E is not enabled")
    environment = dedicated_phase3_environment()
    database_url = environment["TEST_DATABASE_URL"]

    async def exercise() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        library = LibraryService(factory)
        store = _ObjectStore()
        documents = DocumentService(
            factory,
            Settings(database_url=database_url, data_root=tmp_path),
            store,
        )
        unique = uuid.uuid4().hex
        controlled_folder_ids: list[uuid.UUID] = []
        document_ids: list[uuid.UUID] = []
        object_keys: list[str] = []
        validated = False
        try:
            async with engine.connect() as connection:
                await assert_phase3_database(
                    connection, environment["PHASE3_TEST_DATABASE_NAME"]
                )
            validated = True
            upload_folder = await library.create_folder(f"uploads-{unique}", None)
            controlled_folder_ids.append(upload_folder.node_id)
            first, second = await asyncio.gather(
                documents.upload(
                    _upload(b"%PDF-first-phase3", f"same-{unique}.pdf"),
                    upload_folder.node_id,
                ),
                documents.upload(
                    _upload(b"%PDF-second-phase3", f"same-{unique}.pdf"),
                    upload_folder.node_id,
                ),
            )
            document_ids.extend([first.document_id, second.document_id])
            assert {first.display_name, second.display_name} == {
                f"same-{unique}.pdf",
                f"same-{unique} (2).pdf",
            }
            assert first.parent_id == second.parent_id == upload_folder.node_id

            async with factory() as session:
                persisted = list(
                    await session.scalars(
                        select(Document).where(Document.id.in_(document_ids))
                    )
                )
                key_by_document = {
                    document.id: document.object_key for document in persisted
                }
                object_keys.extend(key_by_document.values())
            assert len(persisted) == 2

            assert await documents.delete(first.document_id)
            async with factory() as session:
                assert (
                    await session.scalar(
                        select(LibraryNode.id).where(
                            LibraryNode.document_id == first.document_id
                        )
                    )
                    is None
                )
                assert (
                    await session.scalar(
                        select(ObjectDeletion.id).where(
                            ObjectDeletion.object_key
                            == key_by_document[first.document_id]
                        )
                    )
                    is not None
                )

            race_parent = await library.create_folder(f"race-{unique}", None)
            controlled_folder_ids.append(race_parent.node_id)
            race_results = await asyncio.gather(
                library.create_folder("child", race_parent.node_id),
                library.delete_folder(race_parent.node_id),
                return_exceptions=True,
            )
            assert (
                sum(not isinstance(result, Exception) for result in race_results) == 1
            )
            assert any(
                isinstance(result, (LibraryNotEmpty, LibraryNotFound))
                for result in race_results
            )
        finally:
            if validated:
                async with factory() as session, session.begin():
                    await acquire_library_lock(session)
                    await session.execute(
                        delete(Document).where(Document.id.in_(document_ids))
                    )
                    await session.execute(
                        delete(ObjectDeletion).where(
                            ObjectDeletion.object_key.in_(object_keys)
                        )
                    )
                    await session.execute(
                        delete(LibraryNode).where(
                            LibraryNode.parent_id.in_(controlled_folder_ids)
                        )
                    )
                    await session.execute(
                        delete(LibraryNode).where(
                            LibraryNode.id.in_(controlled_folder_ids)
                        )
                    )
            await engine.dispose()

    run_selector(exercise())
