from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .updates import UpdateError, VerifiedUpdate

ALEMBIC_REVISION = "0006_versioned_claim"
RLS_TABLES = (
    "access_grants",
    "acl_previews",
    "audit_events",
    "backup_runs",
    "chat_scopes",
    "chat_turns",
    "chats",
    "chunks",
    "documents",
    "effective_document_access",
    "folder_create_grants",
    "ingestion_jobs",
    "library_nodes",
    "login_throttles",
    "object_deletions",
    "pre_auth_challenges",
    "security_epochs",
    "service_leases",
    "sessions",
    "team_members",
    "teams",
    "turn_citations",
    "turn_sources",
    "upload_reservations",
    "users",
)
MODEL_NAMES = frozenset({"qwen3:8b", "qwen3-embedding:0.6b"})


class ReleaseError(ValueError):
    """A signed release-evidence artifact is missing or invalid."""


@dataclass(frozen=True, slots=True)
class ReleasePins:
    manifest_sha256: str
    postgres_image_digest: str
    rustfs_image_digest: str
    rustfs_bucket: str
    rustfs_probe_object_key: str
    rustfs_probe_object_sha256: str
    ollama_models: dict[str, str]
    paddleocr_version: str
    ocr_fixture_sha256: str
    verifier_sha256: str
    api_python_sha256: str
    ocr_python_sha256: str
    docker_executable_sha256: str
    api_python_tree_sha256: str
    ocr_python_tree_sha256: str
    node_tree_sha256: str
    openssl_tree_sha256: str
    ocr_expected_output_sha256: str
    ocr_expected_structured_sha256: str
    ocr_expected_text_sha256: str
    ocr_expected_page_count: int
    ocr_model_assets_sha256: str
    reranker_model_assets_sha256: str
    max_evidence_age_seconds: int


def load_release_pins(path: Path, update: VerifiedUpdate) -> ReleasePins:
    if path.name != "release-evidence.json" or path.is_symlink() or not path.is_file():
        raise ReleaseError("release evidence must be a regular release-evidence.json")
    matching = [item for item in update.artifacts if item.filename == path.name]
    if len(matching) != 1:
        raise ReleaseError("release evidence is not bound to the signed update")
    artifact = matching[0]
    raw = path.read_bytes()
    if len(raw) != artifact.size or hashlib.sha256(raw).hexdigest() != artifact.sha256:
        raise ReleaseError("release evidence differs from the signed artifact")
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except (json.JSONDecodeError, UpdateError) as exc:
        raise ReleaseError("release evidence JSON is invalid") from exc
    fields = {
        "schema_version",
        "alembic_revision",
        "force_rls_tables",
        "containers",
        "rustfs",
        "ollama_models",
        "reranker",
        "ocr",
        "runtimes",
        "verifier_sha256",
        "max_evidence_age_seconds",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ReleaseError("release evidence fields are invalid")
    if value["schema_version"] != 1 or value["alembic_revision"] != ALEMBIC_REVISION:
        raise ReleaseError("release schema or Alembic revision is invalid")
    tables = value["force_rls_tables"]
    if (
        not isinstance(tables, list)
        or any(not isinstance(item, str) for item in tables)
        or tuple(sorted(tables)) != RLS_TABLES
    ):
        raise ReleaseError("release FORCE RLS table set is incomplete")
    containers = _exact_object(
        value["containers"],
        {"postgres_image_digest", "rustfs_image_digest"},
        "containers",
    )
    rustfs = _exact_object(
        value["rustfs"],
        {"bucket", "probe_object_key", "probe_object_sha256"},
        "rustfs",
    )
    models = _exact_object(value["ollama_models"], set(MODEL_NAMES), "ollama_models")
    reranker = _exact_object(
        value["reranker"],
        {"identity", "device", "model_assets_sha256"},
        "reranker",
    )
    ocr = _exact_object(
        value["ocr"],
        {
            "paddleocr_version",
            "pipeline_version",
            "fixture_sha256",
            "expected_output_sha256",
            "expected_structured_sha256",
            "expected_text_sha256",
            "expected_page_count",
            "model_assets_sha256",
        },
        "ocr",
    )
    runtimes = _exact_object(
        value["runtimes"],
        {
            "api_python_sha256",
            "ocr_python_sha256",
            "docker_executable_sha256",
            "api_python_tree_sha256",
            "ocr_python_tree_sha256",
            "node_tree_sha256",
            "openssl_tree_sha256",
        },
        "runtimes",
    )
    if reranker["identity"] != "BAAI/bge-reranker-v2-m3" or reranker["device"] != "cpu":
        raise ReleaseError("release reranker identity/device is invalid")
    if ocr["pipeline_version"] != "1.6":
        raise ReleaseError("release OCR pipeline must be 1.6")
    digests = (
        containers["postgres_image_digest"],
        containers["rustfs_image_digest"],
    )
    if any(
        not isinstance(item, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
        for item in digests
    ):
        raise ReleaseError("release container digest is invalid")
    content_digests = (
        *models.values(),
        rustfs["probe_object_sha256"],
        ocr["fixture_sha256"],
        ocr["expected_output_sha256"],
        ocr["expected_structured_sha256"],
        ocr["expected_text_sha256"],
        ocr["model_assets_sha256"],
        reranker["model_assets_sha256"],
        *runtimes.values(),
        value["verifier_sha256"],
    )
    if any(
        not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
        for item in content_digests
    ):
        raise ReleaseError("release content digest is invalid")
    if (
        not isinstance(value["max_evidence_age_seconds"], int)
        or isinstance(value["max_evidence_age_seconds"], bool)
        or not 60 <= value["max_evidence_age_seconds"] <= 3600
    ):
        raise ReleaseError("release evidence freshness limit is invalid")
    if (
        not isinstance(ocr["expected_page_count"], int)
        or isinstance(ocr["expected_page_count"], bool)
        or ocr["expected_page_count"] < 1
    ):
        raise ReleaseError("release OCR expected page count is invalid")
    for label, text in (
        ("RustFS bucket", rustfs["bucket"]),
        ("RustFS probe key", rustfs["probe_object_key"]),
        ("PaddleOCR version", ocr["paddleocr_version"]),
    ):
        if not isinstance(text, str) or not text:
            raise ReleaseError(f"{label} is invalid")
    return ReleasePins(
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        postgres_image_digest=containers["postgres_image_digest"],
        rustfs_image_digest=containers["rustfs_image_digest"],
        rustfs_bucket=rustfs["bucket"],
        rustfs_probe_object_key=rustfs["probe_object_key"],
        rustfs_probe_object_sha256=rustfs["probe_object_sha256"],
        ollama_models=dict(models),
        paddleocr_version=ocr["paddleocr_version"],
        ocr_fixture_sha256=ocr["fixture_sha256"],
        verifier_sha256=value["verifier_sha256"],
        api_python_sha256=runtimes["api_python_sha256"],
        ocr_python_sha256=runtimes["ocr_python_sha256"],
        docker_executable_sha256=runtimes["docker_executable_sha256"],
        api_python_tree_sha256=runtimes["api_python_tree_sha256"],
        ocr_python_tree_sha256=runtimes["ocr_python_tree_sha256"],
        node_tree_sha256=runtimes["node_tree_sha256"],
        openssl_tree_sha256=runtimes["openssl_tree_sha256"],
        ocr_expected_output_sha256=ocr["expected_output_sha256"],
        ocr_expected_structured_sha256=ocr["expected_structured_sha256"],
        ocr_expected_text_sha256=ocr["expected_text_sha256"],
        ocr_expected_page_count=ocr["expected_page_count"],
        ocr_model_assets_sha256=ocr["model_assets_sha256"],
        reranker_model_assets_sha256=reranker["model_assets_sha256"],
        max_evidence_age_seconds=value["max_evidence_age_seconds"],
    )


def _exact_object(value: object, fields: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ReleaseError(f"release {label} fields are invalid")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseError(f"release evidence contains duplicate field: {key}")
        value[key] = item
    return value
