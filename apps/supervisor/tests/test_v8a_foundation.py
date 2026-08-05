from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apps.supervisor.v8a_trust import TrustMetadataError, verify_trust_metadata
from ops.windows.validate_json_schema import validate
from ops.windows.v8a.validate_contracts import (
    ContractError,
    _load_json,
    _validate_capabilities,
    _validate_release,
    validate_contracts,
)

ROOT = Path(__file__).resolve().parents[3]
V8A = ROOT / "ops" / "windows" / "v8a"


class V8AFoundationTests(unittest.TestCase):
    def test_contracts_validate_without_mutation(self) -> None:
        result = validate_contracts()
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["profile"], "personal")
        self.assertEqual(result["payload_state"], "development_template")
        self.assertFalse(result["mutations_performed"])
        self.assertEqual(len(result["contract_sha256"]), 10)
        self.assertTrue(
            all(len(digest) == 64 for digest in result["contract_sha256"].values())
        )

    def test_contract_schemas_are_strict(self) -> None:
        for name in (
            "product-profiles.schema.json",
            "capability-profiles.schema.json",
            "personal-release.schema.json",
            "trust-policy.schema.json",
            "release-trust-metadata.schema.json",
        ):
            schema = json.loads((V8A / name).read_text(encoding="utf-8"))
            self.assertFalse(schema["additionalProperties"], name)
        product_schema = json.loads(
            (V8A / "product-profiles.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(
            product_schema["properties"]["profiles"]["items"]["additionalProperties"]
        )

    def test_personal_is_simple_without_weakening_team(self) -> None:
        profiles = _load_json(V8A / "product-profiles.json")["profiles"]
        by_id = {item["id"]: item for item in profiles}
        personal = by_id["personal"]
        team = by_id["team_lan"]
        self.assertTrue(personal["default"])
        self.assertTrue(personal["authentication_required"])
        self.assertEqual(personal["ingress_mode"], "loopback")
        self.assertEqual(personal["browser_origin"], "http://127.0.0.1:3000")
        for field in (
            "caddy_required",
            "lan_dns_required",
            "certificates_required",
            "inbound_firewall_required",
            "dedicated_windows_identities_required",
        ):
            self.assertFalse(personal[field], field)
            self.assertTrue(team[field], field)
        self.assertEqual(team["browser_origin"], "https://rag.home.arpa")

    def test_capability_support_is_separate_from_local_validation(self) -> None:
        catalog = _load_json(V8A / "capability-profiles.json")
        self.assertTrue(
            all("release_support_class" in item for item in catalog["profiles"])
        )
        self.assertTrue(
            all("local_validation_fixture" in item for item in catalog["profiles"])
        )
        ocr = next(item for item in catalog["profiles"] if item["function"] == "ocr")
        self.assertEqual(ocr["accelerator_vendor"], "cpu")
        self.assertEqual(ocr["model_identity"], "PaddleOCR-VL 1.6")
        for profile in catalog["profiles"]:
            self.assertFalse(
                {
                    "command",
                    "executable_path",
                    "environment_keys",
                    "path",
                    "url",
                }.intersection(profile)
            )

    def test_capability_catalog_accepts_a_bounded_gpu_ocr_profile(self) -> None:
        catalog = copy.deepcopy(_load_json(V8A / "capability-profiles.json"))
        cpu = next(
            item for item in catalog["profiles"] if item["function"] == "ocr"
        )
        gpu = copy.deepcopy(cpu)
        gpu.update(
            {
                "profile_id": "ocr.paddleocr-vl-1.6.amd-gpu0.windows-x64",
                "accelerator_vendor": "amd",
                "runtime_device": "gpu:0",
                "minimum_vram_gib": 12,
            }
        )
        catalog["profiles"].append(gpu)

        schema = _load_json(V8A / "capability-profiles.schema.json")
        validate(catalog, schema)
        _validate_capabilities(catalog)

    def test_release_rejects_unsafe_artifact_path(self) -> None:
        release = copy.deepcopy(_load_json(V8A / "personal-release.json"))
        capabilities = _load_json(V8A / "capability-profiles.json")
        release["artifacts"][0]["relative_path"] = "../outside.exe"
        with self.assertRaisesRegex(ContractError, "safe and relative"):
            _validate_release(release, capabilities)

    def test_every_packaged_or_downloaded_asset_binds_a_license_notice(self) -> None:
        release = _load_json(V8A / "personal-release.json")
        self.assertTrue(all(item["license_notice_id"] for item in release["artifacts"]))
        self.assertTrue(
            all(item["license_notice_id"] for item in release["ollama_models"])
        )
        self.assertTrue(
            all(
                item["download_policy"] == "pinned_resumable"
                and len(item["expected_digest"]) == 64
                for item in release["ollama_models"]
            )
        )

    def test_personal_compose_publishes_loopback_only(self) -> None:
        compose = (V8A / "compose.personal.yaml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:5432:5432"', compose)
        self.assertIn('"127.0.0.1:9000:9000"', compose)
        self.assertNotIn('"0.0.0.0:5432:5432"', compose)
        self.assertNotIn('"0.0.0.0:9000:9000"', compose)
        self.assertNotIn("caddy:", compose)
        self.assertNotIn("9001:9001", compose)
        self.assertIn("RAG_PERSONAL_POSTGRES_DATA", compose)
        self.assertIn("RAG_PERSONAL_RUSTFS_DATA", compose)

    def test_trust_contract_defers_production_signing_to_v8f(self) -> None:
        trust = _load_json(V8A / "trust-policy.json")
        self.assertTrue(trust["production_signature_required"])
        self.assertEqual(trust["production_signing_milestone"], "V8F")
        self.assertEqual(trust["trust_anchor_state"], "v8f_required")
        self.assertTrue(trust["development_mode"]["production_key_forbidden"])
        self.assertTrue(trust["metadata"]["anti_rollback_required"])
        self.assertFalse(any(V8A.rglob("*.key")))

    def test_trust_metadata_fails_closed_for_root_time_revocation_and_rollback(
        self,
    ) -> None:
        now = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
        base = {
            "schema_version": 1,
            "policy_id": "local-rag-v8-release-trust",
            "root_id": "rag-root-v8",
            "release_id": "release-00000012",
            "release_sequence": 12,
            "issued_at": (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "artifacts_sha256": {
                "personal-release.json": "1" * 64,
                "product-profiles.json": "2" * 64,
                "capability-profiles.json": "3" * 64,
            },
            "revoked_release_ids": [],
            "revoked_profile_ids": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trust.json"

            def write(value: dict[str, object]) -> None:
                path.write_text(json.dumps(value), encoding="utf-8")

            write(base)
            verified = verify_trust_metadata(
                path,
                authenticated_root_id="rag-root-v8",
                installed_release_sequence=11,
                selected_profile_ids=frozenset(
                    {"ocr.paddleocr-vl-1.6.cpu.windows-x64"}
                ),
                now=now,
            )
            self.assertEqual(verified.release_sequence, 12)
            cases = []
            wrong_root = copy.deepcopy(base)
            cases.append((wrong_root, {"authenticated_root_id": "other-root"}, "root"))
            stale = copy.deepcopy(base)
            stale["expires_at"] = (now - timedelta(seconds=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            cases.append((stale, {"authenticated_root_id": "rag-root-v8"}, "expired"))
            revoked = copy.deepcopy(base)
            revoked["revoked_release_ids"] = [base["release_id"]]
            cases.append((revoked, {"authenticated_root_id": "rag-root-v8"}, "revoked"))
            rollback = copy.deepcopy(base)
            rollback["release_sequence"] = 10
            cases.append(
                (
                    rollback,
                    {
                        "authenticated_root_id": "rag-root-v8",
                        "installed_release_sequence": 11,
                    },
                    "older",
                )
            )
            for value, arguments, message in cases:
                with self.subTest(message=message):
                    write(value)
                    with self.assertRaisesRegex(TrustMetadataError, message):
                        verify_trust_metadata(path, now=now, **arguments)

    def test_powershell_entrypoints_parse(self) -> None:
        paths = sorted((*V8A.glob("*.ps1"), *V8A.glob("*.psm1")))
        self.assertGreaterEqual(len(paths), 6)
        for path in paths:
            quoted = str(path).replace("'", "''")
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    "[void][scriptblock]::Create((Get-Content -Raw "
                    f"-LiteralPath '{quoted}'))",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 0, f"{path.name}: {result.stderr}")

    def test_source_clone_setup_is_one_command_and_read_only_in_plan_mode(
        self,
    ) -> None:
        launcher = ROOT / "Setup-Local-RAG.cmd"
        setup = V8A / "Setup-RagFromSource.ps1"
        self.assertTrue(launcher.is_file())
        self.assertTrue(setup.is_file())
        launcher_text = launcher.read_text(encoding="utf-8")
        setup_text = setup.read_text(encoding="utf-8")
        self.assertIn("Setup-RagFromSource.ps1", launcher_text)
        for required in (
            "pnpm-lock.yaml",
            "uv.lock",
            "paddlepaddle==3.2.1",
            "paddleocr[doc-parser]==3.7.0",
            "pnpm",
            "build",
            "mc.RELEASE.2025-08-13T08-35-41Z",
            "c8db13ebeda31497f354c0e950809db0ae9b2a2a69b8afee68c128c37300c157",
            "Install-RagPersonal.ps1",
            "-DevelopmentSource",
            "Get-RagPersonalPreflight",
        ):
            self.assertIn(required, setup_text)
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(setup),
                "-Plan",
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["mode"], "read_only_source_setup_plan")
        self.assertFalse(plan["mutations_performed"])
        self.assertEqual(
            plan["user_installs"],
            ["Docker Desktop", "Ollama", "Node.js 20 or newer", "uv"],
        )

    def test_source_launchers_select_development_mode_automatically(self) -> None:
        for name in (
            "Start-Local-RAG.cmd",
            "Issue-New-Setup-Code.cmd",
            "Check-for-Updates.cmd",
        ):
            source = (V8A / name).read_text(encoding="utf-8")
            self.assertIn("development_template", source, name)
        self.assertIn(
            "-DevelopmentSource",
            (V8A / "Start-Local-RAG.cmd").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "-DevelopmentSource",
            (V8A / "Issue-New-Setup-Code.cmd").read_text(encoding="utf-8"),
        )

    def test_journal_is_ordered_resumable_and_ends_setup_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "journal-test.ps1"
            script.write_text(
                """
param([string]$Root,[string]$Module)
Import-Module $Module -Force
$state = Join-Path $Root 'state'
New-Item -ItemType Directory -Path $state | Out-Null
$journalPath = Join-Path $state 'journal.json'
$journal = New-RagPersonalJournal -Path $journalPath `
  -InstallRoot (Join-Path $Root 'install') `
  -DataRoot (Join-Path $Root 'data') -ReleaseRoot (Split-Path $Module -Parent)
$secrets = New-RagPersonalSecretDocument -InstallationId $journal.installation_id
$secretValues = @($secrets.values.PSObject.Properties.Value)
if ($secretValues.Count -ne (@($secretValues | Select-Object -Unique)).Count) {
  throw 'generated secrets are not pairwise distinct'
}
if ($secrets.values.PSObject.Properties.Name -notcontains 'csrf_signing_secret') {
  throw 'CSRF signing secret is missing'
}
$accessKeys = @(
  $secrets.values.rustfs_root_access, $secrets.values.rustfs_api_access,
  $secrets.values.rustfs_ingestion_access, $secrets.values.rustfs_deletion_access,
  $secrets.values.rustfs_maintenance_access
)
if (@($accessKeys | Where-Object { $_ -cnotmatch '^[a-z0-9]{13,20}$' }).Count -ne 0) {
  throw 'RustFS access-key identifier is outside the supported format'
}
$steps = @(
  'contracts_validated','prerequisites_validated','roots_created','secrets_created',
  'stores_started','postgres_provisioned','rustfs_provisioned','schema_migrated',
  'storage_bootstrapped','models_acquired','setup_code_issued'
)
foreach ($step in $steps) {
  Start-RagPersonalStep -Journal $journal -Step $step -JournalPath $journalPath
  Complete-RagPersonalStep -Journal $journal -Step $step -JournalPath $journalPath
}
$loaded = Read-RagPersonalJson -Path $journalPath
Assert-RagPersonalJournal -Journal $loaded
if ($loaded.state -cne 'setup_required') { throw 'wrong terminal state' }
if ($loaded.PSObject.Properties.Name -contains 'password') { throw 'secret field' }
[pscustomobject]@{state=$loaded.state;steps=$loaded.completed_steps.Count} |
  ConvertTo-Json -Compress
""",
                encoding="utf-8-sig",
            )
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-Root",
                    str(root),
                    "-Module",
                    str(V8A / "RagPersonal.psm1"),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(output, {"state": "setup_required", "steps": 11})

    def test_installer_and_uninstaller_enforce_v8a_scope(self) -> None:
        installer = (V8A / "Install-RagPersonal.ps1").read_text(encoding="utf-8")
        uninstaller = (V8A / "Uninstall-RagPersonal.ps1").read_text(encoding="utf-8")
        updater = (V8A / "Update-RagPersonal.ps1").read_text(encoding="utf-8")
        bootstrap = (V8A / "Verify-and-Install-Local-RAG.ps1").read_text(
            encoding="utf-8"
        )
        module = (V8A / "RagPersonal.psm1").read_text(encoding="utf-8")
        for required in (
            "RandomNumberGenerator",
            "installation-journal.json",
            "setup_required",
            "Assert-RagPersonalPortsFree",
            "Initialize-RagPersonalPostgres.ps1",
            "Initialize-RagPersonalRustfs.ps1",
            "storage-bootstrap",
            "pinned_resumable",
            "Issue-RagPersonalSetupCode.ps1",
            "Show-RagPersonalSetupCode.ps1",
            "Install-RagPersonalStartMenu",
        ):
            self.assertIn(required, installer + module)
        for forbidden in (
            "Set-RagFirewall",
            "Set-RagHostsEntry",
            "Install-RagCertificates",
            "bootstrap-admin",
            "New-LocalUser",
        ):
            self.assertNotIn(forbidden, installer)
        self.assertIn("Write-RagPersonalBootstrapEnvironments", installer)
        self.assertIn("DataAction", uninstaller)
        self.assertIn("DELETE LOCAL RAG DATA", uninstaller)
        self.assertIn("restore-verification.json", uninstaller)
        for launcher in (
            "Check-for-Updates.cmd",
            "Uninstall-Local-RAG.cmd",
            "Update-RagPersonal.ps1",
            "New-RagPersonalSignedRelease.ps1",
        ):
            self.assertTrue((V8A / launcher).is_file(), launcher)
        self.assertIn("Write-RagPersonalRuntimeEnvironments", installer)
        self.assertIn("migration.env", installer)
        self.assertIn("maintenance.env", installer)
        self.assertIn("data_preserved", uninstaller)
        self.assertIn("docker_volumes_removed=$false", uninstaller)
        self.assertNotIn("docker volume rm", uninstaller.lower())
        self.assertNotIn("-v", uninstaller.split("compose down", 1)[-1].splitlines()[0])
        self.assertIn("dropdb", updater)
        self.assertIn("createdb", updater)
        self.assertIn("entry.database_revision -cne $ExpectedRevision", updater)
        self.assertIn("Assert-RagPersonalRuntimeStopped", updater)
        self.assertIn("Close the Local RAG application window", updater)
        self.assertIn("cannot change the storage service contract", updater)
        self.assertNotIn("pg_restore -U rag_cluster_admin -d rag --no-owner", updater)
        self.assertIn("previously expanded release file was changed", bootstrap)
        self.assertIn(
            "existing release directory contains an unexpected file", bootstrap
        )


if __name__ == "__main__":
    unittest.main()
