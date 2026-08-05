"""Generate the redistributable V6 Core parsing and batching fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from benchmarks.v3.fixtures import _scan_pdf, _scan_pixels, _text_pdf

GENERATED_ROOT = Path(__file__).resolve().parent / "data" / "generated"
DATASET_ID = "v6-core-synthetic-001"
ROUTING_DATASET_ID = "v6-routing-synthetic-001"

_GOOD_TEXT = (
    "This page contains reliable digital prose and preserves the authoritative "
    "text layer for retrieval and citation."
)


def _owned_output(name: str) -> Path:
    candidate = (GENERATED_ROOT / name).resolve()
    root = GENERATED_ROOT.resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError(f"fixture output must be a child of {root}")
    return candidate


def _visual_pdf(
    pages: list[dict[str, object]],
    *,
    repeated_image: bool = False,
) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    shared_image = _image_reference(writer) if repeated_image else None
    for specification in pages:
        page = writer.add_blank_page(width=612, height=792)
        resources = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        commands: list[str] = []
        text = specification.get("text")
        if isinstance(text, str):
            escaped = (
                text.replace("\\", "\\\\")
                .replace("(", "\\(")
                .replace(")", "\\)")
            )
            commands.append(f"BT /F1 12 Tf 54 730 Td ({escaped}) Tj ET")
        coverage = specification.get("image_coverage")
        if isinstance(coverage, int | float):
            image_reference = shared_image or _image_reference(writer)
            resources[NameObject("/XObject")] = DictionaryObject(
                {NameObject("/Im1"): image_reference}
            )
            width = 300.0
            height = float(coverage) * 612.0 * 792.0 / width
            if specification.get("skewed") is True:
                commands.append(
                    f"q {width} 45 -25 {height} 110 100 cm /Im1 Do Q"
                )
            elif specification.get("unresolved") is True:
                commands.append("q 0 0 0 0 0 0 cm /Im1 Do Q")
            else:
                commands.append(
                    f"q {width} 0 0 {height} 110 100 cm /Im1 Do Q"
                )
        vector_operations = specification.get("vector_operations", 0)
        if isinstance(vector_operations, int):
            commands.extend(
                "80 100 m 500 100 l 500 500 l 80 500 l S"
                for _ in range(vector_operations)
            )
        page[NameObject("/Resources")] = resources
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    from io import BytesIO

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _image_reference(writer: PdfWriter) -> object:
    image = DecodedStreamObject()
    pixels = bytes(
        0 if (row // 8 + column // 8) % 2 else 255
        for row in range(64)
        for column in range(64)
    )
    image.set_data(pixels)
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(64),
            NameObject("/Height"): NumberObject(64),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    return writer._add_object(image)


def _routing_table_pixels() -> tuple[int, int, bytes]:
    width = 720
    height = 200
    pixels = bytearray([255] * (width * height * 3))

    def black(x: int, y: int) -> None:
        offset = (y * width + x) * 3
        pixels[offset : offset + 3] = b"\x00\x00\x00"

    for x in range(4, width - 4):
        for y in (4, 5, 6, 68, 69, 70, height - 7, height - 6, height - 5):
            black(x, y)
    for y in range(4, height - 4):
        for x in (4, 5, 6, 570, 571, 572, width - 7, width - 6, width - 5):
            black(x, y)

    for text, left, top, scale in (
        ("METRIC", 30, 20, 4),
        ("VALUE", 600, 20, 4),
        ("VISUAL ROUTING TOKEN", 30, 104, 4),
        ("29", 625, 104, 4),
    ):
        text_width, text_height, source = _scan_pixels(text, scale=scale)
        for source_y in range(text_height):
            for source_x in range(text_width):
                source_offset = (source_y * text_width + source_x) * 3
                if source[source_offset] != 0:
                    continue
                black(left + source_x, top + source_y)
    return width, height, bytes(pixels)


def _routing_hybrid_pdf() -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    width, height, pixels = _routing_table_pixels()
    image = DecodedStreamObject()
    image.set_data(pixels)
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(width),
            NameObject("/Height"): NumberObject(height),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    image_reference = writer._add_object(image)
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_reference}
            ),
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/Im1"): image_reference}
            ),
        }
    )
    direct_text = (
        f"{_GOOD_TEXT} DIRECT ROUTING TOKEN ALPHA is the digital-page fact."
    )
    escaped = direct_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = DecodedStreamObject()
    stream.set_data(
        (
            f"BT /F1 12 Tf 54 730 Td ({escaped}) Tj ET\n"
            "q 500 0 0 180 56 420 cm /Im1 Do Q"
        ).encode("ascii")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    from io import BytesIO

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _fixture_payloads() -> list[tuple[str, str, bytes, list[str]]]:
    long_document_pages = [
        f"OCR BATCH PAGE {index}" for index in range(1, 34)
    ]
    chunk_pages = [
        (
            f"This boundary page {index} contains reliable digital text "
            "and produces one deterministic chunk."
        )
        for index in range(1, 66)
    ]
    return [
        (
            "clean-digital",
            "clean_digital",
            _text_pdf([_GOOD_TEXT]),
            ["direct"],
        ),
        (
            "image-only-scan",
            "image_only_scan",
            _scan_pdf(["SCANNED POLICY FACT 17"]),
            ["ocr"],
        ),
        (
            "decorative-repeated-logo",
            "decorative_repeated_asset",
            _visual_pdf(
                [{"text": _GOOD_TEXT, "image_coverage": 0.02}] * 3,
                repeated_image=True,
            ),
            ["direct", "direct", "direct"],
        ),
        (
            "mixed-text-informative-image",
            "mixed_text_image",
            _visual_pdf([{"text": _GOOD_TEXT, "image_coverage": 0.30}]),
            ["hybrid"],
        ),
        (
            "vector-chart-table",
            "vector_chart_table",
            _visual_pdf([{"text": _GOOD_TEXT, "vector_operations": 12}]),
            ["hybrid"],
        ),
        (
            "garbled-text-layer",
            "garbled_text_layer",
            _text_pdf(["X" * 80]),
            ["ocr"],
        ),
        (
            "skewed-photographed-page",
            "skewed_photographed_page",
            _visual_pdf([{"image_coverage": 0.75, "skewed": True}]),
            ["ocr"],
        ),
        (
            "unresolved-visual-geometry",
            "unresolved_visual_geometry",
            _visual_pdf(
                [
                    {
                        "text": _GOOD_TEXT,
                        "image_coverage": 0.30,
                        "unresolved": True,
                    }
                ]
            ),
            ["hybrid"],
        ),
        (
            "ocr-batch-33-pages",
            "ocr_batch_boundary",
            _scan_pdf(long_document_pages),
            ["ocr"] * 33,
        ),
        (
            "embedding-batch-65-chunks",
            "embedding_batch_boundary",
            _text_pdf(chunk_pages),
            ["direct"] * 65,
        ),
    ]


def generate(output: str = DATASET_ID) -> dict[str, Any]:
    target = _owned_output(output)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    documents: list[dict[str, Any]] = []
    for fixture_id, category, payload, expected_routing in _fixture_payloads():
        filename = f"{fixture_id}.pdf"
        (target / filename).write_bytes(payload)
        documents.append(
            {
                "id": fixture_id,
                "filename": filename,
                "category": category,
                "license": "CC0-1.0",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "pages": len(expected_routing),
                "expected_routing": expected_routing,
            }
        )
    identity = hashlib.sha256(
        json.dumps(documents, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {
        "schema_version": 1,
        "dataset_id": DATASET_ID,
        "corpus_identity": identity,
        "redistribution": "All content is synthetic and dedicated CC0-1.0.",
        "documents": documents,
    }
    (target / "fixture-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def generate_routing_fixture(output: str = ROUTING_DATASET_ID) -> dict[str, Any]:
    target = _owned_output(output)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    payload = _routing_hybrid_pdf()
    filename = "hybrid-visual-table.pdf"
    (target / filename).write_bytes(payload)
    documents = [
        {
            "id": "hybrid-visual-table",
            "filename": filename,
            "category": "mixed_text_retrievable_table",
            "license": "CC0-1.0",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "pages": 1,
            "expected_routing": ["hybrid"],
            "required_direct_token": "DIRECT ROUTING TOKEN ALPHA",
            "required_visual_token": "VISUAL ROUTING TOKEN 29",
        }
    ]
    identity = hashlib.sha256(
        json.dumps(documents, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {
        "schema_version": 1,
        "dataset_id": ROUTING_DATASET_ID,
        "corpus_identity": identity,
        "redistribution": "All content is synthetic and dedicated CC0-1.0.",
        "documents": documents,
    }
    (target / "fixture-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DATASET_ID)
    parser.add_argument(
        "--routing",
        action="store_true",
        help="generate the supplementary real hybrid-routing fixture",
    )
    args = parser.parse_args(argv)
    output = args.output
    if args.routing and output == DATASET_ID:
        output = ROUTING_DATASET_ID
    manifest = (
        generate_routing_fixture(output)
        if args.routing
        else generate(output)
    )
    print(
        json.dumps(
            {
                "corpus_identity": manifest["corpus_identity"],
                "documents": len(manifest["documents"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
