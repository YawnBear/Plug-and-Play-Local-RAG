import asyncio
import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.services.object_lifecycle import (
    ObjectIntegrityError,
    ObjectMaterializer,
    canonical_object_key,
    validate_remote_metadata,
)
from app.services.object_storage import ObjectStore, ObjectStoreError

_MANIFEST_VERSION = 1
_MANIFEST_FIELDS = {
    "version",
    "captured_at",
    "database_identity",
    "alembic_revision",
    "database_inventory",
    "database_fingerprint",
    "endpoint",
    "bucket",
    "rustfs_image",
    "database_dump",
    "objects",
}
_OBJECT_FIELDS = {"key", "byte_size", "sha256"}
_DATABASE_DUMP_FIELDS = {"filename", "byte_size", "sha256"}
_DATABASE_INVENTORY_FIELDS = {"documents", "object_deletions"}
_DATABASE_DOCUMENT_FIELDS = {"document_id", "object_key", "sha256", "byte_size"}


class StorageTransferError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DatabaseStorageAudit:
    documents: int
    objects: int
    missing_keys: tuple[str, ...]
    mismatched_keys: tuple[str, ...]
    orphan_keys: tuple[str, ...]
    pending_deletion_keys: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not (self.missing_keys or self.mismatched_keys or self.orphan_keys)


@dataclass(frozen=True, slots=True)
class TransferObject:
    key: str
    byte_size: int
    sha256: str


class StorageTransferService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        object_store: ObjectStore,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._store = object_store
        self._settings = settings
        self._materializer = ObjectMaterializer(object_store, settings.object_work_path)

    async def audit(self) -> DatabaseStorageAudit:
        _, _, database_inventory, _ = await self._database_snapshot()
        documents = database_inventory["documents"]
        pending = set(database_inventory["object_deletions"])
        inventory = {item.key: item async for item in self._store.list_prefix("")}
        expected: set[str] = set()
        missing: list[str] = []
        mismatched: list[str] = []
        for document in documents:
            try:
                expected_key = canonical_object_key(document["sha256"])
            except (TypeError, ValueError):
                mismatched.append(str(document["object_key"]))
                continue
            if document["object_key"] != expected_key or document["byte_size"] <= 0:
                mismatched.append(str(document["object_key"]))
                continue
            expected.add(document["object_key"])
            item = inventory.get(document["object_key"])
            if item is None:
                missing.append(document["object_key"])
                continue
            try:
                metadata = await self._store.head(document["object_key"])
                validate_remote_metadata(
                    metadata,
                    key=document["object_key"],
                    sha256=document["sha256"],
                    byte_size=document["byte_size"],
                )
                if item.size != document["byte_size"]:
                    raise ObjectIntegrityError("inventory size mismatch")
            except (ObjectIntegrityError, ObjectStoreError):
                mismatched.append(document["object_key"])
        orphans = sorted(set(inventory) - expected - pending)
        return DatabaseStorageAudit(
            documents=len(documents),
            objects=len(inventory),
            missing_keys=tuple(sorted(missing)),
            mismatched_keys=tuple(sorted(mismatched)),
            orphan_keys=tuple(orphans),
            pending_deletion_keys=tuple(sorted(pending)),
        )

    async def export(self, destination: Path, database_dump: Path) -> Path:
        destination = _new_absolute_directory(destination)
        try:
            dump_source = database_dump.expanduser().resolve()
            dump_checksum, dump_size = await asyncio.to_thread(
                _hash_regular_file, dump_source
            )
            await asyncio.to_thread(
                _copy_exclusive, dump_source, destination / "database.dump"
            )
            objects: list[TransferObject] = []
            async for item in self._store.list_prefix(""):
                metadata = await self._store.head(item.key)
                if metadata.sha256 is None:
                    raise StorageTransferError(
                        f"object {item.key} has no SHA-256 metadata"
                    )
                key = canonical_object_key(metadata.sha256)
                if key != item.key:
                    raise StorageTransferError(f"unsafe object key: {item.key}")
                validate_remote_metadata(
                    metadata,
                    key=key,
                    sha256=metadata.sha256,
                    byte_size=item.size,
                )
                output = _safe_object_path(destination / "objects", key)
                output.parent.mkdir(parents=True, exist_ok=True)
                downloaded = await self._store.download(key, output)
                checksum, size = await asyncio.to_thread(_hash_regular_file, output)
                if (
                    downloaded != item.size
                    or size != item.size
                    or checksum != metadata.sha256
                ):
                    raise StorageTransferError(f"export verification failed: {key}")
                objects.append(TransferObject(key, size, checksum))
            (
                database_identity,
                revision,
                database_inventory,
                database_fingerprint,
            ) = await self._database_snapshot()
            _validate_database_objects(database_inventory, objects)
            manifest = {
                "version": _MANIFEST_VERSION,
                "captured_at": datetime.now(UTC).isoformat(),
                "database_identity": database_identity,
                "alembic_revision": revision,
                "database_inventory": database_inventory,
                "database_fingerprint": database_fingerprint,
                "endpoint": str(self._settings.object_storage_endpoint_url).rstrip("/"),
                "bucket": self._settings.object_storage_bucket,
                "rustfs_image": self._settings.rustfs_image_reference,
                "database_dump": {
                    "filename": "database.dump",
                    "byte_size": dump_size,
                    "sha256": dump_checksum,
                },
                "objects": [
                    asdict(item) for item in sorted(objects, key=lambda x: x.key)
                ],
            }
            await asyncio.to_thread(
                _write_manifest, destination / "manifest.json", manifest
            )
            return destination / "manifest.json"
        except BaseException:
            await asyncio.to_thread(_remove_tree, destination)
            raise

    async def import_archive(self, source: Path) -> int:
        root = source.expanduser().resolve()
        if not root.is_absolute() or not root.is_dir() or root.is_symlink():
            raise StorageTransferError(
                "import source must be an existing real directory"
            )
        manifest = await asyncio.to_thread(_load_manifest, root / "manifest.json")
        entries = _validate_manifest(manifest, root)
        _validate_database_objects(manifest["database_inventory"], entries)
        dump = manifest["database_dump"]
        dump_checksum, dump_size = await asyncio.to_thread(
            _hash_regular_file, root / dump["filename"]
        )
        if dump_checksum != dump["sha256"] or dump_size != dump["byte_size"]:
            raise StorageTransferError("paired PostgreSQL dump is tampered")
        (
            _,
            revision,
            database_inventory,
            database_fingerprint,
        ) = await self._database_snapshot()
        if revision != manifest["alembic_revision"]:
            raise StorageTransferError(
                "restored PostgreSQL revision does not match manifest"
            )
        if (
            database_inventory != manifest["database_inventory"]
            or database_fingerprint != manifest["database_fingerprint"]
        ):
            raise StorageTransferError(
                "restored PostgreSQL document/outbox inventory does not match manifest"
            )
        existing = [item.key async for item in self._store.list_prefix("")]
        if existing:
            raise StorageTransferError("storage import requires an empty bucket")
        for entry in entries:
            path = _safe_object_path(root / "objects", entry.key)
            checksum, size = await asyncio.to_thread(_hash_regular_file, path)
            if checksum != entry.sha256 or size != entry.byte_size:
                raise StorageTransferError(f"manifest object is tampered: {entry.key}")
        for entry in entries:
            path = _safe_object_path(root / "objects", entry.key)
            await self._store.put(
                entry.key,
                path,
                sha256=entry.sha256,
                byte_size=entry.byte_size,
            )
            metadata = await self._store.head(entry.key)
            validate_remote_metadata(
                metadata,
                key=entry.key,
                sha256=entry.sha256,
                byte_size=entry.byte_size,
            )
            async with self._materializer.materialize(
                key=entry.key,
                sha256=entry.sha256,
                byte_size=entry.byte_size,
            ):
                pass
        final = {item.key: item.size async for item in self._store.list_prefix("")}
        expected = {entry.key: entry.byte_size for entry in entries}
        if final != expected:
            raise StorageTransferError("import final inventory does not match manifest")
        return len(entries)

    async def _database_identity(self) -> tuple[str, str]:
        identity, revision, _, _ = await self._database_snapshot()
        return identity, revision

    async def _database_snapshot(
        self,
    ) -> tuple[str, str, dict[str, Any], str]:
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM v4_maintenance_storage_snapshot()")
                    )
                )
                .mappings()
                .one()
            )
        raw_inventory = row["database_inventory"]
        if not isinstance(raw_inventory, dict):
            raise StorageTransferError(
                "maintenance storage snapshot returned invalid inventory"
            )
        raw_documents = raw_inventory.get("documents")
        raw_deletions = raw_inventory.get("object_deletions")
        if not isinstance(raw_documents, list) or not isinstance(raw_deletions, list):
            raise StorageTransferError(
                "maintenance storage snapshot returned invalid inventory"
            )
        document_inventory: list[dict[str, Any]] = []
        for document in raw_documents:
            if not isinstance(document, dict):
                raise StorageTransferError(
                    "maintenance storage snapshot returned invalid document"
                )
            try:
                document_id = str(uuid.UUID(str(document["document_id"])))
                object_key = str(document["object_key"])
                sha256 = str(document["sha256"])
                byte_size = int(document["byte_size"])
                expected_key = canonical_object_key(sha256)
            except (TypeError, ValueError) as exc:
                raise StorageTransferError(
                    "maintenance storage snapshot returned invalid document"
                ) from exc
            if object_key != expected_key or byte_size <= 0:
                raise StorageTransferError(
                    f"document {document_id} has invalid object metadata"
                )
            document_inventory.append(
                {
                    "document_id": document_id,
                    "object_key": object_key,
                    "sha256": sha256,
                    "byte_size": byte_size,
                }
            )
        inventory = {
            "documents": document_inventory,
            "object_deletions": [str(key) for key in raw_deletions],
        }
        if any(
            not _is_canonical_object_key(key) for key in inventory["object_deletions"]
        ):
            raise StorageTransferError(
                "database outbox contains invalid object metadata"
            )
        fingerprint = hashlib.sha256(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        database_fingerprint = row["database_fingerprint"]
        if not isinstance(database_fingerprint, str) or not re.fullmatch(
            r"[0-9a-f]{64}", database_fingerprint
        ):
            raise StorageTransferError(
                "maintenance storage snapshot returned invalid fingerprint"
            )
        return (
            str(row["database_identity"]),
            str(row["schema_revision"]),
            inventory,
            fingerprint,
        )


def _hash_regular_file(path: Path) -> tuple[str, int]:
    if path.is_symlink() or not path.is_file():
        raise StorageTransferError(f"expected a regular file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _new_absolute_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise StorageTransferError("export destination must be absolute")
    resolved_parent = path.parent.expanduser().resolve()
    if not resolved_parent.is_dir() or resolved_parent.is_symlink():
        raise StorageTransferError("export parent must be an existing real directory")
    resolved = resolved_parent / path.name
    try:
        resolved.mkdir()
    except FileExistsError as exc:
        raise StorageTransferError("export destination already exists") from exc
    return resolved


def _safe_object_path(root: Path, key: str) -> Path:
    candidate = root.joinpath(*key.split("/"))
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise StorageTransferError(f"unsafe manifest object key: {key}") from exc
    return resolved


def _write_manifest(path: Path, value: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as output:
        temporary = Path(output.name)
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(temporary, path)


def _copy_exclusive(source: Path, destination: Path) -> None:
    import shutil

    with destination.open("xb") as output, source.open("rb") as input_file:
        shutil.copyfileobj(input_file, output, 1024 * 1024)


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise StorageTransferError("manifest.json must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageTransferError("manifest.json is invalid") from exc
    if not isinstance(value, dict):
        raise StorageTransferError("manifest root must be an object")
    return value


def _validate_manifest(value: dict[str, Any], root: Path) -> list[TransferObject]:
    if set(value) != _MANIFEST_FIELDS or value.get("version") != _MANIFEST_VERSION:
        raise StorageTransferError("manifest fields or version are invalid")
    for field in (
        "captured_at",
        "database_identity",
        "alembic_revision",
        "database_fingerprint",
        "endpoint",
        "bucket",
        "rustfs_image",
    ):
        if not isinstance(value.get(field), str) or not value[field]:
            raise StorageTransferError(f"manifest {field} is invalid")
    try:
        datetime.fromisoformat(value["captured_at"])
    except ValueError as exc:
        raise StorageTransferError("manifest captured_at is invalid") from exc
    _validate_database_inventory(
        value.get("database_inventory"), value["database_fingerprint"]
    )
    dump = value.get("database_dump")
    if not isinstance(dump, dict) or set(dump) != _DATABASE_DUMP_FIELDS:
        raise StorageTransferError("manifest database dump fields are invalid")
    if dump.get("filename") != "database.dump":
        raise StorageTransferError("manifest database dump filename is invalid")
    if (
        not isinstance(dump.get("byte_size"), int)
        or isinstance(dump.get("byte_size"), bool)
        or dump["byte_size"] <= 0
        or not isinstance(dump.get("sha256"), str)
        or len(dump["sha256"]) != 64
    ):
        raise StorageTransferError("manifest database dump identity is invalid")
    try:
        int(dump["sha256"], 16)
    except ValueError as exc:
        raise StorageTransferError(
            "manifest database dump checksum is invalid"
        ) from exc
    raw_objects = value.get("objects")
    if not isinstance(raw_objects, list):
        raise StorageTransferError("manifest objects must be a list")
    entries: list[TransferObject] = []
    seen: set[str] = set()
    for raw in raw_objects:
        if not isinstance(raw, dict) or set(raw) != _OBJECT_FIELDS:
            raise StorageTransferError("manifest object fields are invalid")
        key = raw["key"]
        sha256 = raw["sha256"]
        byte_size = raw["byte_size"]
        if not isinstance(key, str) or not isinstance(sha256, str):
            raise StorageTransferError("manifest object identity is invalid")
        if (
            not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or byte_size <= 0
        ):
            raise StorageTransferError("manifest object size is invalid")
        try:
            canonical_key = canonical_object_key(sha256)
        except ValueError as exc:
            raise StorageTransferError("manifest object checksum is invalid") from exc
        if key in seen or canonical_key != key:
            raise StorageTransferError("manifest contains a duplicate or invalid key")
        seen.add(key)
        path = _safe_object_path(root / "objects", key)
        if path.is_symlink():
            raise StorageTransferError("manifest object cannot be a symlink")
        entries.append(TransferObject(key, byte_size, sha256))
    return entries


def _validate_database_inventory(value: object, fingerprint: str) -> None:
    if not isinstance(value, dict) or set(value) != _DATABASE_INVENTORY_FIELDS:
        raise StorageTransferError("manifest database inventory fields are invalid")
    documents = value.get("documents")
    deletions = value.get("object_deletions")
    if not isinstance(documents, list) or not isinstance(deletions, list):
        raise StorageTransferError("manifest database inventory is invalid")
    document_ids: set[str] = set()
    keys: set[str] = set()
    for document in documents:
        if not isinstance(document, dict) or set(document) != _DATABASE_DOCUMENT_FIELDS:
            raise StorageTransferError("manifest database document fields are invalid")
        document_id = document["document_id"]
        key = document["object_key"]
        sha256 = document["sha256"]
        byte_size = document["byte_size"]
        if (
            not isinstance(document_id, str)
            or not isinstance(key, str)
            or not isinstance(sha256, str)
            or not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or byte_size <= 0
        ):
            raise StorageTransferError("manifest database document identity is invalid")
        try:
            uuid.UUID(document_id)
            canonical_key = canonical_object_key(sha256)
        except (ValueError, TypeError) as exc:
            raise StorageTransferError(
                "manifest database document identity is invalid"
            ) from exc
        if (
            not isinstance(key, str)
            or key != canonical_key
            or document_id in document_ids
            or key in keys
        ):
            raise StorageTransferError("manifest database document locator is invalid")
        document_ids.add(document_id)
        keys.add(key)
    if any(
        not isinstance(key, str) or not _is_canonical_object_key(key)
        for key in deletions
    ) or len(set(deletions)) != len(deletions):
        raise StorageTransferError("manifest database outbox inventory is invalid")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise StorageTransferError("manifest database fingerprint is invalid")
    expected = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if fingerprint != expected:
        raise StorageTransferError("manifest database fingerprint does not match")


def _validate_database_objects(
    inventory: dict[str, Any], objects: list[TransferObject]
) -> None:
    exported = {entry.key: entry for entry in objects}
    for document in inventory["documents"]:
        entry = exported.get(document["object_key"])
        if (
            entry is None
            or entry.sha256 != document["sha256"]
            or entry.byte_size != document["byte_size"]
        ):
            raise StorageTransferError(
                "database-referenced object is missing or mismatched"
            )


def _is_canonical_object_key(value: str) -> bool:
    parts = value.split("/")
    if len(parts) != 3 or parts[0] != "originals" or not parts[2].endswith(".pdf"):
        return False
    checksum = parts[2][:-4]
    try:
        return parts[1] == checksum[:2] and canonical_object_key(checksum) == value
    except ValueError:
        return False


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
