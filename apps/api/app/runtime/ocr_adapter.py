import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader, PdfWriter

from app.services.parsing.types import OcrMode, ParsedOcrBatch


class OcrBatchAdapter(Protocol):
    async def parse_pages(
        self,
        input_directory: Path,
        output_directory: Path,
        expected_pages: set[int],
        *,
        mode: OcrMode,
    ) -> ParsedOcrBatch: ...


class IsolatedOcrAdapter:
    def __init__(
        self, subprocess_adapter: OcrBatchAdapter, *, process_count: int = 1
    ) -> None:
        if not 1 <= process_count <= 16:
            raise ValueError("OCR process count must be from 1 to 16")
        self._subprocess = subprocess_adapter
        self._process_count = process_count
        self.last_metrics: dict[str, int | float | None] | None = None

    async def process(
        self,
        *,
        job_id: str,
        workspace: str,
        pages: Sequence[int],
        mode: OcrMode,
        cancellation: asyncio.Event,
    ) -> Sequence[int]:
        root = Path(workspace)
        reader = PdfReader(root / "input.pdf")
        if len(reader.pages) != len(pages):
            raise RuntimeError("OCR input page count does not match requested pages")
        source_pages = dict(zip(pages, reader.pages, strict=True))
        process_count = min(self._process_count, len(pages))
        batches = [list(pages[index::process_count]) for index in range(process_count)]
        work: list[tuple[Path, Path, list[int]]] = []
        for index, batch_pages in enumerate(batches):
            batch_root = root / f"process-{index + 1:02d}"
            input_directory = batch_root / "pages"
            output_directory = batch_root / "output"
            input_directory.mkdir(parents=True)
            work.append((input_directory, output_directory, batch_pages))
            for page_number in batch_pages:
                source_page = source_pages[page_number]
                writer = PdfWriter()
                writer.add_page(source_page)
                with (
                    input_directory / f"page-{page_number:06d}.pdf"
                ).open("xb") as output:
                    writer.write(output)
        if cancellation.is_set():
            raise asyncio.CancelledError
        tasks = [
            asyncio.create_task(
                self._subprocess.parse_pages(
                    input_directory,
                    output_directory,
                    set(batch_pages),
                    mode=mode,
                )
            )
            for input_directory, output_directory, batch_pages in work
        ]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        parsed_pages = {
            page_number: result[page_number]
            for result in results
            for page_number in result
        }
        durations = [
            result.duration_seconds
            for result in results
            if result.duration_seconds is not None
        ]
        working_sets = [
            result.peak_working_set_bytes
            for result in results
            if result.peak_working_set_bytes is not None
        ]
        self.last_metrics = {
            "duration_seconds": max(durations) if durations else None,
            "peak_working_set_bytes": sum(working_sets) if working_sets else None,
        }
        if cancellation.is_set():
            raise asyncio.CancelledError
        payload = [
            {
                "page_number": page.page_number,
                "text": page.text,
                "parse_method": page.parse_method.value,
                "blocks": [
                    {
                        "block_id": block.block_id,
                        "order": block.order,
                        "text": block.text,
                        "region": list(block.region),
                        "label": block.label,
                    }
                    for block in page.blocks
                ],
            }
            for page in (parsed_pages[number] for number in pages)
        ]
        (root / "result.json").write_text(
            json.dumps(
                {"mode": mode.value, "pages": payload},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return pages
