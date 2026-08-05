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

from app.processes.settings import WorkerProcessSettings
from app.runtime.coordinator_client import (
    CoordinatorClient,
    CoordinatorEmbeddingClient,
    CoordinatorReranker,
)
from app.runtime.ocr_client import OcrServiceClient
from app.runtime.startup_diagnostics import run_with_startup_diagnostics
from app.services.chunking import DocumentChunker
from app.services.ingestion import IngestionProcessor, VersionedIngestionProcessor
from app.services.object_lifecycle import ObjectMaterializer
from app.services.object_storage import S3ObjectStore
from app.services.parsing.pdf import PdfParser
from app.services.reprocessing import ReprocessingWorker
from app.versions import (
    ADAPTIVE_PARSER_VERSION,
    CHUNKING_VERSION,
    EMBEDDING_VERSION,
    FRAGMENT_CHUNKING_VERSION,
    PARSER_VERSION,
)
from app.worker import ControlledServiceLease, IngestionWorker

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestionWorkerContainer:
    database_engine: AsyncEngine
    object_store: S3ObjectStore
    coordinator: CoordinatorClient
    ocr: OcrServiceClient
    worker: IngestionWorker
    reprocessor: ReprocessingWorker
    ownership: ControlledServiceLease

    @classmethod
    def from_settings(
        cls, settings: WorkerProcessSettings
    ) -> "IngestionWorkerContainer":
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
        coordinator = CoordinatorClient(
            str(settings.coordinator_base_url),
            settings.coordinator_service_token.get_secret_value(),
        )
        ocr = OcrServiceClient(
            str(settings.ocr_service_base_url),
            settings.ocr_service_token.get_secret_value(),
        )
        embedder = CoordinatorEmbeddingClient(coordinator, priority="background")
        materializer = ObjectMaterializer(
            object_store, settings.object_work_path.resolve()
        )

        def processor(*, adaptive: bool) -> IngestionProcessor:
            return IngestionProcessor(
                PdfParser(
                    ocr,
                    meaningful_text_threshold=settings.meaningful_text_threshold,
                    work_root=settings.ocr_work_path.resolve(),
                    ocr_batch_size=settings.ocr_page_batch_size,
                    maximum_pdf_pages=settings.maximum_pdf_pages,
                    maximum_ocr_pages=settings.maximum_ocr_pages,
                    maximum_extracted_text_characters=(
                        settings.maximum_extracted_text_characters
                    ),
                    maximum_staged_ocr_result_bytes=(
                        settings.maximum_staged_ocr_result_bytes
                    ),
                    external_batch_max_attempts=settings.external_batch_max_attempts,
                    enable_adaptive_page_routing=adaptive,
                    enable_visual_supplement_ocr=adaptive,
                ),
                DocumentChunker(
                    target_tokens=settings.chunk_target_tokens,
                    max_tokens=settings.chunk_max_tokens,
                    overlap_tokens=settings.chunk_overlap_tokens,
                    parser_version=(
                        ADAPTIVE_PARSER_VERSION if adaptive else PARSER_VERSION
                    ),
                    chunking_version=(
                        FRAGMENT_CHUNKING_VERSION if adaptive else CHUNKING_VERSION
                    ),
                ),
                embedder,
                materializer,
                embedding_batch_size=settings.embedding_batch_size,
                maximum_document_chunks=settings.maximum_document_chunks,
                external_batch_max_attempts=settings.external_batch_max_attempts,
            )

        versioned_processor = VersionedIngestionProcessor(
            {
                (
                    PARSER_VERSION,
                    CHUNKING_VERSION,
                    EMBEDDING_VERSION,
                ): processor(adaptive=False),
                (
                    ADAPTIVE_PARSER_VERSION,
                    FRAGMENT_CHUNKING_VERSION,
                    EMBEDDING_VERSION,
                ): processor(adaptive=True),
            }
        )
        owner = f"ingestion-worker-{uuid.uuid4()}"
        return cls(
            database_engine=engine,
            object_store=object_store,
            coordinator=coordinator,
            ocr=ocr,
            worker=IngestionWorker(
                sessions,
                versioned_processor,
                poll_seconds=settings.worker_poll_seconds,
                owner_id=owner,
                lease_seconds=settings.ingestion_lease_seconds,
                maximum_attempts=settings.ingestion_maximum_attempts,
                retry_base_seconds=settings.object_storage_retry_base_seconds,
                processing_timeout_seconds=(
                    settings.ingestion_processing_timeout_seconds
                ),
            ),
            reprocessor=ReprocessingWorker(
                sessions,
                embedder,
                CoordinatorReranker(coordinator),
                owner_id=owner,
                poll_seconds=settings.worker_poll_seconds,
                lease_seconds=settings.ingestion_lease_seconds,
                batch_size=settings.embedding_batch_size,
            ),
            ownership=ControlledServiceLease(
                sessions,
                service_name="ingestion_worker",
                owner_id=owner,
                lease_seconds=settings.service_lease_seconds,
            ),
        )

    async def run(self) -> None:
        await self.ownership.start()
        await self.worker.start()
        await self.reprocessor.start()
        ownership_wait = asyncio.create_task(self.ownership.wait())
        worker_wait = asyncio.create_task(self.worker.wait())
        reprocessing_wait = asyncio.create_task(self.reprocessor.wait())
        pending: set[asyncio.Task[None]] = {
            ownership_wait,
            worker_wait,
            reprocessing_wait,
        }
        try:
            done, pending = await asyncio.wait(
                {ownership_wait, worker_wait, reprocessing_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for completed in done:
                await completed
        finally:
            for remaining in pending:
                remaining.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await self.reprocessor.stop()
            await self.worker.stop()
            await self.ownership.stop()
            await self.ocr.close()
            await self.coordinator.close()
            await self.object_store.close()
            await self.database_engine.dispose()

    def health(self) -> dict[str, object]:
        ready = (
            self.ownership.healthy and self.worker.running and self.reprocessor.running
        )
        return {
            "status": "ready" if ready else "unhealthy",
            "ownership": self.ownership.healthy,
            "worker": self.worker.running,
            "reprocessor": self.reprocessor.running,
        }


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    container = IngestionWorkerContainer.from_settings(WorkerProcessSettings())
    try:
        asyncio.run(container.run())
    except KeyboardInterrupt:
        logger.info("ingestion worker interrupted")


if __name__ == "__main__":
    run_with_startup_diagnostics("ingestion", main)
