import asyncio
import hashlib
import os
import uuid
from contextlib import suppress
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import Headers, UploadFile

from app.config import Settings
from app.db.models import Document, ObjectDeletion
from app.services.documents import DeletionPendingError, DocumentService
from app.services.object_deletions import ObjectDeletionService
from app.services.object_lifecycle import canonical_object_key
from app.services.object_storage import ObjectStoreError, S3ObjectStore

pytestmark = pytest.mark.integration


class _PausingDeleteStore:
    def __init__(self, delegate: S3ObjectStore) -> None:
        self._delegate = delegate
        self.delete_entered = asyncio.Event()
        self.release_delete = asyncio.Event()

    async def delete(self, key: str) -> None:
        self.delete_entered.set()
        await self.release_delete.wait()
        await self._delegate.delete(key)


def _upload(payload: bytes) -> UploadFile:
    return UploadFile(
        BytesIO(payload),
        filename="phase2-race.pdf",
        headers=Headers({"content-type": "application/pdf"}),
    )


def test_live_delete_reupload_race_is_serialized(tmp_path: Path) -> None:
    if os.getenv("RUN_PHASE2_RACE_E2E") != "1":
        pytest.skip("set RUN_PHASE2_RACE_E2E=1 for the Phase 2 race gate")
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    asyncio.run(
        _exercise(database_url, tmp_path),
        loop_factory=(asyncio.SelectorEventLoop if os.name == "nt" else None),
    )


async def _exercise(database_url: str, tmp_path: Path) -> None:
    nonce = uuid.uuid4()
    payload = f"%PDF-1.7\ncontrolled-phase2-race-{nonce}\n%%EOF\n".encode()
    checksum = hashlib.sha256(payload).hexdigest()
    key = canonical_object_key(checksum)
    settings = Settings(database_url=database_url, data_root=tmp_path / "data")
    store = S3ObjectStore.from_settings(settings)
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    documents = DocumentService(session_factory, settings, store)
    deletion_task: asyncio.Task[bool] | None = None
    pause_store = _PausingDeleteStore(store)
    try:
        readiness = await store.bucket_readiness()
        assert readiness.ready, readiness.detail
        async with session_factory() as session:
            unrelated_outbox = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ObjectDeletion)
                    .where(ObjectDeletion.object_key != key)
                )
                or 0
            )
            existing = await session.scalar(
                select(Document.id).where(Document.sha256 == checksum)
            )
        if unrelated_outbox:
            pytest.skip(
                "isolated race gate requires no unrelated pending object deletions"
            )
        assert existing is None, "controlled checksum unexpectedly already exists"

        first = await documents.upload(_upload(payload))
        assert first.duplicate_of is None
        assert await documents.delete(first.document_id)

        paused_deletions = ObjectDeletionService(
            session_factory,
            pause_store,
            lease_seconds=60,
            retry_base_seconds=1,
            owner=f"phase2-race-{nonce}",
        )
        deletion_task = asyncio.create_task(paused_deletions.run_once())
        await asyncio.wait_for(pause_store.delete_entered.wait(), timeout=5)

        with pytest.raises(DeletionPendingError):
            await asyncio.wait_for(documents.upload(_upload(payload)), timeout=2)
        async with session_factory() as session:
            leased = await session.scalar(
                select(ObjectDeletion).where(ObjectDeletion.object_key == key)
            )
            assert leased is not None and leased.status == "leased"
            assert await session.scalar(select(1)) == 1

        pause_store.release_delete.set()
        assert await asyncio.wait_for(deletion_task, timeout=10)
        deletion_task = None
        await _assert_absent(session_factory, store, key, checksum)

        retry = await documents.upload(_upload(payload))
        assert retry.duplicate_of is None
        assert await documents.delete(retry.document_id)
        cleanup = ObjectDeletionService(
            session_factory,
            store,
            lease_seconds=60,
            retry_base_seconds=1,
            owner=f"phase2-race-final-{nonce}",
        )
        assert await cleanup.run_once()
        await _assert_absent(session_factory, store, key, checksum)
    finally:
        pause_store.release_delete.set()
        if deletion_task is not None:
            with suppress(Exception):
                await asyncio.wait_for(deletion_task, timeout=10)
        async with session_factory() as session, session.begin():
            await session.execute(delete(Document).where(Document.sha256 == checksum))
            await session.execute(
                delete(ObjectDeletion).where(ObjectDeletion.object_key == key)
            )
        await store.delete(key)
        await engine.dispose()


async def _assert_absent(
    session_factory: async_sessionmaker,
    store: S3ObjectStore,
    key: str,
    checksum: str,
) -> None:
    async with session_factory() as session:
        assert (
            await session.scalar(select(Document.id).where(Document.sha256 == checksum))
            is None
        )
        assert (
            await session.scalar(
                select(ObjectDeletion.id).where(ObjectDeletion.object_key == key)
            )
            is None
        )
    with pytest.raises(ObjectStoreError) as captured:
        await store.head(key)
    assert captured.value.not_found
