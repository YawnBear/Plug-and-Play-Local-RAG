import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.services.chunking import DocumentChunker
from app.services.object_lifecycle import ObjectMaterializer
from app.services.ollama_embeddings import EmbeddingServiceError, OllamaEmbeddingClient
from app.services.parsing.pdf import DocumentWorkLimitError, PdfParser

IngestionVersion = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class IngestionLease:
    job_id: uuid.UUID
    document_id: uuid.UUID
    object_key: str
    filename: str
    source_sha256: str
    byte_size: int
    parser_version: str
    chunking_version: str
    embedding_version: str
    attempt: int
    lease_token: uuid.UUID
    fencing_token: int

    @property
    def version(self) -> IngestionVersion:
        return (
            self.parser_version,
            self.chunking_version,
            self.embedding_version,
        )


@dataclass(frozen=True, slots=True)
class ProcessedIngestion:
    page_count: int
    chunks: tuple[dict[str, object], ...]


ProgressCallback = Callable[[str, int, int], Awaitable[None]]


class IngestionProcessor:
    """Performs only external parsing/embedding work; the worker commits results."""

    def __init__(
        self,
        parser: PdfParser,
        chunker: DocumentChunker,
        embedder: OllamaEmbeddingClient,
        materializer: ObjectMaterializer,
        *,
        embedding_batch_size: int = 16,
        maximum_document_chunks: int = 5_000,
        external_batch_max_attempts: int = 2,
    ) -> None:
        if not 1 <= embedding_batch_size <= 32:
            raise ValueError("embedding batch size must be between 1 and 32")
        if maximum_document_chunks < 1:
            raise ValueError("maximum document chunks must be positive")
        if not 1 <= external_batch_max_attempts <= 4:
            raise ValueError("external batch attempts must be between 1 and 4")
        self._parser = parser
        self._chunker = chunker
        self._embedder = embedder
        self._materializer = materializer
        self._embedding_batch_size = embedding_batch_size
        self._maximum_document_chunks = maximum_document_chunks
        self._external_batch_max_attempts = external_batch_max_attempts

    async def process(
        self,
        claim: IngestionLease,
        *,
        progress: ProgressCallback,
    ) -> ProcessedIngestion:
        await progress("parsing", 0, 1)
        parsing_batches_reported = False

        async def parsing_progress(completed: int, total: int) -> None:
            nonlocal parsing_batches_reported
            parsing_batches_reported = True
            await progress("parsing", completed, total)

        async with self._materializer.materialize(
            key=claim.object_key,
            sha256=claim.source_sha256,
            byte_size=claim.byte_size,
        ) as materialized:
            pages = await self._parser.parse(
                materialized.path,
                progress=parsing_progress,
            )
        if not parsing_batches_reported:
            await progress("parsing", 1, 1)
        await progress("chunking", 0, 1)
        drafts = self._chunker.chunk(
            pages,
            document_id=claim.document_id,
            filename=claim.filename,
            source_sha256=claim.source_sha256,
        )
        if not drafts:
            raise RuntimeError("document produced no searchable text chunks")
        if len(drafts) > self._maximum_document_chunks:
            raise DocumentWorkLimitError(
                "chunk_limit_exceeded",
                (
                    "Document produces more than the configured "
                    f"{self._maximum_document_chunks}-chunk limit"
                ),
            )
        await progress("chunking", 1, 1)
        await progress("embedding", 0, len(drafts))
        vectors: list[list[float]] = []
        for start in range(0, len(drafts), self._embedding_batch_size):
            await asyncio.sleep(0)
            batch = drafts[start : start + self._embedding_batch_size]
            embedded = await self._embed_batch([draft.text for draft in batch])
            if len(embedded) != len(batch):
                raise EmbeddingServiceError(
                    "embedding batch returned an unexpected result count"
                )
            vectors.extend(embedded)
            await progress("embedding", len(vectors), len(drafts))
        if len(drafts) != len(vectors):
            raise RuntimeError("embedding count does not match chunk count")
        chunks = tuple(
            {
                "id": str(draft.id),
                "ordinal": draft.ordinal,
                "filename": draft.filename,
                "page_start": draft.page_start,
                "page_end": draft.page_end,
                "section": draft.section,
                "text": draft.text,
                "token_count": draft.token_count,
                "text_sha256": draft.text_sha256,
                "source_sha256": draft.source_sha256,
                "parse_method": draft.parse_method.value,
                "parser_version": draft.parser_version,
                "chunking_version": draft.chunking_version,
                "embedding_version": draft.embedding_version,
                "schema_version": draft.schema_version,
                "citation_label": draft.citation_label,
                "highlight_anchor": draft.highlight_anchor,
                "embedding": "[" + ",".join(str(value) for value in vector) + "]",
            }
            for draft, vector in zip(drafts, vectors, strict=True)
        )
        await progress("indexing", 0, len(chunks))
        return ProcessedIngestion(page_count=len(pages), chunks=chunks)

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        last_error: EmbeddingServiceError | None = None
        for attempt in range(1, self._external_batch_max_attempts + 1):
            try:
                return await self._embedder.embed(texts)
            except asyncio.CancelledError:
                raise
            except EmbeddingServiceError as exc:
                last_error = exc
                if attempt == self._external_batch_max_attempts:
                    break
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
        raise EmbeddingServiceError(
            "embedding batch failed after bounded retries"
        ) from last_error


class IngestionVersionError(RuntimeError):
    pass


class VersionedIngestionProcessor:
    """Dispatches a claimed document only to its exact stored processor identity."""

    def __init__(
        self,
        processors: dict[IngestionVersion, IngestionProcessor],
    ) -> None:
        if not processors:
            raise ValueError("at least one ingestion processor is required")
        self._processors = dict(processors)

    async def process(
        self,
        claim: IngestionLease,
        *,
        progress: ProgressCallback,
    ) -> ProcessedIngestion:
        processor = self._processors.get(claim.version)
        if processor is None:
            raise IngestionVersionError(
                "unsupported_ingestion_version: document ingestion version "
                "tuple is not supported"
            )
        result = await processor.process(claim, progress=progress)
        for chunk in result.chunks:
            emitted_version = (
                chunk.get("parser_version"),
                chunk.get("chunking_version"),
                chunk.get("embedding_version"),
            )
            if emitted_version != claim.version:
                raise IngestionVersionError(
                    "ingestion_version_mismatch: processor output does not "
                    "match the claimed document version"
                )
        return result


class StaleIngestionClaim(RuntimeError):
    pass
