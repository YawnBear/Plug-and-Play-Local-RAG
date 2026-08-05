"""Qualify V6 adaptive page routing and minimal retrieval on the target host."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from app.domain import ParseMethod
from app.services.chunking import ChunkDraft, DocumentChunker
from app.services.ollama_embeddings import OllamaEmbeddingClient
from app.services.parsing.ocr_subprocess import OcrSubprocessAdapter
from app.services.parsing.pdf import PdfParser
from app.services.parsing.types import (
    OcrMode,
    ParsedBlock,
    ParsedOcrBatch,
    ParsedPage,
)
from app.services.reranker import BgeReranker
from app.versions import active_chunking_version, active_parser_version
from benchmarks.v6.fixtures import (
    DATASET_ID,
    GENERATED_ROOT,
    ROUTING_DATASET_ID,
)

REPORT_SCHEMA_VERSION = 1
REQUIRED_ROUTING_CATEGORIES = {
    "clean_digital",
    "image_only_scan",
    "decorative_repeated_asset",
    "mixed_text_image",
    "vector_chart_table",
    "garbled_text_layer",
    "skewed_photographed_page",
    "unresolved_visual_geometry",
    "ocr_batch_boundary",
    "embedding_batch_boundary",
}
LIVE_CASES = (
    {
        "id": "clean-digital",
        "dataset": "core",
        "required_tokens": ("AUTHORITATIVE TEXT LAYER",),
    },
    {
        "id": "image-only-scan",
        "dataset": "core",
        "required_tokens": ("SCANNED POLICY FACT 17",),
    },
    {
        "id": "hybrid-visual-table",
        "dataset": "routing",
        "required_tokens": (
            "DIRECT ROUTING TOKEN ALPHA",
            "VISUAL ROUTING TOKEN 29",
        ),
    },
)
RETRIEVAL_QUERIES = (
    (
        "digital-direct",
        "DIRECT ROUTING TOKEN ALPHA",
        "hybrid-visual-table",
        ParseMethod.DIRECT,
    ),
    (
        "scanned-policy",
        "SCANNED POLICY FACT 17",
        "image-only-scan",
        ParseMethod.OCR,
    ),
    (
        "visual-table",
        "VISUAL ROUTING TOKEN 29",
        "hybrid-visual-table",
        ParseMethod.OCR,
    ),
)


class RoutingGateError(RuntimeError):
    pass


class _CalibrationOcrAdapter:
    async def parse_pages(
        self,
        _input_directory: Path,
        _output_directory: Path,
        expected_pages: set[int],
        *,
        mode: OcrMode = OcrMode.FULL_PAGE,
    ) -> ParsedOcrBatch:
        pages: dict[int, ParsedPage] = {}
        for page_number in sorted(expected_pages):
            visual = mode is OcrMode.VISUAL_SUPPLEMENT
            text = (
                f"VISUAL EVIDENCE PAGE {page_number}"
                if visual
                else f"OCR EVIDENCE PAGE {page_number}"
            )
            block = ParsedBlock(
                block_id=page_number,
                order=page_number,
                text=text,
                region=(0.1, 0.1, 0.8, 0.2),
                label="table" if visual else "text",
            )
            pages[page_number] = ParsedPage(
                page_number,
                text,
                ParseMethod.OCR,
                (block,),
            )
        return ParsedOcrBatch(pages, 0)


def _load_manifest(root: Path, expected_dataset: str) -> dict[str, Any]:
    path = root / "fixture-manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingGateError(f"invalid fixture manifest: {path}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("dataset_id") != expected_dataset
        or not isinstance(payload.get("documents"), list)
    ):
        raise RoutingGateError(f"unexpected fixture manifest contract: {path}")
    documents = payload["documents"]
    expected_identity = hashlib.sha256(
        json.dumps(documents, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if payload.get("corpus_identity") != expected_identity:
        raise RoutingGateError(f"fixture corpus identity mismatch: {path}")
    for document in documents:
        if (
            not isinstance(document, dict)
            or not isinstance(document.get("id"), str)
            or not isinstance(document.get("filename"), str)
            or not isinstance(document.get("sha256"), str)
        ):
            raise RoutingGateError(f"invalid fixture document contract: {path}")
        source = root / document["filename"]
        try:
            actual_sha256 = _sha256(source)
        except OSError as exc:
            raise RoutingGateError(f"fixture source is unreadable: {source}") from exc
        if actual_sha256 != document["sha256"]:
            raise RoutingGateError(
                f"fixture digest mismatch: {document['id']}"
            )
    return payload


def _document_by_id(manifest: dict[str, Any], document_id: str) -> dict[str, Any]:
    matches = [
        value
        for value in manifest["documents"]
        if isinstance(value, dict) and value.get("id") == document_id
    ]
    if len(matches) != 1:
        raise RoutingGateError(
            f"fixture document is missing or duplicated: {document_id}"
        )
    return matches[0]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized(value: str) -> str:
    without_markup = re.sub(r"<[^>]+>", " ", value)
    return " ".join(re.findall(r"[A-Z0-9]+", without_markup.upper()))


def _contains_token(text: str, token: str) -> bool:
    return _normalized(token) in _normalized(text)


def _exception_chain(exc: BaseException) -> str:
    values: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(values) < 4:
        values.append(f"{type(current).__name__}: {str(current)[:4000]}")
        current = current.__cause__ or current.__context__
    return " <- ".join(values)


def _chunker() -> DocumentChunker:
    return DocumentChunker(
        parser_version=active_parser_version(adaptive_page_routing=True),
        chunking_version=active_chunking_version(visual_supplement_ocr=True),
    )


def _chunks_for(
    document_id: str,
    filename: str,
    source_sha256: str,
    pages: list[ParsedPage],
) -> list[ChunkDraft]:
    return _chunker().chunk(
        pages,
        document_id=uuid.uuid5(uuid.NAMESPACE_URL, f"local-rag:v6:{document_id}"),
        filename=filename,
        source_sha256=source_sha256,
    )


def _anchor_kind(chunk: ChunkDraft) -> str | None:
    pages = chunk.highlight_anchor.get("pages")
    if not isinstance(pages, list) or len(pages) != 1:
        return None
    page = pages[0]
    return page.get("kind") if isinstance(page, dict) else None


def _parser(adapter: object, work_root: Path) -> PdfParser:
    return PdfParser(
        adapter,
        meaningful_text_threshold=50,
        work_root=work_root,
        ocr_batch_size=8,
        maximum_pdf_pages=500,
        maximum_ocr_pages=128,
        external_batch_max_attempts=1,
        enable_adaptive_page_routing=True,
        enable_visual_supplement_ocr=True,
    )


async def run_calibration(
    core_root: Path,
    core_manifest: dict[str, Any],
    work_root: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    categories = {
        value.get("category")
        for value in core_manifest["documents"]
        if isinstance(value, dict)
    }
    missing_categories = REQUIRED_ROUTING_CATEGORIES.difference(categories)
    failures = [
        f"routing category is missing: {category}"
        for category in sorted(missing_categories)
    ]
    parser = _parser(_CalibrationOcrAdapter(), work_root)
    observations: list[dict[str, Any]] = []
    for document in core_manifest["documents"]:
        if not isinstance(document, dict):
            failures.append("fixture manifest contains a non-object document")
            continue
        path = core_root / str(document["filename"])
        if _sha256(path) != document.get("sha256"):
            failures.append(f"fixture digest mismatch: {document.get('id')}")
            continue
        pages = await parser.parse(path)
        actual = [page.routing_mode.value for page in pages]
        expected = document.get("expected_routing")
        if actual != expected:
            failures.append(
                f"routing mismatch for {document.get('id')}: "
                f"expected {expected}, got {actual}"
            )
        chunks = _chunks_for(
            str(document["id"]),
            str(document["filename"]),
            str(document["sha256"]),
            pages,
        )
        if not chunks:
            failures.append(f"no chunks produced for {document.get('id')}")
        for chunk in chunks:
            expected_anchor = (
                "ocr_regions"
                if chunk.parse_method is ParseMethod.OCR
                else "text_quote"
            )
            if _anchor_kind(chunk) != expected_anchor:
                failures.append(
                    f"anchor mismatch for {document.get('id')} chunk {chunk.ordinal}"
                )
        if document.get("category") == "unresolved_visual_geometry":
            assessment = pages[0].assessment
            if assessment is None or not assessment.unresolved_visual_geometry:
                failures.append("unresolved visual geometry was not retained")
        observations.append(
            {
                "id": document["id"],
                "category": document["category"],
                "pages": len(pages),
                "expected_routing": expected,
                "actual_routing": actual,
                "reason_codes": [
                    list(page.assessment.reason_codes)
                    if page.assessment is not None
                    else []
                    for page in pages
                ],
                "chunk_count": len(chunks),
                "parse_methods": sorted(
                    {chunk.parse_method.value for chunk in chunks}
                ),
                "passed": actual == expected and bool(chunks),
            }
        )
    return observations, failures


async def run_live_cases(
    *,
    executable: Path,
    core_root: Path,
    routing_root: Path,
    core_manifest: dict[str, Any],
    routing_manifest: dict[str, Any],
    work_root: Path,
) -> tuple[list[dict[str, Any]], list[tuple[str, ChunkDraft]], list[str]]:
    adapter = OcrSubprocessAdapter(
        executable,
        timeout_seconds=1800,
        pipeline_version="v1.6",
        device="cpu",
        cpu_threads=10,
    )
    parser = _parser(adapter, work_root)
    observations: list[dict[str, Any]] = []
    retained_chunks: list[tuple[str, ChunkDraft]] = []
    failures: list[str] = []
    for case in LIVE_CASES:
        manifest = core_manifest if case["dataset"] == "core" else routing_manifest
        root = core_root if case["dataset"] == "core" else routing_root
        document = _document_by_id(manifest, str(case["id"]))
        path = root / str(document["filename"])
        started = time.perf_counter()
        pages = await parser.parse(path)
        elapsed = time.perf_counter() - started
        chunks = _chunks_for(
            str(document["id"]),
            str(document["filename"]),
            str(document["sha256"]),
            pages,
        )
        retained_chunks.extend((str(document["id"]), chunk) for chunk in chunks)
        combined = "\n".join(chunk.text for chunk in chunks)
        token_checks = {
            hashlib.sha256(str(token).encode()).hexdigest(): _contains_token(
                combined, str(token)
            )
            for token in case["required_tokens"]
        }
        actual_routing = [page.routing_mode.value for page in pages]
        expected_routing = document["expected_routing"]
        parse_methods = sorted({chunk.parse_method.value for chunk in chunks})
        anchors = sorted(
            {
                anchor
                for chunk in chunks
                if (anchor := _anchor_kind(chunk)) is not None
            }
        )
        case_failures: list[str] = []
        if actual_routing != expected_routing:
            case_failures.append(
                f"expected routing {expected_routing}, got {actual_routing}"
            )
        if not all(token_checks.values()):
            case_failures.append("one or more required synthetic tokens were lost")
        if case["id"] == "hybrid-visual-table":
            if parse_methods != ["direct", "ocr"]:
                case_failures.append("hybrid did not retain direct and OCR chunks")
            if anchors != ["ocr_regions", "text_quote"]:
                case_failures.append("hybrid did not retain both anchor kinds")
            direct = [
                _normalized(chunk.text)
                for _, chunk in retained_chunks
                if chunk.filename == document["filename"]
                and chunk.parse_method is ParseMethod.DIRECT
            ]
            ocr = [
                _normalized(chunk.text)
                for _, chunk in retained_chunks
                if chunk.filename == document["filename"]
                and chunk.parse_method is ParseMethod.OCR
            ]
            if any(value and value in direct for value in ocr):
                case_failures.append("hybrid OCR duplicated authoritative prose")
        failures.extend(
            f"{case['id']}: {failure}" for failure in case_failures
        )
        observations.append(
            {
                "id": case["id"],
                "source_sha256": document["sha256"],
                "elapsed_seconds": round(elapsed, 6),
                "expected_routing": expected_routing,
                "actual_routing": actual_routing,
                "reason_codes": [
                    list(page.assessment.reason_codes)
                    if page.assessment is not None
                    else []
                    for page in pages
                ],
                "chunk_count": len(chunks),
                "chunk_content_sha256": hashlib.sha256(
                    "\n".join(
                        sorted(
                            f"{chunk.parse_method.value}:{chunk.text_sha256}"
                            for chunk in chunks
                        )
                    ).encode()
                ).hexdigest(),
                "parse_methods": parse_methods,
                "anchor_kinds": anchors,
                "required_token_hash_checks": token_checks,
                "passed": not case_failures,
            }
        )
    return observations, retained_chunks, failures


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise RoutingGateError("embedding vector has zero norm")
    return numerator / (left_norm * right_norm)


async def run_retrieval(
    chunks: list[tuple[str, ChunkDraft]],
    *,
    ollama_url: str,
    embedding_model: str,
    reranker_model: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not chunks:
        raise RoutingGateError("retrieval gate received no chunks")
    client = OllamaEmbeddingClient(ollama_url, embedding_model)
    try:
        embeddings = await client.embed([chunk.text for _, chunk in chunks])
        queries = await client.embed(
            [query for _, query, _, _ in RETRIEVAL_QUERIES]
        )
    finally:
        await client.close()
    if any(len(vector) != 1024 for vector in embeddings + queries):
        raise RoutingGateError("embedding dimension is not the fixed 1024")

    reranker = BgeReranker(reranker_model)
    observations: list[dict[str, Any]] = []
    failures: list[str] = []
    passages = [chunk.text for _, chunk in chunks]
    for (
        query_id,
        query,
        expected_source,
        expected_method,
    ), query_vector in zip(
        RETRIEVAL_QUERIES, queries, strict=True
    ):
        dense_order = sorted(
            range(len(chunks)),
            key=lambda index: _cosine(query_vector, embeddings[index]),
            reverse=True,
        )
        scores = await reranker.score(query, passages)
        rerank_order = sorted(
            range(len(chunks)),
            key=lambda index: scores[index],
            reverse=True,
        )
        dense_candidates = [
            (chunks[index][0], chunks[index][1].parse_method)
            for index in dense_order
        ]
        rerank_candidates = [
            (chunks[index][0], chunks[index][1].parse_method)
            for index in rerank_order
        ]
        expected = (expected_source, expected_method)
        dense_rank = dense_candidates.index(expected) + 1
        rerank_rank = rerank_candidates.index(expected) + 1
        passed = dense_rank == 1 and rerank_rank == 1
        if not passed:
            failures.append(
                f"retrieval {query_id}: dense rank {dense_rank}, "
                f"rerank rank {rerank_rank}"
            )
        observations.append(
            {
                "id": query_id,
                "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                "expected_source": expected_source,
                "expected_parse_method": expected_method.value,
                "dense_rank": dense_rank,
                "reranker_rank": rerank_rank,
                "dense_top_candidates": [
                    {"source": source, "parse_method": method.value}
                    for source, method in dense_candidates[:5]
                ],
                "reranker_top_candidates": [
                    {"source": source, "parse_method": method.value}
                    for source, method in rerank_candidates[:5]
                ],
                "passed": passed,
            }
        )
    return observations, failures


async def execute(arguments: argparse.Namespace) -> dict[str, Any]:
    core_root = arguments.core_root.resolve()
    routing_root = arguments.routing_root.resolve()
    core_manifest = _load_manifest(core_root, DATASET_ID)
    routing_manifest = _load_manifest(routing_root, ROUTING_DATASET_ID)
    all_failures: list[str] = []
    with tempfile.TemporaryDirectory(
        prefix="v6-routing-", dir=arguments.work_root.resolve()
    ) as temporary:
        work_root = Path(temporary)
        calibration, failures = await run_calibration(
            core_root, core_manifest, work_root
        )
        all_failures.extend(failures)
        live, chunks, failures = await run_live_cases(
            executable=arguments.executable.resolve(),
            core_root=core_root,
            routing_root=routing_root,
            core_manifest=core_manifest,
            routing_manifest=routing_manifest,
            work_root=work_root,
        )
        all_failures.extend(failures)
        retrieval, failures = await run_retrieval(
            chunks,
            ollama_url=arguments.ollama_url,
            embedding_model=arguments.embedding_model,
            reranker_model=arguments.reranker_model,
        )
        all_failures.extend(failures)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "benchmark": "v6-adaptive-routing-retrieval-core",
        "status": "qualified" if not all_failures else "rejected",
        "promotion_criteria": {
            "all_declared_routes_exact": all(
                observation["passed"] for observation in calibration
            ),
            "real_direct_scan_and_hybrid_tokens_preserved": all(
                observation["passed"] for observation in live
            ),
            "hybrid_direct_and_ocr_provenance_with_valid_anchors": all(
                observation["passed"] for observation in live
            ),
            "dense_and_cpu_reranker_expected_source_rank": (
                1 if all(observation["passed"] for observation in retrieval) else None
            ),
            "no_duplicate_hybrid_prose": not any(
                "duplicated authoritative prose" in failure
                for failure in all_failures
            ),
            "zero_failures": not all_failures,
        },
        "target": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "ocr_executable": str(arguments.executable.resolve()),
            "ocr_pipeline": "PaddleOCR-VL v1.6",
            "ocr_layout": "1 process x 10 CPU threads",
            "embedding_model": arguments.embedding_model,
            "embedding_dimensions": 1024,
            "reranker_model": arguments.reranker_model,
        },
        "versions": {
            "parser": active_parser_version(adaptive_page_routing=True),
            "chunking": active_chunking_version(visual_supplement_ocr=True),
        },
        "fixtures": {
            "core_dataset_id": core_manifest["dataset_id"],
            "core_corpus_identity": core_manifest["corpus_identity"],
            "routing_dataset_id": routing_manifest["dataset_id"],
            "routing_corpus_identity": routing_manifest["corpus_identity"],
        },
        "calibration": calibration,
        "live_cases": live,
        "retrieval": retrieval,
        "failures": all_failures,
    }


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--executable",
        required=True,
        type=Path,
        help="isolated PaddleOCR Python executable",
    )
    parser.add_argument(
        "--core-root",
        type=Path,
        default=GENERATED_ROOT / DATASET_ID,
    )
    parser.add_argument(
        "--routing-root",
        type=Path,
        default=GENERATED_ROOT / ROUTING_DATASET_ID,
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path(__file__).resolve().parent / "results",
    )
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--embedding-model", default="qwen3-embedding:0.6b")
    parser.add_argument("--reranker-model", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    arguments.work_root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        report = asyncio.run(execute(arguments))
    except Exception as exc:
        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "benchmark": "v6-adaptive-routing-retrieval-core",
            "status": "rejected",
            "failures": [_exception_chain(exc)],
        }
    report["total_elapsed_seconds"] = round(time.perf_counter() - started, 6)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "failures": len(report["failures"]),
                "output": str(arguments.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "qualified" else 1


if __name__ == "__main__":
    sys.exit(main())
