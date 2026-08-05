import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services.object_lifecycle import (
    ObjectIntegrityError,
    ObjectMaterializer,
    canonical_object_key,
)
from app.services.object_storage import ObjectStore


class DocumentReingestNotFound(RuntimeError):
    pass


class DocumentNotRetryable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReingestPreparation:
    document_id: uuid.UUID
    sha256: str
    byte_size: int
    object_key: str
    parser_version: str
    chunking_version: str
    embedding_version: str
    previous_job_id: uuid.UUID
    previous_job_status: str
    snapshot_token: str


@dataclass(frozen=True, slots=True)
class ReingestResult:
    document_id: uuid.UUID
    job_id: uuid.UUID
    status: str = "queued"


class DocumentReingestService:
    def __init__(
        self,
        settings: Settings,
        object_store: ObjectStore,
    ) -> None:
        self._materializer = ObjectMaterializer(
            object_store, settings.object_work_path
        )

    async def prepare(
        self,
        session: AsyncSession,
        document_id: uuid.UUID,
    ) -> ReingestPreparation:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM "
                        "v4_prepare_document_reingest(:document_id)"
                    ),
                    {"document_id": document_id},
                )
            )
            .mappings()
            .one()
        )
        status = str(row["result_status"])
        if status == "not_found":
            raise DocumentReingestNotFound
        if status in {"not_retryable", "active_job"}:
            raise DocumentNotRetryable
        if status != "prepared":
            raise RuntimeError("unexpected document reingest preparation outcome")
        preparation = ReingestPreparation(
            document_id=row["document_id"],
            sha256=str(row["sha256"]),
            byte_size=int(row["byte_size"]),
            object_key=str(row["object_key"]),
            parser_version=str(row["parser_version"]),
            chunking_version=str(row["chunking_version"]),
            embedding_version=str(row["embedding_version"]),
            previous_job_id=row["previous_job_id"],
            previous_job_status=str(row["previous_job_status"]),
            snapshot_token=str(row["snapshot_token"]),
        )
        try:
            expected_key = canonical_object_key(preparation.sha256)
        except ValueError as exc:
            raise ObjectIntegrityError(
                "document has invalid object metadata"
            ) from exc
        if preparation.object_key != expected_key or preparation.byte_size <= 0:
            raise ObjectIntegrityError("document has invalid object metadata")
        return preparation

    async def verify_original(self, preparation: ReingestPreparation) -> None:
        async with self._materializer.materialize(
            key=preparation.object_key,
            sha256=preparation.sha256,
            byte_size=preparation.byte_size,
        ):
            pass

    async def commit(
        self,
        session: AsyncSession,
        preparation: ReingestPreparation,
        job_id: uuid.UUID,
    ) -> ReingestResult:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM v4_commit_document_reingest("
                        ":document_id, :snapshot_token, :job_id)"
                    ),
                    {
                        "document_id": preparation.document_id,
                        "snapshot_token": preparation.snapshot_token,
                        "job_id": job_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        status = str(row["result_status"])
        if status == "not_found":
            raise DocumentReingestNotFound
        if status in {"not_retryable", "active_job", "stale"}:
            raise DocumentNotRetryable
        if status != "created":
            raise RuntimeError("unexpected document reingest commit outcome")
        return ReingestResult(
            document_id=row["document_id"],
            job_id=row["job_id"],
        )
