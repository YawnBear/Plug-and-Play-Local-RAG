import asyncio
import hashlib
import math
import tempfile
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import ContentStream

from app.domain import ParseMethod
from app.services.highlight_anchors import normalize_highlight_text
from app.services.parsing.ocr_subprocess import OcrError
from app.services.parsing.types import (
    OcrMode,
    PageRoutingAssessment,
    PageRoutingMode,
    ParsedBlock,
    ParsedFragment,
    ParsedOcrBatch,
    ParsedPage,
)

_MINIMUM_PRINTABLE_RATIO = 0.85
_MINIMUM_ALPHANUMERIC_RATIO = 0.20
_MAXIMUM_INVALID_RATIO = 0.02
_MAXIMUM_REPEATED_GLYPH_RUN = 20
_MEANINGFUL_IMAGE_COVERAGE = 0.10
_DECORATIVE_REPEATED_COVERAGE = 0.08
_MEANINGFUL_VECTOR_OPERATIONS = 12
_DRAWING_OPERATORS = {
    b"m",
    b"l",
    b"c",
    b"v",
    b"y",
    b"re",
    b"S",
    b"s",
    b"f",
    b"F",
    b"f*",
    b"B",
    b"B*",
    b"b",
    b"b*",
}

OcrProgressCallback = Callable[[int, int], Awaitable[None]]


class PdfParsingError(RuntimeError):
    pass


class DocumentWorkLimitError(PdfParsingError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.safe_message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class _ImagePlacement:
    asset_id: str
    coverage: float | None


@dataclass(frozen=True, slots=True)
class _VisualSignals:
    image_object_count: int
    placements: tuple[_ImagePlacement, ...]
    vector_drawing_count: int
    unresolved_geometry: bool


def meaningful_character_count(text: str) -> int:
    return sum(character.isalnum() for character in text)


class PdfParser:
    def __init__(
        self,
        ocr_adapter: object,
        *,
        meaningful_text_threshold: int,
        work_root: Path,
        ocr_batch_size: int = 8,
        maximum_pdf_pages: int = 500,
        maximum_ocr_pages: int = 128,
        maximum_extracted_text_characters: int = 10_000_000,
        maximum_staged_ocr_result_bytes: int = 64 * 1024 * 1024,
        external_batch_max_attempts: int = 2,
        enable_adaptive_page_routing: bool = False,
        enable_visual_supplement_ocr: bool = False,
    ) -> None:
        if not 1 <= ocr_batch_size <= 32:
            raise ValueError("OCR page batch size must be between 1 and 32")
        if maximum_pdf_pages < 1 or maximum_ocr_pages < 1:
            raise ValueError("PDF and OCR page limits must be positive")
        if maximum_extracted_text_characters < 1:
            raise ValueError("extracted text limit must be positive")
        if maximum_staged_ocr_result_bytes < 1:
            raise ValueError("staged OCR result limit must be positive")
        if not 1 <= external_batch_max_attempts <= 4:
            raise ValueError("external batch attempts must be between 1 and 4")
        if enable_visual_supplement_ocr and not enable_adaptive_page_routing:
            raise ValueError(
                "visual supplement OCR requires adaptive page routing"
            )
        self.ocr_adapter = ocr_adapter
        self.meaningful_text_threshold = meaningful_text_threshold
        self.work_root = work_root
        self.ocr_batch_size = ocr_batch_size
        self.maximum_pdf_pages = maximum_pdf_pages
        self.maximum_ocr_pages = maximum_ocr_pages
        self.maximum_extracted_text_characters = (
            maximum_extracted_text_characters
        )
        self.maximum_staged_ocr_result_bytes = maximum_staged_ocr_result_bytes
        self.external_batch_max_attempts = external_batch_max_attempts
        self.enable_adaptive_page_routing = enable_adaptive_page_routing
        self.enable_visual_supplement_ocr = enable_visual_supplement_ocr

    async def parse(
        self,
        path: Path,
        *,
        progress: OcrProgressCallback | None = None,
    ) -> list[ParsedPage]:
        try:
            reader = PdfReader(path)
        except (OSError, PdfReadError) as exc:
            raise PdfParsingError("unable to read PDF") from exc
        page_count = len(reader.pages)
        if page_count > self.maximum_pdf_pages:
            raise DocumentWorkLimitError(
                "pdf_page_limit_exceeded",
                f"PDF exceeds the {self.maximum_pdf_pages}-page processing limit",
            )

        extracted_text: dict[int, str] = {}
        extraction_succeeded: dict[int, bool] = {}
        visuals: dict[int, _VisualSignals] = {}
        cumulative_text = 0
        for page_number, page in enumerate(reader.pages, start=1):
            succeeded = True
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
                succeeded = False
            text = text.strip()
            extracted_text[page_number] = text
            extraction_succeeded[page_number] = succeeded
            cumulative_text += len(text)
            if cumulative_text > self.maximum_extracted_text_characters:
                raise DocumentWorkLimitError(
                    "extracted_text_limit_exceeded",
                    "PDF extracted text exceeds the configured processing limit",
                )
            visuals[page_number] = (
                _visual_signals(page, reader)
                if self.enable_adaptive_page_routing
                else _VisualSignals(0, (), 0, False)
            )

        repeated_assets = _repeated_decorative_assets(visuals, page_count)
        assessments = {
            page_number: self._assess_page(
                extracted_text[page_number],
                extraction_succeeded=extraction_succeeded[page_number],
                visual=visuals[page_number],
                repeated_assets=repeated_assets,
            )
            for page_number in range(1, page_count + 1)
        }
        full_ocr_pages = [
            number
            for number, assessment in assessments.items()
            if assessment.mode is PageRoutingMode.OCR
        ]
        hybrid_pages = [
            number
            for number, assessment in assessments.items()
            if assessment.mode is PageRoutingMode.HYBRID
        ]
        selected_ocr_pages = len(full_ocr_pages) + len(hybrid_pages)
        if selected_ocr_pages > self.maximum_ocr_pages:
            raise DocumentWorkLimitError(
                "ocr_page_limit_exceeded",
                (
                    "PDF requires OCR for more than the configured "
                    f"{self.maximum_ocr_pages}-page limit"
                ),
            )

        if not selected_ocr_pages:
            return [
                self._direct_page(
                    number,
                    extracted_text[number],
                    assessments[number],
                )
                for number in range(1, page_count + 1)
            ]

        self.work_root.mkdir(parents=True, exist_ok=True)
        ocr_results: dict[int, ParsedPage] = {}
        completed_pages = 0
        staged_bytes = 0
        with tempfile.TemporaryDirectory(
            prefix="ocr-batches-", dir=self.work_root
        ) as temporary:
            batch_root = Path(temporary)
            for mode, page_numbers in (
                (OcrMode.FULL_PAGE, full_ocr_pages),
                (OcrMode.VISUAL_SUPPLEMENT, hybrid_pages),
            ):
                for batch_index, batch in enumerate(
                    _batches(page_numbers, self.ocr_batch_size),
                    start=1,
                ):
                    await asyncio.sleep(0)
                    input_directory = (
                        batch_root / f"{mode.value}-{batch_index:04d}" / "input"
                    )
                    output_directory = input_directory.parent / "output"
                    input_directory.mkdir(parents=True)
                    for page_number in batch:
                        writer = PdfWriter()
                        writer.add_page(reader.pages[page_number - 1])
                        with (
                            input_directory / f"page-{page_number:06d}.pdf"
                        ).open("xb") as output:
                            writer.write(output)
                    parsed = await self._parse_ocr_batch(
                        input_directory,
                        output_directory,
                        batch,
                        mode=mode,
                    )
                    staged_bytes += parsed.staged_bytes
                    if staged_bytes > self.maximum_staged_ocr_result_bytes:
                        raise DocumentWorkLimitError(
                            "ocr_result_bytes_limit_exceeded",
                            "OCR results exceed the configured staging limit",
                        )
                    for page_number in batch:
                        page = parsed[page_number]
                        cumulative_text += len(page.text)
                        if (
                            cumulative_text
                            > self.maximum_extracted_text_characters
                        ):
                            raise DocumentWorkLimitError(
                                "extracted_text_limit_exceeded",
                                "PDF text exceeds the configured processing limit",
                            )
                        ocr_results[page_number] = page
                    completed_pages += len(batch)
                    if progress is not None:
                        await progress(completed_pages, selected_ocr_pages)

        pages: dict[int, ParsedPage] = {}
        for page_number in range(1, page_count + 1):
            assessment = assessments[page_number]
            if assessment.mode is PageRoutingMode.DIRECT:
                pages[page_number] = self._direct_page(
                    page_number,
                    extracted_text[page_number],
                    assessment,
                )
            elif assessment.mode is PageRoutingMode.OCR:
                source = ocr_results[page_number]
                pages[page_number] = ParsedPage(
                    page_number,
                    source.text,
                    ParseMethod.OCR,
                    source.blocks,
                    PageRoutingMode.OCR,
                    (),
                    assessment,
                )
            else:
                pages[page_number] = _hybrid_page(
                    page_number,
                    extracted_text[page_number],
                    ocr_results[page_number],
                    assessment,
                )
        if pages.keys() != set(range(1, page_count + 1)):
            raise PdfParsingError(
                "parser did not return every source page exactly once"
            )
        return [pages[number] for number in sorted(pages)]

    async def _parse_ocr_batch(
        self,
        input_directory: Path,
        output_directory: Path,
        pages: list[int],
        *,
        mode: OcrMode,
    ) -> ParsedOcrBatch:
        expected = set(pages)
        last_error: OcrError | None = None
        for attempt in range(1, self.external_batch_max_attempts + 1):
            try:
                result = await self.ocr_adapter.parse_pages(
                    input_directory,
                    output_directory,
                    expected,
                    mode=mode,
                )
                batch = result
                if list(batch) != pages or set(batch) != expected or any(
                    batch[number].page_number != number for number in pages
                ):
                    raise OcrError(
                        "OCR batch did not return every expected page exactly once"
                    )
                if batch.staged_bytes < 0:
                    raise OcrError("OCR batch returned an invalid staged byte count")
                return batch
            except asyncio.CancelledError:
                raise
            except OcrError as exc:
                last_error = exc
                if attempt == self.external_batch_max_attempts:
                    break
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
        raise OcrError("OCR batch failed after bounded retries") from last_error

    def _assess_page(
        self,
        text: str,
        *,
        extraction_succeeded: bool,
        visual: _VisualSignals,
        repeated_assets: frozenset[str],
    ) -> PageRoutingAssessment:
        quality = _text_quality(text)
        if not self.enable_adaptive_page_routing:
            direct = quality[0] >= self.meaningful_text_threshold
            return PageRoutingAssessment(
                (
                    PageRoutingMode.DIRECT
                    if direct
                    else PageRoutingMode.OCR
                ),
                (
                    "legacy_text_threshold_direct"
                    if direct
                    else "legacy_text_threshold_ocr",
                ),
                extraction_succeeded,
                *quality,
                0,
                0.0,
                0,
                False,
            )

        reasons: list[str] = []
        meaningful_count, printable_ratio, alphanumeric_ratio, invalid, repeated = (
            quality
        )
        invalid_ratio = invalid / max(1, len(text))
        reliable = (
            extraction_succeeded
            and meaningful_count >= self.meaningful_text_threshold
            and printable_ratio >= _MINIMUM_PRINTABLE_RATIO
            and alphanumeric_ratio >= _MINIMUM_ALPHANUMERIC_RATIO
            and invalid_ratio <= _MAXIMUM_INVALID_RATIO
            and repeated < _MAXIMUM_REPEATED_GLYPH_RUN
        )
        if not extraction_succeeded:
            reasons.append("extraction_failed")
        if meaningful_count < self.meaningful_text_threshold:
            reasons.append("insufficient_meaningful_text")
        if printable_ratio < _MINIMUM_PRINTABLE_RATIO:
            reasons.append("low_printable_ratio")
        if alphanumeric_ratio < _MINIMUM_ALPHANUMERIC_RATIO:
            reasons.append("low_alphanumeric_ratio")
        if invalid_ratio > _MAXIMUM_INVALID_RATIO:
            reasons.append("invalid_character_ratio")
        if repeated >= _MAXIMUM_REPEATED_GLYPH_RUN:
            reasons.append("repeated_glyph_run")

        meaningful_placements = [
            placement
            for placement in visual.placements
            if placement.asset_id not in repeated_assets
            and (
                (
                    placement.coverage is not None
                    and placement.coverage >= _MEANINGFUL_IMAGE_COVERAGE
                )
                or (
                    placement.coverage is None
                    and visual.unresolved_geometry
                )
            )
        ]
        displayed_coverage = sum(
            placement.coverage or 0.0 for placement in visual.placements
        )
        meaningful_visual = bool(meaningful_placements) or (
            visual.vector_drawing_count >= _MEANINGFUL_VECTOR_OPERATIONS
        )
        if any(
            placement.coverage is not None
            for placement in meaningful_placements
        ):
            reasons.append("meaningful_image_coverage")
        if visual.vector_drawing_count >= _MEANINGFUL_VECTOR_OPERATIONS:
            reasons.append("vector_drawing_candidate")
        if visual.unresolved_geometry and meaningful_visual:
            reasons.append("unresolved_visual_geometry")
        if visual.image_object_count and not meaningful_visual:
            reasons.append("decorative_or_repeated_image")

        if not reliable:
            mode = PageRoutingMode.OCR
        elif meaningful_visual and self.enable_visual_supplement_ocr:
            mode = PageRoutingMode.HYBRID
        else:
            mode = PageRoutingMode.DIRECT
            if meaningful_visual:
                reasons.append("visual_supplement_disabled")
            elif not reasons:
                reasons.append("reliable_direct_text")
        return PageRoutingAssessment(
            mode,
            tuple(dict.fromkeys(reasons)),
            extraction_succeeded,
            meaningful_count,
            printable_ratio,
            alphanumeric_ratio,
            invalid,
            repeated,
            visual.image_object_count,
            min(displayed_coverage, 1.0),
            visual.vector_drawing_count,
            visual.unresolved_geometry,
        )

    @staticmethod
    def _direct_page(
        page_number: int,
        text: str,
        assessment: PageRoutingAssessment,
    ) -> ParsedPage:
        return ParsedPage(
            page_number,
            text,
            ParseMethod.DIRECT,
            (),
            PageRoutingMode.DIRECT,
            (),
            assessment,
        )


def _text_quality(text: str) -> tuple[int, float, float, int, int]:
    if not text:
        return 0, 1.0, 0.0, 0, 0
    printable = sum(
        character.isprintable() or character.isspace() for character in text
    )
    nonspace = [character for character in text if not character.isspace()]
    alphanumeric = sum(character.isalnum() for character in nonspace)
    invalid = sum(
        character == "\ufffd"
        or character == "\x00"
        or (
            unicodedata.category(character).startswith("C")
            and not character.isspace()
        )
        for character in text
    )
    longest_run = 1
    current_run = 1
    previous = text[0]
    for character in text[1:]:
        if character == previous and not character.isspace():
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 1
            previous = character
    return (
        meaningful_character_count(text),
        printable / len(text),
        alphanumeric / max(1, len(nonspace)),
        invalid,
        longest_run,
    )


def _visual_signals(page: object, reader: PdfReader) -> _VisualSignals:
    image_objects: dict[str, tuple[object, str]] = {}
    unresolved = False
    try:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject") or {}
        xobjects = xobjects.get_object()
        for name, reference in xobjects.items():
            candidate = reference.get_object()
            if str(candidate.get("/Subtype")) != "/Image":
                continue
            image_objects[str(name)] = (
                candidate,
                _asset_identity(reference, candidate),
            )
    except Exception:
        unresolved = True
        image_objects = {}

    placements: list[_ImagePlacement] = []
    vector_count = 0
    stack: list[tuple[float, float, float, float, float, float]] = []
    matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    try:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        if (
            not math.isfinite(width)
            or not math.isfinite(height)
            or width <= 0
            or height <= 0
        ):
            raise ValueError("invalid page geometry")
        content = page.get_contents()
        if content is not None:
            stream = ContentStream(content, reader)
            for operands, operator in stream.operations:
                if operator == b"q":
                    stack.append(matrix)
                elif operator == b"Q":
                    if stack:
                        matrix = stack.pop()
                    else:
                        unresolved = True
                elif operator == b"cm" and len(operands) == 6:
                    values = tuple(float(value) for value in operands)
                    matrix = _compose_matrix(matrix, values)
                elif operator == b"Do" and operands:
                    found = image_objects.get(str(operands[0]))
                    if found is None:
                        continue
                    _, asset_id = found
                    coverage = _matrix_coverage(matrix, width, height)
                    if coverage is None:
                        unresolved = True
                    placements.append(_ImagePlacement(asset_id, coverage))
                elif operator in _DRAWING_OPERATORS:
                    vector_count += 1
    except Exception:
        unresolved = unresolved or bool(image_objects)

    if image_objects and not placements:
        unresolved = True
        placements.extend(
            _ImagePlacement(asset_id, None)
            for _, asset_id in image_objects.values()
        )
    return _VisualSignals(
        len(image_objects),
        tuple(placements),
        vector_count,
        unresolved,
    )


def _asset_identity(reference: object, candidate: object) -> str:
    idnum = getattr(reference, "idnum", None)
    generation = getattr(reference, "generation", None)
    if isinstance(idnum, int):
        return f"indirect:{idnum}:{generation or 0}"
    try:
        content = candidate.get_data()
    except Exception:
        content = repr(candidate).encode("utf-8", errors="replace")
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _compose_matrix(
    current: tuple[float, float, float, float, float, float],
    added: tuple[float, ...],
) -> tuple[float, float, float, float, float, float]:
    a, b, c, d, e, f = current
    g, h, i, j, k, last = added
    return (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * last + e,
        b * k + d * last + f,
    )


def _matrix_coverage(
    matrix: tuple[float, float, float, float, float, float],
    page_width: float,
    page_height: float,
) -> float | None:
    a, b, c, d, e, f = matrix
    points = (
        (e, f),
        (a + e, b + f),
        (c + e, d + f),
        (a + c + e, b + d + f),
    )
    values = [value for point in points for value in point]
    if any(not math.isfinite(value) for value in values):
        return None
    left = max(0.0, min(point[0] for point in points))
    right = min(page_width, max(point[0] for point in points))
    bottom = max(0.0, min(point[1] for point in points))
    top = min(page_height, max(point[1] for point in points))
    if right <= left or top <= bottom:
        return None
    return min(1.0, ((right - left) * (top - bottom)) / (page_width * page_height))


def _repeated_decorative_assets(
    signals: Mapping[int, _VisualSignals],
    page_count: int,
) -> frozenset[str]:
    pages_by_asset: dict[str, set[int]] = {}
    maximum_coverage: dict[str, float] = {}
    for page_number, visual in signals.items():
        for placement in visual.placements:
            pages_by_asset.setdefault(placement.asset_id, set()).add(page_number)
            maximum_coverage[placement.asset_id] = max(
                maximum_coverage.get(placement.asset_id, 0.0),
                placement.coverage or 0.0,
            )
    minimum_pages = max(2, math.ceil(page_count * 0.60))
    return frozenset(
        asset_id
        for asset_id, page_numbers in pages_by_asset.items()
        if len(page_numbers) >= minimum_pages
        and maximum_coverage.get(asset_id, 0.0) <= _DECORATIVE_REPEATED_COVERAGE
    )


def _hybrid_page(
    page_number: int,
    direct_text: str,
    ocr_page: ParsedPage,
    assessment: PageRoutingAssessment,
) -> ParsedPage:
    normalized_direct = normalize_highlight_text(direct_text).casefold()
    unique_blocks: list[ParsedBlock] = []
    for block in ocr_page.blocks:
        normalized = normalize_highlight_text(block.text).casefold()
        if normalized and normalized in normalized_direct:
            continue
        unique_blocks.append(block)
    fragments = [ParsedFragment(ParseMethod.DIRECT, direct_text)]
    if unique_blocks:
        fragments.append(
            ParsedFragment(
                ParseMethod.OCR,
                "\n\n".join(block.text for block in unique_blocks),
                tuple(unique_blocks),
            )
        )
    return ParsedPage(
        page_number,
        direct_text,
        ParseMethod.DIRECT,
        (),
        PageRoutingMode.HYBRID,
        tuple(fragments),
        assessment,
    )


def _batches(values: list[int], size: int) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
