from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence

HIGHLIGHT_NORMALIZATION = "citation-highlight-v1"
MAX_CONTEXT_CHARACTERS = 256
MAX_EXACT_CHARACTERS = 16_000
MAX_REGIONS_PER_PAGE = 256
_WHITESPACE = re.compile(r"\s+")

type HighlightAnchor = dict[str, object]


class HighlightAnchorError(ValueError):
    pass


def normalize_highlight_text(value: str) -> str:
    return _WHITESPACE.sub(
        " ",
        unicodedata.normalize("NFKC", value).replace("\u00ad", ""),
    ).strip()


def text_quote_anchor(
    *,
    page: int,
    page_text: str,
    chunk_text: str,
    start_hint: int | None = None,
) -> HighlightAnchor:
    normalized_page = normalize_highlight_text(page_text)
    exact = normalize_highlight_text(chunk_text)
    if not exact or len(exact) > MAX_EXACT_CHARACTERS:
        raise HighlightAnchorError("digital highlight text is empty or too large")
    matches = _match_offsets(normalized_page, exact)
    if start_hint is None:
        if len(matches) != 1:
            raise HighlightAnchorError(
                "digital chunk does not resolve to one authoritative page range"
            )
        start = matches[0]
    elif start_hint not in matches:
        raise HighlightAnchorError(
            "digital chunk offset does not match authoritative page text"
        )
    else:
        start = start_hint
    selector = {
        "exact": exact,
        "prefix": normalized_page[max(0, start - MAX_CONTEXT_CHARACTERS) : start],
        "suffix": normalized_page[
            start + len(exact) : start + len(exact) + MAX_CONTEXT_CHARACTERS
        ],
        "sha256": hashlib.sha256(exact.encode("utf-8")).hexdigest(),
    }
    resolved = [
        offset
        for offset in matches
        if normalized_page[
            max(0, offset - len(selector["prefix"])) : offset
        ].endswith(selector["prefix"])
        and normalized_page[
            offset + len(exact) : offset + len(exact) + len(selector["suffix"])
        ].startswith(selector["suffix"])
    ]
    if resolved != [start]:
        raise HighlightAnchorError("digital selector context is not unique")
    anchor: HighlightAnchor = {
        "version": 1,
        "normalization": HIGHLIGHT_NORMALIZATION,
        "pages": [{"page": page, "kind": "text_quote", "selector": selector}],
    }
    validate_highlight_anchor(anchor, page_start=page, page_end=page)
    return anchor


def ocr_regions_anchor(
    *,
    page: int,
    regions: Sequence[tuple[float, float, float, float]],
) -> HighlightAnchor:
    unique = list(dict.fromkeys(regions))
    anchor: HighlightAnchor = {
        "version": 1,
        "normalization": HIGHLIGHT_NORMALIZATION,
        "pages": [
            {
                "page": page,
                "kind": "ocr_regions",
                "regions": [
                    {"x": x, "y": y, "width": width, "height": height}
                    for x, y, width, height in unique
                ],
            }
        ],
    }
    validate_highlight_anchor(anchor, page_start=page, page_end=page)
    return anchor


def validate_highlight_anchor(
    anchor: Mapping[str, object],
    *,
    page_start: int,
    page_end: int,
) -> None:
    if anchor.get("version") != 1:
        raise HighlightAnchorError("unsupported highlight anchor version")
    if anchor.get("normalization") != HIGHLIGHT_NORMALIZATION:
        raise HighlightAnchorError("unsupported highlight normalization")
    pages = anchor.get("pages")
    if not isinstance(pages, list) or not pages:
        raise HighlightAnchorError("highlight anchor requires pages")
    seen: set[int] = set()
    for value in pages:
        if not isinstance(value, Mapping):
            raise HighlightAnchorError("invalid highlight page")
        page = value.get("page")
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or page < page_start
            or page > page_end
            or page in seen
        ):
            raise HighlightAnchorError("invalid or duplicate highlight page")
        seen.add(page)
        kind = value.get("kind")
        if kind == "text_quote":
            _validate_text_quote(value)
        elif kind == "ocr_regions":
            _validate_ocr_regions(value)
        else:
            raise HighlightAnchorError("unsupported highlight selector kind")


def _validate_text_quote(page: Mapping[str, object]) -> None:
    selector = page.get("selector")
    if not isinstance(selector, Mapping) or "regions" in page:
        raise HighlightAnchorError("invalid text quote selector")
    exact = selector.get("exact")
    prefix = selector.get("prefix")
    suffix = selector.get("suffix")
    digest = selector.get("sha256")
    if (
        not isinstance(exact, str)
        or not exact
        or len(exact) > MAX_EXACT_CHARACTERS
        or not isinstance(prefix, str)
        or len(prefix) > MAX_CONTEXT_CHARACTERS
        or not isinstance(suffix, str)
        or len(suffix) > MAX_CONTEXT_CHARACTERS
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or hashlib.sha256(exact.encode("utf-8")).hexdigest() != digest
    ):
        raise HighlightAnchorError("invalid text quote values")


def _validate_ocr_regions(page: Mapping[str, object]) -> None:
    regions = page.get("regions")
    if (
        "selector" in page
        or not isinstance(regions, list)
        or not 1 <= len(regions) <= MAX_REGIONS_PER_PAGE
    ):
        raise HighlightAnchorError("invalid OCR highlight regions")
    for region in regions:
        if not isinstance(region, Mapping):
            raise HighlightAnchorError("invalid OCR highlight rectangle")
        values = tuple(region.get(key) for key in ("x", "y", "width", "height"))
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in values
        ):
            raise HighlightAnchorError("OCR rectangle must be finite")
        x, y, width, height = (float(value) for value in values)
        if (
            x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > 1
            or y + height > 1
        ):
            raise HighlightAnchorError("OCR rectangle is outside the page")


def _match_offsets(haystack: str, needle: str) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            return offsets
        offsets.append(found)
        start = found + 1
