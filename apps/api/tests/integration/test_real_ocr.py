import asyncio
import os
from pathlib import Path

import pytest

from app.services.parsing.ocr_subprocess import OcrSubprocessAdapter

pytestmark = pytest.mark.integration


def test_real_paddleocr_vl_page_batch() -> None:
    executable = os.environ.get("OCR_PYTHON_EXECUTABLE")
    input_directory = os.environ.get("OCR_TEST_INPUT_DIRECTORY")
    output_directory = os.environ.get("OCR_TEST_OUTPUT_DIRECTORY")
    if not executable or not input_directory or not output_directory:
        pytest.skip("real OCR integration paths are not configured")
    expected = {
        int(path.stem.removeprefix("page-"))
        for path in Path(input_directory).glob("page-*.pdf")
    }
    adapter = OcrSubprocessAdapter(
        Path(executable),
        timeout_seconds=1800,
        pipeline_version="v1.6",
        device="cpu",
        cpu_threads=int(os.environ.get("OCR_CPU_THREADS", "10")),
    )

    pages = asyncio.run(
        adapter.parse_pages(Path(input_directory), Path(output_directory), expected)
    )

    assert set(pages) == expected
    assert all(page.page_number >= 1 for page in pages.values())
