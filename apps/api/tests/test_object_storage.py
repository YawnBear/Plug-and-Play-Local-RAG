import asyncio
import io
import threading
import time
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from app.config import Settings
from app.services.object_storage import (
    AsyncObjectBody,
    ObjectStoreError,
    S3ObjectStore,
    _BoundedThreadBridge,
)
from app.services.storage_maintenance import StorageMaintenanceService


class _Body(io.BytesIO):
    was_closed = False

    def close(self) -> None:
        self.was_closed = True
        super().close()


class _ReadTimeoutBody(_Body):
    def read(self, amount: int = -1) -> bytes:
        raise ReadTimeoutError(endpoint_url="http://127.0.0.1:9000")


class _S3Client:
    def __init__(self, *, bucket_exists: bool = True) -> None:
        self.bucket_exists = bucket_exists
        self.objects: dict[str, tuple[bytes, dict[str, str], str]] = {}
        self.list_tokens: list[str | None] = []
        self.last_body: _Body | None = None
        self.head_bucket_error: tuple[str, int] | None = None
        self.create_bucket_error: tuple[str, int] | None = None
        self.create_bucket_calls = 0
        self.body_factory = _Body

    def head_bucket(self, *, Bucket: str) -> dict[str, object]:
        if self.head_bucket_error is not None:
            code, status = self.head_bucket_error
            raise _client_error(code, status, "HeadBucket")
        if not self.bucket_exists:
            raise _client_error("NoSuchBucket", 404, "HeadBucket")
        return {}

    def create_bucket(self, **kwargs: object) -> dict[str, object]:
        self.create_bucket_calls += 1
        if self.create_bucket_error is not None:
            code, status = self.create_bucket_error
            if code == "BucketAlreadyOwnedByYou":
                self.bucket_exists = True
            raise _client_error(code, status, "CreateBucket")
        self.bucket_exists = True
        return {}

    def put_object(self, **kwargs: object) -> dict[str, object]:
        body = kwargs["Body"]
        data = body.read()
        key = str(kwargs["Key"])
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise _client_error("PreconditionFailed", 412, "PutObject")
        metadata = dict(kwargs["Metadata"])
        self.objects[key] = (data, metadata, str(kwargs["ContentType"]))
        return {"ETag": '"etag-is-not-a-checksum"'}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        try:
            data, metadata, content_type = self.objects[Key]
        except KeyError as exc:
            raise _client_error("NoSuchKey", 404, "HeadObject") from exc
        return {
            "ContentLength": len(data),
            "Metadata": metadata,
            "ContentType": content_type,
            "ETag": '"etag-is-not-a-checksum"',
        }

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        try:
            data, metadata, content_type = self.objects[key]
        except KeyError as exc:
            raise _client_error("NoSuchKey", 404, "GetObject") from exc
        content_range = None
        if byte_range := kwargs.get("Range"):
            start_text, end_text = str(byte_range).removeprefix("bytes=").split("-")
            start = int(start_text)
            end = int(end_text)
            data = data[start : end + 1]
            content_range = f"bytes {start}-{end}/{len(self.objects[key][0])}"
        self.last_body = self.body_factory(data)
        return {
            "ContentLength": len(data),
            "ContentRange": content_range,
            "ContentType": content_type,
            "Metadata": metadata,
            "Body": self.last_body,
        }

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        self.objects.pop(Key, None)
        return {}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        token = kwargs.get("ContinuationToken")
        self.list_tokens.append(str(token) if token is not None else None)
        start = int(token) if token is not None else 0
        prefix = str(kwargs["Prefix"])
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        page = keys[start : start + 1000]
        next_index = start + len(page)
        truncated = next_index < len(keys)
        return {
            "Contents": [
                {
                    "Key": key,
                    "Size": len(self.objects[key][0]),
                    "ETag": '"etag-is-not-a-checksum"',
                }
                for key in page
            ],
            "IsTruncated": truncated,
            "NextContinuationToken": str(next_index) if truncated else None,
        }


class _SlowGetClient(_S3Client):
    def __init__(self) -> None:
        super().__init__()
        self.get_started = threading.Event()
        self.get_release = threading.Event()

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.get_started.set()
        self.get_release.wait(timeout=5)
        self.last_body = _Body(b"payload")
        return {
            "ContentLength": 7,
            "ContentRange": None,
            "ContentType": "application/pdf",
            "Metadata": {},
            "Body": self.last_body,
        }


def _store(client: _S3Client) -> S3ObjectStore:
    return S3ObjectStore(
        client,
        "rag-originals",
        region="us-east-1",
        blocking_concurrency=2,
    )


def _client_error(code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "redacted fake error"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


def test_put_head_full_range_download_and_idempotent_delete(tmp_path: Path) -> None:
    client = _S3Client()
    store = _store(client)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"0123456789")
    destination = tmp_path / "download.pdf"

    async def exercise() -> None:
        await store.put(
            "originals/ab/hash.pdf",
            source,
            sha256="ab" * 32,
            byte_size=10,
        )
        metadata = await store.head("originals/ab/hash.pdf")
        assert metadata.size == 10
        assert metadata.sha256 == "ab" * 32
        assert metadata.declared_size == 10
        assert metadata.etag == "etag-is-not-a-checksum"

        full = await store.get("originals/ab/hash.pdf")
        assert await full.body.read() == b"0123456789"
        await full.body.close()
        assert full.body.closed

        partial = await store.get("originals/ab/hash.pdf", byte_range="bytes=2-5")
        async with partial.body:
            assert await partial.body.read() == b"2345"
        assert partial.content_range == "bytes 2-5/10"

        assert await store.download("originals/ab/hash.pdf", destination) == 10
        assert destination.read_bytes() == b"0123456789"
        await store.delete("originals/ab/hash.pdf")
        await store.delete("originals/ab/hash.pdf")

    asyncio.run(exercise())
    assert client.objects == {}


def test_conditional_put_proves_whether_request_created_object(
    tmp_path: Path,
) -> None:
    client = _S3Client()
    store = _store(client)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"immutable")

    async def exercise() -> tuple[bool, bool]:
        first = await store.put_if_absent(
            "originals/ab/hash.pdf",
            source,
            sha256="ab" * 32,
            byte_size=9,
        )
        second = await store.put_if_absent(
            "originals/ab/hash.pdf",
            source,
            sha256="ab" * 32,
            byte_size=9,
        )
        return first, second

    assert asyncio.run(exercise()) == (True, False)
    assert client.objects["originals/ab/hash.pdf"][0] == b"immutable"


def test_listing_follows_continuation_tokens_beyond_one_thousand() -> None:
    client = _S3Client()
    for index in range(1005):
        client.objects[f"originals/{index:04d}.pdf"] = (
            b"x",
            {"sha256": "ab" * 32, "byte-size": "1"},
            "application/pdf",
        )
    store = _store(client)

    async def exercise() -> list[str]:
        return [item.key async for item in store.list_prefix("originals/")]

    keys = asyncio.run(exercise())
    assert len(keys) == 1005
    assert client.list_tokens == [None, "1000"]


def test_missing_object_is_typed_and_does_not_leak_sdk_message() -> None:
    store = _store(_S3Client())

    with pytest.raises(ObjectStoreError) as captured:
        asyncio.run(store.head("missing.pdf"))

    assert captured.value.not_found is True
    assert captured.value.code == "NoSuchKey"
    assert "redacted fake error" not in str(captured.value)


def test_bucket_bootstrap_is_explicit_idempotent_and_audit_is_read_only(
    tmp_path: Path,
) -> None:
    client = _S3Client(bucket_exists=False)
    store = _store(client)
    service = StorageMaintenanceService(store)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")

    async def exercise() -> None:
        first = await service.bootstrap_bucket()
        second = await service.bootstrap_bucket()
        assert first.created is True
        assert second.created is False
        await store.put(
            "originals/aa/test.pdf",
            source,
            sha256="aa" * 32,
            byte_size=3,
        )
        audit = await service.audit_bucket()
        assert audit.object_count == 1
        assert audit.byte_count == 3
        assert audit.missing_checksum_metadata == ()
        assert audit.invalid_size_metadata == ()

    asyncio.run(exercise())
    assert client.bucket_exists is True


def test_blocking_bridge_enforces_concurrency_bound() -> None:
    bridge = _BoundedThreadBridge(2)
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def operation() -> None:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1

    async def exercise() -> None:
        await asyncio.gather(*(bridge.run(operation) for _ in range(8)))

    asyncio.run(exercise())
    assert maximum_active == 2


def test_from_settings_configures_bounded_sigv4_path_style_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def client(service: str, **kwargs: object) -> _S3Client:
        captured["service"] = service
        captured.update(kwargs)
        return _S3Client()

    monkeypatch.setattr("app.services.object_storage.boto3.client", client)
    settings = Settings(
        object_storage_access_key_id="test-access",
        object_storage_secret_access_key="test-secret",
        object_storage_connect_timeout_seconds=4,
        object_storage_read_timeout_seconds=17,
        object_storage_max_attempts=3,
        object_storage_connection_pool_size=7,
        object_storage_blocking_concurrency=2,
    )

    S3ObjectStore.from_settings(settings)

    config = captured["config"]
    assert captured["service"] == "s3"
    assert captured["endpoint_url"] == "http://127.0.0.1:9000"
    assert captured["region_name"] == "us-east-1"
    assert captured["aws_access_key_id"] == "test-access"
    assert captured["aws_secret_access_key"] == "test-secret"
    assert captured["use_ssl"] is False
    assert config.signature_version == "s3v4"
    assert config.connect_timeout == 4
    assert config.read_timeout == 17
    assert config.max_pool_connections == 7
    assert config.retries["total_max_attempts"] == 3
    assert config.s3["addressing_style"] == "path"


def test_midstream_read_timeout_is_typed_retryable_error() -> None:
    client = _S3Client()
    client.objects["object.pdf"] = (b"payload", {}, "application/pdf")
    client.body_factory = _ReadTimeoutBody
    store = _store(client)

    async def exercise() -> None:
        read = await store.get("object.pdf")
        with pytest.raises(ObjectStoreError) as captured:
            await read.body.read()
        assert captured.value.retryable is True
        assert captured.value.code == "read_timeout"
        await read.body.close()

    asyncio.run(exercise())
    assert client.last_body is not None
    assert client.last_body.was_closed is True


def test_download_preserves_preexisting_destination(tmp_path: Path) -> None:
    client = _S3Client()
    client.objects["object.pdf"] = (b"new", {}, "application/pdf")
    store = _store(client)
    destination = tmp_path / "existing.pdf"
    destination.write_bytes(b"keep")

    with pytest.raises(FileExistsError):
        asyncio.run(store.download("object.pdf", destination))

    assert destination.read_bytes() == b"keep"
    assert client.last_body is not None
    assert client.last_body.was_closed is True


def test_cancellation_closes_get_response_body() -> None:
    client = _SlowGetClient()
    store = _store(client)

    async def exercise() -> None:
        task = asyncio.create_task(store.get("object.pdf"))
        await asyncio.to_thread(client.get_started.wait, 2)
        task.cancel()
        client.get_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert client.last_body is not None
    assert client.last_body.was_closed is True


def test_cancellation_during_destination_open_closes_and_removes_owned_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "cancelled.pdf"
    raw_body = _Body(b"payload")
    body = AsyncObjectBody(raw_body, _BoundedThreadBridge(1))
    open_started = threading.Event()
    open_release = threading.Event()
    original_open = Path.open

    def delayed_open(path: Path, *args: object, **kwargs: object):
        if path == destination:
            open_started.set()
            open_release.wait(timeout=5)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", delayed_open)

    async def exercise() -> None:
        task = asyncio.create_task(body.download_to(destination))
        await asyncio.to_thread(open_started.wait, 2)
        task.cancel()
        open_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert destination.exists() is False
    assert raw_body.was_closed is True


def test_bootstrap_refuses_access_denied_without_create() -> None:
    client = _S3Client()
    client.head_bucket_error = ("AccessDenied", 403)
    service = StorageMaintenanceService(_store(client))

    with pytest.raises(ObjectStoreError) as captured:
        asyncio.run(service.bootstrap_bucket())

    assert captured.value.access_denied is True
    assert client.create_bucket_calls == 0


def test_bootstrap_handles_owned_bucket_creation_race_but_not_global_conflict() -> None:
    owned_client = _S3Client(bucket_exists=False)
    owned_client.create_bucket_error = ("BucketAlreadyOwnedByYou", 409)
    owned_service = StorageMaintenanceService(_store(owned_client))

    result = asyncio.run(owned_service.bootstrap_bucket())

    assert result.created is False
    conflict_client = _S3Client(bucket_exists=False)
    conflict_client.create_bucket_error = ("BucketAlreadyExists", 409)
    conflict_service = StorageMaintenanceService(_store(conflict_client))
    with pytest.raises(ObjectStoreError, match="BucketAlreadyExists"):
        asyncio.run(conflict_service.bootstrap_bucket())


def test_audit_rejects_malformed_checksum_metadata(tmp_path: Path) -> None:
    client = _S3Client()
    client.objects["bad.pdf"] = (
        b"pdf",
        {"sha256": "garbage", "byte-size": "3"},
        "application/pdf",
    )
    service = StorageMaintenanceService(_store(client))

    result = asyncio.run(service.audit_bucket())

    assert result.invalid_checksum_metadata == ("bad.pdf",)
