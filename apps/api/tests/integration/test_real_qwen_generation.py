import asyncio
import os

import pytest

from app.services.ollama_generation import OllamaGenerationClient

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_GENERATION_INTEGRATION") != "1",
    reason="set RUN_GENERATION_INTEGRATION=1 to query the local qwen3:8b model",
)
def test_qwen_streams_a_grounded_answer_with_the_supplied_label() -> None:
    client = OllamaGenerationClient(
        os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        "qwen3:8b",
        context_size=8192,
    )
    prompt = """Answer using only the source and cite it exactly as [S1].
Question: How long is the return window?
[S1]
Filename: policy.pdf
Page: 4
Content: The return window is thirty days.
"""

    async def exercise() -> str:
        await client.check_available()
        try:
            return "".join([token async for token in client.stream(prompt)])
        finally:
            await client.close()

    answer = asyncio.run(exercise())
    assert "thirty days" in answer.lower()
    assert "[S1]" in answer
