import asyncio
import hashlib
import json
import uuid
from pathlib import Path

import httpx
from pypdf import PdfReader, PdfWriter

from app.domain import ParseMethod
from app.runtime.protocol import OcrResponse
from app.services.parsing.ocr_subprocess import OcrError
from app.services.parsing.types import (
    OcrMode,
    ParsedBlock,
    ParsedOcrBatch,
    ParsedPage,
)


class OcrServiceClient:
    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout_seconds: float = 900,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if len(service_token) < 32:
            raise ValueError("OCR service token must contain at least 32 characters")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {service_token}"},
        )

    async def parse_pages(
        self,
        input_directory: Path,
        output_directory: Path,
        expected_pages: set[int],
        *,
        mode: OcrMode = OcrMode.FULL_PAGE,
    ) -> ParsedOcrBatch:
        job_id = f"ocr-{uuid.uuid4().hex}"
        request_id = f"{job_id}-run"
        pages = sorted(expected_pages)
        writer = PdfWriter()
        for page_number in pages:
            source = input_directory / f"page-{page_number:06d}.pdf"
            reader = PdfReader(source)
            if len(reader.pages) != 1:
                raise OcrError(f"OCR source page is invalid: {source.name}")
            writer.add_page(reader.pages[0])
        output_directory.mkdir(parents=True, exist_ok=True)
        transfer_path = output_directory / f"{job_id}.pdf"
        with transfer_path.open("xb") as output:
            writer.write(output)
        content = transfer_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        try:
            response = await self._client.put(
                f"/jobs/{job_id}/input",
                content=content,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Length": str(len(content)),
                    "X-Content-SHA256": digest,
                },
            )
            response.raise_for_status()
            response = await self._client.post(
                "/ocr",
                json={
                    "request_id": request_id,
                    "job_id": job_id,
                    "pages": pages,
                    "mode": mode.value,
                },
            )
            response.raise_for_status()
            accepted = OcrResponse.model_validate(response.json())
            if (
                accepted.request_id != request_id
                or accepted.completed_pages != pages
                or accepted.mode != mode.value
            ):
                raise ValueError("OCR completion response mismatch")
            response = await self._client.get(f"/jobs/{job_id}/result")
            response.raise_for_status()
            staged_bytes = len(response.content)
            payload = response.json()
            if (
                not isinstance(payload, dict)
                or set(payload) != {"mode", "pages"}
                or payload["mode"] != mode.value
                or not isinstance(payload["pages"], list)
            ):
                raise ValueError("OCR result envelope is invalid")
            items = payload["pages"]
            if len(items) != len(pages):
                raise ValueError("OCR result page count mismatch")
            parsed: dict[int, ParsedPage] = {}
            observed_pages: list[int] = []
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("OCR result page is invalid")
                page_number = int(item["page_number"])
                if page_number in parsed:
                    raise ValueError("OCR result contains a duplicate page")
                observed_pages.append(page_number)
                parsed[page_number] = ParsedPage(
                    page_number,
                    str(item["text"]),
                    ParseMethod(str(item["parse_method"])),
                    tuple(
                        ParsedBlock(
                            block_id=int(block["block_id"]),
                            order=int(block["order"]),
                            text=str(block["text"]),
                            region=tuple(float(value) for value in block["region"]),
                            label=(
                                str(block["label"])
                                if block.get("label") is not None
                                else None
                            ),
                        )
                        for block in item["blocks"]
                        if isinstance(block, dict)
                    ),
                )
            if observed_pages != pages or parsed.keys() != expected_pages:
                raise ValueError("OCR result page mapping mismatch")
            return ParsedOcrBatch(
                parsed,
                staged_bytes,
                duration_seconds=accepted.duration_seconds,
                peak_working_set_bytes=accepted.peak_working_set_bytes,
            )
        except asyncio.CancelledError:
            try:
                await self._client.post(f"/cancel/{request_id}", json={})
            finally:
                raise
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as exc:
            raise OcrError("isolated OCR service request failed") from exc
        finally:
            transfer_path.unlink(missing_ok=True)
            try:
                await self._client.delete(f"/jobs/{job_id}")
            except httpx.HTTPError:
                pass

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
