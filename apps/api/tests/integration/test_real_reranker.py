import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest

from app.db.repositories import RetrievedChunk
from app.services.reranker import BgeReranker

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_RERANKER_INTEGRATION") != "1",
    reason="set RUN_RERANKER_INTEGRATION=1 to load the real CPU reranker",
)
def test_relevant_passage_outranks_irrelevant_passage_on_cpu() -> None:
    candidates = [
        RetrievedChunk(
            chunk=SimpleNamespace(
                id=uuid.uuid4(), text="The return window is thirty days."
            ),
            distance=0.2,
        ),
        RetrievedChunk(
            chunk=SimpleNamespace(
                id=uuid.uuid4(), text="The cafeteria serves soup on Tuesdays."
            ),
            distance=0.1,
        ),
    ]
    reranker = BgeReranker("BAAI/bge-reranker-v2-m3")

    ranked = asyncio.run(
        reranker.rerank("How long is the return window?", candidates, limit=6)
    )

    assert ranked[0].candidate.chunk.text == "The return window is thirty days."
    assert ranked[0].score > ranked[1].score
