from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .backup import backup_status, restore_verification_status
from .evidence import (
    certificate_lifecycle_plan,
    dependency_onboarding_plan,
    identity_acl_plan,
)
from .manifest import DeploymentManifest, ManifestError
from .planning import plan_as_dict
from .runtime import (
    Supervisor,
    load_identity_password,
    load_service_environment,
    validate_windows_secret_acl,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Plan or run the non-installed Local RAG Windows supervisor"
    )
    subcommands = value.add_subparsers(dest="command", required=True)
    for command in ("plan", "validate", "validate-secrets", "status"):
        item = subcommands.add_parser(command)
        item.add_argument("--manifest", type=Path, required=True)
    run = subcommands.add_parser("run-foreground")
    run.add_argument("--manifest", type=Path, required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        manifest = DeploymentManifest.load(arguments.manifest)
    except ManifestError as exc:
        print(json.dumps({"result": "fail", "error": str(exc)}, sort_keys=True))
        return 2
    if arguments.command == "validate":
        output: dict[str, object] = {
            "result": "pass",
            "manifest_sha256": manifest.digest(),
            "changes_applied": False,
            "deployment_readiness": {
                "state": manifest.deployment_state,
                "blockers": list(manifest.deployment_blockers),
                "prerequisite_checks": list(manifest.prerequisite_checks),
            },
        }
    elif arguments.command == "validate-secrets":
        try:
            for service in manifest.services:
                load_service_environment(
                    service,
                    acl_validator=validate_windows_secret_acl,
                )
                load_identity_password(
                    service,
                    acl_validator=validate_windows_secret_acl,
                )
        except (OSError, RuntimeError, ValueError) as exc:
            print(json.dumps({"result": "fail", "error": str(exc)}, sort_keys=True))
            return 4
        output = {
            "result": "pass",
            "manifest_sha256": manifest.digest(),
            "validated_service_secret_sets": len(manifest.services),
            "passwords_exposed": False,
            "changes_applied": False,
        }
    elif arguments.command == "plan":
        output = plan_as_dict(manifest)
        output["dependency_onboarding"] = dependency_onboarding_plan()
        output["service_identity_acls"] = identity_acl_plan()
        output["certificates"] = certificate_lifecycle_plan()
    elif arguments.command == "status":
        output = {
            "schema_version": 2,
            "deployment_id": manifest.deployment_id,
            "manifest_sha256": manifest.digest(),
            "backup": backup_status(manifest.backup_destination),
            "restore_verification": restore_verification_status(),
            "updates": {
                "state": manifest.update_state,
                "automatic_install": False,
                "public_key_pinned": manifest.update_public_key_sha256 is not None,
            },
            "service_installation": manifest.deployment_state,
            "deployment_readiness": {
                "state": manifest.deployment_state,
                "blockers": list(manifest.deployment_blockers),
                "prerequisite_checks": list(manifest.prerequisite_checks),
            },
            "second_lan_device": "unverified",
            "rollback": {
                "state": (
                    "installer_rollback_available"
                    if manifest.deployment_state == "installed"
                    else "not_installed"
                ),
                "reason": (
                    "disable ingress/service first; preserve PostgreSQL and RustFS data"
                ),
                "live_restore_performed": False,
            },
            "live_checks_performed": False,
        }
    else:
        try:
            manifest.assert_runnable()
        except ManifestError as exc:
            print(json.dumps({"result": "fail", "error": str(exc)}, sort_keys=True))
            return 3
        return Supervisor(
            manifest.services,
            manifest.restart_policy,
            startup_diagnostic_path=(
                arguments.manifest.parent / "supervisor-startup-failure.json"
            ),
        ).run(manifest.deployment_id)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0
