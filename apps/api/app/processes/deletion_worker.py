import asyncio
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.processes.settings import ObjectWorkerSettings
from app.runtime.startup_diagnostics import run_with_startup_diagnostics
from app.services.object_deletions import ObjectDeletionService, ObjectDeletionWorker
from app.services.object_storage import S3ObjectStore
from app.worker import ControlledServiceLease

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DeletionWorkerContainer:
    database_engine: AsyncEngine
    object_store: S3ObjectStore
    worker: ObjectDeletionWorker
    ownership: ControlledServiceLease

    @classmethod
    def from_settings(cls, settings: ObjectWorkerSettings) -> "DeletionWorkerContainer":
        engine = create_async_engine(
            settings.worker_database_url,
            pool_size=settings.worker_database_pool_size,
            max_overflow=settings.worker_database_max_overflow,
            pool_pre_ping=True,
            pool_timeout=settings.worker_database_pool_timeout_seconds,
        )
        sessions = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        object_store = S3ObjectStore.from_settings(settings)
        owner = f"deletion-worker-{uuid.uuid4()}"
        service = ObjectDeletionService(
            sessions,
            object_store,
            lease_seconds=settings.object_deletion_lease_seconds,
            retry_base_seconds=settings.object_storage_retry_base_seconds,
            orphan_grace_seconds=settings.upload_orphan_grace_seconds,
            error_limit=settings.object_deletion_error_limit,
            owner=owner,
        )
        return cls(
            database_engine=engine,
            object_store=object_store,
            worker=ObjectDeletionWorker(
                service, poll_seconds=settings.object_deletion_poll_seconds
            ),
            ownership=ControlledServiceLease(
                sessions,
                service_name="deletion_worker",
                owner_id=owner,
                lease_seconds=settings.service_lease_seconds,
            ),
        )

    async def run(self) -> None:
        await self.ownership.start()
        await self.worker.start()
        ownership_wait = asyncio.create_task(self.ownership.wait())
        worker_wait = asyncio.create_task(self.worker.wait())
        pending: set[asyncio.Task[None]] = {ownership_wait, worker_wait}
        try:
            done, pending = await asyncio.wait(
                {ownership_wait, worker_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for completed in done:
                await completed
        finally:
            for remaining in pending:
                remaining.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await self.worker.stop()
            await self.ownership.stop()
            await self.object_store.close()
            await self.database_engine.dispose()

    def health(self) -> dict[str, object]:
        ready = self.ownership.healthy and self.worker.running
        return {
            "status": "ready" if ready else "unhealthy",
            "ownership": self.ownership.healthy,
            "worker": self.worker.running,
        }


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    container = DeletionWorkerContainer.from_settings(ObjectWorkerSettings())
    try:
        asyncio.run(container.run())
    except KeyboardInterrupt:
        logger.info("deletion worker interrupted")


if __name__ == "__main__":
    run_with_startup_diagnostics("deletion", main)
