import re
from dataclasses import dataclass

from app.services.object_storage import ObjectStore, ObjectStoreError


@dataclass(frozen=True, slots=True)
class BucketBootstrapResult:
    created: bool
    detail: str


@dataclass(frozen=True, slots=True)
class StorageAuditResult:
    object_count: int
    byte_count: int
    missing_checksum_metadata: tuple[str, ...]
    invalid_checksum_metadata: tuple[str, ...]
    invalid_size_metadata: tuple[str, ...]


class StorageMaintenanceService:
    def __init__(self, object_store: ObjectStore) -> None:
        self._object_store = object_store

    async def bootstrap_bucket(self) -> BucketBootstrapResult:
        readiness = await self._object_store.bucket_readiness()
        if readiness.ready:
            return BucketBootstrapResult(False, "configured bucket already exists")
        if not readiness.endpoint:
            raise ObjectStoreError(
                readiness.detail,
                code=readiness.code or "endpoint_unreachable",
                retryable=True,
            )
        if not readiness.not_found:
            raise ObjectStoreError(
                readiness.detail,
                code=readiness.code or "bucket_check_failed",
                access_denied=readiness.access_denied,
            )
        created = True
        try:
            await self._object_store.create_bucket()
        except ObjectStoreError as exc:
            if exc.code != "BucketAlreadyOwnedByYou":
                raise
            created = False
        verified = await self._object_store.bucket_readiness()
        if not verified.ready:
            raise ObjectStoreError(
                f"bucket creation could not be verified: {verified.detail}",
                code="bucket_verification_failed",
            )
        detail = (
            "configured bucket created and verified"
            if created
            else "configured bucket already exists and was verified"
        )
        return BucketBootstrapResult(created, detail)

    async def audit_bucket(self, prefix: str = "") -> StorageAuditResult:
        readiness = await self._object_store.bucket_readiness()
        if not readiness.ready:
            raise ObjectStoreError(readiness.detail, code="bucket_unavailable")
        object_count = 0
        byte_count = 0
        missing_checksum: list[str] = []
        invalid_checksum: list[str] = []
        invalid_size: list[str] = []
        async for item in self._object_store.list_prefix(prefix):
            object_count += 1
            byte_count += item.size
            metadata = await self._object_store.head(item.key)
            if not metadata.sha256:
                missing_checksum.append(item.key)
            elif re.fullmatch(r"[0-9a-f]{64}", metadata.sha256) is None:
                invalid_checksum.append(item.key)
            if metadata.declared_size != metadata.size or metadata.size != item.size:
                invalid_size.append(item.key)
        return StorageAuditResult(
            object_count=object_count,
            byte_count=byte_count,
            missing_checksum_metadata=tuple(missing_checksum),
            invalid_checksum_metadata=tuple(invalid_checksum),
            invalid_size_metadata=tuple(invalid_size),
        )
