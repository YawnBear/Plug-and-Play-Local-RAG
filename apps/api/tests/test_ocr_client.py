import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter

from app.runtime.ocr_client import OcrServiceClient
from app.services.parsing.ocr_subprocess import OcrError
from app.services.parsing.types import OcrMode

TOKEN = "a" * 32


def _input_pages(root: Path, count: int = 2) -> Path:
    root.mkdir()
    for page_number in range(1, count + 1):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with (root / f"page-{page_number:06d}.pdf").open("wb") as output:
            writer.write(output)
    return root


def _page(page_number: int) -> dict[str, object]:
    return {
        "page_number": page_number,
        "text": f"OCR page {page_number}",
        "parse_method": "ocr",
        "blocks": [],
    }


@pytest.mark.parametrize(
    "result_pages",
    [
        [_page(1)],
        [_page(1), _page(1)],
        [_page(2), _page(1)],
    ],
)
def test_remote_ocr_rejects_missing_duplicate_or_out_of_order_pages(
    tmp_path: Path,
    result_pages: list[dict[str, object]],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(201, json={"accepted": True}, request=request)
        if request.method == "POST" and request.url.path == "/ocr":
            payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "request_id": payload["request_id"],
                    "completed_pages": [1, 2],
                    "mode": payload["mode"],
                },
                request=request,
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"mode": "full_page", "pages": result_pages},
                request=request,
            )
        return httpx.Response(204, request=request)

    http_client = httpx.AsyncClient(
        base_url="http://ocr.test",
        transport=httpx.MockTransport(handler),
    )
    client = OcrServiceClient(
        "http://ocr.test",
        TOKEN,
        client=http_client,
    )

    async def exercise() -> None:
        with pytest.raises(OcrError, match="isolated OCR service"):
            await client.parse_pages(
                _input_pages(tmp_path / "input"),
                tmp_path / "output",
                {1, 2},
                mode=OcrMode.FULL_PAGE,
            )
        await http_client.aclose()

    asyncio.run(exercise())


def test_remote_ocr_binds_visual_supplement_mode_and_identity(
    tmp_path: Path,
) -> None:
    observed_mode: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_mode
        if request.method == "PUT":
            return httpx.Response(201, json={"accepted": True}, request=request)
        if request.method == "POST" and request.url.path == "/ocr":
            payload = json.loads(request.content)
            observed_mode = payload["mode"]
            return httpx.Response(
                200,
                json={
                    "request_id": payload["request_id"],
                    "completed_pages": [1],
                    "mode": observed_mode,
                },
                request=request,
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "mode": "visual_supplement",
                    "pages": [_page(1)],
                },
                request=request,
            )
        return httpx.Response(204, request=request)

    http_client = httpx.AsyncClient(
        base_url="http://ocr.test",
        transport=httpx.MockTransport(handler),
    )
    client = OcrServiceClient(
        "http://ocr.test",
        TOKEN,
        client=http_client,
    )

    async def exercise():
        result = await client.parse_pages(
            _input_pages(tmp_path / "input"),
            tmp_path / "output",
            {1},
            mode=OcrMode.VISUAL_SUPPLEMENT,
        )
        await http_client.aclose()
        return result

    result = asyncio.run(exercise())
    assert observed_mode == "visual_supplement"
    assert list(result) == [1]
