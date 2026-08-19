from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
PREVIEW = ROOT / "ops" / "windows" / "team_preview"
MODULE = PREVIEW / "RagTeamLanPreview.psm1"


def run_powershell(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    quoted = " ".join("'" + item.replace("'", "''") + "'" for item in arguments)
    command = f"& {{ {script} }} {quoted}"
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        check=False,
        text=True,
    )


class TeamLanPreviewCoreTests(unittest.TestCase):
    def test_rfc1918_contract_rejects_public_loopback_link_local_and_ipv6(self) -> None:
        script = """
param($Module)
Import-Module $Module -Force
$accepted=@('10.0.0.1','172.16.0.1','172.31.255.254','192.168.1.1')
$rejected=@('8.8.8.8','127.0.0.1','169.254.1.1','172.15.1.1','172.32.1.1','::1')
foreach($value in $accepted){
  if((Assert-RagTeamPreviewRfc1918Address $value) -cne $value){throw "reject $value"}
}
foreach($value in $rejected){
  try{[void](Assert-RagTeamPreviewRfc1918Address $value);throw "accepted $value"}
  catch{if($_.Exception.Message -like 'accepted *'){throw}}
}
"pass"
"""
        result = run_powershell(script, str(MODULE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pass", result.stdout)

    def test_exact_inventory_detects_tampering_and_private_material(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "payload.txt").write_text("original", encoding="utf-8")
            create = run_powershell(
                "param($M,$R);Import-Module $M -Force;"
                "New-RagTeamPreviewInventory -Root $R|ConvertTo-Json -Compress",
                str(MODULE),
                str(root),
            )
            self.assertEqual(create.returncode, 0, create.stderr)
            verified = run_powershell(
                "param($M,$R);Import-Module $M -Force;"
                "Test-RagTeamPreviewInventory -Root $R|ConvertTo-Json -Compress",
                str(MODULE),
                str(root),
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            report = json.loads(verified.stdout.strip().splitlines()[-1])
            self.assertEqual(report["authenticity"], "unverified_unsigned")
            self.assertFalse(report["automatic_updates_available"])
            (root / "payload.txt").write_text("tampered", encoding="utf-8")
            tampered = run_powershell(
                "param($M,$R);Import-Module $M -Force;"
                "Test-RagTeamPreviewInventory -Root $R",
                str(MODULE),
                str(root),
            )
            self.assertNotEqual(tampered.returncode, 0)

            (root / "payload.txt").write_text("original", encoding="utf-8")
            (root / "server.key").write_text("private", encoding="utf-8")
            denied = run_powershell(
                "param($M,$R);Import-Module $M -Force;Get-RagTeamPreviewFiles -Root $R",
                str(MODULE),
                str(root),
            )
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("private material", denied.stderr)

    def test_inventory_canonicalizes_filesystem_provider_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "payload"
            root.mkdir()
            (root / "payload.txt").write_text("exact", encoding="utf-8")
            script = r"""
param($Module,$Parent,$Name)
Import-Module $Module -Force
$drive='RagTeamInventoryTest'
New-PSDrive -Name $drive -PSProvider FileSystem -Root $Parent|Out-Null
try {
  $providerRoot=Join-Path ($drive+':') $Name
  $inventory=New-RagTeamPreviewInventory -Root $providerRoot
  if($inventory.files.Count -ne 1 -or $inventory.files[0].path -cne 'payload.txt'){
    throw 'provider path was not canonicalized'
  }
  Test-RagTeamPreviewInventory -Root $providerRoot|Out-Null
} finally { Remove-PSDrive -Name $drive }
"""
            result = run_powershell(
                script,
                str(MODULE),
                str(root.parent),
                root.name,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_packaged_service_environment_templates_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = root / "release" / "ops" / "windows" / "environments"
            allowed.mkdir(parents=True)
            for name in (
                "caddy",
                "web",
                "api",
                "ingestion",
                "deletion",
                "inference",
                "ocr",
            ):
                (allowed / f"{name}.env.example").write_text(
                    "KEY=REPLACE\n", encoding="utf-8"
                )
            result = run_powershell(
                "param($M,$R);Import-Module $M -Force;"
                "New-RagTeamPreviewInventory -Root $R|Out-Null",
                str(MODULE),
                str(root),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            (allowed / "maintenance.env.example").write_text(
                "SECRET=value\n", encoding="utf-8"
            )
            denied = run_powershell(
                "param($M,$R);Import-Module $M -Force;Get-RagTeamPreviewFiles -Root $R",
                str(MODULE),
                str(root),
            )
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("private material", denied.stderr)

    def test_minimal_payload_assembles_and_staged_copy_is_reverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "release-input"
            required_files = (
                "runtimes/api-python/python.exe",
                "runtimes/ocr-python/python.exe",
                "runtimes/node/node.exe",
                "tools/openssl/openssl.exe",
                "tools/openssl/openssl.cnf",
                "tools/mc/mc.exe",
                "caddy.exe",
                "RagSupervisorService.exe",
                "deployment.json",
                "csp-header.caddy",
                "apps/api/app.py",
                "apps/web/server.js",
                "signed-assets/bge-reranker-v2-m3/model.bin",
                "signed-assets/paddleocr-vl-1.6/model.bin",
            )
            for relative in required_files:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((relative + "\n").encode())
            caddy_hash = hashlib.sha256((source / "caddy.exe").read_bytes()).hexdigest()
            openssl_hash = hashlib.sha256(
                (source / "tools/openssl/openssl.exe").read_bytes()
            ).hexdigest()
            output = root / "payload"
            builder = PREVIEW / "New-RagTeamLanPreviewPayload.ps1"
            assembled = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(builder),
                    "-ReleaseRoot",
                    str(source),
                    "-OutputRoot",
                    str(output),
                    "-CaddySha256",
                    caddy_hash,
                    "-OpenSslSha256",
                    openssl_hash,
                    "-AlembicRevision",
                    "0014_restart_without_backup",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(assembled.returncode, 0, assembled.stderr)
            self.assertTrue((output / "team-preview-inventory.json").is_file())
            self.assertTrue(
                (output / "release" / "team-preview-release.json").is_file()
            )
            self.assertTrue(
                (
                    output
                    / "release"
                    / "ops/windows/environments/api.env.example"
                ).is_file()
            )
            staged = root / "installed-release"
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    f"Copy-Item -LiteralPath '{output / 'release'}' "
                    f"-Destination '{staged}' -Recurse",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            inventory = json.loads(
                (output / "team-preview-inventory.json").read_text(encoding="utf-8")
            )
            verified = run_powershell(
                "param($M,$P,$I,$T);Import-Module $M -Force;"
                "Test-RagTeamPreviewInstalledRelease -PayloadRoot $P "
                "-InstalledReleaseRoot $I -ExpectedTreeSha256 $T|Out-Null",
                str(MODULE),
                str(output),
                str(staged),
                inventory["tree_sha256"],
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            (staged / "caddy.exe").write_bytes(b"tampered after privileged copy")
            tampered = run_powershell(
                "param($M,$P,$I,$T);Import-Module $M -Force;"
                "Test-RagTeamPreviewInstalledRelease -PayloadRoot $P "
                "-InstalledReleaseRoot $I -ExpectedTreeSha256 $T|Out-Null",
                str(MODULE),
                str(output),
                str(staged),
                inventory["tree_sha256"],
            )
            self.assertNotEqual(tampered.returncode, 0)

    def test_preview_verifier_refuses_signed_team_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "installed-release-state.json").write_text(
                "{}", encoding="utf-8"
            )
            result = run_powershell(
                "param($M,$R);Import-Module $M -Force;"
                "Assert-RagTeamPreviewProfileState -ProgramDataRoot $R "
                "-Expected FreshInstall",
                str(MODULE),
                str(root),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Signed Team state", result.stderr)

    def test_preserved_data_reinstall_requires_release_copy_when_current_is_absent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "uninstalled-current"
            result = run_powershell(
                "param($M,$R);Import-Module $M -Force;"
                "$copy=Test-RagTeamPreviewReleaseCopyRequired "
                "-CurrentReleaseRoot $R -DurableStoreResume $true;"
                "[pscustomobject]@{copy=$copy}|ConvertTo-Json -Compress",
                str(MODULE),
                str(missing),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout.strip().splitlines()[-1])["copy"])

    def test_host_launchers_and_manual_transactions_are_explicit(self) -> None:
        for name in (
            "Install-Local-RAG-LAN.cmd",
            "Update-Local-RAG-LAN.cmd",
            "Repair-Local-RAG-LAN.cmd",
            "Uninstall-Local-RAG-LAN.cmd",
        ):
            self.assertTrue((PREVIEW / name).is_file(), name)
        payload_builder = (PREVIEW / "New-RagTeamLanPreviewPayload.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("Set-RagAccountRights.ps1", payload_builder)
        self.assertIn("RagFirewallClassification.ps1", payload_builder)
        installer = (PREVIEW / "Install-RagTeamLanPreview.ps1").read_text(
            encoding="utf-8"
        )
        updater = (PREVIEW / "Update-RagTeamLanPreview.ps1").read_text(
            encoding="utf-8"
        )
        repair = (PREVIEW / "Repair-RagTeamLanPreview.ps1").read_text(
            encoding="utf-8"
        )
        for account in (
            "RagProxySvc",
            "RagWebSvc",
            "RagApiSvc",
            "RagIngestionSvc",
            "RagDeletionSvc",
            "RagInferenceSvc",
            "RagOcrSvc",
        ):
            self.assertIn(account, installer)
        self.assertIn("Windows cannot verify the publisher", installer)
        self.assertIn("does not prove publisher authenticity", installer)
        uninstaller = (PREVIEW / "Uninstall-RagTeamLanPreview.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("postgres_data_preserved=$true", uninstaller)
        self.assertIn("rustfs_data_preserved=$true", uninstaller)
        self.assertIn("team-preview-provisioning.json", uninstaller)
        self.assertIn("Managed host entry is missing", uninstaller)
        self.assertIn("Alembic revisions must be identical", updater)
        self.assertIn("automatic_updates_available=$false", updater)
        self.assertIn("rolled_back", updater)
        self.assertIn("release/config rollback", updater)
        self.assertIn("hostsBackup", repair)
        self.assertIn("caddyEnvironmentBackup", repair)
        self.assertIn("apiEnvironmentBackup", repair)
        self.assertIn("Set-EnvironmentValue", repair)
        self.assertIn("-Name RAG_LAN_IPV4", repair)
        self.assertIn("rolled_back", repair)
        module = MODULE.read_text(encoding="utf-8")
        self.assertIn("Local-RAG-LAN-Connector.zip", module)
        self.assertIn("Join-Path $OutputRoot 'connector'", module)
        self.assertIn("Invoke-RagTeamPreviewConnector", repair)
        self.assertIn("Wait-RagTeamPreviewGraphReady", installer)
        self.assertIn("Wait-RagTeamPreviewGraphReady", repair)
        readiness = MODULE.read_text(encoding="utf-8")
        self.assertIn("AttemptStartedAtUtc", readiness)
        self.assertIn("LastWriteTimeUtc -ge", readiness)
        self.assertIn("graph readiness timed out after startup failure", readiness)
        self.assertIn("-AttemptStartedAtUtc $startupAttempt", installer)
        self.assertIn("-AttemptStartedAtUtc $startupAttempt", repair)
        self.assertIn("Test-RagTeamPreviewInstalledRelease", installer)
        self.assertIn("-Action RemoveRequired", installer)
        self.assertIn("priorHostsBytes", installer)
        self.assertIn("importedCaThumbprint", installer)
        self.assertIn("Test-RagTeamPreviewReleaseCopyRequired", installer)
        self.assertNotIn("Get-FileHash -LiteralPath", repair.split("$caddyHash", 1)[1])
        module = MODULE.read_text(encoding="utf-8")
        self.assertIn("supervisor-startup-failure.json", module)
        self.assertIn("https://127.0.0.1:8443/ready", module)
        self.assertIn("https://rag.home.arpa/", module)
        self.assertIn("--cert", module)
        self.assertIn("--key", module)

    def test_preview_caddy_and_network_contract_are_ipv4_only(self) -> None:
        caddy = (PREVIEW / "Caddyfile").read_text(encoding="utf-8")
        network = (PREVIEW / "Test-RagTeamPreviewNetwork.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("bind {$RAG_LAN_IPV4}", caddy)
        self.assertNotIn("RAG_LAN_IPV6", caddy)
        self.assertNotIn(":80", caddy)
        self.assertIn("https://127.0.0.1:8443", caddy)
        self.assertIn("tls_client_auth", caddy)
        for required in (
            "Private",
            "LocalSubnet",
            "Public",
            "3000",
            "5432",
            "8443",
            "9000",
            "11434",
        ):
            self.assertIn(required, network)

    def test_every_preview_powershell_file_parses(self) -> None:
        paths = sorted((*PREVIEW.glob("*.ps1"), *PREVIEW.glob("*.psm1")))
        self.assertGreaterEqual(len(paths), 10)
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


if __name__ == "__main__":
    unittest.main()
