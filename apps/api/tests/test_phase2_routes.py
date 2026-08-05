import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routes.documents as document_routes
from app.routes.documents import router
from app.security.actor import ActorContext, ActorRole
from app.services.document_content import (
    ContentDescriptor,
    parse_single_range,
)
from app.services.object_storage import ObjectStoreError


@pytest.fixture(autouse=True)
def _restore_document_route_authentication():
    original = document_routes.authenticated_request
    yield
    document_routes.authenticated_request = original


class _UploadDocuments:
    def __init__(
        self,
        *,
        created: bool,
        commit_result: object = None,
        commit_error: BaseException | None = None,
    ) -> None:
        self.created = created
        self.commit_result = commit_result
        self.commit_error = commit_error
        self.discarded = 0
        self.staged = SimpleNamespace()

    async def stage(self, upload: object) -> object:
        return self.staged

    async def preflight(
        self,
        actor: object,
        session: object,
        staged: object,
        folder_id: object,
        team_ids: tuple[uuid.UUID, ...],
    ) -> object:
        return SimpleNamespace(upload_required=True, reservation_id=uuid.uuid4())

    async def put(self, staged: object) -> bool:
        return self.created

    async def commit(
        self,
        actor: object,
        session: object,
        staged: object,
        folder_id: object,
        reservation_id: object,
        team_ids: tuple[uuid.UUID, ...],
        **_kwargs: object,
    ) -> object:
        if self.commit_error is not None:
            raise self.commit_error
        return self.commit_result

    async def discard_object(self, staged: object) -> None:
        self.discarded += 1

    async def cleanup(self, staged: object) -> None:
        return None


def _app(*, documents: object = None, content: object = None) -> FastAPI:
    app = FastAPI()
    actor = ActorContext(
        user_id=uuid.UUID(int=1),
        role=ActorRole.ADMIN,
        authentication_version=1,
        authorization_version=1,
        session_id=uuid.UUID(int=2),
    )

    @asynccontextmanager
    async def request_auth(request: object, *, mutation: bool = False):
        yield SimpleNamespace(actor=actor, session=object())

    document_routes.authenticated_request = request_auth
    app.state.container = SimpleNamespace(
        documents=documents,
        document_content=content,
    )
    app.include_router(router)
    return app


def test_upload_storage_failure_is_actionable_503() -> None:
    class Documents:
        async def stage(self, upload: object) -> None:
            raise ObjectStoreError(
                "endpoint is unreachable",
                code="endpoint_unreachable",
                retryable=True,
            )

    response = TestClient(_app(documents=Documents())).post(
        "/api/documents",
        files={"file": ("report.pdf", b"%PDF-fixture", "application/pdf")},
    )

    assert response.status_code == 503
    assert "object storage unavailable" in response.json()["detail"]
    assert "endpoint is unreachable" in response.json()["detail"]


def test_upload_does_not_delete_canonical_object_after_ambiguous_commit() -> None:
    documents = _UploadDocuments(
        created=True, commit_error=RuntimeError("database connection lost")
    )

    response = TestClient(
        _app(documents=documents), raise_server_exceptions=False
    ).post(
        "/api/documents",
        files={"file": ("report.pdf", b"%PDF-fixture", "application/pdf")},
    )

    assert response.status_code == 500
    assert documents.discarded == 0


def test_same_checksum_uploads_never_request_delete_shared_canonical_object() -> None:
    result = SimpleNamespace(
        document_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        status="duplicate",
        duplicate_of=uuid.uuid4(),
        node_id=uuid.uuid4(),
        parent_id=None,
        display_name="report.pdf",
        logical_path="/report.pdf",
        location_reused=True,
        retain_uploaded_object=False,
    )
    created = _UploadDocuments(created=True, commit_result=result)
    preexisting = _UploadDocuments(created=False, commit_result=result)

    for documents in (created, preexisting):
        response = TestClient(_app(documents=documents)).post(
            "/api/documents",
            files={"file": ("report.pdf", b"%PDF-fixture", "application/pdf")},
        )
        assert response.status_code == 200

    assert created.discarded == 0
    assert preexisting.discarded == 0


def test_huge_range_is_416_with_required_headers() -> None:
    class Content:
        async def authorize(
            self, actor: object, session: object, document_id: object
        ) -> object:
            return document_id

        async def resolve(self, authorized: object, **kwargs: object) -> None:
            parse_single_range(str(kwargs["range_header"]), 10)

    response = TestClient(_app(content=Content())).get(
        f"/api/documents/{uuid.uuid4()}/content",
        headers={"Range": f"bytes={'9' * 5000}-"},
    )

    assert response.status_code == 416
    assert response.headers["Content-Range"] == "bytes */10"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["Cache-Control"] == "private, no-store"


def test_streaming_response_closes_body_after_consumption() -> None:
    class Body:
        def __init__(self) -> None:
            self.remaining = [b"%PDF-stream", b""]
            self.closed = False

        async def read(self, amount: int = -1) -> bytes:
            return self.remaining.pop(0)

        async def close(self) -> None:
            self.closed = True

    body = Body()

    class Content:
        async def authorize(
            self, actor: object, session: object, document_id: object
        ) -> object:
            return document_id

        async def remains_authorized(
            self, actor: object, session: object, authorized: object
        ) -> bool:
            return True

        async def resolve(
            self, authorized: object, **kwargs: object
        ) -> ContentDescriptor:
            return ContentDescriptor(
                200,
                {
                    "Content-Type": "application/pdf",
                    "Content-Length": "11",
                    "Accept-Ranges": "bytes",
                },
                body,
            )

    response = TestClient(_app(content=Content())).get(
        f"/api/documents/{uuid.uuid4()}/content"
    )

    assert response.status_code == 200
    assert response.content == b"%PDF-stream"
    assert body.closed is True
