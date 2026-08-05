import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Document, IngestionJob
from app.db.repositories import DocumentRepository, IngestionJobRepository
from app.domain import DocumentState, JobStatus

pytestmark = pytest.mark.integration


def test_document_and_interrupted_job_repository_round_trip() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            document_repository = DocumentRepository(session)
            job_repository = IngestionJobRepository(session)
            document = await document_repository.add(
                Document(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, "phase-1-document"),
                    sha256="a" * 64,
                    original_filename="phase-1.pdf",
                    mime_type="application/pdf",
                    byte_size=1,
                    content_path="C:/test/phase-1.pdf",
                    state=DocumentState.UPLOADED.value,
                    stage=DocumentState.UPLOADED.value,
                    parser_version="test",
                    chunking_version="test",
                    embedding_version="test",
                )
            )
            job = await job_repository.add(
                IngestionJob(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, "phase-1-job"),
                    document_id=document.id,
                    status=JobStatus.RUNNING.value,
                    stage=DocumentState.PARSING.value,
                )
            )
            assert await document_repository.find_by_sha256("a" * 64) == document
            assert await job_repository.interrupt_running() >= 1
            await session.flush()
            await session.refresh(job)
            assert job.status == JobStatus.INTERRUPTED.value
            await session.rollback()
        await engine.dispose()

    asyncio.run(exercise(), loop_factory=asyncio.SelectorEventLoop)
