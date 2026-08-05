import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain import DocumentState, JobStatus
from app.services.object_lifecycle import (
    ObjectIntegrityError,
    ObjectMaterializer,
    canonical_object_key,
)
from app.services.object_storage import ObjectStore, ObjectStoreError

REBUILD_ALL_CONFIRMATION = "REBUILD-ALL"
_ACTIVE_JOB_STATUSES = {JobStatus.QUEUED.value, JobStatus.RUNNING.value}
_RETRYABLE_JOB_STATUSES = {JobStatus.FAILED.value, JobStatus.INTERRUPTED.value}


class MaintenanceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MaintenanceDocument:
    document_id: uuid.UUID
    state: str
    sha256: str
    byte_size: int
    object_key: str
    job_statuses: tuple[str, ...]
    snapshot_token: str


@dataclass(frozen=True, slots=True)
class RequeuedDocument:
    document_id: uuid.UUID
    job_id: uuid.UUID


class MaintenanceRepositoryProtocol(Protocol):
    async def get_document(
        self, document_id: uuid.UUID
    ) -> MaintenanceDocument | None: ...

    async def list_documents(self) -> Sequence[MaintenanceDocument]: ...

    async def requeue(
        self,
        document: MaintenanceDocument,
        job_id: uuid.UUID,
        *,
        retry_only: bool,
    ) -> str: ...


class MaintenanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_document(self, document_id: uuid.UUID) -> MaintenanceDocument | None:
        row = (
            (
                await self._session.execute(
                    text("SELECT * FROM v4_maintenance_get_document(:document_id)"),
                    {"document_id": document_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._record(row) if row is not None else None

    async def list_documents(self) -> Sequence[MaintenanceDocument]:
        rows = (
            await self._session.execute(
                text("SELECT * FROM v4_maintenance_list_documents()")
            )
        ).mappings()
        return [self._record(row) for row in rows]

    async def requeue(
        self,
        document: MaintenanceDocument,
        job_id: uuid.UUID,
        *,
        retry_only: bool,
    ) -> str:
        result = await self._session.scalar(
            text(
                "SELECT v4_maintenance_requeue_document("
                ":document_id, :snapshot_token, :job_id, :retry_only)"
            ),
            {
                "document_id": document.document_id,
                "snapshot_token": document.snapshot_token,
                "job_id": job_id,
                "retry_only": retry_only,
            },
        )
        return str(result)

    @staticmethod
    def _record(row: object) -> MaintenanceDocument:
        return MaintenanceDocument(
            document_id=row["document_id"],
            state=str(row["state"]),
            sha256=str(row["sha256"]),
            byte_size=int(row["byte_size"]),
            object_key=str(row["object_key"]),
            job_statuses=tuple(str(status) for status in row["job_statuses"]),
            snapshot_token=str(row["snapshot_token"]),
        )


class MaintenanceService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        object_store: ObjectStore,
        *,
        repository_factory: Callable[
            [AsyncSession], MaintenanceRepositoryProtocol
        ] = MaintenanceRepository,
    ) -> None:
        self._session_factory = session_factory
        self._repository_factory = repository_factory
        self._materializer = ObjectMaterializer(object_store, settings.object_work_path)

    async def retry(self, document_id: uuid.UUID) -> RequeuedDocument:
        return await self._object_requeue_one(document_id, retry=True)

    async def rebuild(self, document_id: uuid.UUID) -> RequeuedDocument:
        return await self._object_requeue_one(document_id, retry=False)

    async def rebuild_all(self, confirmation: str) -> list[RequeuedDocument]:
        if confirmation != REBUILD_ALL_CONFIRMATION:
            raise MaintenanceError(
                "all-document rebuild requires --confirm REBUILD-ALL"
            )
        return await self._object_rebuild_all()

    async def repair_interrupted_turns(self) -> int:
        async with self._session_factory() as session, session.begin():
            return int(
                await session.scalar(text("SELECT v4_repair_interrupted_turns()")) or 0
            )

    async def _object_requeue_one(
        self, document_id: uuid.UUID, *, retry: bool
    ) -> RequeuedDocument:
        async with self._session_factory() as session, session.begin():
            snapshot = await self._repository_factory(session).get_document(document_id)
            if snapshot is None:
                raise MaintenanceError(f"document not found: {document_id}")
            self._validate_object_state(snapshot, retry=retry)
        await self._verify_object(snapshot)
        job_id = uuid.uuid4()
        async with self._session_factory() as session, session.begin():
            outcome = await self._repository_factory(session).requeue(
                snapshot, job_id, retry_only=retry
            )
            self._require_created(snapshot.document_id, outcome, retry=retry)
        return RequeuedDocument(document_id, job_id)

    async def _object_rebuild_all(self) -> list[RequeuedDocument]:
        async with self._session_factory() as session, session.begin():
            snapshots = list(await self._repository_factory(session).list_documents())
            for document in snapshots:
                self._validate_object_state(document, retry=False)
        for document in snapshots:
            await self._verify_object(document)
        results: list[RequeuedDocument] = []
        async with self._session_factory() as session, session.begin():
            repository = self._repository_factory(session)
            for document in snapshots:
                job_id = uuid.uuid4()
                outcome = await repository.requeue(document, job_id, retry_only=False)
                self._require_created(document.document_id, outcome, retry=False)
                results.append(RequeuedDocument(document.document_id, job_id))
        return results

    def _validate_object_state(
        self,
        document: MaintenanceDocument,
        *,
        retry: bool,
    ) -> None:
        self._validate_inactive(document)
        try:
            expected_key = canonical_object_key(document.sha256)
        except ValueError as exc:
            raise MaintenanceError(
                f"document {document.document_id} has invalid object metadata"
            ) from exc
        if document.object_key != expected_key or document.byte_size <= 0:
            raise MaintenanceError(
                f"document {document.document_id} has invalid object metadata"
            )
        if retry:
            latest = document.job_statuses[0] if document.job_statuses else None
            if document.state != DocumentState.FAILED.value or (
                latest not in _RETRYABLE_JOB_STATUSES
            ):
                raise MaintenanceError(
                    f"document {document.document_id} is not retryable; expected "
                    "failed document with latest failed/interrupted job"
                )

    async def _verify_object(self, document: MaintenanceDocument) -> None:
        try:
            async with self._materializer.materialize(
                key=document.object_key,
                sha256=document.sha256,
                byte_size=document.byte_size,
            ):
                pass
        except (ObjectIntegrityError, ObjectStoreError) as exc:
            raise MaintenanceError(
                f"document {document.document_id} object validation failed: {exc}"
            ) from exc

    @staticmethod
    def _validate_inactive(document: MaintenanceDocument) -> None:
        active = sorted(_ACTIVE_JOB_STATUSES.intersection(document.job_statuses))
        if active:
            raise MaintenanceError(
                f"document {document.document_id} has an active job ({active[0]}); "
                "stop the API/worker and wait for or resolve it before maintenance"
            )

    @staticmethod
    def _require_created(document_id: uuid.UUID, outcome: str, *, retry: bool) -> None:
        if outcome == "created":
            return
        messages = {
            "not_found": f"document not found: {document_id}",
            "stale": f"document {document_id} changed during object validation",
            "active_job": (
                f"document {document_id} has an active job; stop the API/worker "
                "and wait for or resolve it before maintenance"
            ),
            "not_retryable": (
                f"document {document_id} is not retryable; expected failed "
                "document with latest failed/interrupted job"
            ),
        }
        if outcome not in messages:
            raise MaintenanceError(
                f"document {document_id} returned unknown maintenance outcome: "
                f"{outcome}"
            )
        if outcome == "not_retryable" and not retry:
            raise MaintenanceError(
                f"document {document_id} returned invalid rebuild outcome: {outcome}"
            )
        raise MaintenanceError(messages[outcome])
