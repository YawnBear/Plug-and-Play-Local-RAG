import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

import boto3
from botocore.client import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from app.config import Settings

_DETAIL_LIMIT = 500
_COPY_CHUNK_SIZE = 1024 * 1024
_T = TypeVar("_T")


class ObjectStoreError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool = False,
        not_found: bool = False,
        access_denied: bool = False,
    ) -> None:
        super().__init__(message[:_DETAIL_LIMIT])
        self.code = code
        self.retryable = retryable
        self.not_found = not_found
        self.access_denied = access_denied


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    key: str
    size: int
    sha256: str | None
    declared_size: int | None
    content_type: str | None
    etag: str | None


@dataclass(frozen=True, slots=True)
class ObjectListItem:
    key: str
    size: int
    etag: str | None


@dataclass(frozen=True, slots=True)
class BucketReadiness:
    endpoint: bool
    bucket: bool
    detail: str
    code: str | None = None
    not_found: bool = False
    access_denied: bool = False

    @property
    def ready(self) -> bool:
        return self.endpoint and self.bucket


class _BoundedThreadBridge:
    def __init__(self, concurrency: int) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)

    async def run(
        self,
        function: Callable[..., _T],
        *args: object,
        cancel_cleanup: Callable[[_T], None] | None = None,
        **kwargs: object,
    ) -> _T:
        async with self._semaphore:
            worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                try:
                    result = await worker
                except Exception:
                    pass
                else:
                    if cancel_cleanup is not None:
                        try:
                            await asyncio.to_thread(cancel_cleanup, result)
                        except Exception:
                            pass
                raise


class AsyncObjectBody:
    def __init__(self, raw_body: Any, bridge: _BoundedThreadBridge) -> None:
        self._raw_body = raw_body
        self._bridge = bridge
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def read(self, amount: int = -1) -> bytes:
        if self._closed:
            raise RuntimeError("object body is closed")
        try:
            return await self._bridge.run(self._raw_body.read, amount)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _translate_error("read object body", exc) from exc

    async def download_to(self, destination: Path) -> int:
        if self._closed:
            raise RuntimeError("object body is closed")

        output: Any | None = None
        owns_destination = False
        total = 0
        try:
            output = await self._bridge.run(
                destination.open,
                "xb",
                cancel_cleanup=lambda handle: _close_and_unlink(handle, destination),
            )
            owns_destination = True
            while chunk := await self.read(_COPY_CHUNK_SIZE):
                written = await self._bridge.run(output.write, chunk)
                if written != len(chunk):
                    raise OSError("short write while downloading object")
                total += written
            await self._bridge.run(output.flush)
            return total
        except BaseException:
            if output is not None:
                await self._bridge.run(output.close)
            if owns_destination:
                await self._bridge.run(destination.unlink, missing_ok=True)
            raise
        finally:
            if output is not None:
                await self._bridge.run(output.close)
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        try:
            await self._bridge.run(self._raw_body.close)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _translate_error("close object body", exc) from exc
        else:
            self._closed = True

    async def __aenter__(self) -> "AsyncObjectBody":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


@dataclass(frozen=True, slots=True)
class ObjectRead:
    key: str
    size: int
    content_range: str | None
    content_type: str | None
    metadata: dict[str, str]
    body: AsyncObjectBody


class ObjectStore(Protocol):
    async def put(
        self,
        key: str,
        source: Path,
        *,
        sha256: str,
        byte_size: int,
        content_type: str = "application/pdf",
    ) -> None: ...

    async def put_if_absent(
        self,
        key: str,
        source: Path,
        *,
        sha256: str,
        byte_size: int,
        content_type: str = "application/pdf",
    ) -> bool: ...

    async def head(self, key: str) -> ObjectMetadata: ...

    async def get(self, key: str, *, byte_range: str | None = None) -> ObjectRead: ...

    async def download(self, key: str, destination: Path) -> int: ...

    async def delete(self, key: str) -> None: ...

    def list_prefix(self, prefix: str) -> AsyncIterator[ObjectListItem]: ...

    async def bucket_readiness(self) -> BucketReadiness: ...

    async def create_bucket(self) -> None: ...


class S3ObjectStore:
    def __init__(
        self,
        client: Any,
        bucket: str,
        *,
        region: str,
        blocking_concurrency: int,
    ) -> None:
        self._client = client
        self.bucket = bucket
        self.region = region
        self._bridge = _BoundedThreadBridge(blocking_concurrency)

    @classmethod
    def from_settings(cls, settings: Settings) -> "S3ObjectStore":
        addressing_style = (
            "path" if settings.object_storage_force_path_style else "auto"
        )
        client = boto3.client(
            "s3",
            endpoint_url=str(settings.object_storage_endpoint_url).rstrip("/"),
            region_name=settings.object_storage_region,
            aws_access_key_id=settings.object_storage_access_key_id.get_secret_value(),
            aws_secret_access_key=(
                settings.object_storage_secret_access_key.get_secret_value()
            ),
            use_ssl=settings.object_storage_use_tls,
            config=Config(
                signature_version="s3v4",
                connect_timeout=settings.object_storage_connect_timeout_seconds,
                read_timeout=settings.object_storage_read_timeout_seconds,
                max_pool_connections=settings.object_storage_connection_pool_size,
                retries={
                    "total_max_attempts": settings.object_storage_max_attempts,
                    "mode": "standard",
                },
                s3={"addressing_style": addressing_style},
            ),
        )
        return cls(
            client,
            settings.object_storage_bucket,
            region=settings.object_storage_region,
            blocking_concurrency=settings.object_storage_blocking_concurrency,
        )

    async def put(
        self,
        key: str,
        source: Path,
        *,
        sha256: str,
        byte_size: int,
        content_type: str = "application/pdf",
    ) -> None:
        if source.stat().st_size != byte_size:
            raise ObjectStoreError(
                "object upload source size does not match the declared byte size",
                code="source_size_mismatch",
            )

        def operation() -> None:
            with source.open("rb") as body:
                self._client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=body,
                    ContentLength=byte_size,
                    ContentType=content_type,
                    Metadata={"sha256": sha256, "byte-size": str(byte_size)},
                )

        await self._call("put object", operation)

    async def put_if_absent(
        self,
        key: str,
        source: Path,
        *,
        sha256: str,
        byte_size: int,
        content_type: str = "application/pdf",
    ) -> bool:
        if source.stat().st_size != byte_size:
            raise ObjectStoreError(
                "object upload source size does not match the declared byte size",
                code="source_size_mismatch",
            )

        def operation() -> bool:
            try:
                with source.open("rb") as body:
                    self._client.put_object(
                        Bucket=self.bucket,
                        Key=key,
                        Body=body,
                        ContentLength=byte_size,
                        ContentType=content_type,
                        Metadata={"sha256": sha256, "byte-size": str(byte_size)},
                        IfNoneMatch="*",
                    )
            except ClientError as exc:
                response = exc.response if isinstance(exc.response, dict) else {}
                error = response.get("Error", {})
                code = error.get("Code") if isinstance(error, dict) else None
                status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if code in {"PreconditionFailed", "ConditionalRequestConflict"} or (
                    status_code in {409, 412}
                ):
                    return False
                raise
            return True

        return await self._call("put object if absent", operation)

    async def head(self, key: str) -> ObjectMetadata:
        response = await self._call(
            "head object",
            self._client.head_object,
            Bucket=self.bucket,
            Key=key,
        )
        metadata = {
            str(name).lower(): str(value)
            for name, value in response.get("Metadata", {}).items()
        }
        declared_size: int | None = None
        if value := metadata.get("byte-size"):
            try:
                declared_size = int(value)
            except ValueError:
                declared_size = None
        return ObjectMetadata(
            key=key,
            size=int(response["ContentLength"]),
            sha256=metadata.get("sha256"),
            declared_size=declared_size,
            content_type=response.get("ContentType"),
            etag=_clean_etag(response.get("ETag")),
        )

    async def get(self, key: str, *, byte_range: str | None = None) -> ObjectRead:
        arguments: dict[str, object] = {"Bucket": self.bucket, "Key": key}
        if byte_range is not None:
            arguments["Range"] = byte_range
        response = await self._call(
            "get object",
            self._client.get_object,
            cancel_cleanup=_close_get_response,
            **arguments,
        )
        return ObjectRead(
            key=key,
            size=int(response["ContentLength"]),
            content_range=response.get("ContentRange"),
            content_type=response.get("ContentType"),
            metadata={
                str(name).lower(): str(value)
                for name, value in response.get("Metadata", {}).items()
            },
            body=AsyncObjectBody(response["Body"], self._bridge),
        )

    async def download(self, key: str, destination: Path) -> int:
        read = await self.get(key)
        return await read.body.download_to(destination)

    async def delete(self, key: str) -> None:
        await self._call(
            "delete object",
            self._client.delete_object,
            Bucket=self.bucket,
            Key=key,
        )

    async def list_prefix(self, prefix: str) -> AsyncIterator[ObjectListItem]:
        continuation_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            arguments: dict[str, object] = {
                "Bucket": self.bucket,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if continuation_token is not None:
                arguments["ContinuationToken"] = continuation_token
            response = await self._call(
                "list objects", self._client.list_objects_v2, **arguments
            )
            for item in response.get("Contents", []):
                yield ObjectListItem(
                    key=str(item["Key"]),
                    size=int(item["Size"]),
                    etag=_clean_etag(item.get("ETag")),
                )
            if not response.get("IsTruncated", False):
                return
            next_token = response.get("NextContinuationToken")
            if not next_token or next_token in seen_tokens:
                raise ObjectStoreError(
                    "object listing returned an invalid continuation token",
                    code="invalid_pagination",
                )
            seen_tokens.add(next_token)
            continuation_token = next_token

    async def bucket_readiness(self) -> BucketReadiness:
        try:
            await self._call(
                "head bucket", self._client.head_bucket, Bucket=self.bucket
            )
        except ObjectStoreError as exc:
            endpoint = exc.code not in {
                "endpoint_unreachable",
                "connect_timeout",
                "read_timeout",
            }
            return BucketReadiness(
                endpoint=endpoint,
                bucket=False,
                detail=str(exc),
                code=exc.code,
                not_found=exc.not_found,
                access_denied=exc.access_denied,
            )
        return BucketReadiness(True, True, "ready")

    async def create_bucket(self) -> None:
        arguments: dict[str, object] = {"Bucket": self.bucket}
        if self.region != "us-east-1":
            arguments["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
        await self._call("create bucket", self._client.create_bucket, **arguments)

    async def _call(
        self,
        action: str,
        function: Callable[..., _T],
        *,
        cancel_cleanup: Callable[[_T], None] | None = None,
        **kwargs: object,
    ) -> _T:
        try:
            return await self._bridge.run(
                function, cancel_cleanup=cancel_cleanup, **kwargs
            )
        except ObjectStoreError:
            raise
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise _translate_error(action, exc) from exc


def _translate_error(action: str, error: BaseException) -> ObjectStoreError:
    if isinstance(error, EndpointConnectionError):
        return ObjectStoreError(
            f"object storage {action} failed: endpoint is unreachable",
            code="endpoint_unreachable",
            retryable=True,
        )
    if isinstance(error, ConnectTimeoutError):
        return ObjectStoreError(
            f"object storage {action} failed: connection timed out",
            code="connect_timeout",
            retryable=True,
        )
    if isinstance(error, ReadTimeoutError):
        return ObjectStoreError(
            f"object storage {action} failed: read timed out",
            code="read_timeout",
            retryable=True,
        )
    if isinstance(error, ClientError):
        response_error = error.response.get("Error", {})
        code = str(response_error.get("Code", "client_error"))
        status = int(
            error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        )
        not_found = code in {"404", "NoSuchBucket", "NoSuchKey", "NotFound"}
        access_denied = code in {"403", "AccessDenied", "InvalidAccessKeyId"}
        retryable = status >= 500 or code in {
            "RequestTimeout",
            "SlowDown",
            "ServiceUnavailable",
        }
        return ObjectStoreError(
            f"object storage {action} failed ({code})",
            code=code,
            retryable=retryable,
            not_found=not_found,
            access_denied=access_denied,
        )
    if isinstance(error, BotoCoreError):
        return ObjectStoreError(
            f"object storage {action} failed ({type(error).__name__})",
            code="sdk_error",
            retryable=True,
        )
    return ObjectStoreError(
        f"object storage {action} failed ({type(error).__name__})",
        code="unexpected_error",
    )


def _clean_etag(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip('"')


def _close_get_response(response: object) -> None:
    if not isinstance(response, dict):
        return
    body = response.get("Body")
    if body is not None:
        body.close()


def _close_and_unlink(handle: object, destination: Path) -> None:
    try:
        close = getattr(handle, "close", None)
        if close is not None:
            close()
    finally:
        destination.unlink(missing_ok=True)
