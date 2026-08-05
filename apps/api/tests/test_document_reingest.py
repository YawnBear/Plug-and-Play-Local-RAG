import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routes.documents as document_routes
import app.routes.jobs as job_routes
from app.config import Settings
from app.routes.documents import router
from app.routes.jobs import router as jobs_router
from app.security.actor import ActorContext, ActorRole
from app.services.document_reingest import (
    DocumentNotRetryable,
    DocumentReingestNotFound,
    DocumentReingestService,
    ReingestPreparation,
    ReingestResult,
)
from app.services.object_lifecycle import ObjectIntegrityError
from app.services.object_storage import ObjectMetadata, ObjectStoreError


@pytest.fixture(autouse=True)
def _restore_document_route_authentication():
    original = document_routes.authenticated_request
    original_jobs = job_routes.authenticated_request
    yield
    document_routes.authenticated_request = original
    job_routes.authenticated_request = original_jobs


def _actor(*, user_id: int = 1) -> ActorContext:
    return ActorContext(
        user_id=uuid.UUID(int=user_id),
        role=ActorRole.MEMBER,
        authentication_version=1,
        authorization_version=1,
        session_id=uuid.UUID(int=user_id + 10),
    )


def _preparation(document_id: uuid.UUID | None = None) -> ReingestPreparation:
    checksum = "a" * 64
    return ReingestPreparation(
        document_id=document_id or uuid.uuid4(),
        sha256=checksum,
        byte_size=10,
        object_key=f"originals/aa/{checksum}.pdf",
        parser_version="stored-parser",
        chunking_version="stored-chunker",
        embedding_version="stored-embedding",
        previous_job_id=uuid.uuid4(),
        previous_job_status="failed",
        snapshot_token="b" * 64,
    )


class _RouteService:
    def __init__(self) -> None:
        self.preparation = _preparation()
        self.prepare_error: BaseException | None = None
        self.verify_error: BaseException | None = None
        self.commit_error: BaseException | None = None
        self.commit_calls = 0

    async def prepare(self, session: object, document_id: uuid.UUID) -> object:
        if self.prepare_error is not None:
            raise self.prepare_error
        self.preparation = _preparation(document_id)
        return self.preparation

    async def verify_original(self, preparation: object) -> None:
        if self.verify_error is not None:
            raise self.verify_error

    async def commit(
        self, session: object, preparation: ReingestPreparation, job_id: uuid.UUID
    ) -> ReingestResult:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error
        return ReingestResult(preparation.document_id, job_id)


def _route_client(
    service: _RouteService,
    *,
    actors: tuple[ActorContext, ...] = (_actor(),),
) -> TestClient:
    application = FastAPI()
    calls = 0

    @asynccontextmanager
    async def request_auth(request: object, *, mutation: bool = False):
        nonlocal calls
        actor = actors[min(calls, len(actors) - 1)]
        calls += 1
        yield SimpleNamespace(actor=actor, session=object())

    document_routes.authenticated_request = request_auth
    application.state.container = SimpleNamespace(document_reingest=service)
    application.include_router(router)
    return TestClient(application, raise_server_exceptions=False)


def test_reingest_route_returns_strict_queued_contract() -> None:
    service = _RouteService()
    document_id = uuid.uuid4()

    response = _route_client(service).post(
        f"/api/documents/{document_id}/reingest"
    )

    assert response.status_code == 202
    assert set(response.json()) == {"document_id", "job_id", "status"}
    assert response.json()["document_id"] == str(document_id)
    assert response.json()["status"] == "queued"
    assert service.commit_calls == 1


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (DocumentReingestNotFound(), 404, None),
        (DocumentNotRetryable(), 409, "document_not_retryable"),
        (ObjectIntegrityError("invalid"), 410, "document_original_invalid"),
        (
            ObjectStoreError("unavailable", code="endpoint_unreachable"),
            503,
            "object_storage_unavailable",
        ),
    ],
)
def test_reingest_route_maps_prepare_or_object_failures_without_commit(
    error: BaseException,
    status_code: int,
    code: str | None,
) -> None:
    service = _RouteService()
    if isinstance(error, (DocumentReingestNotFound, DocumentNotRetryable)):
        service.prepare_error = error
    else:
        service.verify_error = error

    response = _route_client(service).post(
        f"/api/documents/{uuid.uuid4()}/reingest"
    )

    assert response.status_code == status_code
    if code is not None:
        assert response.json()["detail"]["code"] == code
    assert service.commit_calls == 0


def test_reingest_route_rejects_actor_change_before_commit() -> None:
    service = _RouteService()

    response = _route_client(
        service,
        actors=(_actor(user_id=1), _actor(user_id=2)),
    ).post(f"/api/documents/{uuid.uuid4()}/reingest")

    assert response.status_code == 401
    assert service.commit_calls == 0


def test_job_route_delegates_member_manageability_to_database() -> None:
    job_id = uuid.uuid4()
    document_id = uuid.uuid4()

    class JobSession:
        async def execute(
            self, statement: object, parameters: dict[str, object]
        ) -> object:
            assert "v4_get_job" in str(statement)
            return SimpleNamespace(
                one_or_none=lambda: SimpleNamespace(
                    job_id=job_id,
                    document_id=document_id,
                    status="queued",
                    stage="uploaded",
                    completed_units=0,
                    total_units=None,
                    error=None,
                )
            )

    @asynccontextmanager
    async def request_auth(request: object, *, mutation: bool = False):
        yield SimpleNamespace(actor=_actor(), session=JobSession())

    job_routes.authenticated_request = request_auth
    application = FastAPI()
    application.include_router(jobs_router)

    response = TestClient(application).get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["document_id"] == str(document_id)


class _Result:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def mappings(self) -> "_Result":
        return self

    def one(self) -> dict[str, object]:
        return self._row


class _Session:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.sql: list[str] = []

    async def execute(
        self, statement: object, parameters: dict[str, object]
    ) -> _Result:
        self.sql.append(str(statement))
        return _Result(self.rows.pop(0))


class _Store:
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def head(self, key: str) -> ObjectMetadata:
        checksum = hashlib.sha256(self.content).hexdigest()
        return ObjectMetadata(
            key, len(self.content), checksum, len(self.content), None, None
        )

    async def download(self, key: str, destination: Path) -> int:
        destination.write_bytes(self.content)
        return len(self.content)


def _prepared_row(content: bytes) -> dict[str, object]:
    checksum = hashlib.sha256(content).hexdigest()
    return {
        "result_status": "prepared",
        "document_id": uuid.uuid4(),
        "sha256": checksum,
        "byte_size": len(content),
        "object_key": f"originals/{checksum[:2]}/{checksum}.pdf",
        "parser_version": "stored-parser",
        "chunking_version": "stored-chunker",
        "embedding_version": "stored-embedding",
        "previous_job_id": uuid.uuid4(),
        "previous_job_status": "failed",
        "snapshot_token": "b" * 64,
    }


def test_reingest_service_prepares_verifies_and_commits_stored_identity(
    tmp_path: Path,
) -> None:
    content = b"%PDF-reingest"
    prepared = _prepared_row(content)
    job_id = uuid.uuid4()
    session = _Session(
        [
            prepared,
            {
                "result_status": "created",
                "document_id": prepared["document_id"],
                "job_id": job_id,
            },
        ]
    )
    service = DocumentReingestService(
        Settings(data_root=tmp_path), _Store(content)
    )

    async def exercise() -> tuple[ReingestPreparation, ReingestResult]:
        preparation = await service.prepare(session, prepared["document_id"])
        await service.verify_original(preparation)
        return preparation, await service.commit(session, preparation, job_id)

    preparation, result = asyncio.run(exercise())

    assert preparation.parser_version == "stored-parser"
    assert preparation.chunking_version == "stored-chunker"
    assert preparation.embedding_version == "stored-embedding"
    assert result == ReingestResult(prepared["document_id"], job_id)
    assert "v4_prepare_document_reingest" in session.sql[0]
    assert "v4_commit_document_reingest" in session.sql[1]


@pytest.mark.parametrize(
    ("outcome", "exception"),
    [
        ("not_found", DocumentReingestNotFound),
        ("not_retryable", DocumentNotRetryable),
        ("active_job", DocumentNotRetryable),
    ],
)
def test_reingest_service_maps_prepare_outcomes(
    tmp_path: Path,
    outcome: str,
    exception: type[BaseException],
) -> None:
    session = _Session([{"result_status": outcome}])
    service = DocumentReingestService(Settings(data_root=tmp_path), _Store(b"x"))

    with pytest.raises(exception):
        asyncio.run(service.prepare(session, uuid.uuid4()))


@pytest.mark.parametrize("outcome", ["not_retryable", "active_job", "stale"])
def test_reingest_service_maps_commit_conflicts(
    tmp_path: Path,
    outcome: str,
) -> None:
    service = DocumentReingestService(Settings(data_root=tmp_path), _Store(b"x"))
    session = _Session([{"result_status": outcome}])

    with pytest.raises(DocumentNotRetryable):
        asyncio.run(service.commit(session, _preparation(), uuid.uuid4()))
