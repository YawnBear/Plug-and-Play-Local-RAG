import asyncio
import uuid
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers, UploadFile

from app.config import Settings
from app.db.repositories import DocumentRepository
from app.security.actor import ActorContext, ActorRole
from app.services.documents import (
    DocumentService,
    DuplicateUploadRequiresAccess,
    UploadTooLargeError,
    UploadValidationError,
)
from app.services.object_storage import ObjectMetadata


def _actor(role: ActorRole = ActorRole.ADMIN) -> ActorContext:
    return ActorContext(uuid.uuid4(), role, 1, 1, uuid.uuid4())


def _upload(content: bytes, content_type: str = "application/pdf") -> UploadFile:
    return UploadFile(
        BytesIO(content),
        filename="report.pdf",
        headers=Headers({"content-type": content_type}),
    )


class _ObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put_if_absent(
        self,
        key: str,
        source: Path,
        *,
        sha256: str,
        byte_size: int,
        content_type: str,
    ) -> bool:
        if key in self.objects:
            return False
        content = source.read_bytes()
        assert len(content) == byte_size
        self.objects[key] = (content, sha256)
        return True

    async def head(self, key: str) -> ObjectMetadata:
        content, sha256 = self.objects[key]
        return ObjectMetadata(
            key, len(content), sha256, len(content), "application/pdf", None
        )


class _Result:
    def __init__(self, row: object) -> None:
        self._row = row

    def one(self) -> object:
        return self._row


class _Session:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(
        self, statement: object, parameters: dict[str, object] | None = None
    ) -> _Result:
        self.calls.append((str(statement), parameters or {}))
        return _Result(self.rows.pop(0))


def _version() -> SimpleNamespace:
    return SimpleNamespace(
        parser_version="pypdf+paddleocr-vl-v1.6-adaptive-v2",
        chunking_version="fragment-paragraph-sentence-v2",
        embedding_version="qwen3-embedding-0.6b-1024",
    )


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        data_root=tmp_path,
        ocr_python_executable=tmp_path / "unused.exe",
        object_storage_access_key_id="",
        object_storage_secret_access_key="",
        **overrides,
    )


def test_staged_upload_uses_reservation_then_controlled_commit(tmp_path: Path) -> None:
    service = DocumentService(_settings(tmp_path), _ObjectStore())
    actor = _actor()
    content = b"%PDF-1.4\ncontrolled bytes"
    staged = asyncio.run(service.stage(_upload(content)))
    reservation_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    session = _Session(
        [
            _version(),
            SimpleNamespace(
                result_status="upload_required",
                reservation_id=reservation_id,
                document_id=None,
                job_id=None,
                node_id=None,
                parent_id=None,
                display_name=None,
                logical_path=None,
                duplicate_of=None,
                location_reused=False,
            ),
            SimpleNamespace(
                result_status="created",
                document_id=staged.document_id,
                job_id=staged.job_id,
                node_id=staged.node_id,
                parent_id=parent_id,
                display_name=staged.display_name,
                logical_path=f"/{staged.display_name}",
                duplicate_of=None,
                location_reused=False,
            ),
        ]
    )

    preflight = asyncio.run(service.preflight(actor, session, staged, parent_id))
    assert preflight.upload_required
    assert preflight.reservation_id == reservation_id
    assert asyncio.run(service.put(staged)) is True
    result = asyncio.run(
        service.commit(
            actor,
            session,
            staged,
            parent_id,
            reservation_id,
            preflight=preflight,
        )
    )
    asyncio.run(service.cleanup(staged))

    assert result.status == "queued"
    assert result.document_id == staged.document_id
    assert "v10_effective_ingestion_version" in session.calls[0][0]
    assert "v4_admin_upload_preflight" in session.calls[1][0]
    assert "v4_admin_commit_upload" in session.calls[2][0]
    assert not staged.path.exists()


def test_duplicate_preflight_never_uploads_or_commits(tmp_path: Path) -> None:
    store = _ObjectStore()
    service = DocumentService(_settings(tmp_path), store)
    staged = asyncio.run(service.stage(_upload(b"%PDF-duplicate")))
    session = _Session(
        [
            _version(),
            SimpleNamespace(
                result_status="duplicate",
                reservation_id=None,
                document_id=staged.document_id,
                job_id=uuid.uuid4(),
                node_id=staged.node_id,
                parent_id=None,
                display_name=staged.display_name,
                logical_path=f"/{staged.display_name}",
                duplicate_of=staged.document_id,
                location_reused=True,
            ),
        ]
    )

    preflight = asyncio.run(service.preflight(_actor(), session, staged, None))
    result = service.duplicate_result(preflight)
    asyncio.run(service.cleanup(staged))

    assert result.status == "duplicate"
    assert result.location_reused is True
    assert store.objects == {}
    assert len(session.calls) == 2


def test_unauthorized_duplicate_returns_no_metadata(tmp_path: Path) -> None:
    service = DocumentService(_settings(tmp_path), _ObjectStore())
    staged = asyncio.run(service.stage(_upload(b"%PDF-hidden-duplicate")))
    session = _Session(
        [
            _version(),
            SimpleNamespace(
                result_status="duplicate_forbidden",
                reservation_id=None,
                document_id=None,
                job_id=None,
                node_id=None,
                parent_id=None,
                display_name=None,
                logical_path=None,
                duplicate_of=None,
                location_reused=False,
            ),
        ]
    )

    with pytest.raises(DuplicateUploadRequiresAccess):
        asyncio.run(service.preflight(_actor(ActorRole.MEMBER), session, staged, None))
    asyncio.run(service.cleanup(staged))

    assert not staged.path.exists()
    assert session.calls[1][1]["team_ids"] == []


def test_stage_rejects_mime_signature_and_size(tmp_path: Path) -> None:
    service = DocumentService(
        _settings(tmp_path, maximum_upload_bytes=8), _ObjectStore()
    )

    with pytest.raises(UploadValidationError, match="application/pdf"):
        asyncio.run(service.stage(_upload(b"text", "text/plain")))
    with pytest.raises(UploadValidationError, match="not a PDF"):
        asyncio.run(service.stage(_upload(b"hello")))
    with pytest.raises(UploadTooLargeError, match="exceeds"):
        asyncio.run(service.stage(_upload(b"%PDF-12345")))
    assert not list(service._settings.upload_work_path.glob("upload-*.tmp"))


def test_delete_queues_controlled_outbox_without_object_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checksum = "a" * 64
    document_id = uuid.uuid4()

    async def get(
        _repository: DocumentRepository,
        _actor: ActorContext,
        requested_id: uuid.UUID,
    ) -> object:
        assert requested_id == document_id
        return SimpleNamespace(
            sha256=checksum,
            object_key=f"originals/aa/{checksum}.pdf",
            byte_size=11,
        )

    monkeypatch.setattr(DocumentRepository, "get", get)
    session = _Session([SimpleNamespace()])
    service = DocumentService(_settings(tmp_path), _ObjectStore())

    assert asyncio.run(service.delete(_actor(), session, document_id)) is True
    assert "v4_admin_delete_document" in session.calls[0][0]
