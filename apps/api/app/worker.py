import asyncio
import json
import logging
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.ingestion import (
    IngestionLease,
    IngestionProcessor,
    IngestionVersionError,
    ProcessedIngestion,
    StaleIngestionClaim,
    VersionedIngestionProcessor,
)
from app.services.object_storage import ObjectStoreError
from app.services.ollama_embeddings import EmbeddingServiceError
from app.services.parsing.ocr_subprocess import OcrError
from app.services.parsing.pdf import DocumentWorkLimitError

logger = logging.getLogger(__name__)


class ControlledServiceLease:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        service_name: str,
        owner_id: str,
        lease_seconds: int,
    ) -> None:
        self._session_factory = session_factory
        self._service_name = service_name
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds
        self._lease_token: uuid.UUID | None = None
        self._fencing_token: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._failure: BaseException | None = None

    @property
    def healthy(self) -> bool:
        return (
            self._failure is None and self._task is not None and not self._task.done()
        )

    async def start(self) -> None:
        async with self._session_factory() as session, session.begin():
            identity_valid = await session.scalar(
                text("SELECT v4_runtime_identity('rag_worker')")
            )
            if not identity_valid:
                raise RuntimeError(
                    "worker database identity validation failed; use rag_worker"
                )
            row = (
                await session.execute(
                    text(
                        "SELECT lease_token, fencing_token "
                        "FROM v4_claim_service_lease("
                        ":service_name, :owner_id, :lease_seconds)"
                    ),
                    {
                        "service_name": self._service_name,
                        "owner_id": self._owner_id,
                        "lease_seconds": self._lease_seconds,
                    },
                )
            ).one()
        self._lease_token = row.lease_token
        self._fencing_token = row.fencing_token
        self._failure = None
        self._task = asyncio.create_task(
            self._heartbeat_loop(), name=f"{self._service_name}-lease"
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def wait(self) -> None:
        if self._task is None:
            raise RuntimeError("service ownership lease has not started")
        await self._task
        if self._failure is not None:
            raise RuntimeError(
                f"{self._service_name} ownership lease failed"
            ) from self._failure

    async def _heartbeat_loop(self) -> None:
        interval = max(1.0, self._lease_seconds / 3)
        try:
            while True:
                await asyncio.sleep(interval)
                async with self._session_factory() as session, session.begin():
                    renewed = await session.scalar(
                        text(
                            "SELECT v4_heartbeat_service_lease("
                            ":service_name, :owner_id, :lease_token, "
                            ":fencing_token, :lease_seconds)"
                        ),
                        {
                            "service_name": self._service_name,
                            "owner_id": self._owner_id,
                            "lease_token": self._lease_token,
                            "fencing_token": self._fencing_token,
                            "lease_seconds": self._lease_seconds,
                        },
                    )
                if not renewed:
                    raise RuntimeError(f"{self._service_name} ownership lease was lost")
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._failure = exc
            logger.exception("%s ownership heartbeat failed", self._service_name)


class IngestionWorker:
    """Single worker using only owner-controlled lease/fencing functions."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        processor: IngestionProcessor | VersionedIngestionProcessor,
        *,
        poll_seconds: float,
        owner_id: str,
        lease_seconds: int,
        maximum_attempts: int = 3,
        retry_base_seconds: float = 1.0,
        processing_timeout_seconds: int = 3_600,
    ) -> None:
        self._session_factory = session_factory
        self._processor = processor
        self._poll_seconds = poll_seconds
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds
        self._maximum_attempts = maximum_attempts
        self._retry_base_seconds = retry_base_seconds
        if processing_timeout_seconds < 1:
            raise ValueError("processing timeout must be positive")
        self._processing_timeout_seconds = processing_timeout_seconds
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="ingestion-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def wait(self) -> None:
        if self._task is None:
            raise RuntimeError("ingestion worker has not started")
        await self._task

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            claim = await self._claim()
            if claim is not None:
                try:
                    async with asyncio.timeout(self._processing_timeout_seconds):
                        result = await self._process_with_heartbeat(claim)
                    await self._commit(claim, result)
                except StaleIngestionClaim:
                    logger.warning("ingestion claim became stale: %s", claim.job_id)
                except SQLAlchemyError:
                    raise
                except IngestionVersionError as exc:
                    try:
                        await self._poison(claim, str(exc))
                    except StaleIngestionClaim:
                        logger.warning(
                            "ingestion version transition became stale: %s",
                            claim.job_id,
                        )
                except DocumentWorkLimitError as exc:
                    try:
                        await self._poison(claim, str(exc))
                    except StaleIngestionClaim:
                        logger.warning(
                            "ingestion limit transition became stale: %s",
                            claim.job_id,
                        )
                except TimeoutError:
                    try:
                        await self._poison(
                            claim,
                            (
                                "processing_timeout: document processing exceeded "
                                "the configured time limit"
                            ),
                        )
                    except StaleIngestionClaim:
                        logger.warning(
                            "ingestion timeout transition became stale: %s",
                            claim.job_id,
                        )
                except ObjectStoreError as exc:
                    try:
                        if exc.retryable and claim.attempt < self._maximum_attempts:
                            await self._requeue(claim)
                        else:
                            await self._poison(claim, str(exc))
                    except StaleIngestionClaim:
                        logger.warning(
                            "ingestion error transition became stale: %s",
                            claim.job_id,
                        )
                except (EmbeddingServiceError, OcrError) as exc:
                    try:
                        if claim.attempt < self._maximum_attempts:
                            await self._requeue(claim)
                        else:
                            await self._poison(claim, str(exc))
                    except StaleIngestionClaim:
                        logger.warning(
                            "ingestion retry transition became stale: %s",
                            claim.job_id,
                        )
                except Exception:
                    logger.exception("ingestion processor failed unexpectedly")
                    try:
                        await self._poison(claim, "ingestion processor failed")
                    except StaleIngestionClaim:
                        logger.warning(
                            "ingestion failure transition became stale: %s",
                            claim.job_id,
                        )
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_seconds
                )
            except TimeoutError:
                continue

    async def _claim(self) -> IngestionLease | None:
        async with self._session_factory() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT job_id, document_id, object_key, "
                        "original_filename, sha256, byte_size, attempt, "
                        "lease_token, fencing_token, parser_version, "
                        "chunking_version, embedding_version "
                        "FROM v4_claim_ingestion_job(:owner_id, :lease_seconds)"
                    ),
                    {
                        "owner_id": self._owner_id,
                        "lease_seconds": self._lease_seconds,
                    },
                )
            ).one_or_none()
        if row is None:
            return None
        return IngestionLease(
            job_id=row.job_id,
            document_id=row.document_id,
            object_key=row.object_key,
            filename=row.original_filename,
            source_sha256=row.sha256,
            byte_size=row.byte_size,
            parser_version=row.parser_version,
            chunking_version=row.chunking_version,
            embedding_version=row.embedding_version,
            attempt=row.attempt,
            lease_token=row.lease_token,
            fencing_token=row.fencing_token,
        )

    async def _process_with_heartbeat(
        self, claim: IngestionLease
    ) -> ProcessedIngestion:
        async def progress(stage: str, completed: int, total: int) -> None:
            await self._update_progress(claim, stage, completed, total)

        processing = asyncio.create_task(
            self._processor.process(claim, progress=progress)
        )
        interval = max(1.0, self._lease_seconds / 3)
        try:
            while True:
                done, _ = await asyncio.wait(
                    {processing},
                    timeout=interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done:
                    return await processing
                if not await self._heartbeat(claim):
                    processing.cancel()
                    await asyncio.gather(processing, return_exceptions=True)
                    raise StaleIngestionClaim(
                        f"ingestion lease expired for {claim.job_id}"
                    )
        except BaseException:
            if not processing.done():
                processing.cancel()
                await asyncio.gather(processing, return_exceptions=True)
            raise

    async def _heartbeat(self, claim: IngestionLease) -> bool:
        async with self._session_factory() as session, session.begin():
            return bool(
                await session.scalar(
                    text(
                        "SELECT v4_heartbeat_ingestion_job("
                        ":job_id, :lease_token, :fencing_token, :lease_seconds)"
                    ),
                    {
                        "job_id": claim.job_id,
                        "lease_token": claim.lease_token,
                        "fencing_token": claim.fencing_token,
                        "lease_seconds": self._lease_seconds,
                    },
                )
            )

    async def _update_progress(
        self,
        claim: IngestionLease,
        stage: str,
        completed_units: int,
        total_units: int,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            outcome = await session.scalar(
                text(
                    "SELECT v4_update_ingestion_progress("
                    ":job_id, :lease_token, :fencing_token, :stage, "
                    ":completed_units, :total_units)"
                ),
                {
                    "job_id": claim.job_id,
                    "lease_token": claim.lease_token,
                    "fencing_token": claim.fencing_token,
                    "stage": stage,
                    "completed_units": completed_units,
                    "total_units": total_units,
                },
            )
        if outcome == "stale":
            raise StaleIngestionClaim(
                f"ingestion progress update was stale for {claim.job_id}"
            )
        if outcome != "accepted":
            raise RuntimeError("ingestion progress returned an invalid outcome")

    async def _commit(self, claim: IngestionLease, result: ProcessedIngestion) -> None:
        async with self._session_factory() as session, session.begin():
            committed = await session.scalar(
                text(
                    "SELECT v4_commit_ingestion_job("
                    ":job_id, :lease_token, :fencing_token, "
                    ":page_count, CAST(:chunks AS jsonb))"
                ),
                {
                    "job_id": claim.job_id,
                    "lease_token": claim.lease_token,
                    "fencing_token": claim.fencing_token,
                    "page_count": result.page_count,
                    "chunks": json.dumps(result.chunks, ensure_ascii=False),
                },
            )
        if not committed:
            raise StaleIngestionClaim(f"ingestion commit was stale for {claim.job_id}")

    async def _requeue(self, claim: IngestionLease) -> None:
        exponent = min(max(claim.attempt - 1, 0), 20)
        delay = min(self._retry_base_seconds * (2**exponent), 300.0)
        available_at = datetime.now(UTC) + timedelta(seconds=delay)
        async with self._session_factory() as session, session.begin():
            requeued = await session.scalar(
                text(
                    "SELECT v4_requeue_ingestion_job("
                    ":job_id, :lease_token, :fencing_token, :available_at)"
                ),
                {
                    "job_id": claim.job_id,
                    "lease_token": claim.lease_token,
                    "fencing_token": claim.fencing_token,
                    "available_at": available_at,
                },
            )
        if not requeued:
            raise StaleIngestionClaim(f"ingestion requeue was stale for {claim.job_id}")

    async def _poison(self, claim: IngestionLease, error: str) -> None:
        message = (error.strip() or "ingestion failed")[:500]
        async with self._session_factory() as session, session.begin():
            poisoned = await session.scalar(
                text(
                    "SELECT v4_poison_ingestion_job("
                    ":job_id, :lease_token, :fencing_token, :error)"
                ),
                {
                    "job_id": claim.job_id,
                    "lease_token": claim.lease_token,
                    "fencing_token": claim.fencing_token,
                    "error": message,
                },
            )
        if not poisoned:
            raise StaleIngestionClaim(f"ingestion failure was stale for {claim.job_id}")


def main() -> None:
    from app.processes.ingestion_worker import main as process_main

    process_main()


if __name__ == "__main__":
    main()
