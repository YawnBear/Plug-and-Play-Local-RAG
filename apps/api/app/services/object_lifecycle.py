import asyncio
import hashlib
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.object_storage import ObjectMetadata, ObjectStore, ObjectStoreError


class ObjectIntegrityError(RuntimeError):
    """An immutable original is missing or does not match its database identity."""


def canonical_object_key(sha256: str) -> str:
    checksum = sha256.lower()
    if len(checksum) != 64:
        raise ValueError("SHA-256 must contain exactly 64 hexadecimal characters")
    try:
        int(checksum, 16)
    except ValueError as exc:
        raise ValueError("SHA-256 must contain only hexadecimal characters") from exc
    return f"originals/{checksum[:2]}/{checksum}.pdf"


def checksum_lock_id(sha256: str) -> int:
    canonical_object_key(sha256)
    digest = hashlib.sha256(
        b"rag:object-sha256-lock:v1\0" + bytes.fromhex(sha256)
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


async def acquire_checksum_lock(session: AsyncSession, sha256: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": checksum_lock_id(sha256)},
    )


def validate_remote_metadata(
    metadata: ObjectMetadata,
    *,
    key: str,
    sha256: str,
    byte_size: int,
) -> None:
    if key != canonical_object_key(sha256) or metadata.key != key:
        raise ObjectIntegrityError("object key does not match the document checksum")
    if byte_size <= 0:
        raise ObjectIntegrityError("document byte size must be positive")
    if metadata.size != byte_size or metadata.declared_size != byte_size:
        raise ObjectIntegrityError("object size metadata does not match the document")
    if metadata.sha256 != sha256.lower():
        raise ObjectIntegrityError(
            "object SHA-256 metadata does not match the document"
        )


@dataclass(frozen=True, slots=True)
class MaterializedObject:
    path: Path
    metadata: ObjectMetadata


class ObjectMaterializer:
    def __init__(self, store: ObjectStore, work_root: Path) -> None:
        self._store = store
        self._work_root = work_root

    @asynccontextmanager
    async def materialize(
        self,
        *,
        key: str,
        sha256: str,
        byte_size: int,
    ) -> AsyncIterator[MaterializedObject]:
        metadata = await self._head(key)
        validate_remote_metadata(metadata, key=key, sha256=sha256, byte_size=byte_size)
        self._work_root.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="object-", dir=self._work_root))
        destination = directory / "original.pdf"
        try:
            downloaded = await self._store.download(key, destination)
            if downloaded != byte_size:
                raise ObjectIntegrityError(
                    "downloaded object size does not match the document"
                )
            actual_sha256, actual_size = await _run_thread_cancellation_safe(
                _hash_file, destination
            )
            if actual_size != byte_size or actual_sha256 != sha256.lower():
                raise ObjectIntegrityError(
                    "downloaded object bytes do not match the document checksum"
                )
            yield MaterializedObject(destination, metadata)
        finally:
            await _cleanup_directory(directory)

    async def _head(self, key: str) -> ObjectMetadata:
        try:
            return await self._store.head(key)
        except ObjectStoreError as exc:
            if exc.not_found:
                raise ObjectIntegrityError("document object is missing") from exc
            raise


async def _cleanup_directory(path: Path) -> None:
    await _run_thread_cancellation_safe(_remove_directory_with_retry, path)


def _remove_directory_with_retry(path: Path) -> None:
    for attempt in range(3):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(0.05 * (2**attempt))


async def _run_thread_cancellation_safe[T](
    function: Callable[..., T], *args: object
) -> T:
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size
