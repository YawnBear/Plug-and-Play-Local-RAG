import asyncio
import hashlib
import os
from pathlib import Path

import pytest

from app.config import Settings
from app.services.object_lifecycle import ObjectMaterializer, canonical_object_key
from app.services.object_storage import S3ObjectStore

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_PHASE2_STORAGE_E2E") != "1",
    reason="set RUN_PHASE2_STORAGE_E2E=1 for the destructive isolated-bucket gate",
)
def test_phase2_materializes_verified_original_and_cleans(
    tmp_path: Path,
) -> None:
    asyncio.run(_exercise(tmp_path))


async def _exercise(tmp_path: Path) -> None:
    settings = Settings()
    store = S3ObjectStore.from_settings(settings)
    content = b"%PDF-1.7\nPhase 2 retained original\n"
    checksum = hashlib.sha256(content).hexdigest()
    key = canonical_object_key(checksum)
    source = tmp_path / "source.pdf"
    source.write_bytes(content)
    await store.put(key, source, sha256=checksum, byte_size=len(content))
    try:
        materializer = ObjectMaterializer(store, tmp_path / "work")
        async with materializer.materialize(
            key=key, sha256=checksum, byte_size=len(content)
        ) as result:
            assert result.path.read_bytes() == content
        assert list((tmp_path / "work").iterdir()) == []
    finally:
        await store.delete(key)
