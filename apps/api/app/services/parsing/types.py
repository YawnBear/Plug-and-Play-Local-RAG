from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.domain import ParseMethod


class OcrMode(StrEnum):
    FULL_PAGE = "full_page"
    VISUAL_SUPPLEMENT = "visual_supplement"


class PageRoutingMode(StrEnum):
    DIRECT = "direct"
    OCR = "ocr"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    block_id: int
    order: int
    text: str
    region: tuple[float, float, float, float]
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedFragment:
    parse_method: ParseMethod
    text: str
    blocks: tuple[ParsedBlock, ...] = ()


@dataclass(frozen=True, slots=True)
class PageRoutingAssessment:
    mode: PageRoutingMode
    reason_codes: tuple[str, ...]
    extraction_succeeded: bool
    meaningful_character_count: int
    printable_ratio: float
    alphanumeric_ratio: float
    invalid_character_count: int
    repeated_glyph_run: int
    image_object_count: int
    displayed_image_coverage: float
    vector_drawing_count: int
    unresolved_visual_geometry: bool


@dataclass(frozen=True, slots=True)
class ParsedPage:
    page_number: int
    text: str
    parse_method: ParseMethod
    blocks: tuple[ParsedBlock, ...] = ()
    routing_mode: PageRoutingMode | None = None
    fragments: tuple[ParsedFragment, ...] = ()
    assessment: PageRoutingAssessment | None = None

    def __post_init__(self) -> None:
        if self.routing_mode is None:
            object.__setattr__(
                self,
                "routing_mode",
                (
                    PageRoutingMode.OCR
                    if self.parse_method is ParseMethod.OCR
                    else PageRoutingMode.DIRECT
                ),
            )


@dataclass(frozen=True, slots=True)
class ParsedOcrBatch(Mapping[int, ParsedPage]):
    pages: Mapping[int, ParsedPage]
    staged_bytes: int
    duration_seconds: float | None = None
    peak_working_set_bytes: int | None = None

    def __getitem__(self, page_number: int) -> ParsedPage:
        return self.pages[page_number]

    def __iter__(self) -> Iterator[int]:
        return iter(self.pages)

    def __len__(self) -> int:
        return len(self.pages)
