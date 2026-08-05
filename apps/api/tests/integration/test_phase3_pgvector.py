import asyncio
import math
import os
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Chunk, Document, IngestionJob
from app.db.repositories import ChunkRepository, IngestionJobRepository
from app.domain import JobStatus

pytestmark = pytest.mark.integration


def _cosine_distance(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return 1 - dot / (left_norm * right_norm)


def test_hnsw_matches_exact_and_cascade_restart_semantics() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            document = Document(
                id=uuid.uuid4(),
                sha256=uuid.uuid4().hex * 2,
                original_filename="vectors.pdf",
                mime_type="application/pdf",
                byte_size=1,
                content_path="C:/integration/vectors.pdf",
                state="ready",
                stage="ready",
                parser_version="test",
                chunking_version="test",
                embedding_version="test",
                page_count=1,
                chunk_count=3,
            )
            session.add(document)
            vectors = [
                [1.0, 0.0, *([0.0] * 1022)],
                [0.8, 0.2, *([0.0] * 1022)],
                [0.0, 1.0, *([0.0] * 1022)],
            ]
            chunks = [
                Chunk(
                    id=uuid.uuid4(),
                    document_id=document.id,
                    ordinal=index,
                    filename="vectors.pdf",
                    page_start=1,
                    page_end=1,
                    text=f"controlled {index}",
                    token_count=2,
                    text_sha256=f"{index}" * 64,
                    source_sha256=document.sha256,
                    parse_method="direct",
                    parser_version="test",
                    chunking_version="test",
                    embedding_version="test",
                    schema_version="test",
                    citation_label=f"p1:c{index}",
                    embedding=vector,
                )
                for index, vector in enumerate(vectors)
            ]
            await ChunkRepository(session).add_all(chunks)
            running = IngestionJob(
                id=uuid.uuid4(),
                document_id=document.id,
                status=JobStatus.RUNNING.value,
                stage="embedding",
            )
            session.add(running)
            await session.flush()

            query = [1.0, 0.0, *([0.0] * 1022)]
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            vector_literal = "[" + ",".join(str(value) for value in query) + "]"
            plan_rows = await session.execute(
                text(
                    "EXPLAIN SELECT id FROM chunks "
                    "ORDER BY embedding <=> CAST(:query AS vector) LIMIT 3"
                ),
                {"query": vector_literal},
            )
            plan = "\n".join(row[0] for row in plan_rows)
            assert "chunks_embedding_hnsw" in plan
            approximate = await ChunkRepository(session).retrieve(query, limit=3)
            exact = sorted(
                chunks, key=lambda chunk: _cosine_distance(chunk.embedding, query)
            )
            assert [item.chunk.id for item in approximate] == [
                chunk.id for chunk in exact
            ]

            assert await IngestionJobRepository(session).interrupt_running() >= 1
            await session.flush()
            await session.refresh(running)
            await session.refresh(document)
            assert running.status == JobStatus.INTERRUPTED.value
            assert document.state == "failed"

            await session.delete(document)
            await session.flush()
            remaining = await session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.document_id == document.id)
            )
            assert remaining == 0
            await session.rollback()
        await engine.dispose()

    asyncio.run(exercise(), loop_factory=asyncio.SelectorEventLoop)
