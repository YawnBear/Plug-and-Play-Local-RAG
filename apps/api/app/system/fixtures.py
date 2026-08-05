from __future__ import annotations

import hashlib

SYSTEM_OCR_FIXTURE_ID = "ocr.system-scan-v1"
SYSTEM_OCR_FIXTURE_TEXT = "SYSTEM OCR 17"

_FONT = {
    " ": "00000/00000/00000/00000/00000/00000/00000",
    "1": "00100/01100/00100/00100/00100/00100/01110",
    "7": "11111/00001/00010/00100/01000/01000/01000",
    "C": "01111/10000/10000/10000/10000/10000/01111",
    "E": "11111/10000/10000/11110/10000/10000/11111",
    "M": "10001/11011/10101/10101/10001/10001/10001",
    "O": "01110/10001/10001/10001/10001/10001/01110",
    "R": "11110/10001/10001/11110/10100/10010/10001",
    "S": "01111/10000/10000/01110/00001/00001/11110",
    "T": "11111/00100/00100/00100/00100/00100/00100",
    "Y": "10001/10001/01010/00100/00100/00100/00100",
}


def system_ocr_fixture() -> bytes:
    scale = 4
    text = SYSTEM_OCR_FIXTURE_TEXT
    width = (len(text) * 6 - 1) * scale + 24
    height = 7 * scale + 24
    pixels = bytearray([255] * (width * height * 3))
    for char_index, character in enumerate(text):
        for y, row in enumerate(_FONT[character].split("/")):
            for x, bit in enumerate(row):
                if bit != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        px = 12 + (char_index * 6 + x) * scale + dx
                        py = 12 + y * scale + dy
                        offset = (py * width + px) * 3
                        pixels[offset : offset + 3] = b"\x00\x00\x00"
    image = 4
    content = 5
    draw = b"q %d 0 0 %d 0 0 cm /Im1 Do Q" % (width, height)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << /XObject << /Im1 {image} 0 R >> >> "
            f"/Contents {content} 0 R >>"
        ).encode(),
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length {len(pixels)} >>\n"
            "stream\n"
        ).encode()
        + pixels
        + b"\nendstream",
        f"<< /Length {len(draw)} >>\nstream\n".encode() + draw + b"\nendstream",
    ]
    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]
    body = bytearray(header)
    for number, item in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode())
        body.extend(item)
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


SYSTEM_OCR_FIXTURE_SHA256 = hashlib.sha256(system_ocr_fixture()).hexdigest()
