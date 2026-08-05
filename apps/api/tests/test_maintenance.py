import asyncio
import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.maintenance_cli import _run, main
from app.services.maintenance import (
    REBUILD_ALL_CONFIRMATION,
    MaintenanceDocument,
    MaintenanceError,
    MaintenanceRepository,
    MaintenanceService,
)
from app.services.object_lifecycle import canonical_object_key
from app.services.object_storage import ObjectMetadata, ObjectStoreError


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class _Session:
    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> _Transaction:
        return _Transaction()


class _SessionFactory:
    def __call__(self) -> _Session:
        return _Session()


class _Repository:
    def __init__(self, documents: list[MaintenanceDocument]) -> None:
        self.documents = {document.document_id: document for document in documents}
        self.outcomes: dict[uuid.UUID, str] = {}
        self.requeue_calls: list[tuple[uuid.UUID, uuid.UUID, bool]] = []

    async def get_document(self, document_id: uuid.UUID) -> MaintenanceDocument | None:
        return self.documents.get(document_id)

    async def list_documents(self) -> list[MaintenanceDocument]:
        return list(self.documents.values())

    async def requeue(
        self,
        document: MaintenanceDocument,
        job_id: uuid.UUID,
        *,
        retry_only: bool,
    ) -> str:
        self.requeue_calls.append((document.document_id, job_id, retry_only))
        return self.outcomes.get(document.document_id, "created")


class _ObjectStore:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    async def head(self, key: str) -> ObjectMetadata:
        content = self.objects.get(key)
        if content is None:
            raise ObjectStoreError("missing", code="not_found", not_found=True)
        checksum = hashlib.sha256(content).hexdigest()
        return ObjectMetadata(
            key, len(content), checksum, len(content), "application/pdf", None
        )

    async def download(self, key: str, destination: Path) -> int:
        content = self.objects[key]
        destination.write_bytes(content)
        return len(content)


def _document(
    *,
    content: bytes = b"%PDF-fixture",
    state: str = "ready",
    statuses: tuple[str, ...] = ("completed",),
) -> tuple[MaintenanceDocument, dict[str, bytes]]:
    checksum = hashlib.sha256(content).hexdigest()
    key = canonical_object_key(checksum)
    return (
        MaintenanceDocument(
            uuid.uuid4(),
            state,
            checksum,
            len(content),
            key,
            statuses,
            hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        ),
        {key: content},
    )


def _service(
    tmp_path: Path,
    documents: list[MaintenanceDocument],
    objects: dict[str, bytes],
) -> tuple[MaintenanceService, _Repository]:
    repository = _Repository(documents)
    service = MaintenanceService(
        _SessionFactory(),
        Settings(data_root=tmp_path),
        _ObjectStore(objects),
        repository_factory=lambda _session: repository,
    )
    return service, repository


def test_retry_requeues_failed_document_through_controlled_snapshot(
    tmp_path: Path,
) -> None:
    document, objects = _document(state="failed", statuses=("failed", "completed"))
    service, repository = _service(tmp_path, [document], objects)

    result = asyncio.run(service.retry(document.document_id))

    assert result.document_id == document.document_id
    assert repository.requeue_calls == [(document.document_id, result.job_id, True)]


@pytest.mark.parametrize(
    ("state", "statuses"),
    [
        ("ready", ("completed",)),
        ("failed", ("completed",)),
        ("failed", ()),
    ],
)
def test_retry_refuses_nonretryable_document(
    tmp_path: Path, state: str, statuses: tuple[str, ...]
) -> None:
    document, objects = _document(state=state, statuses=statuses)
    service, repository = _service(tmp_path, [document], objects)

    with pytest.raises(MaintenanceError, match="not retryable"):
        asyncio.run(service.retry(document.document_id))
    assert repository.requeue_calls == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_rebuild_refuses_active_jobs(tmp_path: Path, status: str) -> None:
    document, objects = _document(statuses=(status,))
    service, repository = _service(tmp_path, [document], objects)

    with pytest.raises(MaintenanceError, match="active job"):
        asyncio.run(service.rebuild(document.document_id))
    assert repository.requeue_calls == []


def test_rebuild_refuses_missing_or_tampered_object(tmp_path: Path) -> None:
    missing, _ = _document()
    missing_service, missing_repository = _service(tmp_path, [missing], {})

    with pytest.raises(MaintenanceError, match="object validation failed"):
        asyncio.run(missing_service.rebuild(missing.document_id))
    assert missing_repository.requeue_calls == []

    tampered, objects = _document()
    objects[tampered.object_key] = b"same-size-bad"[: tampered.byte_size]
    tampered_service, tampered_repository = _service(tmp_path, [tampered], objects)
    with pytest.raises(MaintenanceError, match="object validation failed"):
        asyncio.run(tampered_service.rebuild(tampered.document_id))
    assert tampered_repository.requeue_calls == []


def test_rebuild_all_is_atomic_when_snapshot_becomes_stale(tmp_path: Path) -> None:
    first, first_objects = _document(content=b"%PDF-first")
    second, second_objects = _document(content=b"%PDF-second")
    service, repository = _service(
        tmp_path, [first, second], first_objects | second_objects
    )
    repository.outcomes[second.document_id] = "stale"

    with pytest.raises(MaintenanceError, match="changed during object validation"):
        asyncio.run(service.rebuild_all(REBUILD_ALL_CONFIRMATION))
    assert [call[0] for call in repository.requeue_calls] == [
        first.document_id,
        second.document_id,
    ]


def test_rebuild_all_requires_exact_confirmation(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, [], {})
    with pytest.raises(MaintenanceError, match="REBUILD-ALL"):
        asyncio.run(service.rebuild_all("wrong"))


def test_repository_uses_only_controlled_maintenance_functions() -> None:
    source = Path(MaintenanceRepository.__module__.replace(".", "/"))
    source = Path(__file__).parents[1] / f"{source}.py"
    text = source.read_text(encoding="utf-8")
    assert "v4_maintenance_get_document" in text
    assert "v4_maintenance_list_documents" in text
    assert "v4_maintenance_requeue_document" in text
    assert "select(Document)" not in text
    assert "delete(Chunk)" not in text
    assert "update(Document)" not in text


def test_cli_requires_stopped_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    result = main(["retry", str(uuid.uuid4())])
    assert result == 2
    assert "--confirm-stopped" in capsys.readouterr().err


def test_cli_uses_selector_loop_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = SimpleNamespace(loop_factory=None)

    async def operation() -> int:
        return 0

    def fake_run(coroutine: object, *, loop_factory: object) -> int:
        captured.loop_factory = loop_factory
        coroutine.close()
        return 0

    monkeypatch.setattr("app.maintenance_cli.sys.platform", "win32")
    monkeypatch.setattr("app.maintenance_cli.asyncio.run", fake_run)

    assert _run(operation()) == 0
    assert captured.loop_factory is asyncio.SelectorEventLoop
