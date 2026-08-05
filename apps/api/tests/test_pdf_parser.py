import asyncio
import uuid
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from app.domain import ParseMethod
from app.services.chunking import DocumentChunker
from app.services.parsing.ocr_subprocess import OcrError
from app.services.parsing.pdf import (
    DocumentWorkLimitError,
    PdfParser,
    PdfParsingError,
)
from app.services.parsing.types import (
    OcrMode,
    PageRoutingMode,
    ParsedBlock,
    ParsedOcrBatch,
    ParsedPage,
)


def _make_pdf(path: Path, page_texts: list[str | None]) -> None:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if text is not None:
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): font_reference}
                    )
                }
            )
            stream = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as output:
        writer.write(output)


def _make_visual_pdf(
    path: Path,
    pages: list[tuple[str | None, float | None, int]],
    *,
    unresolved_image_geometry: bool = False,
) -> None:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    image = DecodedStreamObject()
    image.set_data(bytes(range(64)))
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(8),
            NameObject("/Height"): NumberObject(8),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    image_reference = writer._add_object(image)
    for text, image_coverage, vector_operations in pages:
        page = writer.add_blank_page(width=612, height=792)
        resources = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        commands: list[str] = []
        if text is not None:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET")
        if image_coverage is not None:
            resources[NameObject("/XObject")] = DictionaryObject(
                {NameObject("/Im1"): image_reference}
            )
            if unresolved_image_geometry:
                commands.append("q 0 0 0 0 0 0 cm /Im1 Do Q")
            else:
                width = 300.0
                height = image_coverage * 612.0 * 792.0 / width
                commands.append(f"q {width} 0 0 {height} 100 100 cm /Im1 Do Q")
        commands.extend("0 0 m 10 10 l S" for _ in range(vector_operations))
        page[NameObject("/Resources")] = resources
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as output:
        writer.write(output)


class _RecordingOcrAdapter:
    def __init__(self) -> None:
        self.calls: list[set[int]] = []

    async def parse_pages(
        self,
        input_directory: Path,
        output_directory: Path,
        expected_pages: set[int],
        *,
        mode: OcrMode,
    ) -> ParsedOcrBatch:
        assert mode is OcrMode.FULL_PAGE
        self.calls.append(expected_pages)
        assert sorted(path.name for path in input_directory.glob("*.pdf")) == [
            f"page-{page:06d}.pdf" for page in sorted(expected_pages)
        ]
        return ParsedOcrBatch(
            {
                page: ParsedPage(page, f"OCR page {page}", ParseMethod.OCR)
                for page in sorted(expected_pages)
            },
            staged_bytes=0,
        )


class _ModeRecordingOcrAdapter:
    def __init__(
        self,
        *,
        fail_once_at_page: int | None = None,
        staged_bytes: int = 0,
        reverse_result: bool = False,
    ) -> None:
        self.calls: list[tuple[OcrMode, list[int]]] = []
        self.fail_once_at_page = fail_once_at_page
        self.staged_bytes = staged_bytes
        self.reverse_result = reverse_result
        self.failed = False

    async def parse_pages(
        self,
        input_directory: Path,
        output_directory: Path,
        expected_pages: set[int],
        *,
        mode: OcrMode,
    ) -> ParsedOcrBatch:
        ordered = sorted(expected_pages)
        self.calls.append((mode, ordered))
        if (
            self.fail_once_at_page in expected_pages
            and not self.failed
        ):
            self.failed = True
            raise OcrError("transient OCR failure")
        if self.reverse_result:
            ordered.reverse()
        pages: dict[int, ParsedPage] = {}
        for page in ordered:
            blocks = (
                (
                    ParsedBlock(
                        1,
                        1,
                        f"Visual evidence page {page}",
                        (0.1, 0.2, 0.8, 0.7),
                        "chart",
                    ),
                )
                if mode is OcrMode.VISUAL_SUPPLEMENT
                else ()
            )
            pages[page] = ParsedPage(
                page,
                f"OCR page {page}",
                ParseMethod.OCR,
                blocks,
            )
        return ParsedOcrBatch(pages, self.staged_bytes)


def test_digital_pdf_pages_avoid_ocr(tmp_path: Path) -> None:
    path = tmp_path / "digital.pdf"
    _make_pdf(path, ["This digital page has enough meaningful text for extraction."])
    adapter = _RecordingOcrAdapter()
    parser = PdfParser(
        adapter, meaningful_text_threshold=20, work_root=tmp_path / "work"
    )

    pages = asyncio.run(parser.parse(path))

    assert adapter.calls == []
    assert pages[0].page_number == 1
    assert pages[0].parse_method is ParseMethod.DIRECT
    assert "digital page" in pages[0].text


def test_only_insufficient_pages_are_batched_for_ocr(tmp_path: Path) -> None:
    path = tmp_path / "mixed.pdf"
    _make_pdf(path, ["A sufficiently long digital first page.", None, "tiny"])
    adapter = _RecordingOcrAdapter()
    parser = PdfParser(
        adapter, meaningful_text_threshold=20, work_root=tmp_path / "work"
    )

    pages = asyncio.run(parser.parse(path))

    assert adapter.calls == [{2, 3}]
    assert [page.page_number for page in pages] == [1, 2, 3]
    assert [page.parse_method for page in pages] == [
        ParseMethod.DIRECT,
        ParseMethod.OCR,
        ParseMethod.OCR,
    ]
    assert not any((tmp_path / "work").iterdir())


def test_corrupt_pdf_is_actionable(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    parser = PdfParser(
        _RecordingOcrAdapter(),
        meaningful_text_threshold=50,
        work_root=tmp_path / "work",
    )

    with pytest.raises(PdfParsingError, match="unable to read PDF"):
        asyncio.run(parser.parse(path))


@pytest.mark.parametrize(
    ("page_count", "expected_sizes"),
    [(1, [1]), (32, [32]), (33, [32, 1]), (65, [32, 32, 1])],
)
def test_ocr_batches_preserve_page_identity_across_protocol_boundaries(
    tmp_path: Path,
    page_count: int,
    expected_sizes: list[int],
) -> None:
    path = tmp_path / f"scan-{page_count}.pdf"
    _make_pdf(path, [None] * page_count)
    adapter = _ModeRecordingOcrAdapter()
    parser = PdfParser(
        adapter,
        meaningful_text_threshold=50,
        work_root=tmp_path / "work",
        ocr_batch_size=32,
        maximum_ocr_pages=100,
    )

    pages = asyncio.run(parser.parse(path))

    assert [len(page_numbers) for _, page_numbers in adapter.calls] == expected_sizes
    assert [page.page_number for page in pages] == list(range(1, page_count + 1))
    assert all(mode is OcrMode.FULL_PAGE for mode, _ in adapter.calls)


def test_failed_middle_ocr_batch_retries_only_that_batch(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    _make_pdf(path, [None] * 65)
    adapter = _ModeRecordingOcrAdapter(fail_once_at_page=33)
    parser = PdfParser(
        adapter,
        meaningful_text_threshold=50,
        work_root=tmp_path / "work",
        ocr_batch_size=32,
        maximum_ocr_pages=100,
    )

    pages = asyncio.run(parser.parse(path))

    assert len(pages) == 65
    assert [call[1][0] for call in adapter.calls] == [1, 33, 33, 65]


def test_cancellation_between_ocr_batches_cleans_staging(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    work_root = tmp_path / "work"
    _make_pdf(path, [None] * 33)
    adapter = _ModeRecordingOcrAdapter()
    parser = PdfParser(
        adapter,
        meaningful_text_threshold=50,
        work_root=work_root,
        ocr_batch_size=32,
        maximum_ocr_pages=100,
    )

    async def progress(completed: int, total: int) -> None:
        assert (completed, total) == (32, 33)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(parser.parse(path, progress=progress))

    assert len(adapter.calls) == 1
    assert list(work_root.iterdir()) == []


def test_ocr_rejects_out_of_order_results(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    _make_pdf(path, [None, None])
    parser = PdfParser(
        _ModeRecordingOcrAdapter(reverse_result=True),
        meaningful_text_threshold=50,
        work_root=tmp_path / "work",
        external_batch_max_attempts=1,
    )

    with pytest.raises(OcrError, match="bounded retries"):
        asyncio.run(parser.parse(path))


def test_work_limits_fail_before_unbounded_ocr(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    _make_pdf(path, [None, None, None])
    adapter = _ModeRecordingOcrAdapter()
    parser = PdfParser(
        adapter,
        meaningful_text_threshold=50,
        work_root=tmp_path / "work",
        maximum_ocr_pages=2,
    )

    with pytest.raises(
        DocumentWorkLimitError,
        match="ocr_page_limit_exceeded",
    ):
        asyncio.run(parser.parse(path))

    assert adapter.calls == []


def test_staged_ocr_byte_limit_is_enforced_and_cleaned(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    work_root = tmp_path / "work"
    _make_pdf(path, [None])
    parser = PdfParser(
        _ModeRecordingOcrAdapter(staged_bytes=101),
        meaningful_text_threshold=50,
        work_root=work_root,
        maximum_staged_ocr_result_bytes=100,
    )

    with pytest.raises(
        DocumentWorkLimitError,
        match="ocr_result_bytes_limit_exceeded",
    ):
        asyncio.run(parser.parse(path))

    assert list(work_root.iterdir()) == []


def test_adaptive_routing_distinguishes_scan_garbage_logo_and_hybrid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "adaptive.pdf"
    good = "This page contains substantial reliable digital text for routing."
    _make_visual_pdf(
        path,
        [
            (None, 0.50, 0),
            ("X" * 60, None, 0),
            (good, 0.02, 0),
            (good, 0.30, 0),
        ],
    )
    adapter = _ModeRecordingOcrAdapter()
    parser = PdfParser(
        adapter,
        meaningful_text_threshold=50,
        work_root=tmp_path / "work",
        enable_adaptive_page_routing=True,
        enable_visual_supplement_ocr=True,
    )

    pages = asyncio.run(parser.parse(path))

    assert [page.routing_mode for page in pages] == [
        PageRoutingMode.OCR,
        PageRoutingMode.OCR,
        PageRoutingMode.DIRECT,
        PageRoutingMode.HYBRID,
    ]
    assert adapter.calls == [
        (OcrMode.FULL_PAGE, [1, 2]),
        (OcrMode.VISUAL_SUPPLEMENT, [4]),
    ]
    assert "repeated_glyph_run" in pages[1].assessment.reason_codes
    assert "decorative_or_repeated_image" in pages[2].assessment.reason_codes
    assert "meaningful_image_coverage" in pages[3].assessment.reason_codes


def test_repeated_small_logo_is_discounted_across_pages(tmp_path: Path) -> None:
    path = tmp_path / "logos.pdf"
    good = "This page contains enough direct text to remain authoritative."
    _make_visual_pdf(path, [(good, 0.02, 0)] * 3)
    adapter = _ModeRecordingOcrAdapter()
    parser = PdfParser(
        adapter,
        meaningful_text_threshold=50,
        work_root=tmp_path / "work",
        enable_adaptive_page_routing=True,
        enable_visual_supplement_ocr=True,
    )

    pages = asyncio.run(parser.parse(path))

    assert adapter.calls == []
    assert all(page.routing_mode is PageRoutingMode.DIRECT for page in pages)


def test_unresolved_nonrepeated_visual_routes_conservatively_to_hybrid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unresolved.pdf"
    good = "This page contains enough reliable direct text for hybrid routing."
    _make_visual_pdf(
        path,
        [(good, 0.30, 0)],
        unresolved_image_geometry=True,
    )
    adapter = _ModeRecordingOcrAdapter()
    parser = PdfParser(
        adapter,
        meaningful_text_threshold=50,
        work_root=tmp_path / "work",
        enable_adaptive_page_routing=True,
        enable_visual_supplement_ocr=True,
    )

    page = asyncio.run(parser.parse(path))[0]

    assert page.routing_mode is PageRoutingMode.HYBRID
    assert page.assessment.unresolved_visual_geometry is True
    assert "unresolved_visual_geometry" in page.assessment.reason_codes


def test_hybrid_chunks_keep_direct_and_ocr_anchor_kinds_separate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hybrid.pdf"
    good = "This page contains enough reliable direct prose for hybrid parsing."
    _make_visual_pdf(path, [(good, 0.30, 0)])
    parser = PdfParser(
        _ModeRecordingOcrAdapter(),
        meaningful_text_threshold=50,
        work_root=tmp_path / "work",
        enable_adaptive_page_routing=True,
        enable_visual_supplement_ocr=True,
    )

    page = asyncio.run(parser.parse(path))[0]
    chunks = DocumentChunker().chunk(
        [page],
        document_id=uuid.uuid4(),
        filename="hybrid.pdf",
        source_sha256="a" * 64,
    )

    assert [chunk.parse_method for chunk in chunks] == [
        ParseMethod.DIRECT,
        ParseMethod.OCR,
    ]
    assert [chunk.highlight_anchor["pages"][0]["kind"] for chunk in chunks] == [
        "text_quote",
        "ocr_regions",
    ]
