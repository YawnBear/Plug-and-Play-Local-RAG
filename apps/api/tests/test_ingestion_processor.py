import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain import ParseMethod
from app.services.chunking import DocumentChunker
from app.services.ingestion import (
    IngestionLease,
    IngestionProcessor,
    IngestionVersionError,
    ProcessedIngestion,
    VersionedIngestionProcessor,
)
from app.services.ollama_embeddings import EmbeddingServiceError
from app.services.parsing.pdf import DocumentWorkLimitError
from app.services.parsing.types import ParsedPage
from app.versions import (
    ADAPTIVE_PARSER_VERSION,
    CHUNKING_VERSION,
    EMBEDDING_VERSION,
    FRAGMENT_CHUNKING_VERSION,
    PARSER_VERSION,
)


class _Parser:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        pages: list[ParsedPage] | None = None,
    ) -> None:
        self.error = error
        self.pages = pages

    async def parse(self, path: Path, *, progress=None) -> list[ParsedPage]:
        if self.error is not None:
            raise self.error
        return self.pages or [
            ParsedPage(1, "Grounded searchable text.", ParseMethod.DIRECT)
        ]


class _Embedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        assert texts == ["Grounded searchable text."]
        return [[1.0, *([0.0] * 1023)]]


class _BatchEmbedder:
    def __init__(
        self,
        *,
        fail_once_on_call: int | None = None,
        cancel_on_call: int | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.fail_once_on_call = fail_once_on_call
        self.cancel_on_call = cancel_on_call
        self.failed = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        call_number = len(self.calls)
        if call_number == self.cancel_on_call:
            raise asyncio.CancelledError
        if call_number == self.fail_once_on_call and not self.failed:
            self.failed = True
            raise EmbeddingServiceError("transient embedding failure")
        return [[float(index), *([0.0] * 1023)] for index, _ in enumerate(texts)]


class _Materializer:
    @asynccontextmanager
    async def materialize(self, **kwargs: object):
        yield SimpleNamespace(path=Path("C:/verified/original.pdf"))


def _claim() -> IngestionLease:
    return IngestionLease(
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        object_key=f"originals/aa/{'a' * 64}.pdf",
        filename="original.pdf",
        source_sha256="a" * 64,
        byte_size=123,
        parser_version=PARSER_VERSION,
        chunking_version=CHUNKING_VERSION,
        embedding_version=EMBEDDING_VERSION,
        attempt=1,
        lease_token=uuid.uuid4(),
        fencing_token=1,
    )


def test_ingestion_returns_bounded_commit_payload_without_database_dml() -> None:
    async def exercise():
        updates: list[tuple[str, int, int]] = []

        async def progress(stage: str, completed: int, total: int) -> None:
            updates.append((stage, completed, total))

        result = await IngestionProcessor(
            _Parser(), DocumentChunker(), _Embedder(), _Materializer()
        ).process(_claim(), progress=progress)
        return result, updates

    result, updates = asyncio.run(exercise())

    assert result.page_count == 1
    assert len(result.chunks) == 1
    chunk = result.chunks[0]
    assert chunk["filename"] == "original.pdf"
    assert chunk["page_start"] == 1
    assert chunk["source_sha256"] == "a" * 64
    assert str(chunk["embedding"]).startswith("[1.0,0.0")
    assert updates == [
        ("parsing", 0, 1),
        ("parsing", 1, 1),
        ("chunking", 0, 1),
        ("chunking", 1, 1),
        ("embedding", 0, 1),
        ("embedding", 1, 1),
        ("indexing", 0, 1),
    ]


def test_ingestion_propagates_external_failure_for_fenced_worker_transition() -> None:
    processor = IngestionProcessor(
        _Parser(error=RuntimeError("parse failed")),
        DocumentChunker(),
        _Embedder(),
        _Materializer(),
    )

    with pytest.raises(RuntimeError, match="parse failed"):
        asyncio.run(
            processor.process(
                _claim(),
                progress=lambda *_args: asyncio.sleep(0),
            )
        )


def _sentence_page(count: int) -> ParsedPage:
    return ParsedPage(
        1,
        " ".join(f"word{index}." for index in range(count)),
        ParseMethod.DIRECT,
    )


@pytest.mark.parametrize(
    ("chunk_count", "expected_sizes"),
    [(1, [1]), (32, [32]), (33, [32, 1]), (65, [32, 32, 1])],
)
def test_embedding_batches_preserve_order_across_protocol_boundaries(
    chunk_count: int,
    expected_sizes: list[int],
) -> None:
    embedder = _BatchEmbedder()
    processor = IngestionProcessor(
        _Parser(pages=[_sentence_page(chunk_count)]),
        DocumentChunker(target_tokens=2, max_tokens=2, overlap_tokens=0),
        embedder,
        _Materializer(),
        embedding_batch_size=32,
    )

    result = asyncio.run(
        processor.process(
            _claim(),
            progress=lambda *_args: asyncio.sleep(0),
        )
    )

    assert len(result.chunks) == chunk_count
    assert [len(batch) for batch in embedder.calls] == expected_sizes
    assert [text for batch in embedder.calls for text in batch] == [
        f"word{index}." for index in range(chunk_count)
    ]


def test_configured_embedding_batch_smaller_than_protocol_limit() -> None:
    embedder = _BatchEmbedder()
    processor = IngestionProcessor(
        _Parser(pages=[_sentence_page(9)]),
        DocumentChunker(target_tokens=2, max_tokens=2, overlap_tokens=0),
        embedder,
        _Materializer(),
        embedding_batch_size=4,
    )

    asyncio.run(
        processor.process(
            _claim(),
            progress=lambda *_args: asyncio.sleep(0),
        )
    )

    assert [len(batch) for batch in embedder.calls] == [4, 4, 1]


def test_failed_middle_embedding_batch_retries_only_that_batch() -> None:
    embedder = _BatchEmbedder(fail_once_on_call=2)
    processor = IngestionProcessor(
        _Parser(pages=[_sentence_page(65)]),
        DocumentChunker(target_tokens=2, max_tokens=2, overlap_tokens=0),
        embedder,
        _Materializer(),
        embedding_batch_size=32,
    )

    result = asyncio.run(
        processor.process(
            _claim(),
            progress=lambda *_args: asyncio.sleep(0),
        )
    )

    assert len(result.chunks) == 65
    assert [batch[0] for batch in embedder.calls] == [
        "word0.",
        "word32.",
        "word32.",
        "word64.",
    ]


def test_embedding_cancellation_stops_before_later_batches() -> None:
    embedder = _BatchEmbedder(cancel_on_call=2)
    processor = IngestionProcessor(
        _Parser(pages=[_sentence_page(65)]),
        DocumentChunker(target_tokens=2, max_tokens=2, overlap_tokens=0),
        embedder,
        _Materializer(),
        embedding_batch_size=32,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            processor.process(
                _claim(),
                progress=lambda *_args: asyncio.sleep(0),
            )
        )

    assert len(embedder.calls) == 2


def test_chunk_limit_fails_before_any_embedding_call() -> None:
    embedder = _BatchEmbedder()
    processor = IngestionProcessor(
        _Parser(pages=[_sentence_page(33)]),
        DocumentChunker(target_tokens=2, max_tokens=2, overlap_tokens=0),
        embedder,
        _Materializer(),
        maximum_document_chunks=32,
    )

    with pytest.raises(DocumentWorkLimitError, match="chunk_limit_exceeded"):
        asyncio.run(
            processor.process(
                _claim(),
                progress=lambda *_args: asyncio.sleep(0),
            )
        )

    assert embedder.calls == []


def test_versioned_processor_routes_legacy_and_adaptive_exactly() -> None:
    calls: list[str] = []

    class Processor:
        def __init__(self, name: str, version: tuple[str, str, str]) -> None:
            self.name = name
            self.version = version

        async def process(
            self, claim: IngestionLease, *, progress: object
        ) -> ProcessedIngestion:
            calls.append(self.name)
            return ProcessedIngestion(
                page_count=1,
                chunks=(
                    {
                        "parser_version": self.version[0],
                        "chunking_version": self.version[1],
                        "embedding_version": self.version[2],
                    },
                ),
            )

    legacy = (PARSER_VERSION, CHUNKING_VERSION, EMBEDDING_VERSION)
    adaptive = (
        ADAPTIVE_PARSER_VERSION,
        FRAGMENT_CHUNKING_VERSION,
        EMBEDDING_VERSION,
    )
    router = VersionedIngestionProcessor(
        {
            legacy: Processor("legacy", legacy),
            adaptive: Processor("adaptive", adaptive),
        }
    )

    async def exercise() -> None:
        claim = _claim()
        await router.process(claim, progress=lambda *_args: asyncio.sleep(0))
        adaptive_claim = IngestionLease(
            job_id=claim.job_id,
            document_id=claim.document_id,
            object_key=claim.object_key,
            filename=claim.filename,
            source_sha256=claim.source_sha256,
            byte_size=claim.byte_size,
            parser_version=adaptive[0],
            chunking_version=adaptive[1],
            embedding_version=adaptive[2],
            attempt=claim.attempt,
            lease_token=claim.lease_token,
            fencing_token=claim.fencing_token,
        )
        await router.process(adaptive_claim, progress=lambda *_args: asyncio.sleep(0))

    asyncio.run(exercise())
    assert calls == ["legacy", "adaptive"]


def test_versioned_processor_rejects_unknown_tuple_before_external_work() -> None:
    class Processor:
        async def process(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("unsupported tuple must not reach a processor")

    claim = _claim()
    unknown_claim = IngestionLease(
        job_id=claim.job_id,
        document_id=claim.document_id,
        object_key=claim.object_key,
        filename=claim.filename,
        source_sha256=claim.source_sha256,
        byte_size=claim.byte_size,
        parser_version=PARSER_VERSION,
        chunking_version=FRAGMENT_CHUNKING_VERSION,
        embedding_version=EMBEDDING_VERSION,
        attempt=claim.attempt,
        lease_token=claim.lease_token,
        fencing_token=claim.fencing_token,
    )
    router = VersionedIngestionProcessor(
        {
            (
                PARSER_VERSION,
                CHUNKING_VERSION,
                EMBEDDING_VERSION,
            ): Processor()
        }
    )

    with pytest.raises(IngestionVersionError, match="unsupported_ingestion_version"):
        asyncio.run(
            router.process(
                unknown_claim,
                progress=lambda *_args: asyncio.sleep(0),
            )
        )


def test_versioned_processor_rejects_forged_result_before_commit() -> None:
    class ForgedProcessor:
        async def process(
            self, claim: IngestionLease, *, progress: object
        ) -> ProcessedIngestion:
            return ProcessedIngestion(
                page_count=1,
                chunks=(
                    {
                        "parser_version": ADAPTIVE_PARSER_VERSION,
                        "chunking_version": FRAGMENT_CHUNKING_VERSION,
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
            ): ForgedProcessor()
        }
    )
    with pytest.raises(IngestionVersionError, match="ingestion_version_mismatch"):
        asyncio.run(
            router.process(
                _claim(),
                progress=lambda *_args: asyncio.sleep(0),
            )
        )
