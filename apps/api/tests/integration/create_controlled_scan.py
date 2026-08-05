import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    output = arguments.output.expanduser().resolve()
    if output.suffix.lower() != ".pdf":
        raise ValueError("controlled scan output must be a PDF")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)

    image = Image.new("RGB", (1654, 2339), "white")
    drawing = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 54)
    bold = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 66)
    drawing.text((150, 220), "CONTROLLED OCR RECOVERY RECORD", fill="black", font=bold)
    drawing.multiline_text(
        (150, 430),
        (
            "This page contains raster pixels only.\n\n"
            "The sapphire scan code is 6729.\n\n"
            "Use this exact code to verify OCR, retrieval,\n"
            "reranking, generation, and page-one citations."
        ),
        fill="black",
        font=font,
        spacing=32,
    )
    image.save(output, "PDF", resolution=150.0)


if __name__ == "__main__":
    main()
