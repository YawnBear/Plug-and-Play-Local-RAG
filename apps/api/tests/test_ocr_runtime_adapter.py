import asyncio
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.domain import ParseMethod
from app.runtime.ocr_adapter import IsolatedOcrAdapter
from app.services.parsing.types import OcrMode, ParsedOcrBatch, ParsedPage


class ParallelFixtureAdapter:
    def __init__(self) -> None:
        self.calls: list[set[int]] = []
        self.active = 0
        self.maximum_active = 0

    async def parse_pages(
        self,
        _input_directory: Path,
        _output_directory: Path,
        expected_pages: set[int],
        *,
        mode: OcrMode,
    ) -> ParsedOcrBatch:
        assert mode is OcrMode.FULL_PAGE
        self.calls.append(expected_pages)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return ParsedOcrBatch(
            {
                number: ParsedPage(
                    number, f"page {number}", ParseMethod.OCR, ()
                )
                for number in expected_pages
            },
            staged_bytes=100,
            duration_seconds=float(len(expected_pages)),
            peak_working_set_bytes=1024,
        )


class FailingFixtureAdapter:
    def __init__(self) -> None:
        self.active = 0
        self.sibling_cancelled = False

    async def parse_pages(
        self,
        _input_directory: Path,
        _output_directory: Path,
        expected_pages: set[int],
        *,
        mode: OcrMode,
    ) -> ParsedOcrBatch:
        assert mode is OcrMode.FULL_PAGE
        self.active += 1
        try:
            if 1 in expected_pages:
                await asyncio.sleep(0)
                raise RuntimeError("fixture failure")
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.sibling_cancelled = True
                raise
            raise AssertionError("sibling OCR batch was not cancelled")
        finally:
            self.active -= 1


@pytest.mark.parametrize("pages", [[], [0], [-1], [1_000_000], [True], [1, 1]])
def test_invalid_page_sets_are_rejected_before_path_construction(
    tmp_path: Path, pages: list[int]
) -> None:
    adapter = IsolatedOcrAdapter(ParallelFixtureAdapter())

    with pytest.raises(ValueError, match="invalid OCR page set"):
        asyncio.run(
            adapter.process(
                job_id="job-1",
                workspace=str(tmp_path),
                pages=pages,
                mode=OcrMode.FULL_PAGE,
                cancellation=asyncio.Event(),
            )
        )


def test_process_count_splits_one_ocr_request_into_parallel_subprocess_batches(
    tmp_path: Path,
) -> None:
    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=100, height=100)
    with (tmp_path / "input.pdf").open("wb") as output:
        writer.write(output)
    subprocess = ParallelFixtureAdapter()
    adapter = IsolatedOcrAdapter(subprocess, process_count=2)

    completed = asyncio.run(
        adapter.process(
            job_id="job-1",
            workspace=str(tmp_path),
            pages=[1, 2, 3, 4],
            mode=OcrMode.FULL_PAGE,
            cancellation=asyncio.Event(),
        )
    )

    assert completed == [1, 2, 3, 4]
    assert subprocess.maximum_active == 2
    assert {frozenset(call) for call in subprocess.calls} == {
        frozenset({1, 3}),
        frozenset({2, 4}),
    }
    assert adapter.last_metrics == {
        "duration_seconds": 2.0,
        "peak_working_set_bytes": 2048,
    }
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert [page["page_number"] for page in result["pages"]] == [1, 2, 3, 4]


def test_failed_ocr_batch_cancels_and_settles_sibling_batches(tmp_path: Path) -> None:
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=100, height=100)
    with (tmp_path / "input.pdf").open("wb") as output:
        writer.write(output)
    subprocess = FailingFixtureAdapter()
    adapter = IsolatedOcrAdapter(subprocess, process_count=2)

    with pytest.raises(RuntimeError, match="fixture failure"):
        asyncio.run(
            adapter.process(
                job_id="job-1",
                workspace=str(tmp_path),
                pages=[1, 2],
                mode=OcrMode.FULL_PAGE,
                cancellation=asyncio.Event(),
            )
        )

    assert subprocess.active == 0
    assert subprocess.sibling_cancelled
