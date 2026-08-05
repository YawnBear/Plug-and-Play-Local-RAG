import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.services.ingestion import (
    IngestionLease,
    IngestionVersionError,
    ProcessedIngestion,
    StaleIngestionClaim,
    VersionedIngestionProcessor,
)
from app.services.parsing.ocr_subprocess import OcrError
from app.versions import CHUNKING_VERSION, EMBEDDING_VERSION, PARSER_VERSION
from app.worker import IngestionWorker


class _Processor:
    async def process(self, claim: IngestionLease, *, progress: object) -> None:
        raise AssertionError("idle worker must not process a claim")


def _worker(processor: object) -> IngestionWorker:
    return IngestionWorker(
        None,
        processor,
        poll_seconds=0.01,
        owner_id="test-worker",
        lease_seconds=30,
    )


def test_worker_start_is_dml_free_until_controlled_claim() -> None:
    async def exercise() -> None:
        worker = _worker(_Processor())

        async def no_claim() -> None:
            return None

        worker._claim = no_claim
        await worker.start()
        await asyncio.sleep(0.03)
        assert worker.running
        await worker.stop()
        assert not worker.running

    asyncio.run(exercise())


def test_worker_uses_controlled_poison_transition_after_processor_failure() -> None:
    processed = asyncio.Event()
    claim = IngestionLease(
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        object_key=f"originals/aa/{'a' * 64}.pdf",
        filename="fixture.pdf",
        source_sha256="a" * 64,
        byte_size=100,
        parser_version=PARSER_VERSION,
        chunking_version=CHUNKING_VERSION,
        embedding_version=EMBEDDING_VERSION,
        attempt=1,
        lease_token=uuid.uuid4(),
        fencing_token=1,
    )

    class FailingProcessor:
        async def process(self, current: IngestionLease, *, progress: object) -> None:
            assert current == claim
            processed.set()
            raise RuntimeError("failed")

    async def exercise() -> None:
        worker = _worker(FailingProcessor())
        claims = [claim]
        poisoned: list[tuple[IngestionLease, str]] = []

        async def next_claim() -> IngestionLease | None:
            return claims.pop(0) if claims else None

        async def no_heartbeat(current: IngestionLease) -> bool:
            return True

        async def poison(current: IngestionLease, error: str) -> None:
            poisoned.append((current, error))

        worker._claim = next_claim
        worker._heartbeat = no_heartbeat
        worker._poison = poison
        await worker.start()
        await asyncio.wait_for(processed.wait(), timeout=1)
        await asyncio.sleep(0.03)
        assert worker.running
        assert poisoned == [(claim, "ingestion processor failed")]
        await worker.stop()

    asyncio.run(exercise())


def test_worker_requeues_transient_ocr_failure() -> None:
    processed = asyncio.Event()
    claim = IngestionLease(
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        object_key=f"originals/aa/{'a' * 64}.pdf",
        filename="fixture.pdf",
        source_sha256="a" * 64,
        byte_size=100,
        parser_version=PARSER_VERSION,
        chunking_version=CHUNKING_VERSION,
        embedding_version=EMBEDDING_VERSION,
        attempt=1,
        lease_token=uuid.uuid4(),
        fencing_token=1,
    )

    class FailingProcessor:
        async def process(self, current: IngestionLease, *, progress: object) -> None:
            processed.set()
            raise OcrError("isolated OCR service unavailable")

    async def exercise() -> None:
        worker = _worker(FailingProcessor())
        claims = [claim]
        requeued: list[IngestionLease] = []

        async def next_claim() -> IngestionLease | None:
            return claims.pop(0) if claims else None

        async def heartbeat(current: IngestionLease) -> bool:
            return True

        async def requeue(current: IngestionLease) -> None:
            requeued.append(current)

        worker._claim = next_claim
        worker._heartbeat = heartbeat
        worker._requeue = requeue
        await worker.start()
        await asyncio.wait_for(processed.wait(), timeout=1)
        await asyncio.sleep(0.03)
        assert requeued == [claim]
        await worker.stop()

    asyncio.run(exercise())


def test_worker_progress_uses_fenced_command_and_rejects_stale_claim() -> None:
    claim = IngestionLease(
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        object_key=f"originals/aa/{'a' * 64}.pdf",
        filename="fixture.pdf",
        source_sha256="a" * 64,
        byte_size=100,
        parser_version=PARSER_VERSION,
        chunking_version=CHUNKING_VERSION,
        embedding_version=EMBEDDING_VERSION,
        attempt=1,
        lease_token=uuid.uuid4(),
        fencing_token=7,
    )

    class Session:
        def __init__(self, outcome: str) -> None:
            self.outcome = outcome
            self.call: tuple[str, dict[str, object]] | None = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def begin(self):
            return self

        async def scalar(self, statement, parameters):
            self.call = (str(statement), parameters)
            return self.outcome

    async def exercise() -> None:
        accepted = Session("accepted")
        worker = IngestionWorker(
            lambda: accepted,
            _Processor(),
            poll_seconds=0.01,
            owner_id="test-worker",
            lease_seconds=30,
        )
        await worker._update_progress(claim, "embedding", 2, 5)
        assert accepted.call is not None
        statement, parameters = accepted.call
        assert "v4_update_ingestion_progress" in statement
        assert parameters == {
            "job_id": claim.job_id,
            "lease_token": claim.lease_token,
            "fencing_token": 7,
            "stage": "embedding",
            "completed_units": 2,
            "total_units": 5,
        }

        stale = Session("stale")
        worker._session_factory = lambda: stale
        with pytest.raises(StaleIngestionClaim):
            await worker._update_progress(claim, "embedding", 3, 5)

    asyncio.run(exercise())


def test_worker_poisoned_version_failure_does_not_stop_worker() -> None:
    processed = asyncio.Event()
    claim = IngestionLease(
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        object_key=f"originals/aa/{'a' * 64}.pdf",
        filename="fixture.pdf",
        source_sha256="a" * 64,
        byte_size=100,
        parser_version=PARSER_VERSION,
        chunking_version="fragment-paragraph-sentence-v2",
        embedding_version=EMBEDDING_VERSION,
        attempt=1,
        lease_token=uuid.uuid4(),
        fencing_token=1,
    )

    class FailingProcessor:
        async def process(self, current: IngestionLease, *, progress: object) -> None:
            processed.set()
            raise IngestionVersionError(
                "unsupported_ingestion_version: document ingestion version "
                "tuple is not supported"
            )

    async def exercise() -> None:
        worker = _worker(FailingProcessor())
        claims = [claim]
        poisoned: list[tuple[IngestionLease, str]] = []

        async def next_claim() -> IngestionLease | None:
            return claims.pop(0) if claims else None

        async def poison(current: IngestionLease, error: str) -> None:
            poisoned.append((current, error))

        worker._claim = next_claim
        worker._poison = poison
        await worker.start()
        await asyncio.wait_for(processed.wait(), timeout=1)
        await asyncio.sleep(0.03)
        assert worker.running
        assert poisoned == [
            (
                claim,
                "unsupported_ingestion_version: document ingestion version "
                "tuple is not supported",
            )
        ]
        await worker.stop()

    asyncio.run(exercise())


def test_worker_database_failure_stops_and_surfaces_from_worker() -> None:
    claim = IngestionLease(
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        object_key=f"originals/aa/{'a' * 64}.pdf",
        filename="fixture.pdf",
        source_sha256="a" * 64,
        byte_size=100,
        parser_version=PARSER_VERSION,
        chunking_version=CHUNKING_VERSION,
        embedding_version=EMBEDDING_VERSION,
        attempt=1,
        lease_token=uuid.uuid4(),
        fencing_token=1,
    )

    class DatabaseFailingProcessor:
        async def process(
            self, current: IngestionLease, *, progress: object
        ) -> None:
            assert current == claim
            raise SQLAlchemyError("database unavailable")

    async def exercise() -> None:
        worker = _worker(DatabaseFailingProcessor())
        claims = [claim]

        async def next_claim() -> IngestionLease | None:
            return claims.pop(0) if claims else None

        worker._claim = next_claim
        await worker.start()
        with pytest.raises(SQLAlchemyError, match="database unavailable"):
            await asyncio.wait_for(worker.wait(), timeout=1)
        assert not worker.running

    asyncio.run(exercise())


def test_worker_routes_and_commits_supported_legacy_claim() -> None:
    committed = asyncio.Event()
    claim = IngestionLease(
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        object_key=f"originals/aa/{'a' * 64}.pdf",
        filename="fixture.pdf",
        source_sha256="a" * 64,
        byte_size=100,
        parser_version=PARSER_VERSION,
        chunking_version=CHUNKING_VERSION,
        embedding_version=EMBEDDING_VERSION,
        attempt=1,
        lease_token=uuid.uuid4(),
        fencing_token=1,
    )

    class LegacyProcessor:
        async def process(
            self, current: IngestionLease, *, progress: object
        ) -> ProcessedIngestion:
            assert current == claim
            return ProcessedIngestion(
                page_count=1,
                chunks=(
                    {
                        "parser_version": PARSER_VERSION,
                        "chunking_version": CHUNKING_VERSION,
                        "embedding_version": EMBEDDING_VERSION,
                    },
                ),
            )

    router = VersionedIngestionProcessor(
        {
            (
                PARSER_VERSION,
                CHUNKING_VERSION,
                EMBEDDING_VERSION,
            ): LegacyProcessor()
        }
    )

    async def exercise() -> None:
        worker = _worker(router)
        claims = [claim]
        commits: list[tuple[IngestionLease, ProcessedIngestion]] = []

        async def next_claim() -> IngestionLease | None:
            return claims.pop(0) if claims else None

        async def commit(current: IngestionLease, result: ProcessedIngestion) -> None:
            commits.append((current, result))
            committed.set()

        worker._claim = next_claim
        worker._commit = commit
        await worker.start()
        await asyncio.wait_for(committed.wait(), timeout=1)
        assert worker.running
        assert commits[0][0] == claim
        assert commits[0][1].page_count == 1
        await worker.stop()

    asyncio.run(exercise())


def test_worker_claim_propagates_document_versions() -> None:
    row = SimpleNamespace(
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        object_key=f"originals/aa/{'a' * 64}.pdf",
        original_filename="fixture.pdf",
        sha256="a" * 64,
        byte_size=100,
        attempt=2,
        lease_token=uuid.uuid4(),
        fencing_token=7,
        parser_version=PARSER_VERSION,
        chunking_version=CHUNKING_VERSION,
        embedding_version=EMBEDDING_VERSION,
    )

    class Result:
        def one_or_none(self) -> object:
            return row

    class Session:
        statement = ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def begin(self):
            return self

        async def execute(self, statement, parameters):
            self.statement = str(statement)
            assert parameters == {
                "owner_id": "test-worker",
                "lease_seconds": 30,
            }
            return Result()

    async def exercise() -> tuple[IngestionLease, str]:
        session = Session()
        worker = IngestionWorker(
            lambda: session,
            _Processor(),
            poll_seconds=0.01,
            owner_id="test-worker",
            lease_seconds=30,
        )
        claim = await worker._claim()
        assert claim is not None
        return claim, session.statement

    claim, statement = asyncio.run(exercise())
    assert claim.version == (
        PARSER_VERSION,
        CHUNKING_VERSION,
        EMBEDDING_VERSION,
    )
    assert "parser_version" in statement
    assert "chunking_version" in statement
    assert "embedding_version" in statement
