import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.supervisor.cli import main
from apps.supervisor.manifest import DeploymentManifest, ManifestError
from apps.supervisor.planning import plan_as_dict

ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = ROOT / "ops" / "windows" / "deployment.json"


class ManifestPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_repository_manifest_is_valid_and_stable(self) -> None:
        first = DeploymentManifest.from_dict(self.value)
        second = DeploymentManifest.from_dict(
            json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        )
        self.assertEqual(first.digest(), second.digest())
        self.assertEqual(first.canonical_host, "rag.home.arpa")
        self.assertEqual(first.backup_state, "not_configured")
        self.assertIsNone(first.backup_destination)

    def test_unsigned_preview_has_one_ipv4_but_signed_team_stays_dual_stack(
        self,
    ) -> None:
        preview = copy.deepcopy(self.value)
        preview["product_profile"] = "team_lan_preview_unsigned"
        preview["deployment_readiness"]["prerequisite_checks"].remove(
            "signed_release_chain"
        )
        preview["deployment_readiness"]["prerequisite_checks"].extend(
            ["unsigned_inventory", "preview_profile_isolation", "rfc1918_ipv4"]
        )
        caddy = next(item for item in preview["services"] if item["name"] == "caddy")
        caddy["working_directory"] = r"C:\Program Files\LocalRAG\current"
        caddy["executable"] = caddy["working_directory"] + r"\caddy.exe"
        caddy["arguments"] = [
            "run",
            "--config",
            caddy["working_directory"] + r"\Caddyfile",
        ]
        caddy["environment_keys"].remove("RAG_LAN_IPV6")
        manifest = DeploymentManifest.from_dict(preview)
        self.assertEqual(manifest.product_profile, "team_lan_preview_unsigned")
        from apps.supervisor.runtime import RuntimeError as SupervisorRuntimeError
        from apps.supervisor.runtime import Supervisor

        supervisor = Supervisor(
            manifest.services,
            manifest.restart_policy,
            product_profile=manifest.product_profile,
        )
        self.assertEqual(
            supervisor._listener_addresses(
                manifest.services[0], {"RAG_LAN_IPV4": "192.168.1.4"}
            ),
            ("192.168.1.4",),
        )
        signed = DeploymentManifest.from_dict(self.value)
        with self.assertRaisesRegex(SupervisorRuntimeError, "IPv4 and IPv6"):
            Supervisor(signed.services, signed.restart_policy)._listener_addresses(
                signed.services[0], {"RAG_LAN_IPV4": "192.168.1.4"}
            )

    def test_only_caddy_can_be_a_lan_listener(self) -> None:
        invalid = copy.deepcopy(self.value)
        service = next(item for item in invalid["services"] if item["name"] == "api")
        service["listen_host"] = "192.168.1.20"
        with self.assertRaisesRegex(ManifestError, "loopback"):
            DeploymentManifest.from_dict(invalid)

    def test_readiness_url_must_match_declared_listener(self) -> None:
        invalid = copy.deepcopy(self.value)
        inference = next(
            item for item in invalid["services"] if item["name"] == "inference"
        )
        inference["readiness"]["url"] = "http://127.0.0.1:9999/health"
        with self.assertRaisesRegex(ManifestError, "declared loopback listener"):
            DeploymentManifest.from_dict(invalid)

    def test_public_firewall_profile_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.value)
        invalid["firewall_profile"] = "Public"
        with self.assertRaisesRegex(ManifestError, "Private"):
            DeploymentManifest.from_dict(invalid)

    def test_unsafe_global_object_deployment_id_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.value)
        invalid["deployment_id"] = r"..\escape"
        with self.assertRaisesRegex(ManifestError, "global objects"):
            DeploymentManifest.from_dict(invalid)

    def test_unknown_service_dependency_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.value)
        web = next(item for item in invalid["services"] if item["name"] == "web")
        web["dependencies"] = ["missing-service"]
        with self.assertRaisesRegex(ManifestError, "unknown dependencies"):
            DeploymentManifest.from_dict(invalid)

    def test_service_self_dependency_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.value)
        web = next(item for item in invalid["services"] if item["name"] == "web")
        web["dependencies"] = ["web"]
        with self.assertRaisesRegex(ManifestError, "itself"):
            DeploymentManifest.from_dict(invalid)

    def test_service_dependency_cycle_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.value)
        web = next(item for item in invalid["services"] if item["name"] == "web")
        web["dependencies"] = ["caddy"]
        with self.assertRaisesRegex(ManifestError, "cycle"):
            DeploymentManifest.from_dict(invalid)

    def test_caddy_version_and_checksum_are_an_atomic_pin(self) -> None:
        invalid = copy.deepcopy(self.value)
        invalid["caddy"]["version"] = "2.10.0"
        with self.assertRaisesRegex(ManifestError, "together"):
            DeploymentManifest.from_dict(invalid)
        invalid = copy.deepcopy(self.value)
        invalid["caddy"]["archive_sha512"] = "0" * 128
        with self.assertRaisesRegex(ManifestError, "approved"):
            DeploymentManifest.from_dict(invalid)
        invalid = copy.deepcopy(self.value)
        invalid["caddy"]["executable_sha256"] = "0" * 64
        with self.assertRaisesRegex(ManifestError, "approved"):
            DeploymentManifest.from_dict(invalid)

    def test_production_backup_cannot_be_enabled_without_approval(self) -> None:
        invalid = copy.deepcopy(self.value)
        invalid["backup"] = {
            "state": "configured",
            "destination": "D:\\backups",
        }
        with self.assertRaisesRegex(ManifestError, "not_configured"):
            DeploymentManifest.from_dict(invalid)

    def test_plan_is_dry_run_idempotent_and_surfaces_blockers(self) -> None:
        manifest = DeploymentManifest.from_dict(self.value)
        first = plan_as_dict(manifest)
        second = plan_as_dict(manifest)
        self.assertEqual(first, second)
        self.assertFalse(first["changes_applied"])
        self.assertEqual(first["mode"], "dry_run")
        statuses = {item["target"]: item["status"] for item in first["steps"]}
        self.assertEqual(statuses["caddy"], "ready")
        self.assertEqual(statuses["backup-schedule"], "not_configured")
        self.assertEqual(statuses["windows-service"], "attended")

    def test_cli_validate_does_not_modify_state(self) -> None:
        self.assertEqual(main(["validate", "--manifest", str(MANIFEST_PATH)]), 0)

    def test_run_foreground_fails_before_supervisor_run(self) -> None:
        with patch("apps.supervisor.cli.Supervisor.run") as run:
            result = main(["run-foreground", "--manifest", str(MANIFEST_PATH)])
        self.assertEqual(result, 3)
        run.assert_not_called()
