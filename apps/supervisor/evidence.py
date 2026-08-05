from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .release import ALEMBIC_REVISION, RLS_TABLES, ReleasePins


@dataclass(frozen=True, slots=True)
class Listener:
    address: str
    port: int
    process_name: str
    process_path: str
    process_id: int | None = None
    parent_process_id: int | None = None
    in_job: bool | None = None


@dataclass(frozen=True, slots=True)
class FirewallRule:
    name: str
    enabled: bool
    direction: str
    action: str
    profile: str
    protocol: str
    local_port: str
    program: str
    service: str
    local_address: str
    remote_address: str
    interface_type: str
    edge_traversal: str


@dataclass(frozen=True, slots=True)
class FirewallProfile:
    name: str
    enabled: bool
    default_inbound_action: str


@dataclass(frozen=True, slots=True)
class DependencyEvidence:
    release_manifest_sha256: str
    docker_executable_sha256: str
    postgres_image_digest: str
    postgres_healthy: bool
    alembic_revision: str
    rls_enabled_tables: tuple[str, ...]
    force_rls_tables: tuple[str, ...]
    rustfs_image_digest: str
    rustfs_healthy: bool
    rustfs_authenticated_object_sha256: str
    rustfs_inventory_exact: bool
    rustfs_anonymous_object_get_denied: bool
    rustfs_anonymous_list_denied: bool
    rustfs_anonymous_policy_denied: bool
    ollama_models: dict[str, str]
    embedding_dimension: int
    reranker_identity: str
    reranker_device: str
    reranker_smoke_completed: bool
    reranker_model_assets_sha256: str
    paddleocr_version: str
    ocr_pipeline_version: str
    ocr_device: str
    ocr_smoke_completed: bool
    ocr_fixture_sha256: str
    ocr_output_sha256: str
    ocr_structured_sha256: str
    ocr_text_sha256: str
    ocr_page_count: int
    ocr_model_assets_sha256: str
    ocr_captured_at: datetime
    verifier_sha256: str
    api_python_sha256: str
    ocr_python_sha256: str


def network_evidence(
    listeners: Iterable[Listener],
    rules: Iterable[FirewallRule],
    profiles: Iterable[FirewallProfile],
    *,
    pinned_caddy_program: str,
    expected_local_addresses: tuple[str, str],
    supervisor_process_id: int | None = None,
) -> dict[str, object]:
    findings: list[str] = []
    normalized_listeners = tuple(listeners)
    normalized_rules = tuple(rules)
    normalized_profiles = tuple(profiles)
    expected_addresses = set(expected_local_addresses)
    try:
        expected_families = {
            ipaddress.ip_address(item).version for item in expected_local_addresses
        }
    except ValueError:
        expected_families = set()
    if len(expected_addresses) != 2 or expected_families != {4, 6}:
        findings.append("configured Caddy listeners must pin one IPv4 and one IPv6")
    lan: list[Listener] = []
    for listener in normalized_listeners:
        try:
            parsed_address = ipaddress.ip_address(listener.address)
            loopback = parsed_address.is_loopback
        except ValueError:
            parsed_address = None
            loopback = False
        if not loopback:
            lan.append(listener)
            if (
                listener.port != 443
                or listener.process_name.casefold() != "caddy"
                or listener.process_path.casefold() != pinned_caddy_program.casefold()
                or parsed_address is None
                or parsed_address.version not in {4, 6}
            ):
                findings.append(
                    f"unexpected LAN listener {listener.address}:{listener.port} "
                    f"owned by {listener.process_name}"
                )
    observed_addresses = {
        str(ipaddress.ip_address(item.address))
        for item in lan
        if item.port == 443
        and item.process_name.casefold() == "caddy"
        and item.process_path.casefold() == pinned_caddy_program.casefold()
        and _is_ip_address(item.address)
    }
    normalized_expected_addresses = {
        str(ipaddress.ip_address(item))
        for item in expected_addresses
        if _is_ip_address(item)
    }
    if observed_addresses != normalized_expected_addresses:
        findings.append(
            "pinned Caddy TCP 443 listeners do not exactly match configured "
            "IPv4 and IPv6 addresses"
        )
    caddy_listeners = [
        item
        for item in lan
        if item.port == 443
        and item.process_path.casefold() == pinned_caddy_program.casefold()
    ]
    caddy_pids = {item.process_id for item in caddy_listeners}
    if None in caddy_pids or len(caddy_pids) != 1:
        findings.append("IPv4 and IPv6 TCP 443 listeners must share one Caddy PID")
    if supervisor_process_id is not None and any(
        item.parent_process_id != supervisor_process_id for item in caddy_listeners
    ):
        findings.append("Caddy parent process is not the expected supervisor")
    if any(item.in_job is not True for item in caddy_listeners):
        findings.append("Caddy Job Object membership is not proven")
    profile_state = {item.name: item for item in normalized_profiles}
    if set(profile_state) != {"Private", "Public"}:
        findings.append("Private and Public firewall profiles must both be present")
    for profile_name in ("Private", "Public"):
        profile = profile_state.get(profile_name)
        if (
            profile is None
            or not profile.enabled
            or profile.default_inbound_action != "Block"
        ):
            findings.append(
                f"{profile_name} firewall must be enabled with default inbound Block"
            )
    expected_rules = [
        rule
        for rule in normalized_rules
        if rule.enabled
        and rule.direction == "Inbound"
        and rule.action == "Allow"
        and rule.name == "Local RAG HTTPS"
    ]
    if len(expected_rules) != 1:
        findings.append("exactly one enabled Local RAG HTTPS allow rule is required")
    if len(expected_rules) == 1:
        rule = expected_rules[0]
        if (
            rule.profile != "Private"
            or rule.protocol != "TCP"
            or rule.local_port != "443"
            or rule.program.casefold() != pinned_caddy_program.casefold()
            or rule.service.casefold() != "any"
            or {item.strip().casefold() for item in rule.local_address.split(",")}
            != {item.casefold() for item in expected_addresses}
            or rule.remote_address != "LocalSubnet"
            or {item.strip() for item in rule.interface_type.split(",")}
            != {"Wired", "Wireless"}
            or rule.edge_traversal != "Block"
        ):
            findings.append(
                "Local RAG HTTPS rule does not match the pinned scoped contract"
            )
    for rule in normalized_rules:
        if (
            not rule.enabled
            or rule.direction != "Inbound"
            or rule.action != "Allow"
            or not _protocol_overlaps_tcp(rule.protocol)
            or not _port_overlaps_443(rule.local_port)
        ):
            continue
        if len(expected_rules) == 1 and rule is expected_rules[0]:
            continue
        findings.append(f"overlapping inbound TCP 443 allow rule: {rule.name}")
    return {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "result": "pass" if not findings else "fail",
        "listeners": [asdict(item) for item in normalized_listeners],
        "firewall_rules": [asdict(item) for item in normalized_rules],
        "firewall_profiles": [asdict(item) for item in normalized_profiles],
        "findings": findings,
        "second_lan_device": "unverified",
    }


def dependency_evidence(
    evidence: DependencyEvidence,
    *,
    release: ReleasePins,
    now: datetime,
) -> dict[str, object]:
    findings: list[str] = []
    if evidence.release_manifest_sha256 != release.manifest_sha256:
        findings.append("dependency evidence is not bound to the signed release")
    if evidence.docker_executable_sha256 != release.docker_executable_sha256:
        findings.append("Docker executable does not match the signed release pin")
    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", release.postgres_image_digest) is None
        or evidence.postgres_image_digest != release.postgres_image_digest
    ):
        findings.append("PostgreSQL image digest is absent or does not match")
    if not evidence.postgres_healthy:
        findings.append("PostgreSQL container health is not healthy")
    if evidence.alembic_revision != ALEMBIC_REVISION:
        findings.append("Alembic revision does not match the release pin")
    if (
        tuple(sorted(evidence.rls_enabled_tables)) != RLS_TABLES
        or tuple(sorted(evidence.force_rls_tables)) != RLS_TABLES
    ):
        findings.append("enabled and forced RLS table evidence is incomplete")
    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", release.rustfs_image_digest) is None
        or evidence.rustfs_image_digest != release.rustfs_image_digest
    ):
        findings.append("RustFS image digest is absent or does not match")
    if not evidence.rustfs_healthy:
        findings.append("RustFS container health is not healthy")
    if (
        evidence.rustfs_authenticated_object_sha256
        != release.rustfs_probe_object_sha256
    ):
        findings.append("RustFS authenticated object proof does not match")
    if not evidence.rustfs_inventory_exact:
        findings.append("RustFS authenticated inventory proof is incomplete")
    if not (
        evidence.rustfs_anonymous_object_get_denied
        and evidence.rustfs_anonymous_list_denied
        and evidence.rustfs_anonymous_policy_denied
    ):
        findings.append("RustFS anonymous GET/list/policy denial proof is incomplete")
    if dict(evidence.ollama_models) != release.ollama_models:
        findings.append("Ollama model identities/digests do not match")
    if evidence.embedding_dimension != 1024:
        findings.append("embedding dimension is not 1024")
    if (
        evidence.reranker_identity != "BAAI/bge-reranker-v2-m3"
        or evidence.reranker_device.casefold() != "cpu"
        or not evidence.reranker_smoke_completed
        or evidence.reranker_model_assets_sha256 != release.reranker_model_assets_sha256
    ):
        findings.append("BGE reranker identity/CPU smoke proof is invalid")
    if evidence.paddleocr_version != release.paddleocr_version:
        findings.append("PaddleOCR package version does not match")
    if evidence.ocr_pipeline_version != "1.6":
        findings.append("OCR pipeline version is not 1.6")
    if evidence.ocr_device.casefold() != "cpu":
        findings.append("OCR device is not CPU")
    if not evidence.ocr_smoke_completed:
        findings.append("OCR smoke test did not complete")
    if evidence.ocr_fixture_sha256 != release.ocr_fixture_sha256:
        findings.append("OCR smoke fixture does not match the release pin")
    if (
        evidence.ocr_output_sha256 != release.ocr_expected_output_sha256
        or evidence.ocr_structured_sha256 != release.ocr_expected_structured_sha256
        or evidence.ocr_text_sha256 != release.ocr_expected_text_sha256
        or evidence.ocr_page_count != release.ocr_expected_page_count
        or evidence.ocr_model_assets_sha256 != release.ocr_model_assets_sha256
    ):
        findings.append("OCR signed output/page/text/model proof is invalid")
    if evidence.verifier_sha256 != release.verifier_sha256:
        findings.append("dependency verifier does not match the release pin")
    if (
        evidence.api_python_sha256 != release.api_python_sha256
        or evidence.ocr_python_sha256 != release.ocr_python_sha256
    ):
        findings.append("dependency verifier runtimes do not match release pins")
    captured = evidence.ocr_captured_at.astimezone(UTC)
    current = now.astimezone(UTC)
    age = (current - captured).total_seconds()
    if age < -5 or age > release.max_evidence_age_seconds:
        findings.append("OCR smoke evidence is stale or future-dated")
    return {
        "schema_version": 1,
        "mode": "read_only",
        "result": "pass" if not findings else "fail",
        "findings": findings,
    }


def _protocol_overlaps_tcp(protocol: str) -> bool:
    return protocol.casefold() in {"tcp", "any", "256"}


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _port_overlaps_443(value: str) -> bool:
    for part in value.split(","):
        token = part.strip()
        if token.casefold() == "any":
            return True
        if token.isdigit() and int(token) == 443:
            return True
        if token.isdigit():
            continue
        if "-" in token:
            bounds = token.split("-", 1)
            if all(item.strip().isdigit() for item in bounds):
                lower, upper = (int(item.strip()) for item in bounds)
                if lower > upper:
                    return True
                if lower <= 443 <= upper:
                    return True
            else:
                return True
            continue
        return True
    return False


def dependency_onboarding_plan() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "read_only",
        "checks": [
            {
                "dependency": "Docker PostgreSQL",
                "transport": "127.0.0.1",
                "checks": [
                    "signed-release exact pinned image digest",
                    "healthy state",
                    f"exact {ALEMBIC_REVISION} revision",
                    "fixed complete enabled and FORCE RLS table set",
                ],
            },
            {
                "dependency": "Docker RustFS",
                "transport": "127.0.0.1",
                "checks": [
                    "signed-release exact pinned image digest",
                    "healthy state",
                    "authenticated object/inventory hash proof",
                    "separate API/worker/maintenance scoped IAM allow/deny proof",
                    "protected per-identity credential-file ACLs",
                    "anonymous object GET, list, and policy access denied",
                ],
            },
            {
                "dependency": "native Ollama",
                "transport": "127.0.0.1:11434",
                "checks": [
                    "exact qwen3:8b digest",
                    "exact qwen3-embedding:0.6b digest",
                    "1024-dimensional embedding",
                    "BAAI/bge-reranker-v2-m3 CPU smoke",
                ],
            },
            {
                "dependency": "isolated PaddleOCR-VL",
                "transport": "restricted per-job directory",
                "checks": [
                    "exact PaddleOCR package version",
                    "fresh pinned-fixture pipeline v1.6 smoke output",
                    "CPU device",
                ],
            },
        ],
    }


def certificate_lifecycle_plan() -> dict[str, object]:
    return {
        "schema_version": 1,
        "changes_applied": False,
        "private_keys_generated": False,
        "canonical_host": "rag.home.arpa",
        "steps": [
            "attended: generate private local CA key and certificate",
            "attended: issue rag.home.arpa server leaf with DNS SAN",
            (
                "attended: issue loopback API server leaf with rag-api-loopback "
                "DNS SAN and 127.0.0.1 IP SAN plus Caddy/supervisor client leaves"
            ),
            "attended: apply service-identity ACLs to private keys",
            "attended admin: install CA certificate into trusted roots",
            (
                "verify: TLS succeeds only after explicit trust and never falls "
                "back to HTTP"
            ),
            "renew: issue a new leaf, validate, atomically switch, and reload Caddy",
            "rollback: retain the previous valid leaf until post-reload verification",
        ],
    }


def identity_acl_plan() -> dict[str, object]:
    return {
        "schema_version": 1,
        "changes_applied": False,
        "identities": {
            "RagSupervisor": [
                "SCM own-process service running as LocalSystem with an unrestricted service SID",
                "automatic-delayed start and bounded SCM recovery",
                "create the ACL-protected global mutex and Job Object",
                "read and validate one child environment at launch",
                "never copy the supervisor environment or retain logon passwords",
            ],
            "RagProxySvc": ["Caddy binary/config", "TLS server key", "mTLS client key"],
            "RagWebSvc": ["read release assets", "write web logs"],
            "RagApiSvc": ["rag_api and API RustFS secrets", "API scratch/logs"],
            "RagIngestionSvc": [
                "rag_worker and worker RustFS secrets",
                "write ingestion scratch/logs",
            ],
            "RagDeletionSvc": ["rag_worker and worker RustFS secrets"],
            "RagInferenceSvc": [
                "no database credential",
                "no object-storage credential",
            ],
            "RagOcrSvc": [
                "no database credential",
                "no object-storage credential",
                "per-job validated OCR directories only",
            ],
        },
        "principles": [
            "seven child accounts use independently generated passwords",
            "grant SeServiceLogonRight and deny interactive, RDP, batch, and network logon",
            "deny inheritance where a secret directory requires explicit ACLs",
            "grant no interactive logon",
            "do not place migration or backup credentials in runtime environments",
            "verify effective ACLs before service installation",
        ],
    }
