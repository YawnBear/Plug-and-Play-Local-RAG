import asyncio
import logging
import uuid
from contextlib import suppress
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.object_storage import ObjectStore, ObjectStoreError

logger = logging.getLogger(__name__)


def bounded_retry_delay(base_seconds: float, attempt: int) -> float:
    exponent = min(max(attempt - 1, 0), 20)
    return min(base_seconds * (2**exponent), 300.0)


@dataclass(frozen=True, slots=True)
class DeletionLease:
    row_id: uuid.UUID
    object_key: str
    token: uuid.UUID
    fencing_token: int
    attempt: int


def checksum_from_object_key(key: str) -> str:
    parts = key.split("/")
    if len(parts) != 3 or parts[0] != "originals" or not parts[2].endswith(".pdf"):
        raise ValueError("outbox object key is not canonical")
    checksum = parts[2][:-4]
    if len(checksum) != 64 or parts[1] != checksum[:2]:
        raise ValueError("outbox object key is not canonical")
    try:
        int(checksum, 16)
    except ValueError as exc:
        raise ValueError("outbox object key is not canonical") from exc
    if checksum != checksum.lower():
        raise ValueError("outbox object key is not canonical")
    return checksum


class ObjectDeletionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        object_store: ObjectStore,
        *,
        lease_seconds: int,
        retry_base_seconds: float,
        orphan_grace_seconds: int = 900,
        error_limit: int = 500,
        owner: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._object_store = object_store
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._orphan_grace_seconds = orphan_grace_seconds
        self._error_limit = error_limit
        self._owner = owner or f"deletion-worker-{uuid.uuid4()}"

    async def run_once(self) -> bool:
        await self._queue_expired_upload_orphans()
        lease = await self._claim()
        if lease is None:
            return False
        try:
            await self._delete_with_heartbeat(lease)
        except (SQLAlchemyError, StaleDeletionLease):
            raise
        except ObjectStoreError as exc:
            if exc.not_found:
                await self._complete(lease)
            else:
                await self._retry(lease, str(exc))
        except Exception as exc:
            await self._retry(lease, str(exc) or type(exc).__name__)
        else:
            await self._complete(lease)
        return True

    async def _queue_expired_upload_orphans(self) -> int:
        async with self._session_factory() as session, session.begin():
            return int(
                await session.scalar(
                    text("SELECT v4_queue_expired_upload_orphans(:grace_seconds)"),
                    {"grace_seconds": self._orphan_grace_seconds},
                )
                or 0
            )

    async def _delete_with_heartbeat(self, lease: DeletionLease) -> None:
        deletion = asyncio.create_task(self._object_store.delete(lease.object_key))
        interval = max(1.0, self._lease_seconds / 3)
        try:
            while True:
                done, _ = await asyncio.wait(
                    {deletion}, timeout=interval, return_when=asyncio.FIRST_COMPLETED
                )
                if done:
                    await deletion
                    return
                if not await self._heartbeat(lease):
                    deletion.cancel()
                    await asyncio.gather(deletion, return_exceptions=True)
                    raise StaleDeletionLease("object deletion lease expired")
        except BaseException:
            if not deletion.done():
                deletion.cancel()
                await asyncio.gather(deletion, return_exceptions=True)
            raise

    async def _claim(self) -> DeletionLease | None:
        async with self._session_factory() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT deletion_id, object_key, lease_token, "
                        "fencing_token, attempt "
                        "FROM v4_claim_object_deletion(:owner_id, :lease_seconds)"
                    ),
                    {
                        "owner_id": self._owner,
                        "lease_seconds": self._lease_seconds,
                    },
                )
            ).one_or_none()
        if row is None:
            return None
        checksum_from_object_key(row.object_key)
        return DeletionLease(
            row.deletion_id,
            row.object_key,
            row.lease_token,
            row.fencing_token,
            row.attempt,
        )

    async def _heartbeat(self, lease: DeletionLease) -> bool:
        async with self._session_factory() as session, session.begin():
            return bool(
                await session.scalar(
                    text(
                        "SELECT v4_heartbeat_object_deletion("
                        ":deletion_id, :lease_token, :fencing_token, "
                        ":lease_seconds)"
                    ),
                    {
                        "deletion_id": lease.row_id,
                        "lease_token": lease.token,
                        "fencing_token": lease.fencing_token,
                        "lease_seconds": self._lease_seconds,
                    },
                )
            )

    async def _complete(self, lease: DeletionLease) -> None:
        async with self._session_factory() as session, session.begin():
            result = await session.scalar(
                text(
                    "SELECT v4_finish_object_deletion("
                    ":deletion_id, :lease_token, :fencing_token, true, NULL)"
                ),
                {
                    "deletion_id": lease.row_id,
                    "lease_token": lease.token,
                    "fencing_token": lease.fencing_token,
                },
            )
        if result != "deleted":
            raise StaleDeletionLease("object deletion completion was stale")

    async def _retry(self, lease: DeletionLease, error: str) -> None:
        async with self._session_factory() as session, session.begin():
            result = await session.scalar(
                text(
                    "SELECT v4_finish_object_deletion("
                    ":deletion_id, :lease_token, :fencing_token, false, :error)"
                ),
                {
                    "deletion_id": lease.row_id,
                    "lease_token": lease.token,
                    "fencing_token": lease.fencing_token,
                    "error": error[: self._error_limit],
                },
            )
        if result == "stale":
            raise StaleDeletionLease("object deletion retry was stale")


class StaleDeletionLease(RuntimeError):
    pass


class ObjectDeletionWorker:
    def __init__(self, service: ObjectDeletionService, *, poll_seconds: float) -> None:
        self._service = service
        self._poll_seconds = poll_seconds
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="object-deletion-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def wait(self) -> None:
        if self._task is None:
            raise RuntimeError("object deletion worker has not started")
        await self._task

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                worked = await self._service.run_once()
            except StaleDeletionLease:
                logger.warning("object deletion lease became stale")
                worked = True
            if worked:
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_seconds
                )
            except TimeoutError:
                pass
