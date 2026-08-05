import asyncio
import hashlib
import json
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.config import Settings
from app.db.models import Document
from app.services.document_content import (
    AuthorizedDocumentContent,
    DocumentContentService,
    InvalidDocumentRange,
    parse_single_range,
)
from app.services.object_deletions import (
    DeletionLease,
    ObjectDeletionService,
    bounded_retry_delay,
)
from app.services.object_lifecycle import (
    ObjectIntegrityError,
    ObjectMaterializer,
    canonical_object_key,
    checksum_lock_id,
)
from app.services.object_storage import (
    ObjectListItem,
    ObjectMetadata,
    ObjectRead,
    ObjectStoreError,
)
from app.services.storage_transfer import StorageTransferError, StorageTransferService


class _MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []
        self.get_calls: list[tuple[str, str | None]] = []
        self.transaction_probe = lambda: False

    async def put(
        self,
        key: str,
        source: Path,
        *,
        sha256: str,
        byte_size: int,
        content_type: str = "application/pdf",
    ) -> None:
        assert not self.transaction_probe()
        content = source.read_bytes()
        assert len(content) == byte_size
        self.objects[key] = (content, sha256)

    async def put_if_absent(
        self,
        key: str,
        source: Path,
        *,
        sha256: str,
        byte_size: int,
        content_type: str = "application/pdf",
    ) -> bool:
        if key in self.objects:
            return False
        await self.put(
            key,
            source,
            sha256=sha256,
            byte_size=byte_size,
            content_type=content_type,
        )
        return True

    async def head(self, key: str) -> ObjectMetadata:
        assert not self.transaction_probe()
        try:
            content, checksum = self.objects[key]
        except KeyError as exc:
            raise ObjectStoreError("missing", code="NoSuchKey", not_found=True) from exc
        return ObjectMetadata(
            key=key,
            size=len(content),
            sha256=checksum,
            declared_size=len(content),
            content_type="application/pdf",
            etag="not-a-checksum",
        )

    async def download(self, key: str, destination: Path) -> int:
        assert not self.transaction_probe()
        content = self.objects[key][0]
        destination.write_bytes(content)
        return len(content)

    async def delete(self, key: str) -> None:
        assert not self.transaction_probe()
        self.objects.pop(key, None)
        self.deleted.append(key)

    async def list_prefix(self, prefix: str) -> AsyncIterator[ObjectListItem]:
        for key, (content, _checksum) in sorted(self.objects.items()):
            if key.startswith(prefix):
                yield ObjectListItem(key, len(content), None)


class _Body:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.offset = 0
        self.closed = False

    async def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self.content)
        result = self.content[self.offset : self.offset + amount]
        self.offset += len(result)
        return result

    async def close(self) -> None:
        self.closed = True


class _ContentStore(_MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.last_body: _Body | None = None

    async def get(self, key: str, *, byte_range: str | None = None) -> ObjectRead:
        self.get_calls.append((key, byte_range))
        content = self.objects[key][0]
        if byte_range is None:
            selected = content
            content_range = None
        else:
            start, end = (int(value) for value in byte_range[6:].split("-"))
            selected = content[start : end + 1]
            content_range = f"bytes {start}-{end}/{len(content)}"
        self.last_body = _Body(selected)
        return ObjectRead(
            key,
            len(selected),
            content_range,
            "application/pdf",
            {},
            self.last_body,
        )


class _IdentitySession:
    async def __aenter__(self) -> "_IdentitySession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, statement: object) -> object:
        current = getattr(self, "current", None)
        documents = (
            [
                {
                    "document_id": str(current.id),
                    "object_key": current.object_key,
                    "sha256": current.sha256,
                    "byte_size": current.byte_size,
                }
            ]
            if current is not None
            else []
        )
        inventory = {"documents": documents, "object_deletions": []}
        return _SnapshotResult(
            {
                "database_identity": "rag-test",
                "schema_revision": "0003_v6_ingestion_version_guard",
                "database_inventory": inventory,
                "database_fingerprint": hashlib.sha256(
                    json.dumps(
                        inventory, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
            }
        )


class _SnapshotResult:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    def mappings(self) -> "_SnapshotResult":
        return self

    def one(self) -> dict[str, object]:
        return self.row


def _session_factory() -> _IdentitySession:
    return _IdentitySession()


class _DocumentSession:
    def __init__(self, document: Document) -> None:
        self.document = document

    async def __aenter__(self) -> "_DocumentSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, model: object, document_id: object) -> Document | None:
        return self.document if document_id == self.document.id else None


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path / "data",
        ocr_python_executable=tmp_path / "python.exe",
    )


def test_canonical_key_and_stable_advisory_lock_vectors() -> None:
    assert canonical_object_key("a" * 64) == f"originals/aa/{'a' * 64}.pdf"
    assert checksum_lock_id("00" * 32) == 4787024118669066380
    assert checksum_lock_id("ff" * 32) == -4042390480985954959
    assert checksum_lock_id("0123456789abcdef" * 4) == -5996651347091924702
    with pytest.raises(ValueError, match="64 hexadecimal"):
        canonical_object_key("not-a-checksum")


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        ("bytes=0-0", 10, (0, 0)),
        ("bytes=2-", 10, (2, 9)),
        ("bytes=-3", 10, (7, 9)),
        ("bytes=2-99", 10, (2, 9)),
        ("bytes=-99", 10, (0, 9)),
    ],
)
def test_strict_single_range_forms(
    header: str, size: int, expected: tuple[int, int]
) -> None:
    result = parse_single_range(header, size)
    assert (result.start, result.end) == expected


@pytest.mark.parametrize(
    "header",
    ["items=0-1", "bytes=", "bytes=0-1,3-4", "bytes=-0", "bytes=8-2", "bytes=10-"],
)
def test_invalid_or_unsatisfiable_ranges(header: str) -> None:
    with pytest.raises(InvalidDocumentRange) as caught:
        parse_single_range(header, 10)
    assert caught.value.size == 10


def test_huge_numeric_range_is_416_not_an_unhandled_value_error() -> None:
    with pytest.raises(InvalidDocumentRange):
        parse_single_range(f"bytes={'9' * 5000}-", 10)


def test_materializer_verifies_bytes_and_always_cleans(tmp_path: Path) -> None:
    content = b"%PDF-verified"
    checksum = hashlib.sha256(content).hexdigest()
    key = canonical_object_key(checksum)
    store = _MemoryStore()
    store.objects[key] = (content, checksum)
    materializer = ObjectMaterializer(store, tmp_path / "work")

    async def exercise() -> None:
        async with materializer.materialize(
            key=key, sha256=checksum, byte_size=len(content)
        ) as result:
            assert result.path.read_bytes() == content
            assert result.path.exists()
        assert list((tmp_path / "work").iterdir()) == []

    asyncio.run(exercise())

    store.objects[key] = (b"%PDF-tampered", checksum)
    with pytest.raises(ObjectIntegrityError, match="bytes"):
        asyncio.run(_materialize_once(materializer, key, checksum, len(content)))
    assert list((tmp_path / "work").iterdir()) == []


def test_materializer_waits_for_hash_thread_before_cancellation_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.object_lifecycle as lifecycle

    content = b"%PDF-cancellation"
    checksum = hashlib.sha256(content).hexdigest()
    key = canonical_object_key(checksum)
    store = _MemoryStore()
    store.objects[key] = (content, checksum)
    materializer = ObjectMaterializer(store, tmp_path / "work")
    started = threading.Event()
    release = threading.Event()
    original_hash = lifecycle._hash_file

    def paused_hash(path: Path) -> tuple[str, int]:
        started.set()
        release.wait(2)
        return original_hash(path)

    monkeypatch.setattr(lifecycle, "_hash_file", paused_hash)

    async def exercise() -> None:
        task = asyncio.create_task(
            _materialize_once(materializer, key, checksum, len(content))
        )
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert list((tmp_path / "work").iterdir()) == []

    asyncio.run(exercise())


def test_materializer_cleanup_failure_is_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.object_lifecycle as lifecycle

    directory = tmp_path / "object-work"
    directory.mkdir()
    (directory / "open.pdf").write_bytes(b"fixture")
    calls = 0

    def always_locked(path: Path) -> None:
        nonlocal calls
        calls += 1
        raise PermissionError("simulated Windows sharing violation")

    monkeypatch.setattr(lifecycle.shutil, "rmtree", always_locked)

    with pytest.raises(PermissionError, match="sharing violation"):
        asyncio.run(lifecycle._cleanup_directory(directory))
    assert calls == 3
    assert directory.exists()


def test_content_head_avoids_get_and_range_body_closes(tmp_path: Path) -> None:
    content = b"%PDF-0123456789"
    checksum = hashlib.sha256(content).hexdigest()
    key = canonical_object_key(checksum)
    document = Document(
        id=__import__("uuid").uuid4(),
        sha256=checksum,
        original_filename="report name.pdf",
        mime_type="application/pdf",
        byte_size=len(content),
        object_key=key,
        state="ready",
        stage="ready",
        parser_version="v1",
        chunking_version="v1",
        embedding_version="v1",
    )
    store = _ContentStore()
    store.objects[key] = (content, checksum)
    service = DocumentContentService(store)
    authorized = AuthorizedDocumentContent(
        document.id,
        document.sha256,
        document.object_key,
        document.byte_size,
        document.original_filename,
    )

    head = asyncio.run(
        service.resolve(authorized, range_header=None, include_body=False)
    )
    assert head.status_code == 200
    assert head.body is None
    assert head.headers["Content-Length"] == str(len(content))
    assert head.headers["Accept-Ranges"] == "bytes"
    assert head.headers["X-Content-Type-Options"] == "nosniff"
    assert head.headers["Cache-Control"] == "private, no-store"
    assert store.get_calls == []

    ranged = asyncio.run(
        service.resolve(authorized, range_header="bytes=5-8", include_body=True)
    )
    assert ranged.status_code == 206
    assert ranged.headers["Content-Range"] == f"bytes 5-8/{len(content)}"
    assert store.get_calls == [(key, "bytes=5-8")]
    assert ranged.body is not None
    asyncio.run(ranged.body.close())
    assert store.last_body is not None and store.last_body.closed


class _DeletionHarness(ObjectDeletionService):
    def __init__(self, store: object, lease: DeletionLease) -> None:
        super().__init__(None, store, lease_seconds=60, retry_base_seconds=1)
        self.lease = lease
        self.completed: list[DeletionLease] = []
        self.retried: list[tuple[DeletionLease, str]] = []

    async def _claim(self) -> DeletionLease | None:
        lease, self.lease = self.lease, None
        return lease

    async def _queue_expired_upload_orphans(self) -> int:
        return 0

    async def _complete(self, lease: DeletionLease) -> None:
        self.completed.append(lease)

    async def _retry(self, lease: DeletionLease, error: str) -> None:
        self.retried.append((lease, error))


def test_deletion_worker_treats_missing_as_success_and_retries_outages() -> None:
    import uuid

    lease = DeletionLease(
        uuid.uuid4(), f"originals/aa/{'a' * 64}.pdf", uuid.uuid4(), 1, 1
    )

    class MissingStore:
        async def delete(self, key: str) -> None:
            raise ObjectStoreError("missing", code="NoSuchKey", not_found=True)

    missing = _DeletionHarness(MissingStore(), lease)
    assert asyncio.run(missing.run_once())
    assert missing.completed == [lease]
    assert missing.retried == []

    class UnavailableStore:
        async def delete(self, key: str) -> None:
            raise ObjectStoreError("unavailable", code="SlowDown", retryable=True)

    unavailable = _DeletionHarness(UnavailableStore(), lease)
    assert asyncio.run(unavailable.run_once())
    assert unavailable.completed == []
    assert unavailable.retried == [(lease, "unavailable")]


def test_deletion_retry_delay_handles_untrusted_huge_attempt() -> None:
    assert bounded_retry_delay(1.0, 10**1000) == 300.0


class _BoundaryTransaction:
    def __init__(self, owner: "_BoundaryFactory") -> None:
        self.owner = owner

    async def __aenter__(self) -> None:
        self.owner.active = True

    async def __aexit__(self, *args: object) -> None:
        self.owner.active = False


class _BoundarySession:
    def __init__(self, owner: "_BoundaryFactory") -> None:
        self.owner = owner

    async def __aenter__(self) -> "_BoundarySession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> _BoundaryTransaction:
        return _BoundaryTransaction(self.owner)

    async def execute(self, statement: object, parameters: object = None) -> None:
        return None

    async def scalar(self, statement: object) -> Document:
        return self.owner.document


class _BoundaryFactory:
    def __init__(self, document: Document) -> None:
        self.document = document
        self.active = False

    def __call__(self) -> _BoundarySession:
        return _BoundarySession(self)


async def _materialize_once(
    materializer: ObjectMaterializer, key: str, checksum: str, size: int
) -> None:
    async with materializer.materialize(key=key, sha256=checksum, byte_size=size):
        pass


def test_export_import_paginates_over_1000_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    source = _MemoryStore()
    for index in range(1001):
        content = f"%PDF-{index}".encode()
        checksum = hashlib.sha256(content).hexdigest()
        source.objects[canonical_object_key(checksum)] = (content, checksum)
    settings = _settings(tmp_path)
    exporter = StorageTransferService(_session_factory, source, settings)
    destination = tmp_path / "archive"
    database_dump = tmp_path / "database.dump"
    database_dump.write_bytes(b"PostgreSQL custom dump fixture")

    manifest_path = asyncio.run(exporter.export(destination, database_dump))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["objects"]) == 1001
    assert manifest["database_dump"]["filename"] == "database.dump"
    target = _MemoryStore()
    importer = StorageTransferService(_session_factory, target, settings)
    assert asyncio.run(importer.import_archive(destination)) == 1001
    assert target.objects == source.objects

    invalid_checksum_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    invalid_checksum_manifest["objects"][0]["sha256"] = "z" * 64
    manifest_path.write_text(json.dumps(invalid_checksum_manifest), encoding="utf-8")
    with pytest.raises(StorageTransferError, match="checksum"):
        asyncio.run(
            StorageTransferService(
                _session_factory, _MemoryStore(), settings
            ).import_archive(destination)
        )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    first = manifest["objects"][0]
    object_path = destination / "objects" / Path(*first["key"].split("/"))
    object_path.write_bytes(b"tampered")
    clean_target = _MemoryStore()
    with pytest.raises(StorageTransferError, match="tampered"):
        asyncio.run(
            StorageTransferService(
                _session_factory, clean_target, settings
            ).import_archive(destination)
        )
    assert clean_target.objects == {}


def test_import_refuses_restored_database_inventory_before_object_put(
    tmp_path: Path,
) -> None:
    import uuid

    content = b"%PDF-authoritative-inventory"
    checksum = hashlib.sha256(content).hexdigest()
    key = canonical_object_key(checksum)

    def document(document_id: uuid.UUID) -> Document:
        return Document(
            id=document_id,
            sha256=checksum,
            original_filename="inventory.pdf",
            mime_type="application/pdf",
            byte_size=len(content),
            object_key=key,
            state="ready",
            stage="ready",
            parser_version="v1",
            chunking_version="v1",
            embedding_version="v1",
        )

    class InventorySession(_IdentitySession):
        def __init__(self, current: Document) -> None:
            self.current = current

        async def scalars(self, statement: object) -> list[object]:
            return [self.current] if "FROM documents" in str(statement) else []

    def factory(current: Document):
        return lambda: InventorySession(current)

    source_document = document(uuid.uuid4())
    source = _MemoryStore()
    source.objects[key] = (content, checksum)
    settings = _settings(tmp_path)
    archive = tmp_path / "inventory-archive"
    dump = tmp_path / "inventory.dump"
    dump.write_bytes(b"paired PostgreSQL dump")
    missing_archive = tmp_path / "missing-object-archive"
    with pytest.raises(
        StorageTransferError, match="database-referenced object is missing"
    ):
        asyncio.run(
            StorageTransferService(
                factory(source_document), _MemoryStore(), settings
            ).export(missing_archive, dump)
        )
    assert not missing_archive.exists()

    asyncio.run(
        StorageTransferService(factory(source_document), source, settings).export(
            archive, dump
        )
    )

    restored_document = document(uuid.uuid4())
    target = _MemoryStore()
    with pytest.raises(StorageTransferError, match="inventory"):
        asyncio.run(
            StorageTransferService(
                factory(restored_document), target, settings
            ).import_archive(archive)
        )
    assert target.objects == {}
