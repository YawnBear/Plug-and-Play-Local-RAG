"""Create and validate reproducible fresh-V3 benchmark manifests.

The default output is explicitly a draft. An accepted manifest is possible
only with non-empty measurements, a clean tagged source reference, pinned
container images, and explicit migration/model installation evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import stat
import subprocess
import sys
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePath
from typing import Any

from .schema_validation import SchemaValidationError, validate_schema
from .trust import (
    EvidenceError,
    canonical_json,
    decode_public_key,
    key_fingerprint,
    verify_envelope,
)

ROOT = Path(__file__).resolve().parents[2]
OWNED_ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = OWNED_ROOT / "results"
EVALUATION_PATH = OWNED_ROOT / "data" / "evaluation.json"
MANIFEST_SCHEMA = OWNED_ROOT / "schemas" / "manifest.schema.json"
SAMPLES_SCHEMA = OWNED_ROOT / "schemas" / "samples.schema.json"

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40,64}$")
TIMING_ABSOLUTE_TOLERANCE_MS = 100.0
TIMING_RELATIVE_TOLERANCE = 0.10
ALLOWED_SAMPLE_FIELDS = {
    "sample_id",
    "stage",
    "temperature",
    "queue",
    "elapsed_ms",
    "first_token_ms",
    "client_first_token_ms",
    "client_final_ms",
    "success",
    "timeout",
    "throughput_items_per_s",
    "cpu_percent",
    "ram_mb",
    "vram_mb",
    "ram_headroom_mb",
    "vram_headroom_mb",
    "queue_depth",
    "concurrency",
    "model_residency",
    "corpus_documents",
    "corpus_chunks",
    "retrieval_hit_at_20",
    "rerank_hit_at_6",
    "citation_correct",
    "abstention_correct",
    "expected_terms_correct",
    "case_id",
    "document_id",
    "document_kind",
    "fixture_document_id",
    "profile_evidence_id",
    "execution_evidence_id",
    "sample_evidence_id",
    "terminal_status",
    "parse_ms",
    "ocr_ms",
    "embedding_ms",
    "retrieval_ms",
    "rerank_ms",
    "generation_ms",
    "repetition",
}
ALLOWED_STAGES = {
    "ingest",
    "ocr",
    "embedding",
    "retrieval",
    "rerank",
    "generation",
    "api",
}
ALLOWED_TEMPERATURES = {"cold", "warm"}
ALLOWED_QUEUES = {"queue-free", "contended"}
ALLOWED_RESIDENCY = {"cold", "warm", "mixed", "unknown"}
MODEL_IDENTIFIERS = {
    "generation": "qwen3:8b",
    "embeddings": "qwen3-embedding:0.6b (1024 dimensions)",
    "reranker": "BAAI/bge-reranker-v2-m3",
    "ocr": "PaddleOCR-VL 1.6",
}
_CANARY_PATTERNS = (
    re.compile(r"CANARY_(?:SECRET|CONTENT)", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAuthorization\s*:\s*Bearer\b", re.IGNORECASE),
    re.compile(r"\bpassword\s*=", re.IGNORECASE),
)


class HarnessError(ValueError):
    """Raised for invalid fixtures, samples, evidence, or output locations."""


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_nonnegative_number(value: Any) -> bool:
    return _is_finite_number(value) and value >= 0


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _timings_agree(client_ms: float, server_ms: float) -> bool:
    difference = abs(client_ms - server_ms)
    return difference <= max(
        TIMING_ABSOLUTE_TOLERANCE_MS,
        TIMING_RELATIVE_TOLERANCE * max(client_ms, server_ms),
    )


def _validate_client_final_timing(sample: dict[str, Any]) -> None:
    client_ms = float(sample["client_final_ms"])
    server_ms = float(sample["elapsed_ms"])
    if client_ms > 60_000 or not _timings_agree(client_ms, server_ms):
        raise HarnessError("client stream timings differ from signed server metrics")


def _require_object(
    value: Any, allowed: set[str], required: set[str], name: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HarnessError(f"{name} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise HarnessError(f"{name} has unknown fields: {sorted(unknown)}")
    missing = required - set(value)
    if missing:
        raise HarnessError(f"{name} missing: {sorted(missing)}")
    return value


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"unable to read JSON: {path}") from exc


def _repo_relative(path: Path, repo_root: Path = ROOT) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise HarnessError("persisted paths must be repository-relative") from exc


def ensure_results_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = RESULTS_ROOT / candidate
    resolved = candidate.resolve()
    results = RESULTS_ROOT.resolve()
    if resolved == results or results not in resolved.parents:
        raise HarnessError(f"output must be a file inside {results}")
    return resolved


def percentile(samples: list[float], percentile_value: float) -> float | None:
    if not samples:
        return None
    if not 0 <= percentile_value <= 100:
        raise HarnessError("percentile must be between 0 and 100")
    values = sorted(float(sample) for sample in samples)
    position = (len(values) - 1) * percentile_value / 100
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return round(values[lower] + (values[upper] - values[lower]) * fraction, 6)


def _safe_free_string(value: str, name: str) -> None:
    if not value or len(value) > 512 or any(ord(char) < 32 for char in value):
        raise HarnessError(f"{name} is not a bounded printable string")
    if any(pattern.search(value) for pattern in _CANARY_PATTERNS):
        raise HarnessError(f"{name} contains a secret/content canary")


def _validate_all_strings(value: Any, name: str = "manifest") -> None:
    if isinstance(value, str):
        _safe_free_string(value, name)
    elif isinstance(value, dict):
        for key, item in value.items():
            _safe_free_string(str(key), f"{name} key")
            _validate_all_strings(item, f"{name}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_all_strings(item, f"{name}[{index}]")


@lru_cache(maxsize=1)
def _manifest_content_canaries() -> tuple[str, ...]:
    evaluation = _read_json(EVALUATION_PATH)
    canaries: list[str] = []
    for document in evaluation.get("documents", []):
        if isinstance(document, dict):
            canaries.extend(
                item
                for item in document.get("page_content", [])
                if isinstance(item, str)
            )
    canaries.extend(
        case["query"]
        for case in evaluation.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("query"), str)
    )
    return tuple(canaries)


def _reject_evaluation_content(value: Any, name: str = "manifest") -> None:
    if isinstance(value, str):
        if any(canary in value for canary in _manifest_content_canaries()):
            raise HarnessError(f"{name} contains evaluation content")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_evaluation_content(item, f"{name}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_evaluation_content(item, f"{name}[{index}]")


def _validate_samples(samples: Any) -> list[dict[str, Any]]:
    if not isinstance(samples, list):
        raise HarnessError("samples must be an array")
    checked: list[dict[str, Any]] = []
    sample_ids: set[str] = set()
    for index, supplied in enumerate(samples):
        sample = _require_object(
            supplied,
            ALLOWED_SAMPLE_FIELDS,
            {
                "sample_id",
                "stage",
                "temperature",
                "queue",
                "elapsed_ms",
                "success",
                "repetition",
            },
            f"sample {index}",
        )
        sample_id = sample["sample_id"]
        if not isinstance(sample_id, str) or not SAFE_ID.fullmatch(sample_id):
            raise HarnessError(f"sample {index} has invalid sample_id")
        if sample_id in sample_ids:
            raise HarnessError(f"duplicate sample_id: {sample_id}")
        sample_ids.add(sample_id)
        if sample["stage"] not in ALLOWED_STAGES:
            raise HarnessError(f"sample {index} has unsupported stage")
        if sample["temperature"] not in ALLOWED_TEMPERATURES:
            raise HarnessError(f"sample {index} has invalid temperature")
        if sample["queue"] not in ALLOWED_QUEUES:
            raise HarnessError(f"sample {index} has invalid queue")
        if not _is_nonnegative_number(sample["elapsed_ms"]):
            raise HarnessError(f"sample {index} has invalid elapsed_ms")
        if not isinstance(sample["success"], bool):
            raise HarnessError(f"sample {index} has invalid success")
        if (
            not isinstance(sample["repetition"], int)
            or not 1 <= sample["repetition"] <= 99
        ):
            raise HarnessError(f"sample {index} has invalid repetition")
        for field in (
            "first_token_ms",
            "client_first_token_ms",
            "client_final_ms",
            "throughput_items_per_s",
            "ram_mb",
            "vram_mb",
            "ram_headroom_mb",
            "vram_headroom_mb",
            "parse_ms",
            "ocr_ms",
            "embedding_ms",
            "retrieval_ms",
            "rerank_ms",
            "generation_ms",
        ):
            if field in sample and not _is_nonnegative_number(sample[field]):
                raise HarnessError(f"sample {index} has invalid {field}")
        for field in ("queue_depth", "corpus_documents", "corpus_chunks"):
            if field in sample and not _is_nonnegative_int(sample[field]):
                raise HarnessError(f"sample {index} has invalid {field}")
        if "cpu_percent" in sample and (
            not _is_finite_number(sample["cpu_percent"])
            or not 0 <= sample["cpu_percent"] <= 100
        ):
            raise HarnessError(f"sample {index} has invalid cpu_percent")
        if "concurrency" in sample and (
            not isinstance(sample["concurrency"], int)
            or isinstance(sample["concurrency"], bool)
            or sample["concurrency"] < 1
        ):
            raise HarnessError(f"sample {index} has invalid concurrency")
        if (
            "model_residency" in sample
            and sample["model_residency"] not in ALLOWED_RESIDENCY
        ):
            raise HarnessError(f"sample {index} has invalid model_residency")
        if sample["temperature"] == "cold" and sample.get("model_residency") not in {
            None,
            "cold",
        }:
            raise HarnessError(f"sample {index} cold residency claim is invalid")
        for field in (
            "case_id",
            "document_id",
            "profile_evidence_id",
            "execution_evidence_id",
            "sample_evidence_id",
            "fixture_document_id",
        ):
            if (
                field in sample
                and sample[field] is not None
                and (
                    not isinstance(sample[field], str)
                    or not SAFE_ID.fullmatch(sample[field])
                )
            ):
                raise HarnessError(f"sample {index} has invalid {field}")
        if "document_kind" in sample and sample["document_kind"] not in {
            "digital",
            "scanned",
        }:
            raise HarnessError(f"sample {index} has invalid document_kind")
        if "terminal_status" in sample and sample["terminal_status"] != "completed":
            raise HarnessError(f"sample {index} has invalid terminal_status")
        for field in ("retrieval_hit_at_20", "rerank_hit_at_6"):
            if field in sample and (
                not _is_finite_number(sample[field]) or not 0 <= sample[field] <= 1
            ):
                raise HarnessError(f"sample {index} has invalid {field}")
        for field in (
            "timeout",
            "citation_correct",
            "abstention_correct",
            "expected_terms_correct",
        ):
            if field in sample and not isinstance(sample[field], bool):
                raise HarnessError(f"sample {index} has invalid {field}")
        checked.append(dict(sample))
    _validate_all_strings(checked, "samples")
    return checked


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    checked = _validate_samples(samples)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for sample in checked:
        groups[(sample["stage"], sample["temperature"], sample["queue"])].append(sample)
    summaries: dict[str, Any] = {}
    for (stage, temperature, queue), rows in sorted(groups.items()):
        successful = [row for row in rows if row["success"]]
        elapsed = [float(row["elapsed_ms"]) for row in successful]
        first_token = [
            float(row["first_token_ms"])
            for row in successful
            if "first_token_ms" in row
        ]
        summaries[f"{stage}|{temperature}|{queue}"] = {
            "stage": stage,
            "temperature": temperature,
            "queue": queue,
            "count": len(rows),
            "success_count": len(successful),
            "timeout_count": sum(bool(row.get("timeout", False)) for row in rows),
            "elapsed_ms": {f"p{p}": percentile(elapsed, p) for p in (50, 95, 99)},
            "first_token_ms": (
                {f"p{p}": percentile(first_token, p) for p in (50, 95, 99)}
                if first_token
                else None
            ),
            "throughput_items_per_s": _metric_percentiles(
                successful, "throughput_items_per_s"
            ),
            "quality": _quality_summary(successful),
        }
    return summaries


def _metric_percentiles(
    rows: list[dict[str, Any]], field: str
) -> dict[str, float | None] | None:
    values = [float(row[field]) for row in rows if field in row]
    return {f"p{p}": percentile(values, p) for p in (50, 95, 99)} if values else None


def _quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in ("retrieval_hit_at_20", "rerank_hit_at_6"):
        values = [float(row[field]) for row in rows if field in row]
        if values:
            result[field] = {
                "count": len(values),
                "mean": round(sum(values) / len(values), 6),
            }
    for field in ("citation_correct", "abstention_correct", "expected_terms_correct"):
        values = [row[field] for row in rows if field in row]
        if values:
            result[field] = {
                "count": len(values),
                "correct": sum(values),
                "accuracy": round(sum(values) / len(values), 6),
            }
    return result


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _source_files(repo_root: Path) -> list[Path]:
    root = repo_root / "benchmarks" / "v3"
    excluded = {
        "results",
        "generated",
        "private",
        "evidence",
        "drafts",
        "runtime",
        "__pycache__",
        ".pytest_cache",
    }
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and not excluded.intersection(path.relative_to(root).parts)
        ),
        key=lambda path: path.relative_to(repo_root).as_posix(),
    )


def _source_content_identity(repo_root: Path) -> str:
    digest = hashlib.sha256()
    for path in _source_files(repo_root):
        relative = path.relative_to(repo_root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest()


def _git_source(repo_root: Path, reference_tag: str | None = None) -> dict[str, Any]:
    revision = _git(repo_root, "rev-parse", "HEAD")
    status = _git(repo_root, "status", "--porcelain")
    tag_object = None
    if reference_tag is not None:
        if not SAFE_ID.fullmatch(reference_tag):
            raise HarnessError("reference tag is invalid")
        tag_ref = f"refs/tags/{reference_tag}"
        if (
            _git(repo_root, "cat-file", "-t", tag_ref) != "tag"
            or _git(repo_root, "rev-parse", f"{tag_ref}^{{commit}}") != revision
        ):
            raise HarnessError(
                "reference tag must be an annotated tag for the current revision"
            )
        tag_object = _git(repo_root, "rev-parse", tag_ref)
    return {
        "revision": revision,
        "dirty": None if status is None else bool(status),
        "changed_file_count": None if status is None else len(status.splitlines()),
        "content_identity": _source_content_identity(repo_root),
        "reference_tag": reference_tag,
        "reference_tag_object": tag_object,
    }


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _locked_dependencies(lock_path: Path) -> list[dict[str, str]]:
    if not lock_path.exists():
        return []
    text = lock_path.read_text(encoding="utf-8", errors="replace")
    return [
        {"name": name, "version": version}
        for name, version in re.findall(
            r"(?ms)^name = \"([^\"]+)\"\s*$.*?^version = \"([^\"]+)\"\s*$", text
        )
    ]


def _container_images(compose_path: Path) -> list[dict[str, str | None]]:
    if not compose_path.exists():
        return []
    refs = re.findall(
        r"^\s*image:\s*([^\s#]+)",
        compose_path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    return [
        {
            "reference": ref,
            "digest": "sha256:" + ref.split("@sha256:", 1)[1]
            if "@sha256:" in ref
            else None,
        }
        for ref in refs
    ]


def _alembic_graph(repo_root: Path) -> list[dict[str, str | None]]:
    versions = repo_root / "apps" / "api" / "alembic" / "versions"
    graph: list[dict[str, str | None]] = []
    for path in sorted(versions.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.search(
            r"^revision(?:\s*:\s*[^=]+)?\s*=\s*['\"]([^'\"]+)",
            text,
            re.MULTILINE,
        )
        if not match:
            continue
        down_match = re.search(
            r"^down_revision(?:\s*:\s*[^=]+)?\s*=\s*([^\n]+)",
            text,
            re.MULTILINE,
        )
        down_revision = down_match.group(1).strip().strip("'\"") if down_match else None
        graph.append(
            {
                "revision": match.group(1),
                "down_revision": None if down_revision == "None" else down_revision,
            }
        )
    return graph


def _alembic_head(repo_root: Path) -> list[str]:
    graph = _alembic_graph(repo_root)
    revisions = {entry["revision"] for entry in graph}
    referenced = {entry["down_revision"] for entry in graph if entry["down_revision"]}
    return sorted(revisions - referenced)


def _machine() -> dict[str, Any]:
    return {
        "os": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor() or "unspecified",
        "cpu_count": os.cpu_count() or 0,
        "power_mode": os.environ.get("V3_POWER_MODE", "unspecified"),
    }


def load_evaluation(path: Path = EVALUATION_PATH) -> dict[str, Any]:
    value = _read_json(path)
    if not isinstance(value, dict):
        raise HarnessError("evaluation root must be an object")
    documents = value.get("documents")
    cases = value.get("cases")
    if not isinstance(documents, list) or not documents or not isinstance(cases, list):
        raise HarnessError("evaluation must contain non-empty documents and cases")
    kinds = Counter(
        document.get("kind") for document in documents if isinstance(document, dict)
    )
    if kinds != Counter({"digital": 8, "scanned": 2}):
        raise HarnessError("evaluation must contain exactly 8 digital and 2 scanned")
    document_ids: set[str] = set()
    document_pages: dict[str, int] = {}
    for document in documents:
        required = {"id", "kind", "pages", "page_content"}
        if not isinstance(document, dict) or set(document) != required:
            raise HarnessError("evaluation document shape is invalid")
        if (
            not isinstance(document["id"], str)
            or not SAFE_ID.fullmatch(document["id"])
            or document["id"] in document_ids
            or not isinstance(document["pages"], int)
            or document["pages"] < 1
            or not isinstance(document["page_content"], list)
            or len(document["page_content"]) != document["pages"]
            or not all(
                isinstance(item, str) and item for item in document["page_content"]
            )
        ):
            raise HarnessError("evaluation document content is invalid")
        document_ids.add(document["id"])
        document_pages[document["id"]] = document["pages"]
    required_categories = {
        "supported",
        "unsupported",
        "exact-term",
        "unicode-multilingual-safety",
        "adversarial",
        "citation",
        "abstention",
    }
    categories: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "id",
            "category",
            "query",
            "expected_sources",
            "expected_terms",
            "expect_abstention",
        }:
            raise HarnessError("evaluation case shape is invalid")
        categories.add(case["category"])
        if (
            not isinstance(case["id"], str)
            or not SAFE_ID.fullmatch(case["id"])
            or not isinstance(case["category"], str)
            or not isinstance(case["query"], str)
            or not isinstance(case["expected_terms"], list)
            or not all(
                isinstance(term, str) and term for term in case["expected_terms"]
            )
        ):
            raise HarnessError("evaluation case content is invalid")
        if not isinstance(case["expected_sources"], list):
            raise HarnessError("evaluation expected_sources must be an array")
        expected_ids = [
            source.get("id")
            for source in case["expected_sources"]
            if isinstance(source, dict)
        ]
        if len(expected_ids) != len(set(expected_ids)):
            raise HarnessError("evaluation expected documents must be unique")
        for source in case["expected_sources"]:
            if (
                not isinstance(source, dict)
                or set(source) != {"id", "pages"}
                or source["id"] not in document_ids
                or not isinstance(source["pages"], list)
                or not source["pages"]
                or not all(
                    isinstance(page, int) and page > 0 for page in source["pages"]
                )
                or any(
                    page > document_pages.get(source["id"], 0)
                    for page in source["pages"]
                )
            ):
                raise HarnessError("evaluation expected source is invalid")
        if not isinstance(case["expect_abstention"], bool):
            raise HarnessError("evaluation abstention expectation is invalid")
    if not required_categories.issubset(categories):
        raise HarnessError("evaluation is missing required categories")
    return value


def create_manifest(
    repo_root: Path = ROOT,
    evaluation_path: Path = EVALUATION_PATH,
    samples: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    command: str = "python -m benchmarks.v3.harness create-manifest",
    manifest_kind: str = "draft",
    alembic_current: str | None = None,
    model_evidence: dict[str, str] | None = None,
    reference_tag: str | None = None,
) -> dict[str, Any]:
    evaluation = load_evaluation(evaluation_path)
    raw_samples = _validate_samples(samples or [])
    dataset_path = _repo_relative(evaluation_path, repo_root)
    counts = Counter(document["kind"] for document in evaluation["documents"])
    evidence = model_evidence or {}
    manifest = {
        "schema_version": 2,
        "manifest_kind": manifest_kind,
        "run": {
            "id": run_id or str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
            "command": command,
        },
        "source": _git_source(repo_root, reference_tag),
        "alembic": {
            "current_revision": alembic_current,
            "head_revision": _alembic_head(repo_root),
            "graph": _alembic_graph(repo_root),
            "evidence_command": "uv --directory apps/api run alembic current",
        },
        "dependencies": {
            "lock_file": "apps/api/uv.lock",
            "lock_sha256": _sha256(repo_root / "apps" / "api" / "uv.lock"),
            "packages": _locked_dependencies(repo_root / "apps" / "api" / "uv.lock"),
        },
        "containers": {
            "compose_file": "compose.yaml",
            "images": _container_images(repo_root / "compose.yaml"),
        },
        "models": {
            role: {
                "identifier": identifier,
                "installed_evidence": evidence.get(role, "not-collected"),
            }
            for role, identifier in MODEL_IDENTIFIERS.items()
        },
        "machine": _machine(),
        "dataset": {
            "identity": _sha256(evaluation_path),
            "path": dataset_path,
            "document_count": len(evaluation["documents"]),
            "digital_count": counts["digital"],
            "scanned_count": counts["scanned"],
            "case_count": len(evaluation["cases"]),
        },
        "methodology": {
            "temperature_labels": ["cold", "warm"],
            "queue_labels": ["queue-free", "contended"],
            "model_residency": {
                "generation": "mixed",
                "embeddings": "mixed",
                "reranker": "mixed",
                "ocr": "mixed",
            },
            "concurrency": {
                "chat_active": 1,
                "chat_queued": 1,
                "ingest_workers": 1,
            },
            "fixed_corpus": dataset_path,
            "run_procedure": "fresh-v3-synthetic-acceptance-v1",
            "repetitions": 5,
            "warmups": 1,
            "timeout_ms": 120000,
            "percentile_method": "linear-n-minus-one",
        },
        "samples": raw_samples,
        "summaries": summarize_samples(raw_samples),
        "secrets_policy": "numeric-metrics-and-identifiers-only",
        "benchmark_evidence": None,
        "retained_evidence": None,
    }
    validate_manifest(manifest)
    return manifest


def _safe_persisted_path(value: Any, name: str) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or Path(value).is_absolute()
        or re.match(r"^[A-Za-z]:", value)
        or "\\" in value
        or ".." in Path(value).parts
    ):
        raise HarnessError(f"{name} must be a repository-relative POSIX path")
    resolved = (ROOT / value).resolve()
    if ROOT.resolve() not in resolved.parents:
        raise HarnessError(f"{name} escapes the repository")
    return resolved


def _verify_artifact(entry: Any, name: str) -> tuple[str, Path]:
    artifact_hash, path, _data = _read_verified_artifact(entry, name)
    return artifact_hash, path


def _read_verified_artifact(entry: Any, name: str) -> tuple[str, Path, bytes]:
    """Read and hash one retained artifact through the same open handle."""
    checked = _require_object(entry, {"path", "sha256"}, {"path", "sha256"}, name)
    path = _safe_persisted_path(checked["path"], f"{name}.path")
    if not isinstance(checked["sha256"], str) or not SHA256.fullmatch(
        checked["sha256"]
    ):
        raise HarnessError(f"{name}.sha256 is invalid")
    try:
        with path.open("rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise HarnessError(f"{name} retained artifact is unavailable") from exc
    actual_hash = "sha256:" + hashlib.sha256(data).hexdigest()
    if actual_hash != checked["sha256"]:
        raise HarnessError(f"{name} retained artifact hash does not match")
    return actual_hash, path, data


_RETAINED_COMMANDS = {
    "alembic": "uv --directory apps/api run alembic current",
    "containers": "docker compose images --format json",
    "schema": "uv --directory apps/api run python -m app.maintenance_cli verify-schema",
    "server_build": (
        "uv --directory apps/api run python -m app.maintenance_cli verify-server-build"
    ),
    "generation": "ollama show qwen3:8b",
    "embeddings": "ollama show qwen3-embedding:0.6b",
    "reranker": (
        "uv --directory apps/api run python -m app.maintenance_cli verify-reranker"
    ),
    "ocr": "paddleocr --version",
}


def _verify_verifier_report(
    entry: Any,
    name: str,
    *,
    subject: str,
    expected_version: str | None = None,
) -> tuple[str, dict[str, Any]]:
    report_hash, _report_path, report_bytes = _read_verified_artifact(entry, name)
    try:
        report = json.loads(report_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessError(f"{name} verifier report is invalid") from exc
    required = {
        "schema_version",
        "kind",
        "verifier",
        "subject",
        "command",
        "exit_code",
        "version",
        "digest",
        "output",
    }
    if (
        not isinstance(report, dict)
        or set(report) != required
        or report["schema_version"] != 1
        or report["kind"] != "runtime-verification"
        or report["verifier"] != "v3-isolated-runtime-verifier-v1"
        or report["subject"] != subject
        or report["command"] != _RETAINED_COMMANDS[subject]
        or report["exit_code"] != 0
        or not isinstance(report["version"], str)
        or not report["version"]
        or (expected_version is not None and report["version"] != expected_version)
        or not isinstance(report["digest"], str)
        or not SHA256.fullmatch(report["digest"])
    ):
        raise HarnessError(f"{name} verifier report is invalid")
    output_hash, _output_path, output_bytes = _read_verified_artifact(
        report["output"], f"{name}.output"
    )
    if output_hash != report["output"]["sha256"]:
        raise HarnessError(f"{name} output evidence is stale")
    report["__retained_output_bytes"] = output_bytes
    return report_hash, report


def _verify_accepted_fixture(dataset: dict[str, Any]) -> None:
    required = {"fixture_manifest_path", "fixture_identity", "fixture_hashes"}
    if not required.issubset(dataset):
        raise HarnessError("accepted manifest lacks complete fixture identity")
    _safe_persisted_path(
        dataset["fixture_manifest_path"], "dataset.fixture_manifest_path"
    )
    fixture_path = ROOT / dataset["fixture_manifest_path"]
    generated_root = (OWNED_ROOT / "data" / "generated").resolve()
    current = ROOT
    for part in PurePath(dataset["fixture_manifest_path"]).parts:
        current = current / part
        attributes = (
            getattr(current.stat(follow_symlinks=False), "st_file_attributes", 0)
            if current.exists()
            else 0
        )
        if current.is_symlink() or attributes & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise HarnessError("accepted fixture path contains a link or reparse point")
    try:
        resolved = fixture_path.resolve(strict=True)
    except OSError as exc:
        raise HarnessError("accepted fixture manifest is unavailable") from exc
    if (
        generated_root not in resolved.parents
        or resolved.name != "fixture-manifest.json"
        or resolved.is_symlink()
        or not resolved.is_file()
    ):
        raise HarnessError("accepted fixture manifest path is unsafe")
    fixture = _read_json(resolved)
    approved = _read_json(OWNED_ROOT / "data" / "approved-fixture-hashes.json")
    if (
        not isinstance(fixture, dict)
        or fixture.get("corpus_identity") != dataset["fixture_identity"]
        or fixture.get("corpus_identity") != approved.get("corpus_identity")
        or fixture.get("evaluation_identity")
        != dataset["identity"].removeprefix("sha256:")
        or fixture.get("evaluation_identity") != approved.get("evaluation_identity")
        or fixture.get("dataset_id") != approved.get("dataset_id")
    ):
        raise HarnessError("accepted fixture identity is not canonical")
    documents = fixture.get("documents")
    if not isinstance(documents, list):
        raise HarnessError("accepted fixture document records are invalid")
    expected_hashes = approved.get("documents")
    if (
        not isinstance(expected_hashes, dict)
        or dataset["fixture_hashes"] != expected_hashes
        or {item.get("id") for item in documents if isinstance(item, dict)}
        != set(expected_hashes)
    ):
        raise HarnessError("accepted fixture hashes are incomplete")
    for document in documents:
        if (
            not isinstance(document, dict)
            or document.get("sha256") != expected_hashes.get(document.get("id"))
            or not isinstance(document.get("filename"), str)
            or PurePath(document["filename"]).name != document["filename"]
        ):
            raise HarnessError("accepted fixture document identity is invalid")
        pdf = resolved.parent / document["filename"]
        try:
            attributes = getattr(
                pdf.stat(follow_symlinks=False), "st_file_attributes", 0
            )
            pdf_resolved = pdf.resolve(strict=True)
        except OSError as exc:
            raise HarnessError("accepted fixture PDF is unavailable") from exc
        if pdf.is_symlink() or attributes & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise HarnessError("accepted fixture PDF path is unsafe")
        if (
            pdf_resolved.parent != resolved.parent
            or pdf_resolved.is_symlink()
            or not pdf_resolved.is_file()
            or _sha256(pdf_resolved) != "sha256:" + document["sha256"]
        ):
            raise HarnessError("accepted fixture PDF bytes are not canonical")


def _verify_retained_evidence(value: Any, manifest: dict[str, Any]) -> dict[str, str]:
    retained = _require_object(
        value,
        {"alembic", "models", "containers", "schema", "server_build"},
        {"alembic", "models", "containers", "schema", "server_build"},
        "retained_evidence",
    )
    alembic_hash, alembic = _verify_verifier_report(
        retained["alembic"],
        "retained_evidence.alembic",
        subject="alembic",
        expected_version="0001_v3_baseline",
    )
    if manifest["alembic"]["current_revision"] != "0001_v3_baseline":
        raise HarnessError("retained Alembic evidence is not the fresh V3 baseline")
    models = _require_object(
        retained["models"],
        set(MODEL_IDENTIFIERS),
        set(MODEL_IDENTIFIERS),
        "retained_evidence.models",
    )
    for role, entry in models.items():
        evidence_hash, report = _verify_verifier_report(
            entry,
            f"retained_evidence.models.{role}",
            subject=role,
            expected_version=MODEL_IDENTIFIERS[role],
        )
        if manifest["models"][role]["installed_evidence"] != (
            "evidence-" + evidence_hash
        ):
            raise HarnessError("model evidence does not bind its retained artifact")
    container_hash, containers = _verify_verifier_report(
        retained["containers"],
        "retained_evidence.containers",
        subject="containers",
    )
    expected_container_digest = (
        "sha256:"
        + hashlib.sha256(canonical_json(manifest["containers"]["images"])).hexdigest()
    )
    if containers["digest"] != expected_container_digest:
        raise HarnessError("retained container inventory digest is stale")
    schema_hash, schema = _verify_verifier_report(
        retained["schema"], "retained_evidence.schema", subject="schema"
    )
    migration_hash = _sha256(
        ROOT / "apps" / "api" / "alembic" / "versions" / "0001_v3_baseline.py"
    )
    if schema["digest"] != migration_hash:
        raise HarnessError("retained schema evidence does not match the root migration")
    server_hash, server = _verify_verifier_report(
        retained["server_build"],
        "retained_evidence.server_build",
        subject="server_build",
    )
    if not all((alembic_hash, container_hash, schema_hash, server_hash, alembic)):
        raise HarnessError("retained runtime evidence is incomplete")
    return {
        "container_inventory": container_hash,
        "schema_evidence": schema_hash,
        "server_evidence": server_hash,
        "server_artifact": server["digest"],
    }


def _verify_benchmark_evidence(
    value: Any, manifest: dict[str, Any], retained_hashes: dict[str, str]
) -> None:
    evidence = _require_object(
        value,
        {
            "ed25519_public_key",
            "fingerprint",
            "pre_attestation",
            "post_attestation",
            "profile_attestations",
            "executions",
            "sample_attestations",
        },
        {
            "ed25519_public_key",
            "fingerprint",
            "pre_attestation",
            "post_attestation",
            "profile_attestations",
            "executions",
            "sample_attestations",
        },
        "benchmark_evidence",
    )
    try:
        public_key = decode_public_key(evidence["ed25519_public_key"])
    except (EvidenceError, TypeError) as exc:
        raise HarnessError("benchmark public key is invalid") from exc
    fingerprint = key_fingerprint(public_key)
    if evidence["fingerprint"] != fingerprint:
        raise HarnessError("benchmark public key fingerprint is invalid")
    trust = _read_json(OWNED_ROOT / "data" / "benchmark-trust.json")
    if (
        not isinstance(trust, dict)
        or trust.get("status") != "configured"
        or trust.get("ed25519_public_key") != evidence["ed25519_public_key"]
        or trust.get("fingerprint") != fingerprint
    ):
        raise HarnessError("accepted manifest key is not pinned in benchmark trust")
    try:
        run_time = datetime.fromisoformat(
            manifest["run"]["created_at"].replace("Z", "+00:00")
        )
    except (AttributeError, ValueError) as exc:
        raise HarnessError("manifest run timestamp is invalid") from exc

    common_fields = {
        "kind",
        "evidence_id",
        "issued_at",
        "expires_at",
        "run_id",
        "nonce",
        "deployment_id",
        "store_id",
        "store_mode",
        "namespace",
        "corpus_identity",
        "fixture_identity",
        "sequence",
        "source_revision",
        "source_content_identity",
        "runtime_artifact_hashes",
    }

    def verified(envelope: Any, kind: str, extra_fields: set[str]) -> dict[str, Any]:
        try:
            payload = verify_envelope(
                envelope, public_key, expected_kind=kind, now=run_time
            )
        except EvidenceError as exc:
            raise HarnessError(f"signed {kind} evidence is invalid") from exc
        if set(payload) != common_fields | extra_fields:
            raise HarnessError(f"signed {kind} payload shape is invalid")
        return payload

    pre = verified(
        evidence["pre_attestation"],
        "pre",
        {
            "namespace_state",
            "namespace_owner_run_id",
            "owned_document_ids",
            "measurement_profiles",
        },
    )
    post = verified(
        evidence["post_attestation"],
        "post",
        {"namespace_state", "namespace_owner_run_id", "owned_document_ids"},
    )
    common_pairs = {
        "run_id": manifest["run"]["id"],
        "corpus_identity": manifest["dataset"]["fixture_identity"],
        "fixture_identity": manifest["dataset"]["identity"].removeprefix("sha256:"),
        "nonce": pre.get("nonce"),
        "deployment_id": pre.get("deployment_id"),
        "store_id": pre.get("store_id"),
        "store_mode": "isolated-benchmark",
        "namespace": pre.get("namespace"),
        "source_revision": manifest["source"]["revision"],
        "source_content_identity": manifest["source"]["content_identity"],
        "runtime_artifact_hashes": pre.get("runtime_artifact_hashes"),
    }
    retained = manifest["retained_evidence"]
    expected_model_inventory = (
        "sha256:"
        + hashlib.sha256(
            canonical_json(
                {
                    role: retained["models"][role]["sha256"]
                    for role in sorted(MODEL_IDENTIFIERS)
                }
            )
        ).hexdigest()
    )
    runtime_hashes = pre.get("runtime_artifact_hashes")
    if (
        not isinstance(runtime_hashes, dict)
        or set(runtime_hashes)
        != {
            "dependency_lock",
            "container_inventory",
            "model_inventory",
            "server_artifact",
            "schema_evidence",
            "server_evidence",
        }
        or runtime_hashes["dependency_lock"] != manifest["dependencies"]["lock_sha256"]
        or runtime_hashes["container_inventory"]
        != retained_hashes["container_inventory"]
        or runtime_hashes["model_inventory"] != expected_model_inventory
        or runtime_hashes["schema_evidence"] != retained_hashes["schema_evidence"]
        or runtime_hashes["server_evidence"] != retained_hashes["server_evidence"]
        or runtime_hashes["server_artifact"] != retained_hashes["server_artifact"]
        or any(not SHA256.fullmatch(item) for item in runtime_hashes.values())
    ):
        raise HarnessError("signed runtime artifact hashes are incomplete or stale")
    for payload in (pre, post):
        if any(payload.get(key) != expected for key, expected in common_pairs.items()):
            raise HarnessError("signed attestation is not bound to this manifest")
    if (
        not isinstance(pre.get("sequence"), int)
        or isinstance(pre.get("sequence"), bool)
        or not isinstance(post.get("sequence"), int)
        or isinstance(post.get("sequence"), bool)
        or pre["sequence"] >= post["sequence"]
    ):
        raise HarnessError("signed pre/post evidence ordering is invalid")
    if (
        pre.get("namespace_state") != "empty"
        or pre.get("namespace_owner_run_id") is not None
        or pre.get("owned_document_ids") != []
        or post.get("namespace_state") != "empty"
        or post.get("namespace_owner_run_id") is not None
        or post.get("owned_document_ids") != []
    ):
        raise HarnessError("signed post-attestation is not empty")
    required_profiles = {
        ("cold", "queue-free"),
        ("cold", "contended"),
        ("warm", "queue-free"),
        ("warm", "contended"),
    }
    if {
        (item.get("temperature"), item.get("queue"))
        for item in pre.get("measurement_profiles", [])
        if isinstance(item, dict) and set(item) == {"temperature", "queue"}
    } != required_profiles:
        raise HarnessError("pre-attestation does not bind every required profile")
    profile_envelopes = evidence["profile_attestations"]
    execution_envelopes = evidence["executions"]
    sample_envelopes = evidence["sample_attestations"]
    if (
        not isinstance(profile_envelopes, list)
        or not isinstance(execution_envelopes, list)
        or not isinstance(sample_envelopes, list)
    ):
        raise HarnessError("signed evidence collections must be arrays")
    profiles: dict[str, dict[str, Any]] = {}
    for envelope in profile_envelopes:
        payload = verified(
            envelope,
            "profile",
            {
                "profile",
                "applied",
                "profile_token",
                "active_requests",
                "queued_requests",
                "correlation",
            },
        )
        evidence_id = payload.get("evidence_id")
        if (
            not isinstance(evidence_id, str)
            or evidence_id in profiles
            or payload.get("applied") is not True
            or (
                payload.get("active_requests"),
                payload.get("queued_requests"),
            )
            != (
                (1, 1)
                if payload.get("profile", {}).get("queue") == "contended"
                else (1, 0)
            )
            or any(
                payload.get(key) != expected for key, expected in common_pairs.items()
            )
        ):
            raise HarnessError("profile evidence is invalid or replayed")
        profiles[evidence_id] = payload
    executions: dict[str, dict[str, Any]] = {}
    for envelope in execution_envelopes:
        payload = verified(
            envelope,
            "execution",
            {
                "sample_id",
                "stage",
                "case_id",
                "document_id",
                "fixture_document_id",
                "profile",
                "repetition",
                "profile_evidence_id",
                "success",
                "terminal_status",
                "metrics",
                "retrieval_candidates",
                "reranked_sources",
                "workload",
                "correlation",
                "upload",
            },
        )
        evidence_id = payload.get("evidence_id")
        if (
            not isinstance(evidence_id, str)
            or evidence_id in executions
            or payload.get("success") is not True
            or payload.get("terminal_status") != "completed"
            or any(
                payload.get(key) != expected for key, expected in common_pairs.items()
            )
        ):
            raise HarnessError("execution evidence is invalid, failed, or replayed")
        executions[evidence_id] = payload
    if len(executions) != len(manifest["samples"]):
        raise HarnessError("every sample must have exactly one execution evidence")
    sealed_samples: dict[str, dict[str, Any]] = {}
    for envelope in sample_envelopes:
        payload = verified(
            envelope,
            "sample",
            {"sample_id", "execution_evidence_id", "sample"},
        )
        evidence_id = payload.get("evidence_id")
        if (
            not isinstance(evidence_id, str)
            or evidence_id in sealed_samples
            or any(
                payload.get(key) != expected for key, expected in common_pairs.items()
            )
        ):
            raise HarnessError("sample acknowledgement is invalid or replayed")
        sealed_samples[evidence_id] = payload
    if len(sealed_samples) != len(manifest["samples"]):
        raise HarnessError("every sample must have a signed sample acknowledgement")
    evaluation = load_evaluation()
    cases = {case["id"]: case for case in evaluation["cases"]}
    fixture_manifest = _read_json(ROOT / manifest["dataset"]["fixture_manifest_path"])
    fixture_documents = {
        item["id"]: item
        for item in fixture_manifest["documents"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    fixture_document_ids = {
        sample["fixture_document_id"]: sample["document_id"]
        for sample in manifest["samples"]
        if sample["stage"] == "ingest" and sample["sample_id"].startswith("ingest-syn-")
    }
    if set(fixture_document_ids) != {
        document["id"] for document in evaluation["documents"]
    }:
        raise HarnessError("signed fixture-to-document identity is incomplete")

    def evidence_pairs(
        items: Any, maximum: int, *, allowed_document_ids: set[str]
    ) -> set[tuple[str, int]]:
        if not isinstance(items, list) or len(items) > maximum:
            raise HarnessError("signed retrieval evidence cardinality is invalid")
        pairs: set[tuple[str, int]] = set()
        for item in items:
            if (
                not isinstance(item, dict)
                or set(item) != {"document_id", "page_start", "page_end"}
                or not isinstance(item["document_id"], str)
                or not isinstance(item["page_start"], int)
                or isinstance(item["page_start"], bool)
                or not isinstance(item["page_end"], int)
                or isinstance(item["page_end"], bool)
                or not 0 < item["page_start"] <= item["page_end"]
                or item["document_id"] not in allowed_document_ids
            ):
                raise HarnessError("signed retrieval source shape is invalid")
            pairs.update(
                (item["document_id"], page)
                for page in range(item["page_start"], item["page_end"] + 1)
            )
        return pairs

    for sample in manifest["samples"]:
        execution = executions.get(sample.get("execution_evidence_id"))
        profile = profiles.get(sample.get("profile_evidence_id"))
        if execution is None or profile is None:
            raise HarnessError("sample evidence reference is missing")
        sealed = sealed_samples.get(sample.get("sample_evidence_id"))
        unsigned_sample = {
            key: item for key, item in sample.items() if key != "sample_evidence_id"
        }
        if (
            sealed is None
            or sealed.get("sample_id") != sample["sample_id"]
            or sealed.get("execution_evidence_id") != sample["execution_evidence_id"]
            or sealed.get("sample") != unsigned_sample
        ):
            raise HarnessError("sample metrics are not signed server observations")
        expected_profile = {
            "temperature": sample["temperature"],
            "queue": sample["queue"],
        }
        correlation = execution.get("correlation")
        if (
            sample.get("success") is not True
            or sample.get("terminal_status") != "completed"
            or execution.get("sample_id") != sample["sample_id"]
            or execution.get("stage") != sample["stage"]
            or execution.get("case_id") != sample.get("case_id")
            or execution.get("document_id") != sample.get("document_id")
            or execution.get("fixture_document_id") != sample.get("fixture_document_id")
            or execution.get("profile") != expected_profile
            or execution.get("repetition") != sample["repetition"]
            or execution.get("profile_evidence_id") != sample["profile_evidence_id"]
            or profile.get("profile") != expected_profile
            or not isinstance(correlation, dict)
            or set(correlation)
            != {
                "sample_id",
                "nonce",
                "profile_id",
                "evidence_id",
                "execution_id",
            }
            or correlation.get("sample_id") != sample["sample_id"]
            or correlation.get("nonce") != pre["nonce"]
            or profile.get("correlation") != correlation
            or any(
                not isinstance(item, str) or not SAFE_ID.fullmatch(item)
                for item in correlation.values()
            )
            or not (
                pre["sequence"]
                < profile["sequence"]
                < execution["sequence"]
                < sealed["sequence"]
                < post["sequence"]
            )
        ):
            raise HarnessError("sample fields are not bound to signed evidence")
        metrics = execution.get("metrics")
        required_metrics = {
            "cpu_percent",
            "ram_mb",
            "vram_mb",
            "ram_headroom_mb",
            "vram_headroom_mb",
            "queue_depth",
            "concurrency",
            "corpus_chunks",
            "elapsed_ms",
        }
        if (
            not isinstance(metrics, dict)
            or any(sample.get(key) != metric for key, metric in metrics.items())
            or not required_metrics.issubset(metrics)
        ):
            raise HarnessError("sample metrics differ from signed execution evidence")
        if sample["stage"] == "generation":
            if "client_final_ms" not in sample or (
                "first_token_ms" in sample
                and (
                    "client_first_token_ms" not in sample
                    or not _timings_agree(
                        float(sample["client_first_token_ms"]),
                        float(sample["first_token_ms"]),
                    )
                )
            ):
                raise HarnessError(
                    "client stream timings differ from signed server metrics"
                )
            _validate_client_final_timing(sample)
        elif execution.get("upload") is not None and sample["stage"] != "ingest":
            raise HarnessError("non-ingestion sample contains upload evidence")
        if sample["stage"] == "ingest" and sample["sample_id"].startswith(
            "ingest-syn-"
        ):
            upload = execution.get("upload")
            fixture_id = sample.get("fixture_document_id")
            fixture_hash = manifest["dataset"]["fixture_hashes"].get(fixture_id)
            fixture_document = fixture_documents.get(fixture_id)
            if fixture_document is None:
                raise HarnessError("signed upload fixture identity is unknown")
            fixture_pdf = (
                ROOT / manifest["dataset"]["fixture_manifest_path"]
            ).parent / fixture_document["filename"]
            boundary = "----V3SyntheticBoundary"
            prefix = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{fixture_document["filename"]}"\r\n'
                "Content-Type: application/pdf\r\n\r\n"
            ).encode()
            upload_body = (
                prefix + fixture_pdf.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
            )
            expected_upload_hash = "sha256:" + hashlib.sha256(upload_body).hexdigest()
            if (
                not isinstance(upload, dict)
                or set(upload) != {"payload_sha256", "fixture_sha256"}
                or not all(
                    isinstance(item, str) and SHA256.fullmatch(item)
                    for item in upload.values()
                )
                or upload["fixture_sha256"] != "sha256:" + str(fixture_hash)
                or upload["payload_sha256"] != expected_upload_hash
            ):
                raise HarnessError("signed upload evidence is incomplete or stale")
        if (
            sample["temperature"] == "cold" and sample.get("model_residency") != "cold"
        ) or (
            sample["queue"] == "contended"
            and (sample["concurrency"] < 2 or sample["queue_depth"] < 1)
        ):
            raise HarnessError("sample residency or contention evidence is false")
        workload = execution.get("workload")
        measured_workload = (
            "ingestion" if sample["stage"] == "ingest" else sample["stage"]
        )
        if (
            not isinstance(workload, dict)
            or set(workload)
            != {"synchronized", "active_workloads", "resource_observed"}
            or workload.get("resource_observed") is not True
            or measured_workload not in workload.get("active_workloads", [])
            or sample["ram_headroom_mb"] < 4096
            or sample["vram_headroom_mb"] < 1024
            or (
                sample["queue"] == "contended"
                and (
                    workload.get("synchronized") is not True
                    or not {"generation", "ingestion"}.issubset(
                        workload.get("active_workloads", [])
                    )
                )
            )
        ):
            raise HarnessError("sample lacks signed workload/resource safety evidence")
        adopted_document_ids = set(fixture_document_ids.values())
        evidence_pairs(
            execution.get("retrieval_candidates"),
            20,
            allowed_document_ids=adopted_document_ids,
        )
        evidence_pairs(
            execution.get("reranked_sources"),
            6,
            allowed_document_ids=adopted_document_ids,
        )
        if sample["stage"] == "generation" and any(
            sample.get(field) is not True
            for field in (
                "citation_correct",
                "abstention_correct",
                "expected_terms_correct",
            )
        ):
            raise HarnessError("accepted query sample failed a correctness metric")
        if sample["stage"] == "generation":
            case = cases.get(sample.get("case_id"))
            if case is None:
                raise HarnessError("generation evidence references an unknown case")
            expected_pairs = {
                (fixture_document_ids[source["id"]], page)
                for source in case["expected_sources"]
                for page in source["pages"]
            }
            retrieval_pairs = evidence_pairs(
                execution.get("retrieval_candidates"),
                20,
                allowed_document_ids=adopted_document_ids,
            )
            rerank_pairs = evidence_pairs(
                execution.get("reranked_sources"),
                6,
                allowed_document_ids=adopted_document_ids,
            )
            if (
                (
                    expected_pairs
                    and sample.get("retrieval_hit_at_20")
                    != int(expected_pairs.issubset(retrieval_pairs))
                )
                or (
                    expected_pairs
                    and sample.get("rerank_hit_at_6")
                    != int(expected_pairs.issubset(rerank_pairs))
                )
                or (not case["expect_abstention"] and "first_token_ms" not in sample)
            ):
                raise HarnessError(
                    "generation quality/timing differs from signed retrieval evidence"
                )
    referenced_profiles = [
        sample.get("profile_evidence_id") for sample in manifest["samples"]
    ]
    if len(set(referenced_profiles)) != len(referenced_profiles):
        raise HarnessError(
            "each recorded sample requires a unique profile acknowledgement"
        )
    all_payloads = [
        pre,
        *profiles.values(),
        *executions.values(),
        *sealed_samples.values(),
        post,
    ]
    sequences = [payload.get("sequence") for payload in all_payloads]
    if (
        any(
            not isinstance(sequence, int) or isinstance(sequence, bool)
            for sequence in sequences
        )
        or len(set(sequences)) != len(sequences)
        or any(
            not pre["sequence"] < sequence < post["sequence"]
            for sequence in sequences[1:-1]
        )
    ):
        raise HarnessError("signed evidence sequence is replayed or out of order")

    expected_queries = {
        (case["id"], temperature, queue, repetition)
        for case in evaluation["cases"]
        for temperature in ("cold", "warm")
        for queue in ("queue-free", "contended")
        for repetition in range(1, 6)
    }
    actual_queries = {
        (
            sample.get("case_id"),
            sample["temperature"],
            sample["queue"],
            sample["repetition"],
        )
        for sample in manifest["samples"]
        if sample["stage"] == "generation"
    }
    generation_samples = [
        sample for sample in manifest["samples"] if sample["stage"] == "generation"
    ]
    if actual_queries != expected_queries or len(generation_samples) != len(
        expected_queries
    ):
        raise HarnessError("accepted samples do not cover every required case/profile")
    ingestion = [
        sample for sample in manifest["samples"] if sample["stage"] == "ingest"
    ]
    bootstrap_ingestion = [
        sample for sample in ingestion if sample["sample_id"].startswith("ingest-syn-")
    ]
    if (
        len(bootstrap_ingestion) != 10
        or len({sample.get("document_id") for sample in bootstrap_ingestion}) != 10
        or {sample.get("fixture_document_id") for sample in bootstrap_ingestion}
        != {document["id"] for document in evaluation["documents"]}
        or any(
            sample.get("terminal_status") != "completed"
            or sample.get("success") is not True
            or sample.get("document_kind") not in {"digital", "scanned"}
            or "throughput_items_per_s" not in sample
            or (sample.get("document_kind") == "digital" and "parse_ms" not in sample)
            or (sample.get("document_kind") == "scanned" and "ocr_ms" not in sample)
            for sample in ingestion
        )
    ):
        raise HarnessError("accepted ingestion evidence is incomplete or duplicated")
    for stage in ("ingest", "ocr", "embedding", "retrieval", "rerank", "api"):
        expected_stage_profiles = {
            (temperature, queue, repetition)
            for temperature, queue in required_profiles
            for repetition in range(1, 6)
        }
        stage_samples = [
            sample
            for sample in manifest["samples"]
            if sample["stage"] == stage
            and not sample["sample_id"].startswith("ingest-syn-")
        ]
        actual_stage_profiles = {
            (sample["temperature"], sample["queue"], sample["repetition"])
            for sample in stage_samples
        }
        if actual_stage_profiles != expected_stage_profiles or len(
            stage_samples
        ) != len(expected_stage_profiles):
            raise HarnessError(
                f"accepted {stage} samples do not cover every required profile"
            )
    warm_generation = [
        sample
        for sample in manifest["samples"]
        if sample["stage"] == "generation" and sample["temperature"] == "warm"
    ]
    queue_free_first = [
        float(sample["first_token_ms"])
        for sample in warm_generation
        if sample["queue"] == "queue-free" and "first_token_ms" in sample
    ]
    contended_first = [
        float(sample["first_token_ms"])
        for sample in warm_generation
        if sample["queue"] == "contended" and "first_token_ms" in sample
    ]
    all_generation_final = [
        float(sample["elapsed_ms"])
        for sample in manifest["samples"]
        if sample["stage"] == "generation"
    ]
    api_elapsed = [
        float(sample["elapsed_ms"])
        for sample in manifest["samples"]
        if sample["stage"] == "api"
    ]
    queue_free_p95 = percentile(queue_free_first, 95)
    contended_p95 = percentile(contended_first, 95)
    final_p95 = percentile(all_generation_final, 95)
    api_p95 = percentile(api_elapsed, 95)
    if (
        queue_free_p95 is None
        or contended_p95 is None
        or final_p95 is None
        or len(all_generation_final) != len(expected_queries)
        or any(value > 60_000 for value in all_generation_final)
        or api_p95 is None
        or queue_free_p95 > 10_000
        or contended_p95 > queue_free_p95 * 1.25
        or final_p95 > 60_000
        or api_p95 > 300
    ):
        raise HarnessError("accepted performance gates are not satisfied")


def validate_manifest(value: Any) -> dict[str, Any]:
    top_level = {
        "schema_version",
        "manifest_kind",
        "run",
        "source",
        "alembic",
        "dependencies",
        "containers",
        "models",
        "machine",
        "dataset",
        "methodology",
        "samples",
        "summaries",
        "secrets_policy",
        "benchmark_evidence",
        "retained_evidence",
    }
    value = _require_object(value, top_level, top_level, "manifest")
    if value["schema_version"] != 2 or value["manifest_kind"] not in {
        "draft",
        "accepted",
    }:
        raise HarnessError("unsupported manifest version or kind")
    run = _require_object(
        value["run"],
        {"id", "created_at", "command"},
        {"id", "created_at", "command"},
        "run",
    )
    if not isinstance(run["id"], str) or not SAFE_ID.fullmatch(run["id"]):
        raise HarnessError("run.id is invalid")
    if not isinstance(run["created_at"], str):
        raise HarnessError("run.created_at is invalid")
    if run["command"] not in {
        "python -m benchmarks.v3.harness create-manifest",
        "python -m benchmarks.v3.runner run [safe-arguments-redacted]",
    }:
        raise HarnessError("run.command is not an approved fixed command")
    source = _require_object(
        value["source"],
        {
            "revision",
            "dirty",
            "changed_file_count",
            "content_identity",
            "reference_tag",
            "reference_tag_object",
        },
        {
            "revision",
            "dirty",
            "changed_file_count",
            "content_identity",
            "reference_tag",
            "reference_tag_object",
        },
        "source",
    )
    if source["revision"] is not None and (
        not isinstance(source["revision"], str)
        or not REVISION.fullmatch(source["revision"])
    ):
        raise HarnessError("source.revision is invalid")
    if source["dirty"] is not None and not isinstance(source["dirty"], bool):
        raise HarnessError("source.dirty is invalid")
    if source["changed_file_count"] is not None and not _is_nonnegative_int(
        source["changed_file_count"]
    ):
        raise HarnessError("source.changed_file_count is invalid")
    if not isinstance(source["content_identity"], str) or not SHA256.fullmatch(
        source["content_identity"]
    ):
        raise HarnessError("source.content_identity is invalid")
    if source["reference_tag"] is not None and (
        not isinstance(source["reference_tag"], str)
        or not SAFE_ID.fullmatch(source["reference_tag"])
    ):
        raise HarnessError("source.reference_tag is invalid")
    if source["reference_tag_object"] is not None and (
        not isinstance(source["reference_tag_object"], str)
        or not REVISION.fullmatch(source["reference_tag_object"])
    ):
        raise HarnessError("source.reference_tag_object is invalid")
    alembic = _require_object(
        value["alembic"],
        {"current_revision", "head_revision", "graph", "evidence_command"},
        {"current_revision", "head_revision", "graph", "evidence_command"},
        "alembic",
    )
    if alembic["current_revision"] is not None and not isinstance(
        alembic["current_revision"], str
    ):
        raise HarnessError("alembic.current_revision is invalid")
    if alembic["evidence_command"] != "uv --directory apps/api run alembic current":
        raise HarnessError("alembic evidence command is invalid")
    if not isinstance(alembic["head_revision"], list) or not all(
        isinstance(item, str) for item in alembic["head_revision"]
    ):
        raise HarnessError("alembic.head_revision is invalid")
    if not isinstance(alembic["graph"], list):
        raise HarnessError("alembic.graph is invalid")
    for entry in alembic["graph"]:
        checked_entry = _require_object(
            entry,
            {"revision", "down_revision"},
            {"revision", "down_revision"},
            "revision",
        )
        if not isinstance(checked_entry["revision"], str) or (
            checked_entry["down_revision"] is not None
            and not isinstance(checked_entry["down_revision"], str)
        ):
            raise HarnessError("alembic graph entry is invalid")
    dependencies = _require_object(
        value["dependencies"],
        {"lock_file", "lock_sha256", "packages"},
        {"lock_file", "lock_sha256", "packages"},
        "dependencies",
    )
    if dependencies["lock_file"] != "apps/api/uv.lock":
        raise HarnessError("dependency lock path is invalid")
    if dependencies["lock_sha256"] is not None and not SHA256.fullmatch(
        dependencies["lock_sha256"]
    ):
        raise HarnessError("dependency lock hash is invalid")
    if not isinstance(dependencies["packages"], list):
        raise HarnessError("dependency packages are invalid")
    for package in dependencies["packages"]:
        checked_package = _require_object(
            package, {"name", "version"}, {"name", "version"}, "package"
        )
        if not all(
            isinstance(checked_package[field], str) for field in ("name", "version")
        ):
            raise HarnessError("dependency package is invalid")
    containers = _require_object(
        value["containers"],
        {"compose_file", "images"},
        {"compose_file", "images"},
        "containers",
    )
    if containers["compose_file"] != "compose.yaml" or not isinstance(
        containers["images"], list
    ):
        raise HarnessError("container metadata is invalid")
    for image in containers["images"]:
        _require_object(
            image, {"reference", "digest"}, {"reference", "digest"}, "image"
        )
        if not isinstance(image["reference"], str) or (
            image["digest"] is not None
            and (
                not isinstance(image["digest"], str)
                or not SHA256.fullmatch(image["digest"])
            )
        ):
            raise HarnessError("container image metadata is invalid")
        if "@sha256:" in image["reference"] and image["digest"] != (
            "sha256:" + image["reference"].split("@sha256:", 1)[1]
        ):
            raise HarnessError("container image digest does not match its reference")
    models = _require_object(
        value["models"], set(MODEL_IDENTIFIERS), set(MODEL_IDENTIFIERS), "models"
    )
    for role, model in models.items():
        _require_object(
            model,
            {"identifier", "installed_evidence"},
            {"identifier", "installed_evidence"},
            f"models.{role}",
        )
        if model["identifier"] != MODEL_IDENTIFIERS[role]:
            raise HarnessError("model identifier is invalid")
        if not isinstance(model["installed_evidence"], str):
            raise HarnessError("model installed evidence is invalid")
    machine = _require_object(
        value["machine"],
        {"os", "python", "processor", "cpu_count", "power_mode"},
        {"os", "python", "processor", "cpu_count", "power_mode"},
        "machine",
    )
    if not _is_nonnegative_int(machine["cpu_count"]):
        raise HarnessError("machine.cpu_count is invalid")
    if not all(
        isinstance(machine[field], str)
        for field in ("os", "python", "processor", "power_mode")
    ):
        raise HarnessError("machine string metadata is invalid")
    dataset = _require_object(
        value["dataset"],
        {
            "identity",
            "path",
            "document_count",
            "digital_count",
            "scanned_count",
            "case_count",
            "fixture_manifest_path",
            "fixture_identity",
            "fixture_hashes",
        },
        {
            "identity",
            "path",
            "document_count",
            "digital_count",
            "scanned_count",
            "case_count",
        },
        "dataset",
    )
    if not isinstance(dataset["identity"], str) or not SHA256.fullmatch(
        dataset["identity"]
    ):
        raise HarnessError("dataset identity is invalid")
    for field in ("path", "fixture_manifest_path"):
        if field in dataset and (
            not isinstance(dataset[field], str)
            or Path(dataset[field]).is_absolute()
            or re.match(r"^[A-Za-z]:", dataset[field])
            or "\\" in dataset[field]
            or ".." in Path(dataset[field]).parts
        ):
            raise HarnessError(f"dataset.{field} must be repository-relative")
    for field in ("document_count", "digital_count", "scanned_count", "case_count"):
        if not _is_nonnegative_int(dataset[field]):
            raise HarnessError(f"dataset.{field} is invalid")
    if dataset["digital_count"] + dataset["scanned_count"] != dataset["document_count"]:
        raise HarnessError("dataset kind counts do not add up")
    if "fixture_identity" in dataset and (
        not isinstance(dataset["fixture_identity"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", dataset["fixture_identity"])
    ):
        raise HarnessError("dataset.fixture_identity is invalid")
    if "fixture_hashes" in dataset and (
        not isinstance(dataset["fixture_hashes"], dict)
        or not all(
            isinstance(key, str)
            and SAFE_ID.fullmatch(key)
            and isinstance(item, str)
            and re.fullmatch(r"[0-9a-f]{64}", item)
            for key, item in dataset["fixture_hashes"].items()
        )
    ):
        raise HarnessError("dataset.fixture_hashes is invalid")
    methodology = _require_object(
        value["methodology"],
        {
            "temperature_labels",
            "queue_labels",
            "model_residency",
            "concurrency",
            "fixed_corpus",
            "run_procedure",
            "repetitions",
            "warmups",
            "timeout_ms",
            "percentile_method",
        },
        {
            "temperature_labels",
            "queue_labels",
            "model_residency",
            "concurrency",
            "fixed_corpus",
            "run_procedure",
            "repetitions",
            "warmups",
            "timeout_ms",
            "percentile_method",
        },
        "methodology",
    )
    if methodology["temperature_labels"] != ["cold", "warm"] or methodology[
        "queue_labels"
    ] != ["queue-free", "contended"]:
        raise HarnessError("methodology labels are invalid")
    if methodology["repetitions"] != 5 or methodology["warmups"] != 1:
        raise HarnessError("methodology run counts are invalid")
    if methodology["fixed_corpus"] != dataset["path"]:
        raise HarnessError("methodology fixed corpus does not match dataset")
    if (
        methodology["run_procedure"] != "fresh-v3-synthetic-acceptance-v1"
        or methodology["percentile_method"] != "linear-n-minus-one"
        or not isinstance(methodology["timeout_ms"], int)
        or isinstance(methodology["timeout_ms"], bool)
        or methodology["timeout_ms"] < 1
    ):
        raise HarnessError("methodology procedure metadata is invalid")
    residency = _require_object(
        methodology["model_residency"],
        set(MODEL_IDENTIFIERS),
        set(MODEL_IDENTIFIERS),
        "methodology.model_residency",
    )
    if any(item not in ALLOWED_RESIDENCY for item in residency.values()):
        raise HarnessError("methodology model residency is invalid")
    concurrency = _require_object(
        methodology["concurrency"],
        {"chat_active", "chat_queued", "ingest_workers"},
        {"chat_active", "chat_queued", "ingest_workers"},
        "methodology.concurrency",
    )
    if any(not _is_nonnegative_int(item) for item in concurrency.values()):
        raise HarnessError("methodology concurrency is invalid")
    raw_samples = _validate_samples(value["samples"])
    if value["summaries"] != summarize_samples(raw_samples):
        raise HarnessError("manifest summaries do not match samples")
    if value["manifest_kind"] == "accepted":
        if not raw_samples:
            raise HarnessError("accepted manifests require non-zero samples")
        if (
            source["dirty"] is not False
            or source["reference_tag"] is None
            or source["reference_tag_object"] is None
        ):
            raise HarnessError(
                "accepted manifests require a clean annotated-tag reference"
            )
        if alembic["current_revision"] != "0001_v3_baseline" or alembic[
            "head_revision"
        ] != ["0001_v3_baseline"]:
            raise HarnessError("accepted manifests require fresh V3 migration evidence")
        if any(image["digest"] is None for image in containers["images"]):
            raise HarnessError("accepted manifests reject unpinned container images")
        if any(
            model["installed_evidence"] == "not-collected" for model in models.values()
        ):
            raise HarnessError(
                "accepted manifests require installed model/OCR evidence"
            )
        if any(
            not re.fullmatch(
                r"evidence-sha256:[0-9a-f]{64}", model["installed_evidence"]
            )
            for model in models.values()
        ):
            raise HarnessError("accepted model/OCR evidence must be content-addressed")
        _verify_accepted_fixture(dataset)
        retained_hashes = _verify_retained_evidence(value["retained_evidence"], value)
        _verify_benchmark_evidence(value["benchmark_evidence"], value, retained_hashes)
    if value["secrets_policy"] != "numeric-metrics-and-identifiers-only":
        raise HarnessError("secrets policy is invalid")
    _validate_all_strings(value)
    _reject_evaluation_content(value)
    try:
        validate_schema(value, MANIFEST_SCHEMA)
    except SchemaValidationError as exc:
        raise HarnessError(
            "manifest does not satisfy its standalone JSON Schema"
        ) from exc
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def promote_manifest(
    draft_path: str | Path,
    retained_evidence_path: str | Path,
    output: str | Path,
    reference_tag: str,
) -> Path:
    draft_file = ensure_results_path(draft_path)
    output_file = ensure_results_path(output)
    retained_file = _safe_persisted_path(
        str(retained_evidence_path).replace("\\", "/"),
        "retained evidence bundle",
    )
    manifest = _read_json(draft_file)
    if not isinstance(manifest, dict) or manifest.get("manifest_kind") != "draft":
        raise HarnessError("only a verified draft manifest can be promoted")
    validate_manifest(manifest)
    retained = _read_json(retained_file)
    checked = _require_object(
        retained,
        {"alembic", "models", "containers", "schema", "server_build"},
        {"alembic", "models", "containers", "schema", "server_build"},
        "retained evidence bundle",
    )
    source = _git_source(ROOT, reference_tag)
    if source["dirty"] is not False or source["reference_tag"] != reference_tag:
        raise HarnessError("promotion requires the current clean annotated tag")
    manifest["source"] = source
    manifest["retained_evidence"] = checked
    _alembic_hash, alembic = _verify_verifier_report(
        checked["alembic"],
        "retained_evidence.alembic",
        subject="alembic",
        expected_version="0001_v3_baseline",
    )
    manifest["alembic"]["current_revision"] = alembic["version"]
    _container_hash, container_report = _verify_verifier_report(
        checked["containers"],
        "retained_evidence.containers",
        subject="containers",
    )
    container_output_bytes = container_report.pop("__retained_output_bytes", None)
    if not isinstance(container_output_bytes, bytes):
        _output_hash, _output_path, container_output_bytes = _read_verified_artifact(
            container_report["output"], "retained_evidence.containers.output"
        )
    try:
        container_output = json.loads(container_output_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessError("container verifier output is invalid") from exc
    if (
        not isinstance(container_output, dict)
        or set(container_output) != {"images"}
        or not isinstance(container_output["images"], list)
    ):
        raise HarnessError("container verifier output is invalid")
    manifest["containers"]["images"] = container_output["images"]
    for role in MODEL_IDENTIFIERS:
        evidence_hash, _path = _verify_artifact(
            checked["models"][role], f"retained_evidence.models.{role}"
        )
        manifest["models"][role]["installed_evidence"] = "evidence-" + evidence_hash
    manifest["manifest_kind"] = "accepted"
    validate_manifest(manifest)
    _write_json(output_file, manifest)
    return output_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-manifest")
    create.add_argument("--output", default="draft-manifest.json")
    create.add_argument("--samples")
    create.add_argument("--run-id")
    create.add_argument("--alembic-current")
    create.add_argument("--model-evidence")
    create.add_argument("--reference-tag")
    promote = subparsers.add_parser("promote")
    promote.add_argument("--input", required=True)
    promote.add_argument("--retained-evidence", required=True)
    promote.add_argument("--output", required=True)
    promote.add_argument("--reference-tag", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("path")
    args = parser.parse_args(argv)
    if args.command == "promote":
        print(
            promote_manifest(
                args.input,
                args.retained_evidence,
                args.output,
                args.reference_tag,
            )
        )
        return 0
    if args.command == "validate":
        value = _read_json(Path(args.path))
        if isinstance(value, list):
            _validate_samples(value)
        elif isinstance(value, dict) and "manifest_kind" in value:
            validate_manifest(value)
        elif isinstance(value, dict) and "documents" in value:
            load_evaluation(Path(args.path))
        elif isinstance(value, dict) and set(value) == {"samples"}:
            _validate_samples(value["samples"])
        else:
            raise HarnessError("unrecognized JSON validation shape")
        print("valid")
        return 0
    samples: list[dict[str, Any]] = []
    if args.samples:
        supplied = _read_json(Path(args.samples))
        if isinstance(supplied, dict):
            if set(supplied) != {"samples"}:
                raise HarnessError("samples wrapper may contain only samples")
            samples = supplied["samples"]
        else:
            samples = supplied
    evidence = _read_json(Path(args.model_evidence)) if args.model_evidence else None
    if evidence is not None and (
        not isinstance(evidence, dict)
        or set(evidence) != set(MODEL_IDENTIFIERS)
        or not all(isinstance(item, str) for item in evidence.values())
    ):
        raise HarnessError("model evidence must contain exactly four string entries")
    manifest = create_manifest(
        samples=samples,
        run_id=args.run_id,
        manifest_kind="draft",
        alembic_current=args.alembic_current,
        model_evidence=evidence,
        reference_tag=args.reference_tag,
    )
    output = ensure_results_path(args.output)
    _write_json(output, manifest)
    print(output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
