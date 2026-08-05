import asyncio
import hashlib
import os
import uuid
from pathlib import Path

import pytest

from app.config import Settings
from app.services.object_storage import ObjectStoreError, S3ObjectStore

pytestmark = pytest.mark.integration


def test_live_rustfs_s3_compatibility(tmp_path: Path) -> None:
    if os.environ.get("RUN_RUSTFS_INTEGRATION") != "1":
        pytest.skip("set RUN_RUSTFS_INTEGRATION=1 for the destructive RustFS gate")
    settings = Settings()
    store = S3ObjectStore.from_settings(settings)
    key = f"phase1/{uuid.uuid4()}.pdf"
    source = tmp_path / "source.pdf"
    payload = b"%PDF-1.7\nphase-one-rustfs-compatibility\n%%EOF\n"
    source.write_bytes(payload)
    checksum = hashlib.sha256(payload).hexdigest()
    destination = tmp_path / "download.pdf"

    async def exercise() -> None:
        readiness = await store.bucket_readiness()
        assert readiness.ready, readiness.detail
        try:
            await store.put(
                key,
                source,
                sha256=checksum,
                byte_size=len(payload),
            )
            metadata = await store.head(key)
            assert metadata.size == len(payload)
            assert metadata.sha256 == checksum
            assert metadata.declared_size == len(payload)

            full = await store.get(key)
            async with full.body:
                assert await full.body.read() == payload

            partial = await store.get(key, byte_range="bytes=5-13")
            async with partial.body:
                assert await partial.body.read() == payload[5:14]
            assert partial.content_range == f"bytes 5-13/{len(payload)}"

            assert await store.download(key, destination) == len(payload)
            assert destination.read_bytes() == payload
            keys = [item.key async for item in store.list_prefix("phase1/")]
            assert key in keys
        finally:
            await store.delete(key)
            await store.delete(key)
        with pytest.raises(ObjectStoreError) as captured:
            await store.head(key)
        assert captured.value.not_found

    asyncio.run(exercise())
