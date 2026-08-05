import pytest

from app.services.highlight_anchors import (
    HighlightAnchorError,
    normalize_highlight_text,
    ocr_regions_anchor,
    text_quote_anchor,
    validate_highlight_anchor,
)


def test_highlight_normalization_is_deterministic() -> None:
    assert normalize_highlight_text(" A\u00ad\u00a0\uFB01le\n name ") == "A file name"


def test_repeated_text_uses_context_and_source_offset() -> None:
    anchor = text_quote_anchor(
        page=1,
        page_text="Before repeated text. Between repeated text. After.",
        chunk_text="repeated text.",
        start_hint=30,
    )

    selector = anchor["pages"][0]["selector"]  # type: ignore[index]
    assert str(selector["prefix"]).endswith("Between ")  # type: ignore[index]


@pytest.mark.parametrize(
    "region",
    [
        (-0.1, 0.1, 0.2, 0.2),
        (0.1, 0.1, 0.0, 0.2),
        (0.9, 0.1, 0.2, 0.2),
        (0.1, 0.9, 0.2, 0.2),
        (float("nan"), 0.1, 0.2, 0.2),
        (0.1, float("inf"), 0.2, 0.2),
    ],
)
def test_ocr_regions_reject_invalid_geometry(
    region: tuple[float, float, float, float],
) -> None:
    with pytest.raises(HighlightAnchorError):
        ocr_regions_anchor(page=1, regions=[region])


def test_anchor_rejects_duplicate_pages() -> None:
    anchor = text_quote_anchor(
        page=1,
        page_text="Unique text",
        chunk_text="Unique text",
    )
    anchor["pages"] = [*anchor["pages"], anchor["pages"][0]]  # type: ignore[index]

    with pytest.raises(HighlightAnchorError, match="duplicate"):
        validate_highlight_anchor(anchor, page_start=1, page_end=1)
