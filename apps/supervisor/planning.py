from __future__ import annotations

from dataclasses import asdict, dataclass

from .manifest import DeploymentManifest


@dataclass(frozen=True, slots=True)
class PlanStep:
    sequence: int
    action: str
    target: str
    privileged: bool
    status: str
    detail: str


def build_install_plan(manifest: DeploymentManifest) -> tuple[PlanStep, ...]:
    """Return a stable dry-run plan. This function never changes host state."""
    steps: list[PlanStep] = [
        PlanStep(
            1,
            "verify",
            "manifest",
            False,
            "ready",
            f"Validated deployment manifest SHA-256 {manifest.digest()}",
        ),
        PlanStep(
            2,
            "verify",
            "dependencies",
            False,
            "planned",
            "Check Docker PostgreSQL/RustFS, native Ollama models, and isolated OCR",
        ),
        PlanStep(
            3,
            "provision",
            "service-identities-and-acls",
            True,
            "attended",
            (
                "Create least-privilege identities and apply explicit "
                "directory/secret ACLs"
            ),
        ),
        PlanStep(
            4,
            "provision",
            "private-ca-and-leaf-certificates",
            True,
            "attended",
            (
                "Generate local CA, rag.home.arpa leaf, and loopback mTLS "
                "identities; install trust explicitly"
            ),
        ),
        PlanStep(
            5,
            "install",
            "caddy",
            True,
            "blocked" if manifest.caddy_sha256 is None else "ready",
            (
                "Pin a Caddy version and SHA-256 before installation"
                if manifest.caddy_sha256 is None
                else f"Verify Caddy {manifest.caddy_version} before installation"
            ),
        ),
        PlanStep(
            6,
            "configure",
            "firewall",
            True,
            "attended",
            "Allow inbound TCP 443 on Private only; create no Public rule",
        ),
        PlanStep(
            7,
            "install",
            "windows-service",
            True,
            "attended",
            "Install the automatic-delayed own-process RagSupervisor service",
        ),
        PlanStep(
            8,
            "configure",
            "backup-schedule",
            True,
            "not_configured",
            (
                "Disabled until an administrator approves an encrypted "
                "external destination"
            ),
        ),
        PlanStep(
            9,
            "configure",
            "update-trust",
            True,
            manifest.update_state,
            "Pin an offline Ed25519 release key before update checks",
        ),
        PlanStep(
            10,
            "verify",
            "network-and-recovery",
            False,
            "planned",
            (
                "Prove Caddy is the only LAN listener, Public is closed, "
                "and rollback evidence is retained"
            ),
        ),
    ]
    return tuple(steps)


def plan_as_dict(manifest: DeploymentManifest) -> dict[str, object]:
    steps = build_install_plan(manifest)
    return {
        "schema_version": 2,
        "mode": "dry_run",
        "deployment_id": manifest.deployment_id,
        "manifest_sha256": manifest.digest(),
        "changes_applied": False,
        "idempotency_key": f"{manifest.deployment_id}:{manifest.digest()}",
        "deployment_readiness": {
            "state": manifest.deployment_state,
            "blockers": list(manifest.deployment_blockers),
            "prerequisite_checks": list(manifest.prerequisite_checks),
        },
        "steps": [asdict(item) for item in steps],
    }
