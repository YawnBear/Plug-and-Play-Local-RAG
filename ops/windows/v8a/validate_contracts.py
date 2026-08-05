from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
V8A = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.windows.validate_json_schema import validate  # noqa: E402


class ContractError(ValueError):
    """A V8A product, capability, trust, or package contract is invalid."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContractError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid JSON document: {path.name}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path.name} must contain a JSON object")
    return value


def _validate_schema(document: str, schema: str) -> dict[str, Any]:
    value = _load_json(V8A / document)
    schema_value = _load_json(V8A / schema)
    try:
        validate(value, schema_value)
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(f"{document} failed schema validation: {exc}") from exc
    return value


def _validate_product_profiles(value: dict[str, Any]) -> None:
    profiles = {item["id"]: item for item in value["profiles"]}
    if set(profiles) != {"personal", "team_lan", "contributor"}:
        raise ContractError("product profile set must be exact")
    if sum(bool(item["default"]) for item in profiles.values()) != 1:
        raise ContractError("exactly one product profile must be default")
    personal = profiles["personal"]
    expected_false = {
        "caddy_required",
        "lan_dns_required",
        "certificates_required",
        "inbound_firewall_required",
        "dedicated_windows_identities_required",
    }
    if (
        not personal["default"]
        or not personal["authentication_required"]
        or personal["ingress_mode"] != "loopback"
        or personal["browser_origin"] != "http://127.0.0.1:3000"
        or any(personal[field] for field in expected_false)
    ):
        raise ContractError("Personal must remain authenticated and loopback-only")
    team = profiles["team_lan"]
    if (
        team["ingress_mode"] != "private_lan_https"
        or team["browser_origin"] != "https://rag.home.arpa"
        or not all(team[field] for field in expected_false)
    ):
        raise ContractError("Team/LAN hardening contract was weakened")


def _validate_capabilities(value: dict[str, Any]) -> None:
    profiles = value["profiles"]
    ids = [item["profile_id"] for item in profiles]
    if len(ids) != len(set(ids)):
        raise ContractError("capability profile IDs must be unique")
    functions = [item["function"] for item in profiles]
    if any(functions.count(name) != 1 for name in ("embedding", "generation", "reranking")):
        raise ContractError("V8A baseline must contain one non-OCR profile per function")
    if functions.count("ocr") < 1:
        raise ContractError("V8A baseline must contain at least one OCR profile")
    forbidden_fields = {
        "command",
        "commands",
        "executable",
        "executable_path",
        "path",
        "paths",
        "url",
        "urls",
        "environment",
        "environment_key",
        "environment_keys",
    }
    for profile in profiles:
        if forbidden_fields.intersection(profile):
            raise ContractError("capability profiles must remain data-only")
        if (
            profile["release_support_class"] == "release_qualified"
            and not profile["local_validation_fixture"]
        ):
            raise ContractError("release-qualified profiles require a local fixture")
    embedding = next(item for item in profiles if item["function"] == "embedding")
    ocr_profiles = [item for item in profiles if item["function"] == "ocr"]
    ocr = next(
        (
            item
            for item in ocr_profiles
            if item["profile_id"] == "ocr.paddleocr-vl-1.6.cpu.windows-x64"
        ),
        None,
    )
    if (
        embedding["model_identity"] != "qwen3-embedding:0.6b"
        or embedding["impact_class"] != "full_shadow_reindex"
    ):
        raise ContractError("embedding baseline or impact contract changed")
    if (
        ocr is None
        or ocr["accelerator_vendor"] != "cpu"
        or ocr["model_identity"] != "PaddleOCR-VL 1.6"
    ):
        raise ContractError("V8A OCR baseline must remain PaddleOCR-VL 1.6 on CPU")
    runtime_devices: set[str] = set()
    for profile in ocr_profiles:
        device = profile.get("runtime_device")
        if profile["accelerator_vendor"] == "cpu":
            if device not in (None, "cpu"):
                raise ContractError("CPU OCR profiles must use runtime_device cpu")
            device = "cpu"
        elif not isinstance(device, str) or re.fullmatch(r"gpu:[0-9]+", device) is None:
            raise ContractError("accelerated OCR profiles require a bounded GPU device")
        if device in runtime_devices:
            raise ContractError("OCR runtime devices must be unique")
        runtime_devices.add(device)


def _safe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    raw_parts = normalized.split("/")
    return not path.is_absolute() and all(
        part not in {"", ".", ".."} for part in raw_parts
    )


def _validate_release(value: dict[str, Any], capabilities: dict[str, Any]) -> None:
    dependency_ids = {item["id"] for item in value["external_dependencies"]}
    if dependency_ids != {"docker_desktop", "ollama"}:
        raise ContractError("Personal external dependency set must be exact")
    artifact_ids: set[str] = set()
    for artifact in value["artifacts"]:
        if artifact["artifact_id"] in artifact_ids:
            raise ContractError("release artifact IDs must be unique")
        artifact_ids.add(artifact["artifact_id"])
        if not _safe_relative_path(artifact["relative_path"]):
            raise ContractError("release artifact path must be safe and relative")
        if not artifact["license_notice_id"]:
            raise ContractError("release artifact is missing a license notice binding")
    model_profiles = {item["profile_id"] for item in capabilities["profiles"]}
    model_identities = {item["identity"] for item in value["ollama_models"]}
    if model_identities != {"qwen3:8b", "qwen3-embedding:0.6b"}:
        raise ContractError("Personal Ollama model set must be exact")
    expected_model_digests = {
        "qwen3:8b": "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41",
        "qwen3-embedding:0.6b": "ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d",
    }
    if {
        item["identity"]: item["expected_digest"] for item in value["ollama_models"]
    } != expected_model_digests or any(
        item["download_policy"] != "pinned_resumable" for item in value["ollama_models"]
    ):
        raise ContractError("Personal Ollama model digests must remain pinned")
    if any(
        item["capability_profile_id"] not in model_profiles
        for item in value["ollama_models"]
    ):
        raise ContractError("release model references an unknown capability profile")
    expected_steps = [
        "contracts_validated",
        "prerequisites_validated",
        "roots_created",
        "secrets_created",
        "stores_started",
        "postgres_provisioned",
        "rustfs_provisioned",
        "schema_migrated",
        "storage_bootstrapped",
        "models_acquired",
        "setup_code_issued",
    ]
    if value["install_steps"] != expected_steps:
        raise ContractError("Personal install step order must be exact")
    compose = ROOT / value["stores"]["compose_file"]
    if not compose.is_file():
        raise ContractError("Personal Compose file is missing")
    compose_text = compose.read_text(encoding="utf-8")
    for required in (
        value["stores"]["postgres_image"],
        value["stores"]["rustfs_image"],
        '"127.0.0.1:5432:5432"',
        '"127.0.0.1:9000:9000"',
        "com.localrag.installation-id",
        "RAG_PERSONAL_POSTGRES_DATA",
        "RAG_PERSONAL_RUSTFS_DATA",
    ):
        if required not in compose_text:
            raise ContractError(f"Personal Compose contract is missing: {required}")
    for forbidden in (
        "caddy:",
        '"0.0.0.0:5432:5432"',
        '"0.0.0.0:9000:9000"',
        "9001:9001",
    ):
        if forbidden in compose_text:
            raise ContractError(f"Personal Compose contract contains: {forbidden}")


def _validate_trust(value: dict[str, Any]) -> None:
    if (
        not value["production_signature_required"]
        or value["production_signing_milestone"] != "V8F"
        or value["trust_anchor_state"] != "v8f_required"
        or not value["development_mode"]["production_key_forbidden"]
        or not value["metadata"]["anti_rollback_required"]
        or not value["revocation"]["fail_closed"]
    ):
        raise ContractError("V8A trust policy was weakened")
    suspicious = tuple(V8A.rglob("*private*")) + tuple(V8A.rglob("*.key"))
    if suspicious:
        raise ContractError("private signing material must not exist in V8A contracts")


def validate_contracts() -> dict[str, Any]:
    products = _validate_schema("product-profiles.json", "product-profiles.schema.json")
    capabilities = _validate_schema(
        "capability-profiles.json", "capability-profiles.schema.json"
    )
    release = _validate_schema("personal-release.json", "personal-release.schema.json")
    trust = _validate_schema("trust-policy.json", "trust-policy.schema.json")
    _validate_product_profiles(products)
    _validate_capabilities(capabilities)
    _validate_release(release, capabilities)
    _validate_trust(trust)
    files = (
        "product-profiles.schema.json",
        "product-profiles.json",
        "capability-profiles.schema.json",
        "capability-profiles.json",
        "trust-policy.schema.json",
        "trust-policy.json",
        "release-trust-metadata.schema.json",
        "personal-release.schema.json",
        "personal-release.json",
        "compose.personal.yaml",
    )
    return {
        "result": "pass",
        "schema_version": 1,
        "profile": "personal",
        "payload_state": release["payload_state"],
        "contract_sha256": {
            name: hashlib.sha256((V8A / name).read_bytes()).hexdigest()
            for name in files
        },
        "mutations_performed": False,
    }


def main() -> int:
    try:
        result = validate_contracts()
    except ContractError as exc:
        print(json.dumps({"result": "fail", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
