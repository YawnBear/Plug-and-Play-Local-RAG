from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
PREVIEW = ROOT / "ops" / "windows" / "team_preview"
MODULE = PREVIEW / "RagTeamLanPreview.psm1"
UPDATER = PREVIEW / "Update-RagTeamLanPreview.ps1"
BACKUP = PREVIEW / "Backup-RagTeamLanPreview.ps1"
RESTORE = PREVIEW / "Restore-Verify-RagTeamLanPreviewBackup.ps1"
LIVE_RESTORE = PREVIEW / "Restore-RagTeamLanPreviewBackup.ps1"
COMPOSE = PREVIEW / "Restore-RagTeamLanPreview.compose.yaml"


def run_powershell(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    quoted = " ".join("'" + item.replace("'", "''") + "'" for item in arguments)
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& {{ {script} }} {quoted}",
        ],
        capture_output=True,
        check=False,
        text=True,
    )


class TeamLanPreviewBackupUpdateTests(unittest.TestCase):
    def test_plan_is_read_only_and_does_not_require_an_installed_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate"
            (candidate / "release").mkdir(parents=True)
            (candidate / "team-preview-release.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile": "team_lan_preview_unsigned",
                        "payload_state": "assembled_unsigned",
                        "authenticity": "unverified_unsigned",
                        "automatic_updates_available": False,
                        "alembic_revision": "0014_restart_without_backup",
                        "caddy_sha256": "a" * 64,
                        "openssl_sha256": "b" * 64,
                    }
                ),
                encoding="utf-8",
            )
            inventory = run_powershell(
                "param($M,$R);Import-Module $M -Force;"
                "New-RagTeamPreviewInventory -Root $R|Out-Null",
                str(MODULE),
                str(candidate),
            )
            self.assertEqual(inventory.returncode, 0, inventory.stderr)
            result = run_powershell(
                "param($U,$R);& $U -CandidateRoot $R -Plan",
                str(UPDATER),
                str(candidate),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["result"], "plan")
            self.assertFalse(report["mutations_performed"])
            self.assertTrue(report["verified_backup_retained"])

    def test_update_requires_verified_backup_before_release_mutation(self) -> None:
        source = UPDATER.read_text(encoding="utf-8")
        required_states = (
            "prepared",
            "service_stopped",
            "backup_verified",
            "release_switched",
            "candidate_started",
            "verified",
            "committed",
            "rollback_started",
            "rolled_back",
            "recovery_failed",
        )
        for state in required_states:
            self.assertIn(f"'{state}'", source)
        backup_gate = source.index("Restore-Verify-RagTeamLanPreviewBackup.ps1")
        verified_state = source.index("Set-PreviewUpdateState 'backup_verified'")
        stage_copy = source.index("Copy-Item -LiteralPath (Join-Path $candidate 'release')")
        release_move = source.index("[IO.Directory]::Move($current,$previous)")
        self.assertLess(backup_gate, verified_state)
        self.assertLess(verified_state, stage_copy)
        self.assertLess(stage_copy, release_move)
        self.assertIn("restore_evidence_sha256", source)
        self.assertIn("nonterminal preview update journal", source)
        self.assertIn("original_service_running", source)
        self.assertIn("Verified backup retained", source)
        self.assertIn("Restore-RagTeamLanPreviewBackup.ps1", source)
        rollback = source.index("Restore-RagTeamLanPreviewBackup.ps1")
        prior_restart = source.index(
            "if($originalRunning){Start-Service RagSupervisor}", rollback
        )
        self.assertLess(rollback, prior_restart)

    def test_schema_upgrade_is_migrated_while_stopped_and_rolls_back_data(self) -> None:
        source = UPDATER.read_text(encoding="utf-8")
        switched = source.index("Set-PreviewUpdateState 'release_switched'")
        head = source.index("-m alembic heads")
        migration_state = source.index("Set-PreviewUpdateState 'migration_started'")
        upgrade = source.index("-m alembic upgrade head")
        migration_verified = source.index(
            "Set-PreviewUpdateState 'migration_verified'"
        )
        candidate_start = source.index("Start-Service RagSupervisor", migration_verified)
        self.assertLess(switched, head)
        self.assertLess(head, migration_state)
        self.assertLess(migration_state, upgrade)
        self.assertLess(upgrade, migration_verified)
        self.assertLess(migration_verified, candidate_start)
        self.assertIn("MIGRATION_DATABASE_URL", source)
        self.assertIn("postgres_migrator", source)
        self.assertIn("migration_attempted=$true", source)
        self.assertIn("after_migration_upgrade", source)
        self.assertIn("if($dataMayHaveMutated)", source)
        self.assertIn("prior_alembic_revision", source)
        self.assertIn("Restore-RagTeamLanPreviewBackup.ps1", source)

    def test_backup_revision_contract_is_generalized_but_bounded(self) -> None:
        revision_pattern = "^[0-9]{4}_[a-z0-9_]+$"
        for path in (BACKUP, RESTORE, LIVE_RESTORE):
            self.assertIn(revision_pattern, path.read_text(encoding="utf-8"))

    def test_graph_readiness_waits_and_surfaces_bounded_diagnostics(self) -> None:
        script = r"""
param($Updater)
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Updater,[ref]$tokens,[ref]$errors)
$function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Wait-RagTeamPreviewGraphReady'},$true)
if($null -eq $function){throw 'readiness helper missing'}
Invoke-Expression $function.Extent.Text
$script:probes=0
Wait-RagTeamPreviewGraphReady -ProgramDataRoot 'C:\synthetic-data' -ProfilesRoot 'C:\synthetic-profiles' -LocalAddress '192.168.1.20' -TimeoutSeconds 5 -ServiceProbe {'Running'} -ListenerProbe {param($Address) $script:probes++;if($script:probes -lt 2){@()}else{@([pscustomobject]@{LocalAddress=$Address})}} -Delay {}
if($script:probes -ne 2){throw 'delayed readiness was not polled'}
try{Wait-RagTeamPreviewGraphReady -ProgramDataRoot 'C:\synthetic-data' -ProfilesRoot 'C:\synthetic-profiles' -LocalAddress '192.168.1.20' -TimeoutSeconds 5 -ServiceProbe {'Stopped'} -ListenerProbe {@()} -Delay {} -DiagnosticProbe {'api[stage=readiness <- RuntimeError]'};throw 'failure accepted'}
catch{if($_.Exception.Message -like '*failure accepted*' -or $_.Exception.Message -notlike '*startup diagnostic: api[stage=readiness*'){throw}}
'pass'
"""
        result = run_powershell(script, str(UPDATER))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pass", result.stdout)

    def test_staged_release_inventory_rejects_copy_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary) / "stage"
            stage.mkdir()
            (stage / "payload.txt").write_text("exact", encoding="utf-8")
            script = r"""
param($Updater,$Module,$Stage)
Import-Module $Module -Force
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Updater,[ref]$tokens,[ref]$errors)
$function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Assert-StagedReleaseInventory'},$true)
if($null -eq $function){throw 'stage verifier missing'}
Invoke-Expression $function.Extent.Text
$file=Get-Item -LiteralPath (Join-Path $Stage 'payload.txt')
$inventory=[pscustomobject]@{files=@([pscustomobject]@{path='release/payload.txt';sha256=(Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant();size=[int64]$file.Length})}
$first=Assert-StagedReleaseInventory -Stage $Stage -PayloadInventory $inventory
if($first -cnotmatch '^[0-9a-f]{64}$'){throw 'exact stage rejected'}
[IO.File]::WriteAllText($file.FullName,'tampered',[Text.UTF8Encoding]::new($false))
try{Assert-StagedReleaseInventory -Stage $Stage -PayloadInventory $inventory|Out-Null;throw 'tamper accepted'}
catch{if($_.Exception.Message -like '*tamper accepted*'){throw}}
'pass'
"""
            result = run_powershell(script, str(UPDATER), str(MODULE), str(stage))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("pass", result.stdout)

    def test_same_schema_update_blocks_store_proxy_and_host_contracts(self) -> None:
        source = UPDATER.read_text(encoding="utf-8")
        for contract in (
            "compose.team-preview.yaml",
            "team-preview-provisioning.json",
            "Initialize-RagTeamPreviewPostgres.ps1",
            "Initialize-RagTeamPreviewRustfs.ps1",
            "storage_transfer.py",
            "object_storage.py",
            "Caddyfile",
            "caddy.exe",
            "RagSupervisorService.exe",
            "deployment.json",
        ):
            self.assertIn(contract, source)
        for binding in (
            "installed-deployment.json",
            "service\\RagSupervisorService.exe",
            "installed_deployment_sha256",
            "installed_service_host_sha256",
            "candidateControlPlane",
            "candidateReleaseControlPlane",
        ):
            self.assertIn(binding, source)

    def test_backup_and_restore_contracts_are_exact_and_secret_safe(self) -> None:
        backup = BACKUP.read_text(encoding="utf-8")
        restore = RESTORE.read_text(encoding="utf-8")
        live_restore = LIVE_RESTORE.read_text(encoding="utf-8")
        compose = COMPOSE.read_text(encoding="utf-8")
        self.assertIn("--format=custom", backup)
        self.assertIn("storage-export", backup)
        self.assertIn("outside ProgramData, Program Files, current, and candidate", backup)
        self.assertIn("$ConfirmServiceStopped", backup)
        self.assertIn("storage-import", restore)
        self.assertIn("storage-audit", restore)
        self.assertIn("v9_runtime_configuration_integrity()", restore)
        self.assertIn("relforcerowsecurity", restore)
        self.assertIn("has_function_privilege", restore)
        self.assertIn("source_catalog", restore)
        self.assertIn("object_inventory='exact_size_sha256_pass'", restore)
        self.assertIn("postgres_image_digest", restore)
        self.assertIn("rustfs_image_digest", restore)
        self.assertIn("dropdb", live_restore)
        self.assertIn("pg_restore", live_restore)
        self.assertIn("storage-import", live_restore)
        self.assertIn("storage-audit", live_restore)
        self.assertIn("restored_and_verified", live_restore)
        self.assertIn("EvidenceSha256", live_restore)
        self.assertIn("host_ip: 127.0.0.1", compose)
        self.assertIn("pgvector/pgvector:0.8.5-pg18-bookworm@sha256:", compose)
        self.assertIn("rustfs/rustfs:1.0.0-beta.10@sha256:", compose)
        self.assertNotIn("postgres_cluster_admin)@", restore)
        self.assertNotIn("rustfs_root_secret@", restore)


if __name__ == "__main__":
    unittest.main()
