"""Generate deterministic, self-contained V3 acceptance PDF fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .harness import EVALUATION_PATH, HarnessError, load_evaluation

GENERATED_ROOT = Path(__file__).resolve().parent / "data" / "generated"
DATASET_ID = "v3-synthetic-en-first-001"
APPROVED_HASHES_PATH = (
    Path(__file__).resolve().parent / "data" / "approved-fixture-hashes.json"
)

# A deliberately small OCR-friendly 5x7 font. Scanned fixture text is restricted
# to these characters so every declared fact is actually visible in the image.
_FONT = {
    "A": "01110/10001/10001/11111/10001/10001/10001",
    "B": "11110/10001/10001/11110/10001/10001/11110",
    "C": "01111/10000/10000/10000/10000/10000/01111",
    "D": "11110/10001/10001/10001/10001/10001/11110",
    "E": "11111/10000/10000/11110/10000/10000/11111",
    "F": "11111/10000/10000/11110/10000/10000/10000",
    "G": "01111/10000/10000/10111/10001/10001/01111",
    "H": "10001/10001/10001/11111/10001/10001/10001",
    "I": "11111/00100/00100/00100/00100/00100/11111",
    "J": "00111/00010/00010/00010/10010/10010/01100",
    "K": "10001/10010/10100/11000/10100/10010/10001",
    "L": "10000/10000/10000/10000/10000/10000/11111",
    "M": "10001/11011/10101/10101/10001/10001/10001",
    "N": "10001/11001/11001/10101/10011/10011/10001",
    "O": "01110/10001/10001/10001/10001/10001/01110",
    "P": "11110/10001/10001/11110/10000/10000/10000",
    "Q": "01110/10001/10001/10001/10101/10010/01101",
    "R": "11110/10001/10001/11110/10100/10010/10001",
    "S": "01111/10000/10000/01110/00001/00001/11110",
    "T": "11111/00100/00100/00100/00100/00100/00100",
    "U": "10001/10001/10001/10001/10001/10001/01110",
    "V": "10001/10001/10001/10001/10001/01010/00100",
    "W": "10001/10001/10001/10101/10101/10101/01010",
    "X": "10001/10001/01010/00100/01010/10001/10001",
    "Y": "10001/10001/01010/00100/00100/00100/00100",
    "Z": "11111/00001/00010/00100/01000/10000/11111",
    "0": "01110/10001/10011/10101/11001/10001/01110",
    "1": "00100/01100/00100/00100/00100/00100/01110",
    "2": "01110/10001/00001/00010/00100/01000/11111",
    "3": "11110/00001/00001/01110/00001/00001/11110",
    "4": "00010/00110/01010/10010/11111/00010/00010",
    "5": "11111/10000/10000/11110/00001/00001/11110",
    "6": "01110/10000/10000/11110/10001/10001/01110",
    "7": "11111/00001/00010/00100/01000/01000/01000",
    "8": "01110/10001/10001/01110/10001/10001/01110",
    "9": "01110/10001/10001/01111/00001/00001/01110",
    "-": "00000/00000/00000/11111/00000/00000/00000",
    " ": "00000/00000/00000/00000/00000/00000/00000",
}


def _owned_output(path: str | Path | None) -> Path:
    candidate = GENERATED_ROOT / (str(path) if path is not None else DATASET_ID)
    resolved = candidate.resolve()
    root = GENERATED_ROOT.resolve()
    if resolved == root or root not in resolved.parents:
        raise HarnessError(f"fixture output must be inside {root}")
    return resolved


def _pdf(objects: list[bytes]) -> bytes:
    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]
    body = bytearray(header)
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode())
        body.extend(obj)
        body.extend(b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(body)


def _pdf_literal(text: str) -> bytes:
    encoded = text.encode("cp1252")
    return encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _text_pdf(page_content: list[str]) -> bytes:
    pages = len(page_content)
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    page_numbers = [3 + index * 2 for index in range(pages)]
    font_number = 3 + pages * 2
    kids = "".join(f"{number} 0 R " for number in page_numbers)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    for index, fact in enumerate(page_content):
        page_number = 3 + index * 2
        content_number = page_number + 1
        stream = b"BT /F1 12 Tf 54 720 Td (" + _pdf_literal(fact) + b") Tj ET"
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_number} 0 R >> >> "
                f"/Contents {content_number} 0 R >>"
            ).encode()
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    return _pdf(objects)


def _scan_pixels(text: str, scale: int = 4) -> tuple[int, int, bytes]:
    if any(character not in _FONT for character in text):
        raise HarnessError(
            "scanned fixture contains a character outside its fixed font"
        )
    width = (len(text) * 6 - 1) * scale + 2 * 12
    height = 7 * scale + 2 * 12
    pixels = bytearray([255] * (width * height * 3))
    for char_index, character in enumerate(text):
        glyph = _FONT[character].split("/")
        for y, row in enumerate(glyph):
            for x, bit in enumerate(row):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            px = 12 + (char_index * 6 + x) * scale + dx
                            py = 12 + y * scale + dy
                            offset = (py * width + px) * 3
                            pixels[offset : offset + 3] = b"\x00\x00\x00"
    return width, height, bytes(pixels)


def _scan_pdf(page_content: list[str]) -> bytes:
    pages = len(page_content)
    page_numbers = [3 + index * 3 for index in range(pages)]
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = "".join(f"{number} 0 R " for number in page_numbers)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    for index, fact in enumerate(page_content):
        page_number = 3 + index * 3
        image_number = page_number + 1
        content_number = page_number + 2
        width, height, pixels = _scan_pixels(fact)
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                f"/Resources << /XObject << /Im1 {image_number} 0 R >> >> "
                f"/Contents {content_number} 0 R >>"
            ).encode()
        )
        image_header = (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length {len(pixels)} >>\n"
            "stream\n"
        )
        objects.append(image_header.encode() + pixels + b"\nendstream")
        draw = b"q %d 0 0 %d 0 0 cm /Im1 Do Q" % (width, height)
        objects.append(
            f"<< /Length {len(draw)} >>\nstream\n".encode() + draw + b"\nendstream"
        )
    return _pdf(objects)


def generate_fixture(output: str | Path | None = None) -> dict[str, Any]:
    evaluation = load_evaluation(EVALUATION_PATH)
    try:
        approved = json.loads(APPROVED_HASHES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError("approved fixture hashes are unavailable") from exc
    evaluation_identity = hashlib.sha256(EVALUATION_PATH.read_bytes()).hexdigest()
    if (
        not isinstance(approved, dict)
        or set(approved)
        != {
            "schema_version",
            "dataset_id",
            "evaluation_identity",
            "corpus_identity",
            "documents",
        }
        or approved["schema_version"] != 1
        or approved["dataset_id"] != DATASET_ID
        or approved["evaluation_identity"] != evaluation_identity
        or not isinstance(approved["documents"], dict)
    ):
        raise HarnessError("approved fixture hash registry is invalid or stale")
    target = _owned_output(output)
    target.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for document in evaluation["documents"]:
        payload = (
            _text_pdf(document["page_content"])
            if document["kind"] == "digital"
            else _scan_pdf(document["page_content"])
        )
        filename = f"{document['id']}.pdf"
        payload_sha256 = hashlib.sha256(payload).hexdigest()
        if approved["documents"].get(document["id"]) != payload_sha256:
            raise HarnessError(
                f"generated PDF does not match approved bytes: {document['id']}"
            )
        (target / filename).write_bytes(payload)
        records.append(
            {
                "id": document["id"],
                "kind": document["kind"],
                "pages": document["pages"],
                "filename": filename,
                "sha256": payload_sha256,
                "evaluation_content_sha256": hashlib.sha256(
                    json.dumps(
                        {
                            "id": document["id"],
                            "pages": document["pages"],
                            "page_content": document["page_content"],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
                "source_tokens": [
                    {"page": page, "token": content}
                    for page, content in enumerate(document["page_content"], start=1)
                ],
            }
        )
    identity_input = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    corpus_identity = hashlib.sha256(identity_input).hexdigest()
    if (
        set(approved["documents"])
        != {document["id"] for document in evaluation["documents"]}
        or approved["corpus_identity"] != corpus_identity
    ):
        raise HarnessError("approved fixture hash registry is incomplete or stale")
    manifest = {
        "schema_version": 2,
        "dataset_id": DATASET_ID,
        "evaluation_identity": evaluation_identity,
        "corpus_identity": corpus_identity,
        "documents": records,
    }
    (target / "fixture-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generate", nargs="?", choices=["generate"], default="generate")
    parser.add_argument("--output", default=DATASET_ID)
    args = parser.parse_args(argv)
    manifest = generate_fixture(args.output)
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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
