import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.db.models import Document, LibraryNode
from app.db.repositories import DocumentRepository
from app.main import create_app
from app.schemas.auth import AuthUser
from app.security.actor import ActorContext, ActorRole
from app.services.authentication import SessionView
from app.services.documents import (
    UploadPreflight,
    UploadResult,
    UploadTooLargeError,
    UploadValidationError,
)
from app.services.library import LibraryLocation


class _Session:
    def __init__(self, documents: "_Documents") -> None:
        self.documents = documents

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def begin(self) -> "_Session":
        return self

    async def execute(self, statement: object, parameters: object = None) -> object:
        sql = str(statement)
        self.documents.sql_calls.append(sql)
        if "v4_activate_actor" in sql:
            return _Result(
                SimpleNamespace(
                    user_id=uuid.UUID(int=1),
                    actor_role="admin",
                    authentication_version=1,
                    authorization_version=1,
                    session_id=uuid.UUID(int=2),
                )
            )
        if "v4_get_job" in sql:
            job_id = parameters["job_id"]
            row = (
                SimpleNamespace(
                    job_id=self.documents.job_id,
                    document_id=self.documents.document_id,
                    status="completed",
                    stage="ready",
                    completed_units=3,
                    total_units=3,
                    error=None,
                )
                if job_id == self.documents.job_id
                else None
            )
            return _Result(row)
        if "v4_document_team_recipients" in sql:
            return _Result(
                SimpleNamespace(
                    document_id=self.documents.document_id,
                    team_ids=[],
                )
            )
        location = {
            "id": uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"urn:local-rag:library-node:v1:{self.documents.document_id}",
            ),
            "node_id": uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"urn:local-rag:library-node:v1:{self.documents.document_id}",
            ),
            "parent_id": None,
            "kind": "file",
            "name": "report.pdf",
            "name_key": "report.pdf",
            "logical_path": "/report.pdf",
            "document_id": self.documents.document_id,
            "uploader_user_id": uuid.UUID(int=1),
        }
        return _Result(location)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def scalars(self, statement: object) -> list[LibraryNode]:
        return [
            LibraryNode(
                id=uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"urn:local-rag:library-node:v1:{self.documents.document_id}",
                ),
                parent_id=None,
                kind="file",
                name="report.pdf",
                name_key="report.pdf",
                document_id=self.documents.document_id,
                uploader_user_id=uuid.UUID(int=1),
            )
        ]


class _Result:
    def __init__(self, row: object) -> None:
        self.row = row

    def one(self) -> object:
        return self.row

    def one_or_none(self) -> object | None:
        return self.row

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[object]:
        return [self.row]

    def __iter__(self):
        return iter((self.row,))


class _Authentication:
    async def current(
        self, session_token: str | None, csrf_token: str | None = None
    ) -> SessionView | None:
        if session_token is None:
            return None
        user = AuthUser(
            id=uuid.UUID(int=1),
            username="admin",
            display_name="Admin",
            role="admin",
            status="active",
        )
        actor = ActorContext(uuid.UUID(int=1), ActorRole.ADMIN, 1, 1, uuid.UUID(int=2))
        return SessionView(user, actor, csrf_token or "csrf")


class _Documents:
    def __init__(self) -> None:
        self.upload_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.deleted = True
        self.document_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.sql_calls: list[str] = []

    async def stage(self, upload: object) -> object:
        if self.upload_error is not None:
            raise self.upload_error
        return object()

    async def preflight(
        self,
        actor: object,
        session: object,
        staged: object,
        folder_id: uuid.UUID | None,
        team_ids: tuple[uuid.UUID, ...],
    ) -> UploadPreflight:
        return UploadPreflight("upload_required", uuid.uuid4())

    async def put(self, staged: object) -> bool:
        return True

    async def commit(
        self,
        actor: object,
        session: object,
        staged: object,
        folder_id: uuid.UUID | None,
        reservation_id: uuid.UUID,
        team_ids: tuple[uuid.UUID, ...],
        **_kwargs: object,
    ) -> UploadResult:
        return UploadResult(
            self.document_id,
            self.job_id,
            "queued",
            None,
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"urn:local-rag:library-node:v1:{self.document_id}",
            ),
            folder_id,
            "report.pdf",
            "/report.pdf",
            False,
        )

    async def cleanup(self, staged: object) -> None:
        return None

    @staticmethod
    def duplicate_result(preflight: UploadPreflight) -> UploadResult:
        raise AssertionError("duplicate path was not expected")

    async def delete(
        self, actor: object, session: object, document_id: uuid.UUID
    ) -> bool:
        if self.delete_error is not None:
            raise self.delete_error
        return self.deleted


def _client(documents: _Documents) -> TestClient:
    database = SimpleNamespace(session_factory=lambda: _Session(documents))
    location = LibraryLocation(
        uuid.uuid4(),
        None,
        "report.pdf",
        "/report.pdf",
        uuid.UUID(int=1),
    )

    class Library:
        async def locations_for_documents(
            self, document_ids: list[uuid.UUID]
        ) -> dict[uuid.UUID, LibraryLocation]:
            return {document_id: location for document_id in document_ids}

    container = SimpleNamespace(
        database=database,
        documents=documents,
        library=Library(),
        authentication=_Authentication(),
    )
    client = TestClient(
        create_app(Settings(environment="test"), container),
        base_url="https://rag.home.arpa",
        headers={"Origin": "https://rag.home.arpa", "X-CSRF-Token": "csrf"},
    )
    client.cookies.set("rag_session", "opaque")
    client.cookies.set("csrf_token", "csrf")
    return client


def test_upload_route_returns_frozen_202_contract() -> None:
    documents = _Documents()
    response = _client(documents).post(
        "/api/documents",
        files={"file": ("report.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 202
    assert response.json() == {
        "document_id": str(documents.document_id),
        "job_id": str(documents.job_id),
        "status": "queued",
        "duplicate_of": None,
        "node_id": str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"urn:local-rag:library-node:v1:{documents.document_id}",
            )
        ),
        "parent_id": None,
        "display_name": "report.pdf",
        "logical_path": "/report.pdf",
        "location_reused": False,
    }


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (UploadTooLargeError("too large"), 413),
        (UploadValidationError("PDF only"), 415),
    ],
)
def test_upload_route_maps_validation_errors(
    error: Exception, status_code: int
) -> None:
    documents = _Documents()
    documents.upload_error = error
    response = _client(documents).post(
        "/api/documents",
        files={"file": ("report.pdf", b"content", "application/pdf")},
    )

    assert response.status_code == status_code


def test_document_and_job_read_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    documents = _Documents()
    now = datetime.now(UTC)
    document = Document(
        id=documents.document_id,
        sha256="a" * 64,
        original_filename="report.pdf",
        mime_type="application/pdf",
        byte_size=10,
        object_key=f"originals/aa/{'a' * 64}.pdf",
        state="ready",
        stage="ready",
        parser_version="v1",
        chunking_version="v1",
        embedding_version="v1",
        page_count=2,
        chunk_count=3,
        created_at=now,
        updated_at=now,
    )

    async def list_documents(
        repository: DocumentRepository,
        actor: object,
        *,
        page: int,
        limit: int,
    ) -> list[Document]:
        return [document]

    monkeypatch.setattr(DocumentRepository, "list", list_documents)
    client = _client(documents)

    document_response = client.get("/api/documents")
    job_response = client.get(f"/api/jobs/{documents.job_id}")
    missing_response = client.get(f"/api/jobs/{uuid.uuid4()}")

    assert document_response.status_code == 200
    assert document_response.json()[0]["filename"] == "report.pdf"
    assert job_response.status_code == 200
    assert job_response.json()["completed_units"] == 3
    assert missing_response.status_code == 404
    assert any("v4_get_job" in statement for statement in documents.sql_calls)
    assert not any(
        "FROM ingestion_jobs" in statement for statement in documents.sql_calls
    )


def test_delete_route_returns_204_or_404() -> None:
    documents = _Documents()
    client = _client(documents)

    assert client.delete(f"/api/documents/{uuid.uuid4()}").status_code == 204
    documents.deleted = False
    assert client.delete(f"/api/documents/{uuid.uuid4()}").status_code == 404


def test_delete_route_maps_database_capability_denial_to_403() -> None:
    class CapabilityDenied(Exception):
        sqlstate = "42501"

    documents = _Documents()
    documents.delete_error = DBAPIError(
        "SELECT v4_admin_delete_document(...)",
        {},
        CapabilityDenied("administrator capability required"),
        False,
    )

    response = _client(documents).delete(f"/api/documents/{uuid.uuid4()}")

    assert response.status_code == 403
    assert response.json() == {"detail": "capability denied"}
