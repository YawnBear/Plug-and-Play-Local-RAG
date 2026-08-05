import ast
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WINDOWS = ROOT / "ops" / "windows"


def _replace_allowed_signers_pin(source: str, digest: str) -> str:
    updated, count = re.subn(
        r"(?m)^(\$PinnedAllowedSignersSha256 = ')[0-9a-f]{64}(')$",
        rf"\g<1>{digest}\g<2>",
        source,
    )
    if count != 1:
        raise AssertionError("expected exactly one allowed-signers digest pin")
    return updated


class WindowsArtifactTests(unittest.TestCase):
    def test_pinned_mc_installer_has_protected_exact_path_contract(self) -> None:
        installer = (WINDOWS / "Install-RagPinnedMc.ps1").read_text(
            encoding="utf-8"
        )
        for required in (
            "must run elevated",
            "LocalRAG-Tools\\mc",
            "ExpectedSha256",
            "Pinned mc tool root contains unexpected content",
            "Pinned mc copy verification failed",
            "'*S-1-5-18:(OI)(CI)(F)'",
            "'*S-1-5-32-544:(OI)(CI)(F)'",
            "'*S-1-5-18:(F)'",
            "'*S-1-5-32-544:(F)'",
            "Pinned mc post-lockdown verification failed",
            "Pinned mc file lacks an exact trusted full-control grant",
            "Pinned mc unapproved allow-ACE removal failed",
            "Pinned mc unapproved deny-ACE removal failed",
            "Pinned mc path remains writable by an unapproved identity",
        ):
            self.assertIn(required, installer)

    def test_store_provisioners_accept_only_protected_credential_inputs(self) -> None:
        postgres = (ROOT / "ops/security/provision-postgres-roles.ps1").read_text(
            encoding="utf-8"
        )
        rustfs = (ROOT / "ops/security/provision-rustfs-iam.ps1").read_text(
            encoding="utf-8"
        )
        for required in (
            "RoleSecretDirectory",
            '"$RoleName.password"',
            "SET client_min_messages TO warning;",
            "to_regclass('public.alembic_version') IS NULL",
            "\\if :bootstrap_function_acl",
            "\\endif",
            "Protected PostgreSQL role-secret path has an unapproved owner",
            "Protected PostgreSQL role-secret path grants an unapproved identity",
        ):
            self.assertIn(required, postgres)
        for required in (
            "RootCredentialFile",
            "RUSTFS_ROOT_ACCESS_KEY",
            "RUSTFS_ROOT_SECRET_KEY",
            "incompatible with MC_HOST syntax",
            "Protected RustFS root-credential file is invalid",
            "Protected RustFS root-credential file grants an unapproved identity",
        ):
            self.assertIn(required, rustfs)

    def test_production_application_initializer_separates_root_credentials(self) -> None:
        initializer = (WINDOWS / "Initialize-RagProductionApplication.ps1").read_text(
            encoding="utf-8"
        )
        bootstrap = (WINDOWS / "Bootstrap-RagProductionAdmin.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("Assert-RagAdministrator", initializer)
        self.assertIn("[Parameter(Mandatory)][string]$LanIpv4", initializer)
        self.assertIn("[Parameter(Mandatory)][string]$LanIpv6", initializer)
        self.assertIn("LanIpv4 must be an RFC1918 address", initializer)
        self.assertIn("LanIpv6 must be a unique-local IPv6 address", initializer)
        self.assertIn("RAG_LAN_IPV4 = $normalizedLanIpv4", initializer)
        self.assertIn("RAG_LAN_IPV6 = $normalizedLanIpv6", initializer)
        self.assertIn("RoleSecretDirectory $roleSecretRoot", initializer)
        self.assertIn("RootCredentialFile $rustfsRootCredential", initializer)
        self.assertIn("postgresql+psycopg://$Role", initializer)
        self.assertIn("OBJECT_STORAGE_ACCESS_KEY_ID = $storage.api", initializer)
        self.assertIn(
            "OBJECT_STORAGE_ACCESS_KEY_ID = $storage.maintenance",
            initializer,
        )
        self.assertNotIn("RUSTFS_ROOT_ACCESS_KEY =", initializer)
        self.assertNotIn("RUSTFS_ROOT_SECRET_KEY =", initializer)
        self.assertIn(
            "-ArgumentList @('-m', 'alembic', 'upgrade', 'head')",
            initializer,
        )
        self.assertIn("storage-bootstrap", initializer)
        self.assertIn("$output = @(foreach", initializer)
        self.assertIn("Invoke-RagNativeCommand", initializer)
        self.assertIn("--confirm-stopped bootstrap-admin", bootstrap)
        self.assertNotIn("Read-Host", bootstrap)
        alembic_environment = (ROOT / "apps/api/alembic/env.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('migration_url.replace("%", "%%")', alembic_environment)

    def test_production_store_bootstrap_is_isolated_and_fail_closed(self) -> None:
        bootstrap = (WINDOWS / "Start-RagProductionStores.ps1").read_text(
            encoding="utf-8"
        )
        for required in (
            "SupportsShouldProcess",
            "ConfirmImpact='High'",
            ".env.production.local",
            "$project = 'rag-prod'",
            "POSTGRES_PORT = '45432'",
            "RUSTFS_API_PORT = '59000'",
            "RUSTFS_CONSOLE_PORT = '59001'",
            "RandomNumberGenerator",
            "Set-RagProductionEnvironmentAcl",
            "Refusing to overwrite an existing production environment file",
            "Refusing to attach new credentials to pre-existing rag-prod resources",
            "--project-name $project",
            "--env-file $environmentPath",
            "up -d --wait postgres rustfs",
            "fresh_data",
        ):
            self.assertIn(required, bootstrap)
        for forbidden in (" compose down", " volume rm", "--volumes", "-v "):
            self.assertNotIn(forbidden, bootstrap)

    def test_windows_updater_exposes_transactional_same_schema_contract(self) -> None:
        updater = (WINDOWS / "Update-RagWindows.ps1").read_text(encoding="utf-8")
        transaction = (WINDOWS / "RagUpdateTransaction.ps1").read_text(
            encoding="utf-8"
        )
        installer = (WINDOWS / "Install-RagWindows.ps1").read_text(encoding="utf-8")
        for required in (
            "SupportsShouldProcess",
            "ConfirmImpact='High'",
            "Global\\LocalRAG.Update.v1",
            "Legacy bootstrap requires CurrentSignedReleaseManifest, CurrentSignedReleaseSignature, and CurrentReleaseArtifactRoot",
            "release_evidence_sha256",
            "Test-RagInstalledReleaseBinding",
            "alembic_revision",
            "authenticated restore evidence is unavailable",
            "Test-RagUpdate.ps1",
            "Test-RagDependencies.ps1",
            "Set-RagReleaseAcl.ps1",
            "Test-RagNetwork.ps1",
            "Updater v1 rejects Caddy or RagSupervisor service-host executable changes",
            "A nonterminal update journal requires explicit -Recover",
            "original_service_running",
            "original_start_mode",
            "automatic_scheduling='not_configured'",
            "Verified release staging parent must be empty before update verification",
            "New-Item -ItemType Directory -Path $verifiedReleaseRoot",
            (
                "LocalAddress must exactly match the protected "
                "installed-hosts.json addresses"
            ),
            "Get-RagInstalledUpdateAddresses",
            "Managed update prerequisite grants untrusted write access",
            '"*$SupervisorSid`:(R)"',
            '"*$ProxySid`:(R)"',
            "Set-RagInstalledUpdateArtifactAcl",
            "Start-RagUpdateGraph -Addresses $Addresses",
            "Assert-RagPathComponentsNotReparse -Path $path",
            "installed-release-state.json",
            "Get-RagZipTreeSha256",
            "commit_cleanup",
            "rollback_cleanup",
            "Assert-RagRollbackPreflight",
            "Assert-RagReplacementSourceHash",
            "MutexSecurity",
        ):
            self.assertIn(required, updater)
        self.assertNotIn("sc.exe config", updater)
        self.assertNotIn("Set-Service", updater)
        for required in (
            "installed-release-evidence.json",
            "installed-release-state.json",
            "final_manifest_sha256",
            "release_evidence_sha256",
            "release_tree_sha256",
            "Get-RagZipTreeSha256",
        ):
            self.assertIn(required, installer)
        for state in (
            "prepared",
            "service_stopped",
            "release_switched",
            "files_switched",
            "candidate_started",
            "verified",
            "commit_cleanup",
            "rollback_started",
            "rollback_cleanup",
            "rolled_back",
            "recovery_failed",
            "committed",
        ):
            self.assertIn(f"'{state}'", transaction)

    def test_windows_update_journal_path_and_revision_logic_is_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            harness = Path(temporary) / "journal.ps1"
            helper = WINDOWS / "RagUpdateTransaction.ps1"
            harness.write_text(
                f"""
$ErrorActionPreference='Stop'
. '{helper}'
. '{WINDOWS / 'RagManagedRootSafety.ps1'}'
$pf='C:\\Program Files\\LocalRAG'
$updates='C:\\ProgramData\\LocalRAG\\updates'
$id='0123456789abcdef0123456789abcdef'
$journal=[pscustomobject]@{{
  schema_version=1; update_id=$id; state='prepared'; created_at='2026-08-01T00:00:00Z'
  original_service_running=$false; original_start_mode='Manual'
  current_path=(Join-Path $pf 'current'); candidate_path=(Join-Path $pf "candidate-$id")
  previous_path=(Join-Path $pf "previous-$id")
  transaction_path=(Join-Path $updates "update-$id")
  verified_stage_path='C:\\ProgramData\\LocalRAG\\verified-release\\update-abcdefabcdefabcdefabcdefabcdefab'
  verified_root_created=$true
  replacements=@([pscustomobject]@{{target_relative='installed-release-evidence.json';
    backup_path=(Join-Path $updates "update-$id\\backup\\0.bin");
    staged_path=(Join-Path $updates "update-$id\\staged\\0.bin");
    old_sha256=('a'*64);new_sha256=('b'*64)}}); failure=$null
}}
Assert-RagUpdateJournal $journal $pf $updates
$escaped=$journal | ConvertTo-Json -Depth 10 | ConvertFrom-Json
$escaped.candidate_path='C:\\Windows\\candidate-'+$id
$rejected=$false
try {{ Assert-RagUpdateJournal $escaped $pf $updates }} catch {{ $rejected=$true }}
if (-not $rejected) {{ throw 'escaped path was accepted' }}
$same=([string]'0006_versioned_claim' -ceq [string]'0006_versioned_claim')
$changed=([string]'0007_changed' -ceq [string]'0006_versioned_claim')
if (-not $same -or $changed) {{ throw 'revision comparison is not ordinal exact' }}
$v4=(ConvertTo-RagUpdateAddress '192.168.1.10').ToString()
$v6=(ConvertTo-RagUpdateAddress 'fd00:0:0:0:0:0:0:10').ToString()
if ($v4 -ne '192.168.1.10' -or $v6 -ne 'fd00::10') {{
  throw 'address normalization failed'
}}
$fixture=Join-Path $PSScriptRoot 'fixture';New-Item -ItemType Directory -Path $fixture|Out-Null
$backup=Join-Path $fixture 'backup.bin';$staged=Join-Path $fixture 'staged.bin'
[IO.File]::WriteAllText($backup,'old');[IO.File]::WriteAllText($staged,'new')
$replacement=[pscustomobject]@{{target_relative='installed-release-state.json';backup_path=$backup;
  staged_path=$staged;old_sha256=(Get-RagFileSha256OrNull $backup);new_sha256=(Get-RagFileSha256OrNull $staged)}}
Assert-RagReplacementSourceHash $replacement
[IO.File]::WriteAllText($staged,'tampered');$hashRejected=$false
try {{ Assert-RagReplacementSourceHash $replacement }} catch {{ $hashRejected=$true }}
if (-not $hashRejected) {{ throw 'staged hash mismatch was accepted' }}
$preflight=[pscustomobject]@{{replacements=@($replacement)}}
[IO.File]::WriteAllText($staged,'new');Assert-RagRollbackPreflight $preflight $fixture
[IO.File]::WriteAllText($backup,'corrupt');$backupRejected=$false
try {{ Assert-RagRollbackPreflight $preflight $fixture }} catch {{ $backupRejected=$true }}
if (-not $backupRejected) {{ throw 'corrupt rollback backup was accepted' }}
'pass'
""",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "pass")

    def test_windows_update_cleanup_and_rollback_are_resumable_without_services(
        self,
    ) -> None:
        updater = WINDOWS / "Update-RagWindows.ps1"
        helper = WINDOWS / "RagUpdateTransaction.ps1"
        safety = WINDOWS / "RagManagedRootSafety.ps1"
        source = updater.read_text(encoding="utf-8")
        updates_assignment = source.index(
            "$updatesRoot = Join-Path $programData 'updates'"
        )
        updates_assertion = source.index(
            "Assert-RagPathComponentsNotReparse -Path $updatesRoot",
            updates_assignment,
        )
        journal_probe = source.index(
            "if (Test-Path -LiteralPath $journalPath -PathType Leaf)",
            updates_assignment,
        )
        self.assertLess(updates_assertion, journal_probe)
        self.assertIn(
            "$existingJournal.state -cin @('commit_cleanup','rollback_cleanup')",
            source,
        )
        self.assertIn(
            "if ($journal.state -ceq 'rollback_cleanup')",
            source,
        )
        transaction_source = helper.read_text(encoding="utf-8")
        preflight = transaction_source[
            transaction_source.index("function Assert-RagRollbackPreflight") :
        ]
        self.assertNotIn(
            "if (Test-Path -LiteralPath $path) { "
            "Assert-RagPathComponentsNotReparse -Path $path }",
            preflight,
        )
        for checked_path in ("$Path", "$temporary", "$Source", "$Target", "$pending"):
            self.assertIn(
                f"Assert-RagPathComponentsNotReparse -Path {checked_path}",
                transaction_source,
            )

        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            harness = Path(temporary) / "update-transaction-failpoints.ps1"
            harness.write_text(
                rf"""
$ErrorActionPreference='Stop'
. '{helper}'
. '{safety}'
$stateJournal=[pscustomobject]@{{state='commit_cleanup';failure=$null}}
$script:persistedState=$null
function global:Write-RagUpdateJsonAtomic {{
  param($Path,$Value)
  $script:persistedState=$Value.state
}}
function global:Set-RagProtectedUpdateAcl {{ throw 'failpoint:terminal-acl' }}
$terminalInterrupted=$false
try {{ Set-RagJournalState $stateJournal 'fixture-journal.json' committed $null }}
catch {{ $terminalInterrupted=$true }}
if (-not $terminalInterrupted -or $script:persistedState -cne 'committed' -or
    $stateJournal.state -cne 'commit_cleanup') {{
  throw 'terminal persistence failure lost the forward cleanup direction'
}}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(
  '{updater}',[ref]$tokens,[ref]$errors
)
if ($errors.Count -ne 0) {{ throw 'updater parse failed in fixture' }}
foreach ($name in @('Restore-RagUpdate','Invoke-RagUpdateCleanup')) {{
  $definition=@($ast.FindAll({{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -ceq $name
  }},$true))
  if ($definition.Count -ne 1) {{ throw "missing fixture function: $name" }}
  Invoke-Expression $definition[0].Extent.Text
}}
function global:Set-RagProtectedUpdateAcl {{ param($Path,[switch]$Recursive) }}
function global:Set-RagInstalledUpdateArtifactAcl {{
  param($Path,$TargetRelative,$SupervisorSid,$ProxySid)
}}
$script:serviceRunning=$false
$script:serviceEvents=[Collections.Generic.List[string]]::new()
function global:Stop-RagUpdateGraph {{
  param($AccountName)
  $script:serviceRunning=$false;$script:serviceEvents.Add('stop')
}}
function global:Start-RagUpdateGraph {{
  param($Addresses)
  $script:serviceRunning=$true;$script:serviceEvents.Add('start')
}}
$script:stateFailpoint=$null
function global:Set-RagJournalState {{
  param($Journal,$Path,$State,$Failure)
  $Journal.state=$State;$Journal.failure=$Failure
  if ($script:stateFailpoint -ceq $State) {{
    $script:stateFailpoint=$null
    throw "failpoint:$State"
  }}
}}
$script:cleanupRemoveFailAfter=-1
$script:cleanupRemoveCount=0
function global:Remove-Item {{
  [CmdletBinding()]param(
    [Parameter(Mandatory)][string]$LiteralPath,
    [switch]$Recurse,[switch]$Force
  )
  if ($script:cleanupRemoveCount -eq $script:cleanupRemoveFailAfter) {{
    $script:cleanupRemoveFailAfter=-1
    throw 'failpoint:cleanup-remove'
  }}
  $script:cleanupRemoveCount++
  Microsoft.PowerShell.Management\Remove-Item -LiteralPath $LiteralPath `
    -Recurse:$Recurse -Force:$Force
}}
function New-FixtureJournal {{
  param($Root,[bool]$OriginalRunning,[string]$State='prepared')
  $id='0123456789abcdef0123456789abcdef'
  $pf=Join-Path $Root 'program-files';$pd=Join-Path $Root 'program-data'
  $updates=Join-Path $pd 'updates';$verified=Join-Path $pd 'verified-release'
  foreach ($path in @($pf,$updates,$verified)) {{
    New-Item -ItemType Directory -Path $path -Force | Out-Null
  }}
  [pscustomobject]@{{
    schema_version=1;update_id=$id;state=$State
    created_at='2026-08-01T00:00:00Z'
    original_service_running=$OriginalRunning;original_start_mode='Manual'
    current_path=(Join-Path $pf 'current')
    candidate_path=(Join-Path $pf "candidate-$id")
    previous_path=(Join-Path $pf "previous-$id")
    transaction_path=(Join-Path $updates "update-$id")
    verified_stage_path=(Join-Path $verified "update-$id")
    verified_root_created=$false;replacements=@();failure=$null
  }}
}}
function New-OwnedDirectories {{
  param($Journal,[string[]]$Names)
  foreach ($name in $Names) {{
    New-Item -ItemType Directory -Path $Journal.$name -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $Journal.$name 'owned.txt'),'owned')
  }}
}}

foreach ($mode in @('Commit','Rollback')) {{
  $caseRoot=Join-Path $PSScriptRoot "cleanup-$mode"
  $state=if ($mode -ceq 'Commit') {{'commit_cleanup'}} else {{'rollback_cleanup'}}
  $journal=New-FixtureJournal $caseRoot $false $state
  New-OwnedDirectories $journal @('candidate_path','previous_path','transaction_path')
  $script:cleanupRemoveCount=0;$script:cleanupRemoveFailAfter=1
  $interrupted=$false
  try {{ Invoke-RagUpdateCleanup $journal $mode }} catch {{ $interrupted=$true }}
  if (-not $interrupted -or $journal.state -cne $state) {{
    throw "$mode cleanup did not remain resumable"
  }}
  Invoke-RagUpdateCleanup $journal $mode
  Invoke-RagUpdateCleanup $journal $mode
  foreach ($property in @('candidate_path','previous_path','transaction_path')) {{
    if (Test-Path -LiteralPath $journal.$property) {{
      throw "$mode cleanup was not idempotent: $property"
    }}
  }}
}}

foreach ($originalRunning in @($false,$true)) {{
  foreach ($boundary in @('rollback_started','rollback_cleanup')) {{
    $caseRoot=Join-Path $PSScriptRoot "restore-$originalRunning-$boundary"
    $journal=New-FixtureJournal $caseRoot $originalRunning
    New-OwnedDirectories $journal @('current_path','previous_path','transaction_path')
    [IO.File]::WriteAllText((Join-Path $journal.current_path 'version.txt'),'new')
    [IO.File]::WriteAllText((Join-Path $journal.previous_path 'version.txt'),'old')
    $script:serviceRunning=$true;$script:serviceEvents.Clear()
    $script:stateFailpoint=$boundary
    $interrupted=$false
    try {{
      Restore-RagUpdate $journal (Join-Path $caseRoot 'journal.json') `
        (Join-Path $caseRoot 'program-data') (Join-Path $caseRoot 'program-files') `
        @('fixture') @('127.0.0.1','::1') 'S-1-5-18' 'S-1-5-18'
    }} catch {{ $interrupted=$true }}
    if (-not $interrupted -or $journal.state -cne $boundary) {{
      throw "rollback boundary was not interrupted: $boundary"
    }}
    if ($boundary -ceq 'rollback_started') {{
      Restore-RagUpdate $journal (Join-Path $caseRoot 'journal.json') `
        (Join-Path $caseRoot 'program-data') (Join-Path $caseRoot 'program-files') `
        @('fixture') @('127.0.0.1','::1') 'S-1-5-18' 'S-1-5-18'
    }} else {{
      Invoke-RagUpdateCleanup $journal Rollback
      Set-RagJournalState $journal (Join-Path $caseRoot 'journal.json') `
        rolled_back $journal.failure
    }}
    $restored=[IO.File]::ReadAllText((Join-Path $journal.current_path 'version.txt'))
    if ($restored -cne 'old' -or $script:serviceRunning -ne $originalRunning -or
        $journal.state -cne 'rolled_back') {{
      throw "rollback recovery contract failed: $originalRunning/$boundary"
    }}
  }}
}}
'pass'
""",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness),
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "pass")

    def test_tree_digest_is_utf8_bytewise_identical_across_python_and_powershell(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            for name, value in {
                "Zeta.txt": b"Z",
                "alpha.txt": b"a",
                "\u00e9.txt": b"precomposed",
                "zeta/\U0001f600.bin": b"emoji",
            }.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
            powershell = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    (
                        f". '{WINDOWS / 'Expand-RagVerifiedRelease.ps1'}'; "
                        f"Get-RagTreeSha256 -Root '{root}'"
                    ),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(powershell.returncode, 0, powershell.stderr)
            python = subprocess.run(
                [
                    str(ROOT / "apps" / "api" / ".venv" / "Scripts" / "python.exe"),
                    "-c",
                    (
                        "from pathlib import Path;"
                        "from ops.windows.verify_dependencies import tree_sha256;"
                        f"print(tree_sha256(Path({str(root)!r})))"
                    ),
                ],
                cwd=ROOT,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(python.returncode, 0, python.stderr)
            self.assertEqual(powershell.stdout.strip(), python.stdout.strip())
            archive = Path(temporary) / "release.zip"
            with zipfile.ZipFile(archive, "w") as output:
                for path in sorted(root.rglob("*")):
                    if path.is_file() and path != archive:
                        output.write(path, path.relative_to(root).as_posix())
            zip_digest = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    (
                        f". '{WINDOWS / 'Expand-RagVerifiedRelease.ps1'}'; "
                        f"Get-RagZipTreeSha256 -Archive '{archive}'"
                    ),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(zip_digest.returncode, 0, zip_digest.stderr)
            self.assertEqual(zip_digest.stdout.strip(), powershell.stdout.strip())

    def test_ocr_output_digest_ignores_only_nondeterministic_docx_bytes(self) -> None:
        from ops.windows.verify_dependencies import ocr_output_sha256

        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            (root / "page_res.json").write_text('{"text":"stable"}', encoding="utf-8")
            (root / "page.md").write_text("stable", encoding="utf-8")
            (root / "page.png").write_bytes(b"stable-png")
            (root / "page.docx").write_bytes(b"first-zip-timestamp")
            first = ocr_output_sha256(root)
            (root / "page.docx").write_bytes(b"second-zip-timestamp")
            self.assertEqual(first, ocr_output_sha256(root))
            (root / "page_res.json").write_text('{"text":"changed"}', encoding="utf-8")
            self.assertNotEqual(first, ocr_output_sha256(root))

    def test_caddy_has_https_only_and_loopback_upstreams(self) -> None:
        value = (WINDOWS / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("https://rag.home.arpa:443", value)
        self.assertNotIn("http://rag.home.arpa", value)
        self.assertNotIn(":80", value)
        self.assertIn("https://127.0.0.1:8443", value)
        self.assertIn("tls_client_auth", value)
        self.assertIn("header_up Host rag.home.arpa", value)
        self.assertIn("header_up -Forwarded", value)
        self.assertIn("header_up -X-Real-IP", value)
        self.assertIn("flush_interval -1", value)
        self.assertIn("max_size 100MB", value)
        self.assertIn("admin off", value)
        self.assertIn("bind {$RAG_LAN_IPV4} {$RAG_LAN_IPV6}", value)
        self.assertNotIn("admin 127.0.0.1", value)
        self.assertIn("csp-header.caddy", value)
        self.assertIn(
            "@document_content path_regexp "
            "^/api/documents/[0-9a-fA-F-]{36}/content$",
            value,
        )
        self.assertIn(
            'header @document_content X-Frame-Options "SAMEORIGIN"',
            value,
        )
        self.assertIn(
            'header @non_document_content X-Frame-Options "DENY"',
            value,
        )
        self.assertNotIn('\t\tX-Frame-Options "DENY"', value)
        self.assertNotIn("'unsafe-inline'", value)
        self.assertNotIn("'unsafe-eval'", value)

    def test_generated_next_csp_uses_exact_hashes_and_tamper_fails(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            html = root / "index.html"
            html.write_text(
                "<html><head><style>body{color:#123}</style></head>"
                '<body><div style="max-width:520px"></div>'
                "<script>self.__next_f.push([1])</script></body></html>",
                encoding="utf-8",
            )
            artifact = root / "csp-header.caddy"
            generated = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(WINDOWS / "New-RagCspArtifact.ps1"),
                    "-RenderedHtmlRoot",
                    str(root),
                    "-OutputPath",
                    str(artifact),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            content = artifact.read_text(encoding="utf-8")
            self.assertIn("'sha256-", content)
            self.assertIn("'unsafe-hashes'", content)
            self.assertIn(
                "header @document_content Content-Security-Policy "
                "\"frame-ancestors 'self';",
                content,
            )
            self.assertIn(
                "header @non_document_content Content-Security-Policy "
                "\"frame-ancestors 'none';",
                content,
            )
            self.assertNotIn("'unsafe-inline'", content)
            self.assertIn("worker-src 'self'", content)
            verified = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(WINDOWS / "Test-RagCspArtifact.ps1"),
                    "-RenderedHtmlRoot",
                    str(root),
                    "-CspArtifact",
                    str(artifact),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            artifact.write_text(content + "# tampered\n", encoding="utf-8")
            rejected = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(WINDOWS / "Test-RagCspArtifact.ps1"),
                    "-RenderedHtmlRoot",
                    str(root),
                    "-CspArtifact",
                    str(artifact),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)

    def test_release_trust_is_unconfigured_and_caddy_is_pinned(self) -> None:
        deployment = json.loads(
            (WINDOWS / "deployment.json").read_text(encoding="utf-8")
        )
        caddy = json.loads((WINDOWS / "caddy-release.json").read_text(encoding="utf-8"))
        self.assertEqual(deployment["updates"]["state"], "not_configured")
        self.assertIsNone(deployment["updates"]["public_key_sha256"])
        self.assertEqual(deployment["deployment_readiness"]["state"], "installable")
        self.assertEqual(caddy["state"], "configured")
        self.assertEqual(caddy["version"], "2.11.3")
        self.assertEqual(
            caddy["archive_sha512"],
            "338f5557a1554677875b79dbc4b10d008781111ad29223811e64217936fa5d58602ddd54724ef1cb1473b7ec07805cf5286d6aa1e810febde7e36daf497d791f",
        )
        self.assertEqual(
            caddy["executable_sha256"],
            "67514bc0449ae9b1465cf3d59ab269cb451e8ed88d991e461b24d1337b67f536",
        )
        identities = {service["identity"] for service in deployment["services"]}
        self.assertTrue(all(identity.startswith(".\\Rag") for identity in identities))
        self.assertFalse(any("NT SERVICE" in identity for identity in identities))
        self.assertEqual(
            {service["name"] for service in deployment["services"]},
            {"caddy", "web", "api", "ingestion", "deletion", "inference", "ocr"},
        )
        web = next(
            service for service in deployment["services"] if service["name"] == "web"
        )
        self.assertNotIn("INTERNAL_API_URL", web["environment_keys"])
        executables = {
            service["name"]: service["executable"] for service in deployment["services"]
        }
        self.assertEqual(
            executables["web"],
            r"C:\Program Files\LocalRAG\current\runtimes\node\node.exe",
        )
        for name in ("api", "ingestion", "deletion", "inference", "ocr"):
            self.assertEqual(
                executables[name],
                r"C:\Program Files\LocalRAG\current\runtimes\api-python\python.exe",
            )
        runtime_schema = json.loads(
            (WINDOWS / "release-evidence.schema.json").read_text(encoding="utf-8")
        )["properties"]["runtimes"]
        self.assertTrue(
            {
                "api_python_tree_sha256",
                "ocr_python_tree_sha256",
                "node_tree_sha256",
                "openssl_tree_sha256",
            }.issubset(runtime_schema["required"])
        )

    def test_powershell_update_verifier_checks_signed_artifact_contract(self) -> None:
        script = (WINDOWS / "Test-RagUpdate.ps1").read_text(encoding="utf-8")
        for required in (
            "update-manifest.json",
            "update-manifest.json.sig",
            "Assert-ExactProperties",
            "Artifact filename is unsafe or duplicated",
            "Artifact size verification failed",
            "Artifact SHA-256 verification failed",
            "Get-FileHash",
            "[Environment]::SystemDirectory",
            "OpenSSH\\ssh-keygen.exe",
            "Join-Path ([Environment]::SystemDirectory) 'cmd.exe'",
            "$process.StartInfo.WorkingDirectory = $stagePath",
            "< update-manifest.json",
            "ParameterSetName = 'Existing'",
            "ExistingSignedStage",
            "Signed update stage direct-file set is not exact",
            "Signed update stage ACL is not protected RX-only",
            "CleanupOnSuccess",
        ):
            self.assertIn(required, script)
        self.assertIn(
            "[Parameter(Mandatory, ParameterSetName = 'Copy')]",
            script,
        )
        self.assertIn(
            "[Parameter(Mandatory, ParameterSetName = 'Existing')]",
            script,
        )
        self.assertEqual(
            script.count("[StringComparison]::OrdinalIgnoreCase"),
            2,
        )
        self.assertNotIn(
            "[IO.Path]::GetDirectoryName($source) -cne $artifactRootPath",
            script,
        )
        self.assertNotIn(
            "[IO.Path]::GetDirectoryName($artifactPath) -cne $stagePath",
            script,
        )
        self.assertNotIn("$process.StartInfo.FileName = 'ssh-keygen.exe'", script)
        self.assertNotIn("RedirectStandardInput", script)
        schema = json.loads(
            (WINDOWS / "update-manifest.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertTrue(schema["properties"]["artifacts"]["uniqueItems"])
        artifact_schema = schema["properties"]["artifacts"]["items"]
        self.assertFalse(artifact_schema["additionalProperties"])
        self.assertEqual(
            set(artifact_schema["required"]), {"filename", "sha256", "size"}
        )

    def test_powershell_duplicate_parser_rejects_escaped_duplicate_key(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            manifest = root / "update-manifest.json"
            manifest.write_text(
                '{"schema_version":1,"version":"3","\\u0076ersion":"4","artifacts":[]}',
                encoding="utf-8",
            )
            signature = root / "update-manifest.json.sig"
            signature.write_text("not reached", encoding="utf-8")
            allowed = root / "allowed_signers"
            allowed.write_text("not reached", encoding="utf-8")
            stage = root / "stage"
            stage.mkdir()
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(WINDOWS / "Test-RagUpdate.ps1"),
                    "-Manifest",
                    str(manifest),
                    "-Signature",
                    str(signature),
                    "-ArtifactRoot",
                    str(root),
                    "-SignedArtifactStageRoot",
                    str(stage),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate JSON key: version", result.stderr)

    def test_failed_update_signature_removes_immutable_stage(self) -> None:
        ssh_keygen = Path(r"C:\Windows\System32\OpenSSH\ssh-keygen.exe")
        if not ssh_keygen.is_file():
            self.skipTest("Windows OpenSSH signer is unavailable")
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            copied_windows = root / "ops" / "windows"
            copied_windows.mkdir(parents=True)
            verifier = copied_windows / "Test-RagUpdate.ps1"
            verifier.write_bytes((WINDOWS / "Test-RagUpdate.ps1").read_bytes())

            private_key = root / "offline-key"
            generated = subprocess.run(
                [
                    str(ssh_keygen),
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-f",
                    str(private_key),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            public_parts = private_key.with_suffix(".pub").read_text().split()
            allowed = copied_windows / "release-allowed-signers"
            allowed.write_bytes(
                f"rag-release {public_parts[0]} {public_parts[1]}\n".encode()
            )
            allowed_sha = hashlib.sha256(allowed.read_bytes()).hexdigest()
            verifier.write_text(
                _replace_allowed_signers_pin(
                    verifier.read_text(encoding="utf-8"),
                    allowed_sha,
                ),
                encoding="utf-8",
            )

            artifact = root / "artifact.bin"
            artifact.write_bytes(b"signed release candidate")
            manifest = root / "update-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "version": "cleanup-test",
                        "artifacts": [
                            {
                                "filename": artifact.name,
                                "sha256": hashlib.sha256(
                                    artifact.read_bytes()
                                ).hexdigest(),
                                "size": artifact.stat().st_size,
                            }
                        ],
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            signature = root / "update-manifest.json.sig"
            signature.write_text("deliberately-invalid-signature\n", encoding="utf-8")
            stage_root = root / "stage"
            stage_root.mkdir()

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(verifier),
                    "-Manifest",
                    str(manifest),
                    "-Signature",
                    str(signature),
                    "-ArtifactRoot",
                    str(root),
                    "-SignedArtifactStageRoot",
                    str(stage_root),
                    "-CleanupOnFailure",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("signature verification failed", result.stderr)
            self.assertEqual(list(stage_root.iterdir()), [])

    def test_certificate_transaction_rolls_back_every_fault_point(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            script = rf"""
$ErrorActionPreference = 'Stop'
. '{WINDOWS / "RagCertificateTransaction.ps1"}'
$names = @('rag.home.arpa','api-loopback','caddy-api-client','supervisor-api-client')
foreach ($fault in 1..16) {{
  $root = Join-Path '{temporary}' "fault-$fault"
  $liveCert = Join-Path $root 'live-cert'; $liveKey = Join-Path $root 'live-key'
  $stageCert = Join-Path $root 'stage-cert'; $stageKey = Join-Path $root 'stage-key'
  $rollbackCert = Join-Path $root 'rollback-cert'; $rollbackKey = Join-Path $root 'rollback-key'
  New-Item -ItemType Directory -Path $liveCert,$liveKey,$stageCert,$stageKey,$rollbackCert,$rollbackKey | Out-Null
  foreach ($name in $names) {{
    Set-Content -LiteralPath (Join-Path $liveCert "$name.crt") -Value "old-cert-$name"
    Set-Content -LiteralPath (Join-Path $liveKey "$name.key") -Value "old-key-$name"
    Set-Content -LiteralPath (Join-Path $stageCert "$name.crt") -Value "new-cert-$name"
    Set-Content -LiteralPath (Join-Path $stageKey "$name.key") -Value "new-key-$name"
  }}
  try {{
    Invoke-RagCertificateLeafSetSwitch -LiveCertificateRoot $liveCert -LiveSecretRoot $liveKey `
      -StageCertificateRoot $stageCert -StageSecretRoot $stageKey `
      -RollbackCertificateRoot $rollbackCert -RollbackSecretRoot $rollbackKey `
      -LeafNames $names -FaultAfterMutation $fault | Out-Null
    throw 'fault injection unexpectedly succeeded'
  }} catch {{
    if ($_.Exception.Message -notmatch 'Injected certificate mutation fault') {{ throw }}
  }}
  foreach ($name in $names) {{
    if ((Get-Content -Raw (Join-Path $liveCert "$name.crt")).Trim() -cne "old-cert-$name") {{ throw 'certificate rollback mismatch' }}
    if ((Get-Content -Raw (Join-Path $liveKey "$name.key")).Trim() -cne "old-key-$name") {{ throw 'key rollback mismatch' }}
    if ((Get-Content -Raw (Join-Path $stageCert "$name.crt")).Trim() -cne "new-cert-$name") {{ throw 'staged certificate rollback mismatch' }}
    if ((Get-Content -Raw (Join-Path $stageKey "$name.key")).Trim() -cne "new-key-$name") {{ throw 'staged key rollback mismatch' }}
  }}
}}
"""
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_certificate_validator_enforces_exact_supervisor_san_and_eku(
        self,
    ) -> None:
        openssl = Path(r"C:\tmp\rag-v4-release-inputs\tools\openssl\openssl.exe")
        if not openssl.is_file():
            self.skipTest("packaged OpenSSL test fixture is unavailable")
        openssl_sha = hashlib.sha256(openssl.read_bytes()).hexdigest()
        openssl_config = openssl.with_name("openssl.cnf")
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            certificates = root / "certificates"
            secrets = root / "secrets"
            script = rf"""
$ErrorActionPreference = 'Stop'
function global:Import-Certificate {{
  param([string]$FilePath,[string]$CertStoreLocation)
  [pscustomobject]@{{ FilePath=$FilePath; CertStoreLocation=$CertStoreLocation }}
}}
& '{WINDOWS / "Install-RagCertificates.ps1"}' -OpenSslPath '{openssl}' `
  -OpenSslSha256 '{openssl_sha}' -CertificateRoot '{certificates}' -SecretRoot '{secrets}' | Out-Null
& '{WINDOWS / "Test-RagCertificateSet.ps1"}' -OpenSslPath '{openssl}' `
  -OpenSslSha256 '{openssl_sha}' -CertificateRoot '{certificates}' -SecretRoot '{secrets}' | Out-Null
$original = Join-Path '{certificates}' 'supervisor-api-client.original.crt'
Copy-Item (Join-Path '{certificates}' 'supervisor-api-client.crt') $original
$csr = Join-Path '{certificates}' 'supervisor-test.csr'
$ext = Join-Path '{certificates}' 'supervisor-test.cnf'
& '{openssl}' req -config '{openssl_config}' -new -key (Join-Path '{secrets}' 'supervisor-api-client.key') `
  -subj '/CN=supervisor-api-client' -out $csr
@('basicConstraints=critical,CA:FALSE','subjectAltName=DNS:supervisor-api-client,DNS:extra.invalid','extendedKeyUsage=clientAuth','keyUsage=digitalSignature,keyEncipherment') |
  Set-Content -LiteralPath $ext -Encoding ascii
& '{openssl}' x509 -req -sha256 -days 825 -in $csr `
  -CA (Join-Path '{certificates}' 'local-rag-ca.crt') `
  -CAkey (Join-Path '{secrets}' 'local-rag-ca.key') -extfile $ext `
  -out (Join-Path '{certificates}' 'supervisor-api-client.crt')
try {{
  & '{WINDOWS / "Test-RagCertificateSet.ps1"}' -OpenSslPath '{openssl}' `
    -OpenSslSha256 '{openssl_sha}' -CertificateRoot '{certificates}' -SecretRoot '{secrets}' | Out-Null
  throw 'extra SAN unexpectedly accepted'
}} catch {{
  if ($_.Exception.Message -notmatch 'SAN set is not exact') {{ throw }}
}}
Copy-Item $original (Join-Path '{certificates}' 'supervisor-api-client.crt') -Force
@('basicConstraints=critical,CA:FALSE','subjectAltName=DNS:supervisor-api-client','extendedKeyUsage=clientAuth,serverAuth','keyUsage=digitalSignature,keyEncipherment') |
  Set-Content -LiteralPath $ext -Encoding ascii
& '{openssl}' x509 -req -sha256 -days 825 -in $csr `
  -CA (Join-Path '{certificates}' 'local-rag-ca.crt') `
  -CAkey (Join-Path '{secrets}' 'local-rag-ca.key') -extfile $ext `
  -out (Join-Path '{certificates}' 'supervisor-api-client.crt')
try {{
  & '{WINDOWS / "Test-RagCertificateSet.ps1"}' -OpenSslPath '{openssl}' `
    -OpenSslSha256 '{openssl_sha}' -CertificateRoot '{certificates}' -SecretRoot '{secrets}' | Out-Null
  throw 'extra EKU unexpectedly accepted'
}} catch {{
  if ($_.Exception.Message -notmatch 'EKU set is not exact') {{ throw }}
}}
exit 0
"""
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_service_host_compiles_and_uses_packaged_python_contract(self) -> None:
        compiler = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
        if not compiler.is_file():
            self.skipTest(".NET Framework C# compiler is unavailable")
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            executable = Path(temporary) / "RagSupervisorService.exe"
            harness = Path(temporary) / "ServiceHostContract.cs"
            harness.write_text(
                """
using System;
using System.Reflection;
public static class ServiceHostContract {
  public static int Main() {
    Environment.SetEnvironmentVariable("ProgramFiles", @"C:\\missing-rag-test");
    object service=Activator.CreateInstance(typeof(LocalRag.RagSupervisorService), true);
    MethodInfo start=service.GetType().GetMethod("OnStart", BindingFlags.Instance|BindingFlags.NonPublic);
    try { start.Invoke(service, new object[] { new string[0] }); return 9; }
    catch (TargetInvocationException error) {
      return error.InnerException is InvalidOperationException ? 0 : 8;
    }
  }
}
""",
                encoding="utf-8",
            )
            compiled = subprocess.run(
                [
                    str(compiler),
                    "/nologo",
                    "/target:exe",
                    "/main:ServiceHostContract",
                    f"/out:{executable}",
                    "/reference:System.ServiceProcess.dll",
                    str(WINDOWS / "RagSupervisorService.cs"),
                    str(harness),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            launched = subprocess.run(
                [str(executable)],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )
            self.assertEqual(launched.returncode, 0, launched.stderr)
        source = (WINDOWS / "RagSupervisorService.cs").read_text(encoding="utf-8")
        self.assertIn('"runtimes", "api-python", "python.exe"', source)
        self.assertNotIn('".venv"', source)

    def test_managed_root_preplant_and_reparse_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            existing = base / "existing"
            missing = base / "missing"
            existing.mkdir()
            script = (
                f". '{WINDOWS / 'RagManagedRootSafety.ps1'}'; "
                f"try {{ Assert-RagFreshManagedRoots -ProgramDataRoot '{existing}' "
                f"-ProgramFilesRoot '{missing}'; throw 'unexpected acceptance' }} catch {{ "
                "if ($_.Exception.Message -notmatch 'must not preexist') { throw } }; exit 0"
            )
            rejected = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(rejected.returncode, 0, rejected.stderr)
            target = base / "target"
            target.mkdir()
            junction = base / "junction"
            made = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    f"New-Item -ItemType Junction -Path '{junction}' -Target '{target}'",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            if made.returncode == 0:
                reparse = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        (
                            f". '{WINDOWS / 'RagManagedRootSafety.ps1'}'; "
                            f"try {{ Assert-RagPathComponentsNotReparse '{junction}'; "
                            "throw 'unexpected acceptance' } catch { if ($_.Exception.Message -notmatch "
                            "'reparse point') { throw } }; exit 0"
                        ),
                    ],
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(reparse.returncode, 0, reparse.stderr)

    def test_release_acl_plan_has_exact_runtime_ownership(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            release = Path(temporary)
            targets = (
                "runtimes/api-python",
                "apps/api",
                "runtimes/node",
                "apps/web",
                "runtimes/ocr-python",
                "signed-assets/bge-reranker-v2-m3",
                "signed-assets/paddleocr-vl-1.6",
            )
            for target in targets:
                (release / target).mkdir(parents=True, exist_ok=True)
            sid_map = ";".join(
                f"{name}='S-1-5-21-1-2-3-{index}'"
                for index, name in enumerate(
                    (
                        "api",
                        "caddy",
                        "deletion",
                        "inference",
                        "ingestion",
                        "ocr",
                        "web",
                    ),
                    100,
                )
            )
            planned = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    (
                        f"$sids=@{{{sid_map}}}; & '{WINDOWS / 'Set-RagReleaseAcl.ps1'}' "
                        f"-ReleaseRoot '{release}' -ServiceSid $sids -PlanOnly"
                    ),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(planned.returncode, 0, planned.stderr)
            plan = {
                item["relative"]: item["services"]
                for item in json.loads(planned.stdout)
            }
            self.assertEqual(plan["runtimes\\node"], ["web"])
            self.assertEqual(plan["runtimes\\ocr-python"], ["api", "ocr"])
            self.assertEqual(
                set(plan["runtimes\\api-python"]),
                {"api", "ingestion", "deletion", "inference", "ocr"},
            )
            self.assertNotIn("ocr", plan)
        release_acl = (WINDOWS / "Set-RagReleaseAcl.ps1").read_text(
            encoding="utf-8"
        )
        installer = (WINDOWS / "Install-RagWindows.ps1").read_text(
            encoding="utf-8"
        )
        for direct_effective_grant in (
            "'*S-1-5-18:(F)'",
            "'*S-1-5-32-544:(F)'",
            '"*$($ServiceSid[$_]):(RX)"',
        ):
            self.assertIn(direct_effective_grant, release_acl)
        release_lockdown = installer[
            installer.index("icacls.exe $release /inheritance:r /grant:r") :
            installer.index("$standaloneWeb =", installer.index(
                "icacls.exe $release /inheritance:r /grant:r"
            ))
        ]
        self.assertIn("'*S-1-5-18:(F)'", release_lockdown)
        self.assertIn("'*S-1-5-32-544:(F)'", release_lockdown)

    def test_host_binding_rejects_stale_or_different_machine_evidence(self) -> None:
        script = rf"""
function Get-CimInstance {{
  [pscustomobject]@{{ UUID='12345678-1234-1234-1234-123456789abc' }}
}}
$env:COMPUTERNAME='RAGHOST'
. '{WINDOWS / "RagHostBinding.ps1"}'
$fingerprint=Get-RagMachineFingerprint
$fresh=[pscustomobject]@{{machine_fingerprint=$fingerprint;captured_at=[DateTimeOffset]::UtcNow.ToString('o')}}
Assert-RagFreshHostEvidence -Evidence $fresh -MaxAgeSeconds 60
try {{
  Assert-RagFreshHostEvidence -Evidence ([pscustomobject]@{{machine_fingerprint=('0'*64);captured_at=$fresh.captured_at}}) -MaxAgeSeconds 60
  throw 'unexpected acceptance'
}} catch {{ if ($_.Exception.Message -notmatch 'different machine') {{ throw }} }}
try {{
  Assert-RagFreshHostEvidence -Evidence ([pscustomobject]@{{machine_fingerprint=$fingerprint;captured_at=[DateTimeOffset]::UtcNow.AddMinutes(-5).ToString('o')}}) -MaxAgeSeconds 60
  throw 'unexpected acceptance'
}} catch {{ if ($_.Exception.Message -notmatch 'stale') {{ throw }} }}
exit 0
"""
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_managed_hosts_block_round_trips_exact_prior_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            hosts = Path(temporary) / "hosts"
            original = b"127.0.0.1 localhost\n10.0.0.7 unrelated.local"
            hosts.write_bytes(original)
            prior = Path(temporary) / "hosts-prior.bin"
            command = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                (
                    f"& '{WINDOWS / 'Set-RagHostsEntry.ps1'}' -Action Apply "
                    f"-Address @('192.168.50.2','fd00::2') -HostsPath '{hosts}' "
                    f"-PriorContentPath '{prior}'"
                ),
            ]
            first = subprocess.run(command, capture_output=True, check=False, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = subprocess.run(
                command, capture_output=True, check=False, text=True
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already contains", second.stderr)
            value = hosts.read_text()
            self.assertEqual(value.count("# BEGIN LOCAL RAG MANAGED HOST"), 1)
            self.assertIn("10.0.0.7 unrelated.local", value)
            remove = command.copy()
            remove[-1] = remove[-1].replace("-Action Apply", "-Action Remove")
            removed = subprocess.run(
                remove, capture_output=True, check=False, text=True
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertEqual(hosts.read_bytes(), original)
            self.assertNotIn("rag.home.arpa", hosts.read_text())
            self.assertIn("10.0.0.7 unrelated.local", hosts.read_text())
            hosts.write_text("127.0.0.1 localhost\n10.0.0.9 rag.home.arpa\n")
            if prior.exists():
                prior.unlink()
            conflict = subprocess.run(
                command, capture_output=True, check=False, text=True
            )
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("unmanaged rag.home.arpa", conflict.stderr)

    def test_process_safety_refuses_a_live_process(self) -> None:
        script = (
            f". '{WINDOWS / 'RagProcessSafety.ps1'}'; "
            "Assert-RagProcessSetStopped -ProcessId $null; "
            "try { Assert-RagProcessSetStopped -ProcessId $PID; exit 9 } "
            "catch { if ($_.Exception.Message -notmatch 'remain running') { throw } }; "
            "Assert-RagProcessSetStopped -ProcessId 2147483000"
        )
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failure_and_uninstall_stop_proof_precedes_removal(self) -> None:
        installer = (WINDOWS / "Install-RagWindows.ps1").read_text(encoding="utf-8")
        failure = installer.split(
            "} catch {\n    $installFailure = $_.Exception",
            1,
        )[1]
        self.assertLess(
            failure.index("Stop-RagManagedProcesses"),
            failure.index("sc.exe delete RagSupervisor"),
        )
        self.assertLess(
            failure.index("Stop-RagManagedProcesses"),
            failure.index("Remove-LocalUser"),
        )
        self.assertLess(
            failure.index("Stop-RagManagedProcesses"),
            failure.index("[IO.Directory]::Delete($artifact, $true)"),
        )
        uninstaller = (WINDOWS / "Uninstall-RagWindows.ps1").read_text(encoding="utf-8")
        proof = uninstaller.index(
            "Assert-RagProcessSetStopped -ProcessId $accountProcesses"
        )
        self.assertLess(proof, uninstaller.index("sc.exe delete RagSupervisor"))
        self.assertLess(
            proof,
            uninstaller.index(
                "Get-NetFirewallRule -DisplayName 'Local RAG OCR Outbound - *'",
                proof,
            ),
        )

    def test_verified_release_extraction_requires_fixed_nonempty_payload(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            archive = root / "release.zip"
            required = {
                "runtimes/api-python/python.exe": b"api",
                "runtimes/ocr-python/python.exe": b"ocr",
                "runtimes/node/node.exe": b"node",
                "tools/openssl/openssl.exe": b"openssl",
                "tools/openssl/openssl.cnf": b"openssl_conf = openssl_init\n",
                "apps/api/app/main.py": b"main",
                "apps/supervisor/__main__.py": b"supervisor",
                "apps/web/.next/standalone/apps/web/server.js": b"web",
                "apps/web/.next/standalone/apps/web/.next/static/app.js": b"static",
                "apps/web/.next/standalone/apps/web/public/icon.txt": b"public",
                "signed-assets/bge-reranker-v2-m3/config.json": b"bge",
                "signed-assets/paddleocr-vl-1.6/config.json": b"ocr-model",
            }
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, value in required.items():
                    bundle.writestr(name, value)
            destination = root / "current"
            script = (
                f". '{WINDOWS / 'Expand-RagVerifiedRelease.ps1'}'; "
                f"Expand-RagVerifiedRelease -Archive '{archive}' "
                f"-Destination '{destination}'"
            )
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (destination / "apps/api/app/main.py").read_bytes(), b"main"
            )
            bad_archive = root / "bad.zip"
            with zipfile.ZipFile(bad_archive, "w") as bundle:
                bundle.writestr("apps/api/app/main.py", b"only")
            bad_script = (
                f". '{WINDOWS / 'Expand-RagVerifiedRelease.ps1'}'; "
                f"Expand-RagVerifiedRelease -Archive '{bad_archive}' "
                f"-Destination '{root / 'bad-current'}'"
            )
            rejected = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    bad_script,
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("missing required file", rejected.stderr)

    def test_release_builder_rejects_secret_shaped_payload_content(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            source = root / "source"
            app = source / "apps/api/app"
            app.mkdir(parents=True)
            (app / ".env").write_text("SECRET=must-not-ship\n")
            output = root / "output"
            output.mkdir()
            digest = "1" * 64
            command = (
                f"& '{WINDOWS / 'New-RagReleaseArtifact.ps1'}' "
                f"-SourceRoot '{source}' -ApiRuntimeRoot '{source}' "
                f"-OcrRuntimeRoot '{source}' -NodeRuntimeRoot '{source}' "
                f"-OpenSslRuntimeRoot '{source}' -RerankerModelRoot '{source}' "
                f"-OcrModelRoot '{source}' -CaddyExecutable '{source}' "
                f"-CspArtifact '{source}' -ServiceHostExecutable '{source}' "
                f"-PostgresImageDigest 'sha256:{digest}' "
                f"-RustfsImageDigest 'sha256:{digest}' "
                f"-RustfsProbeObjectSha256 '{digest}' "
                f"-QwenGenerationDigest '{digest}' -QwenEmbeddingDigest '{digest}' "
                f"-PaddleOcrVersion '3.7.0' -OcrFixtureSha256 '{digest}' "
                f"-OcrOutputSha256 '{digest}' -OcrStructuredSha256 '{digest}' "
                f"-OcrTextSha256 '{digest}' -OcrPageCount 2 "
                f"-DockerExecutableSha256 '{digest}' "
                f"-OutputRoot '{output}' -Confirm:$false"
            )
            result = subprocess.run(
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
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("denied secret-shaped filename", result.stderr)
            self.assertEqual(list(output.iterdir()), [])

    def test_release_signer_rejects_unknown_artifact_set_before_signing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            stage = root / "stage"
            artifacts.mkdir()
            stage.mkdir()
            (artifacts / "release-evidence.json").write_text("{}")
            (artifacts / "verify_dependencies.py").write_text("pass\n")
            (artifacts / "unknown.bin").write_bytes(b"unknown")
            key = root / "offline-key"
            key.write_text("test-only")
            command = (
                f"& '{WINDOWS / 'New-RagSignedReleaseManifest.ps1'}' "
                f"-Mode Preliminary -Version '3-test' -ArtifactRoot '{artifacts}' "
                f"-PrivateKeyPath '{key}' -ValidationPython '{key}' "
                f"-SignedArtifactStageRoot '{stage}' -Confirm:$false"
            )
            result = subprocess.run(
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
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("artifact set is not exact", result.stderr)
            self.assertFalse((artifacts / "update-manifest.json").exists())

    def test_isolated_synthetic_two_pass_release_signing_happy_path(self) -> None:
        ssh_keygen = Path(r"C:\Windows\System32\OpenSSH\ssh-keygen.exe")
        if not ssh_keygen.is_file():
            self.skipTest("Windows OpenSSH signer is unavailable")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            self.skipTest("Windows LocalAppData is unavailable")
        # The Personal updater creates and verifies its immutable stage under
        # LocalAppData. GitHub checks repositories out on a separate D: volume,
        # whose hosted-runner ACL behavior is not the production path.
        with tempfile.TemporaryDirectory(dir=local_app_data) as temporary:
            root = Path(temporary)
            copied_windows = root / "isolated" / "ops" / "windows"
            copied_windows.mkdir(parents=True)
            for name in (
                "New-RagSignedReleaseManifest.ps1",
                "Test-RagUpdate.ps1",
                "RagHostBinding.ps1",
                "validate_json_schema.py",
                "release-evidence.schema.json",
                "update-manifest.schema.json",
            ):
                (copied_windows / name).write_bytes((WINDOWS / name).read_bytes())
            host_binding_copy = copied_windows / "RagHostBinding.ps1"
            host_binding_copy.write_text(
                host_binding_copy.read_text(encoding="utf-8").replace(
                    "$identity = Get-CimInstance Win32_ComputerSystemProduct -ErrorAction Stop",
                    (
                        "$identity = [pscustomobject]@{ "
                        "UUID='12345678-1234-1234-1234-123456789abc' }"
                    ),
                ),
                encoding="utf-8",
            )
            private_key = root / "offline-key"
            generated = subprocess.run(
                [
                    str(ssh_keygen),
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-f",
                    str(private_key),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            public_parts = private_key.with_suffix(".pub").read_text().split()
            allowed = copied_windows / "release-allowed-signers"
            allowed.write_bytes(
                f"rag-release {public_parts[0]} {public_parts[1]}\n".encode()
            )
            allowed_sha = hashlib.sha256(allowed.read_bytes()).hexdigest()
            verifier_copy = copied_windows / "Test-RagUpdate.ps1"
            verifier_copy.write_text(
                _replace_allowed_signers_pin(
                    verifier_copy.read_text(encoding="utf-8"),
                    allowed_sha,
                ),
                encoding="utf-8",
            )
            current_identity = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    "[Security.Principal.WindowsIdentity]::GetCurrent().Name",
                ],
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip()
            signer_copy = copied_windows / "New-RagSignedReleaseManifest.ps1"
            signer_copy.write_text(
                signer_copy.read_text(encoding="utf-8").replace(
                    "'NT AUTHORITY\\SYSTEM','BUILTIN\\Administrators'",
                    (
                        "'NT AUTHORITY\\SYSTEM','BUILTIN\\Administrators',"
                        f"'{current_identity}'"
                    ),
                ),
                encoding="utf-8",
            )
            protected_key = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    (
                        f"$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
                        f"icacls.exe '{private_key}' /inheritance:r /grant:r "
                        '"*$sid`:(F)" | Out-Null; if($LASTEXITCODE-ne 0){exit 1}'
                    ),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(protected_key.returncode, 0, protected_key.stderr)
            artifacts = root / "artifacts"
            stage = root / "stage"
            artifacts.mkdir()
            stage.mkdir()
            archive = artifacts / "local-rag-release.zip"
            executable_fixture = Path(r"C:\Windows\System32\where.exe").read_bytes()
            synthetic_payload = {
                "runtimes/api-python/python.exe": executable_fixture,
                "runtimes/ocr-python/python.exe": executable_fixture,
                "runtimes/node/node.exe": executable_fixture,
                "tools/openssl/openssl.exe": executable_fixture,
                "tools/openssl/openssl.cnf": b"openssl_conf=openssl_init\n",
                "apps/api/app/main.py": b"synthetic-app",
                "apps/supervisor/__main__.py": b"synthetic-supervisor",
                "apps/web/.next/standalone/apps/web/server.js": b"synthetic-web",
                "apps/web/.next/standalone/apps/web/.next/static/app.js": b"x",
                "apps/web/.next/standalone/apps/web/public/icon.txt": b"x",
                "signed-assets/bge-reranker-v2-m3/config.json": b"{}",
                "signed-assets/paddleocr-vl-1.6/config.json": b"{}",
            }
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, content in synthetic_payload.items():
                    bundle.writestr(name, content)
            for name in (
                "Caddyfile",
                "RagSupervisorService.exe",
                "caddy.exe",
                "csp-header.caddy",
                "deployment.json",
                "verify_dependencies.py",
            ):
                (artifacts / name).write_bytes(f"synthetic-{name}".encode())
            digest = "1" * 64
            schema = json.loads(
                (WINDOWS / "release-evidence.schema.json").read_text(encoding="utf-8")
            )
            evidence = {
                "schema_version": 1,
                "alembic_revision": "0006_versioned_claim",
                "force_rls_tables": schema["properties"]["force_rls_tables"]["items"][
                    "enum"
                ],
                "containers": {
                    "postgres_image_digest": f"sha256:{digest}",
                    "rustfs_image_digest": f"sha256:{digest}",
                },
                "rustfs": {
                    "bucket": "rag-originals",
                    "probe_object_key": "_release-probe/v4.bin",
                    "probe_object_sha256": digest,
                },
                "ollama_models": {
                    "qwen3:8b": digest,
                    "qwen3-embedding:0.6b": digest,
                },
                "reranker": {
                    "identity": "BAAI/bge-reranker-v2-m3",
                    "device": "cpu",
                    "model_assets_sha256": digest,
                },
                "ocr": {
                    "paddleocr_version": "3.7.0",
                    "pipeline_version": "1.6",
                    "fixture_sha256": digest,
                    "expected_output_sha256": digest,
                    "expected_structured_sha256": digest,
                    "expected_text_sha256": digest,
                    "expected_page_count": 2,
                    "model_assets_sha256": digest,
                },
                "runtimes": {
                    "api_python_sha256": digest,
                    "ocr_python_sha256": digest,
                    "openssl_tree_sha256": digest,
                    "api_python_tree_sha256": digest,
                    "ocr_python_tree_sha256": digest,
                    "node_tree_sha256": digest,
                    "docker_executable_sha256": digest,
                },
                "verifier_sha256": hashlib.sha256(
                    (artifacts / "verify_dependencies.py").read_bytes()
                ).hexdigest(),
                "max_evidence_age_seconds": 900,
            }
            evidence_path = artifacts / "release-evidence.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            validation_python = (
                ROOT / "apps" / "api" / ".venv" / "Scripts" / "python.exe"
            )

            def sign(
                mode: str,
                version: str,
                preliminary_manifest: Path | None = None,
            ) -> subprocess.CompletedProcess[str]:
                preliminary_argument = (
                    f"-PreliminaryManifestPath '{preliminary_manifest}' "
                    if preliminary_manifest is not None
                    else ""
                )
                return subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        (
                            f"& '{signer_copy}' -Mode '{mode}' -Version '{version}' "
                            f"-ArtifactRoot '{artifacts}' -PrivateKeyPath '{private_key}' "
                            f"-ValidationPython '{validation_python}' "
                            f"{preliminary_argument}"
                            f"-SignedArtifactStageRoot '{stage}' -Confirm:$false"
                        ),
                    ],
                    capture_output=True,
                    check=False,
                    text=True,
                )

            preliminary = sign("Preliminary", "3-synthetic-preliminary")
            self.assertEqual(preliminary.returncode, 0, preliminary.stderr)
            preliminary_result = json.loads(preliminary.stdout)
            self.assertTrue(preliminary_result["self_verification_stage_cleaned"])
            self.assertEqual(list(stage.iterdir()), [])
            staged = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    (
                        f"& '{verifier_copy}' "
                        f"-Manifest '{artifacts / 'update-manifest.json'}' "
                        f"-Signature '{artifacts / 'update-manifest.json.sig'}' "
                        f"-ArtifactRoot '{artifacts}' "
                        f"-SignedArtifactStageRoot '{stage}'"
                    ),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(staged.returncode, 0, staged.stderr)
            staged_path = Path(json.loads(staged.stdout)["stage_directory"])
            tampered = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    (
                        f"$p='{staged_path}'; $sid=[Security.Principal."
                        "WindowsIdentity]::GetCurrent().User.Value; "
                        f"icacls.exe '{staged_path / 'Caddyfile'}' "
                        "/grant:r \"*$sid`:(F)\" | Out-Null; "
                        f"[IO.File]::AppendAllText('{staged_path / 'Caddyfile'}','tamper'); "
                        "icacls.exe $p /inheritance:r /grant:r "
                        "'*S-1-5-18:(OI)(CI)(RX)' "
                        "'*S-1-5-32-544:(OI)(CI)(RX)' "
                        "\"*$sid`:(OI)(CI)(RX)\" | Out-Null; "
                        f"icacls.exe '{staged_path / 'Caddyfile'}' /reset | Out-Null; "
                        f"& '{verifier_copy}' -ExistingSignedStage $p "
                        "-CleanupOnFailure"
                    ),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertNotEqual(tampered.returncode, 0)
            self.assertIn("verification failed: Caddyfile", tampered.stderr)
            self.assertFalse(staged_path.exists())
            payload = root / "immutable-payload"
            expanded = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    (
                        f". '{WINDOWS / 'Expand-RagVerifiedRelease.ps1'}'; "
                        f"Expand-RagVerifiedRelease -Archive "
                        f"'{artifacts / 'local-rag-release.zip'}' "
                        f"-Destination '{payload}'"
                    ),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(expanded.returncode, 0, expanded.stderr)
            for executable in (
                payload / "runtimes" / "api-python" / "python.exe",
                payload / "runtimes" / "ocr-python" / "python.exe",
                payload / "runtimes" / "node" / "node.exe",
                payload / "tools" / "openssl" / "openssl.exe",
            ):
                probe = subprocess.run(
                    [str(executable), "/?"],
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(probe.returncode, 0, probe.stderr)
            preliminary_manifest = artifacts / "update-manifest.json"
            preliminary_hash = hashlib.sha256(
                preliminary_manifest.read_bytes()
            ).hexdigest()
            saved_preliminary_manifest = root / "preliminary-manifest.json"
            preliminary_manifest.replace(saved_preliminary_manifest)
            (artifacts / "update-manifest.json.sig").replace(
                root / "preliminary-manifest.json.sig"
            )
            fingerprint = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    (
                        f". '{copied_windows / 'RagHostBinding.ps1'}'; "
                        "Get-RagMachineFingerprint"
                    ),
                ],
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip()
            dependency_evidence = {
                "schema_version": 1,
                "mode": "verification",
                "result": "pass",
                "checks": [
                    {
                        "dependency": "synthetic immutable payload",
                        "result": "pass",
                        "detail": "isolated two-pass contract fixture",
                    }
                ],
                "release_evidence_sha256": hashlib.sha256(
                    evidence_path.read_bytes()
                ).hexdigest(),
                "verifier_sha256": hashlib.sha256(
                    (artifacts / "verify_dependencies.py").read_bytes()
                ).hexdigest(),
                "preliminary_manifest_sha256": preliminary_hash,
                "machine_fingerprint": fingerprint,
                "captured_at": subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-Command",
                        "[DateTimeOffset]::UtcNow.ToString('o')",
                    ],
                    capture_output=True,
                    check=True,
                    text=True,
                ).stdout.strip(),
                "changes_applied": True,
                "mutation_scope": "isolated synthetic fixture only",
            }
            dependency_evidence_path = artifacts / "dependency-evidence.json"
            dependency_evidence_path.write_text(
                json.dumps(dependency_evidence), encoding="utf-8"
            )
            dependency_evidence["preliminary_manifest_sha256"] = "2" * 64
            dependency_evidence_path.write_text(
                json.dumps(dependency_evidence), encoding="utf-8"
            )
            rejected = sign("Final", "3-synthetic", saved_preliminary_manifest)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("requires passing dependency evidence", rejected.stderr)
            self.assertFalse((artifacts / "update-manifest.json").exists())
            dependency_evidence["preliminary_manifest_sha256"] = preliminary_hash
            dependency_evidence_path.write_text(
                json.dumps(dependency_evidence), encoding="utf-8"
            )
            caddyfile = artifacts / "Caddyfile"
            original_caddyfile = caddyfile.read_bytes()
            caddyfile.write_bytes(original_caddyfile + b"-post-preliminary-tamper")
            rejected = sign("Final", "3-synthetic", saved_preliminary_manifest)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(
                "differs from preliminary complete-release manifest",
                rejected.stderr,
            )
            self.assertFalse((artifacts / "update-manifest.json").exists())
            caddyfile.write_bytes(original_caddyfile)
            final = sign("Final", "3-synthetic", saved_preliminary_manifest)
            self.assertEqual(final.returncode, 0, final.stderr)
            final_result = json.loads(final.stdout)
            self.assertEqual(final_result["mode"], "final")
            final_manifest = json.loads(
                (artifacts / "update-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(final_manifest["artifacts"]), 9)

    def test_network_and_dependency_scripts_require_exact_evidence(self) -> None:
        network = (WINDOWS / "Test-RagNetwork.ps1").read_text(encoding="utf-8")
        for required in (
            "PinnedCaddyProgram",
            "PinnedCaddySha256",
            "PinnedServiceHostProgram",
            "PinnedServiceHostSha256",
            "PinnedSupervisorPython",
            "OpenJobObject",
            "InNamedJob",
            "ExpectedLocalAddress",
            "Get-NetFirewallApplicationFilter",
            "Get-NetFirewallServiceFilter",
            "Get-NetFirewallAddressFilter",
            "Get-NetFirewallInterfaceTypeFilter",
            "EdgeTraversalPolicy",
            "Get-NetFirewallProfile",
            "PolicyStore ActiveStore",
            "DefaultInboundAction",
            "PackageFamilyName",
            "package_family_name",
            "exit 1",
            "same_caddy_pid",
            "job_membership",
            "ConvertTo-RagNormalizedIPAddress",
            "normalized_address",
            "[Net.IPAddress]::TryParse",
        ):
            self.assertIn(required, network)
        self.assertIn("$_.service -ceq 'Any'", network)
        self.assertNotIn("PinnedProxyService", network)
        dependencies = (WINDOWS / "Test-RagDependencies.ps1").read_text(
            encoding="utf-8"
        )
        verifier = (WINDOWS / "verify_dependencies.py").read_text(encoding="utf-8")
        dependency_contract = dependencies + verifier
        for required in (
            "0006_versioned_claim",
            "relrowsecurity",
            "relforcerowsecurity",
            "anonymous_object_get_denied",
            "anonymous_list_denied",
            "anonymous_policy_denied",
            "qwen3-embedding:0.6b",
            "1024",
            "BAAI/bge-reranker-v2-m3",
            "release-evidence.json",
            "verify_dependencies.py",
            "SignedArtifactStageRoot",
            "ExistingSignedStage",
            "ExistingReleaseRoot",
            "-ExistingSignedStage $ExistingSignedStage",
            "Remove-OwnedDependencyRoot",
            "takeown.exe",
            "$ownsDependencyStage",
            "$ownsDependencyPayload",
            "Dependency temporary-root cleanup failed",
            "$proof = $null",
            "$proof.ocr.captured_at",
            "Immutable staged artifact changed before execution",
            "PinnedDockerProgram",
            "docker_executable_sha256",
            "public.alembic_version",
            "pg_catalog.pg_namespace",
            "PostgreSQL role contract",
            "PostgreSQL table grants",
            "expected_structured_sha256",
            "expected_text_sha256",
            "expected_page_count",
            "model_assets_sha256",
            "PostgreSQL function grants",
            "RustFS scoped IAM",
            "RustfsApiCredentials",
            "root_credentials_used",
            "ingestion_put_denied",
            "public.v4_get_job(uuid)",
            "public.v4_prepare_document_reingest(uuid)",
            "public.v4_commit_document_reingest(uuid,text,uuid)",
            "public.v4_interrupt_turn(uuid,uuid,uuid)",
            "public.v4_repair_interrupted_turns()",
            "a.rolinherit",
            "parent_name='rag_owner'",
            "member_name='rag_migrator'",
        ):
            self.assertIn(required, dependency_contract)

    def test_firewall_classifier_distinguishes_explicit_443_from_ambient_any(
        self,
    ) -> None:
        script = (
            f". '{WINDOWS / 'RagFirewallClassification.ps1'}'; "
            "if (-not (Test-RagPortIsAmbientAny 'Any')) { throw 'Any not ambient' }; "
            "if (Test-RagPortExplicitlyScopes443 'Any') { throw 'Any explicit' }; "
            "if (-not (Test-RagPortExplicitlyScopes443 '443')) { throw '443 missed' }; "
            "if (-not (Test-RagPortExplicitlyScopes443 '400-500')) { throw 'range missed' }; "
            "if (Test-RagPortExplicitlyScopes443 '80') { throw '80 overlaps' }"
        )
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        scope_script = (
            f". '{WINDOWS / 'RagFirewallClassification.ps1'}'; "
            "$pin='C:\\Program Files\\LocalRAG\\caddy.exe'; "
            "$danger=[pscustomobject]@{protocol='Any';local_port='Any';program='Any';local_address='Any'}; "
            "if (-not (Test-RagRuleCanAdmitPinnedCaddy443 $danger $pin)) { throw 'Any scope missed' }; "
            "$public=[pscustomobject]@{protocol='TCP';local_port='443';program=$pin;local_address='192.168.1.2'}; "
            "if (-not (Test-RagRuleCanAdmitPinnedCaddy443 $public $pin)) { throw 'Public overlap missed' }; "
            "$packaged=[pscustomobject]@{protocol='Any';local_port='Any';program='Any';local_address='Any';"
            "package_family_name='Microsoft.Store_8wekyb3d8bbwe'}; "
            "if (Test-RagRuleCanAdmitPinnedCaddy443 $packaged $pin) { throw 'packaged app overlaps' }; "
            "$loopback=[pscustomobject]@{protocol='TCP';local_port='443';program='other.exe';local_address='127.0.0.1'}; "
            "if (Test-RagRuleCanAdmitPinnedCaddy443 $loopback $pin) { throw 'non-overlap accepted' }"
        )
        scoped = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                scope_script,
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(scoped.returncode, 0, scoped.stderr)
        network = (WINDOWS / "Test-RagNetwork.ps1").read_text(encoding="utf-8")
        self.assertIn("ambient_any_port_rules", network)
        self.assertIn("exposedInternalListeners", network)
        self.assertIn("unexpected443Listeners", network)
        release_schema = json.loads(
            (WINDOWS / "release-evidence.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            release_schema["properties"]["alembic_revision"]["const"],
            "0006_versioned_claim",
        )
        rls = release_schema["properties"]["force_rls_tables"]
        self.assertEqual(rls["minItems"], 25)
        self.assertEqual(rls["maxItems"], 25)
        self.assertEqual(len(rls["items"]["enum"]), 25)

    def test_dependency_function_acl_proof_rejects_extra_overload(self) -> None:
        dependencies = (WINDOWS / "Test-RagDependencies.ps1").read_text(
            encoding="utf-8"
        )
        authoritative = dependencies.split(
            "# Compare exact schema/signature/grantee/grant-option", 1
        )[1]
        for required in (
            "pg_catalog.aclexplode",
            "pg_catalog.format_type",
            "procedure.proargtypes::oid[]",
            "public.v4_update_ingestion_progress(uuid,uuid,bigint,text,integer,integer)",
            "public.v4_mark_turn_access_revoked_trusted(uuid,uuid)",
            "public.v4_mark_turn_citation_failed(uuid,uuid,uuid,text)",
            "public.v4_mark_turn_length_limited(uuid,uuid,uuid,text)",
            "public.v4_prepare_document_reingest(uuid)",
            "public.v4_commit_document_reingest(uuid,text,uuid)",
            "public.cosine_distance(vector,vector)",
            "acl.is_grantable",
            "(SELECT * FROM actual EXCEPT SELECT * FROM expected)",
        ):
            self.assertIn(required, authoritative)
        expected = {("rag_api", "public.v4_get_job(uuid)")}
        actual_with_extra_overload = expected | {("rag_api", "public.v4_get_job(text)")}
        self.assertNotEqual(actual_with_extra_overload, expected)
        expected_acl = {("rag_api", "public.v4_get_job(uuid)", False)}
        actual_with_grant_option = {("rag_api", "public.v4_get_job(uuid)", True)}
        self.assertNotEqual(actual_with_grant_option, expected_acl)
        self.assertNotEqual(
            expected_acl | {("evil_login", "public.v4_get_job(uuid)", False)},
            expected_acl,
        )

    def test_dependency_contract_targets_only_fresh_v4_database_objects(self) -> None:
        dependencies = (WINDOWS / "Test-RagDependencies.ps1").read_text(
            encoding="utf-8"
        )
        release = (WINDOWS / "New-RagReleaseArtifact.ps1").read_text(
            encoding="utf-8"
        )
        schema = (WINDOWS / "release-evidence.schema.json").read_text(
            encoding="utf-8"
        )
        contract = dependencies + release + schema
        for required in (
            "0006_versioned_claim",
            "public.v4_readiness()",
            "trg_v4_turn_source_immutability",
            "public.v4_enforce_turn_source_immutability()",
            "public.v4_can_read_folder(uuid)",
            "public.v4_can_create_children(uuid)",
            "public.v4_create_folder(uuid,uuid,text,text)",
            "public.v4_account_active_teams()",
            "public.v4_document_team_recipients(uuid[])",
            "public.v4_admin_access_context(uuid)",
            "public.v4_admin_upload_preflight(text,text,text,text,text,text,bigint,text,text,text,uuid,uuid[])",
            "public.v4_admin_commit_upload(uuid,uuid,uuid,uuid,text,text,text,text,text,text,bigint,text,text,text,uuid,uuid[])",
            "public.v4_prepare_document_reingest(uuid)",
            "public.v4_commit_document_reingest(uuid,text,uuid)",
        ):
            self.assertIn(required, contract)
        for stale in ("0001_v3_baseline", "public.v3_", "trg_v3_"):
            self.assertNotIn(stale, contract)

    def test_dependency_function_acl_contract_matches_v4_migration(self) -> None:
        dependencies = (WINDOWS / "Test-RagDependencies.ps1").read_text(
            encoding="utf-8"
        )
        function_block = dependencies.split("$functionProofSql = @'", 1)[1].split(
            "'@", 1
        )[0]
        actual: dict[str, set[str]] = {}
        for role, array_body in re.findall(
            (
                r"SELECT '(rag_(?:api|worker|maintenance))', "
                r"unnest\(ARRAY\[(.*?)\]\), false"
            ),
            function_block,
            re.DOTALL,
        ):
            actual[role] = set(re.findall(r"'(public\.[^']+)'", array_body))
        for role in ("rag_migrator", "rag_backup"):
            match = re.search(
                rf"SELECT '{role}', '(public\.[^']+)', false",
                function_block,
            )
            self.assertIsNotNone(match)
            actual[role] = {match.group(1)}

        migration_path = (
            ROOT / "apps" / "api" / "alembic" / "versions" / "0001_v4_baseline.py"
        )
        module = ast.parse(migration_path.read_text(encoding="utf-8"))
        values: dict[str, object] = {}
        for node in module.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "_EXPECTED_V4_REGPROCEDURES",
                    "_EXPECTED_FUNCTION_GRANTS",
                }:
                    values[target.id] = ast.literal_eval(node.value)
        signatures = values["_EXPECTED_V4_REGPROCEDURES"]
        grants = values["_EXPECTED_FUNCTION_GRANTS"]
        signature_by_name = {
            signature.removeprefix("public.").split("(", 1)[0]: signature.replace(
                "timestamptz", "timestampwithtimezone"
            )
            for signature in signatures
        }
        expected = {
            role: {signature_by_name[name] for name in names}
            for role, names in grants.items()
        }
        expected["rag_api"].add("public.cosine_distance(vector,vector)")
        expected["rag_api"].update(
            {
                "public.v4_can_create_children(uuid)",
                "public.v4_create_folder(uuid,uuid,text,text)",
                "public.v5_readiness()",
                "public.v5_citation_evidence(uuid,uuid,smallint)",
                "public.v4_prepare_document_reingest(uuid)",
                "public.v4_commit_document_reingest(uuid,text,uuid)",
            }
        )
        expected["rag_maintenance"].add("public.v5_readiness()")
        self.assertEqual(actual, expected)

    def test_dependency_acl_and_trigger_proofs_reject_privilege_drift(self) -> None:
        dependencies = (WINDOWS / "Test-RagDependencies.ps1").read_text(
            encoding="utf-8"
        )
        for required in (
            "PostgreSQL schema privileges",
            "PostgreSQL sequence privileges",
            "PostgreSQL turn-source immutability trigger",
            "acl.grantee=0",
            "namespace.nspowner='pg_database_owner'::regrole",
            "trigger.tgtype=27",
            "public.v4_enforce_turn_source_immutability()",
            "acl.is_grantable",
        ):
            self.assertIn(required, dependencies)
        expected_table_acl = {("rag_api", "documents", "SELECT", False)}
        self.assertNotEqual(
            {("rag_api", "documents", "SELECT", True)},
            expected_table_acl,
        )
        self.assertNotEqual(
            expected_table_acl | {("evil_login", "documents", "SELECT", False)},
            expected_table_acl,
        )
        expected_schema_acl = {("PUBLIC", "USAGE")}
        self.assertNotEqual(
            expected_schema_acl | {("PUBLIC", "CREATE")},
            expected_schema_acl,
        )
        self.assertNotEqual(
            expected_schema_acl | {("evil_login", "USAGE")},
            expected_schema_acl,
        )
        self.assertNotEqual("evil_login", "pg_database_owner")
        self.assertIn("acl.grantee <> procedure.proowner", dependencies)
        self.assertIn(
            "acl.grantee<>'pg_database_owner'::regrole",
            dependencies,
        )
        self.assertIn("acl.grantee<>sequence.relowner", dependencies)

    def test_dependency_readiness_rejects_policy_and_owner_drift(self) -> None:
        dependencies = (WINDOWS / "Test-RagDependencies.ps1").read_text(
            encoding="utf-8"
        )
        migration = (
            ROOT / "apps" / "api" / "alembic" / "versions" / "0001_v4_baseline.py"
        ).read_text(encoding="utf-8")
        for required in (
            "PostgreSQL hardened readiness",
            "FROM public.v5_readiness()",
            "$readinessFields[0] -ceq '0006_versioned_claim'",
            "$readinessFields[1] -ceq 'true'",
            "$readinessFields[3] -ceq 'true'",
            "bootstrap_required=$bootstrapState",
        ):
            self.assertIn(required, dependencies)
        for catalog_integrity_guard in (
            "owner.rolname <> 'rag_owner'",
            "policy.polpermissive IS DISTINCT FROM true",
            "policy.polwithcheck IS NOT NULL",
            "routine.prosecdef",
            "routine.proconfig",
            "ARRAY['security_barrier=true']",
        ):
            self.assertIn(catalog_integrity_guard, migration)
        expected = ("0006_versioned_claim", "true", "false", "true")
        policy_drift = ("0006_versioned_claim", "true", "false", "false")
        owner_drift = ("0006_versioned_claim", "true", "false", "false")
        self.assertNotEqual(policy_drift, expected)
        self.assertNotEqual(owner_drift, expected)

    def test_powershell_acl_contract_uses_numeric_masks_and_recursive_assets(
        self,
    ) -> None:
        scripts = [
            WINDOWS / "Test-RagDependencies.ps1",
            ROOT / "ops" / "security" / "provision-postgres-roles.ps1",
            ROOT / "ops" / "security" / "provision-rustfs-iam.ps1",
        ]
        for script in scripts:
            value = script.read_text(encoding="utf-8")
            self.assertIn("$WriteCapableRightsMask", value)
            self.assertIn("0x2 -bor 0x4", value)
            self.assertNotIn("FileSystemRights.ToString()", value)
        dependency = scripts[0].read_text(encoding="utf-8")
        self.assertIn("EnumerateFileSystemInfos()", dependency)
        self.assertIn("[Collections.Generic.Stack[IO.DirectoryInfo]]", dependency)
        self.assertIn(
            "Signed model asset tree must not contain reparse points", dependency
        )
        for service_writable_child_right in (0x2, 0x4):
            self.assertNotEqual(service_writable_child_right & 0x6, 0)

    def test_read_only_scripts_contain_no_mutating_windows_commands(self) -> None:
        prohibited = (
            "New-Service",
            "Set-Service",
            "sc.exe create",
            "New-NetFirewallRule ",
            "Import-Certificate",
            "New-SelfSignedCertificate",
            "Remove-Item",
        )
        read_only_scripts = (
            "Get-RagCertificatePlan.ps1",
            "Get-RagFirewallPlan.ps1",
            "Invoke-RagWindowsPlan.ps1",
            "Test-RagCaddyArtifact.ps1",
            "Test-RagCspArtifact.ps1",
            "Test-RagDependencies.ps1",
            "Test-RagNetwork.ps1",
            "Test-RagUpdate.ps1",
        )
        for name in read_only_scripts:
            script = WINDOWS / name
            value = script.read_text(encoding="utf-8")
            for command in prohibited:
                with self.subTest(script=script.name, command=command):
                    if (
                        script.name == "Get-RagFirewallPlan.ps1"
                        and command == "New-NetFirewallRule "
                    ):
                        continue
                    if (
                        script.name == "Test-RagDependencies.ps1"
                        and command == "Remove-Item"
                    ):
                        self.assertEqual(value.count("Remove-Item"), 2)
                        self.assertEqual(
                            value.count(
                                "Remove-Item -LiteralPath $verifierErrorPath"
                            ),
                            2,
                        )
                        continue
                    self.assertNotIn(command, value)

    def test_rustfs_iam_provisioner_creates_only_scoped_runtime_keys(self) -> None:
        script = (ROOT / "ops" / "security" / "provision-rustfs-iam.ps1").read_text(
            encoding="utf-8"
        )
        for required in (
            "Read-Host 'RustFS root access key' -AsSecureString",
            "Read-Host 'RustFS root secret key' -AsSecureString",
            "RandomNumberGenerator",
            "$env:MC_CONFIG_DIR = Join-Path $temporaryRoot 'mc-config'",
            "Name = 'api'",
            "Name = 'ingestion'",
            "Name = 'deletion'",
            "Name = 'maintenance'",
            '"$($role.Name)-object-storage.env"',
            "root_credentials_persisted = $false",
            "secrets_logged = $false",
        ):
            self.assertIn(required, script)
        for prohibited in ("backup-object-storage.env", "Write-Host $secretKey"):
            self.assertNotIn(prohibited, script)
        self.assertNotIn("EscapeDataString", script)
        ingestion_policy = script.split("Name = 'ingestion'", 1)[1].split(
            "Name = 'deletion'", 1
        )[0]
        self.assertNotIn("'s3:PutObject'", ingestion_policy)

    def test_compose_and_templates_separate_bootstrap_and_runtime_identities(
        self,
    ) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        root_environment = (ROOT / ".env.example").read_text(encoding="utf-8")
        api_environment = (ROOT / "apps" / "api" / ".env.example").read_text(
            encoding="utf-8"
        )
        self.assertIn("POSTGRES_USER: rag_cluster_admin", compose)
        self.assertIn("POSTGRES_CLUSTER_ADMIN_PASSWORD", compose)
        self.assertIn("RUSTFS_ROOT_ACCESS_KEY", compose)
        self.assertNotIn("POSTGRES_USER=", root_environment)
        for role in ("rag_api", "rag_worker", "rag_maintenance", "rag_migrator"):
            self.assertIn(role, api_environment)
        self.assertNotIn("rag_cluster_admin", api_environment)
        self.assertNotIn("rag_backup", api_environment)
        self.assertNotIn("RUSTFS_ROOT_", api_environment)

    def test_supervised_environment_templates_match_exact_allowlists(self) -> None:
        deployment = json.loads(
            (WINDOWS / "deployment.json").read_text(encoding="utf-8")
        )
        for service in deployment["services"]:
            template = WINDOWS / "environments" / f"{service['name']}.env.example"
            keys = {
                line.split("=", 1)[0]
                for line in template.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")
            }
            self.assertEqual(keys, set(service["environment_keys"]), service["name"])

    def test_worker_environment_templates_respect_process_concurrency_limit(
        self,
    ) -> None:
        for name in ("ingestion", "deletion"):
            values = dict(
                line.split("=", 1)
                for line in (
                    WINDOWS / "environments" / f"{name}.env.example"
                ).read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")
            )
            self.assertGreaterEqual(
                int(values["OBJECT_STORAGE_BLOCKING_CONCURRENCY"]), 1
            )
            self.assertLessEqual(
                int(values["OBJECT_STORAGE_BLOCKING_CONCURRENCY"]), 4
            )
        installer = (WINDOWS / "Install-RagWindows.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("$blockingConcurrency -gt 4", installer)
        self.assertIn(
            "Prepared API canonical host/origin does not match",
            installer,
        )
        self.assertIn(
            "Prepared service tokens must contain at least 32 UTF-8 bytes",
            installer,
        )
        self.assertIn(
            "Prepared inference $setting must be a positive integer",
            installer,
        )
        self.assertIn(
            "$files['api']['OCR_PYTHON_EXECUTABLE']",
            installer,
        )
        api_values = dict(
            line.split("=", 1)
            for line in (
                WINDOWS / "environments" / "api.env.example"
            ).read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        )
        self.assertEqual(
            api_values["OCR_PYTHON_EXECUTABLE"],
            r"C:\Program Files\LocalRAG\current\runtimes\ocr-python\python.exe",
        )

    def test_managed_python_services_use_diagnostic_bootstrap(self) -> None:
        deployment = json.loads(
            (WINDOWS / "deployment.json").read_text(encoding="utf-8")
        )
        services = {service["name"]: service for service in deployment["services"]}
        for name in ("api", "ingestion", "deletion", "inference", "ocr"):
            self.assertEqual(
                services[name]["arguments"][:3],
                ["-m", "app.runtime.startup_bootstrap", name],
            )
        installer = (WINDOWS / "Install-RagWindows.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('$details.Add("stage=$startupStage")', installer)

    def test_installer_has_exact_modify_grants_for_noninheriting_writable_descendants(
        self,
    ) -> None:
        installer = (WINDOWS / "Install-RagWindows.ps1").read_text(encoding="utf-8")
        start = installer.index("$writableDirectoryGrants = @(")
        end = installer.index(
            "    foreach ($grant in $writableDirectoryGrants)", start
        )
        block = installer[start:end]
        grant_block_end = installer.index(
            "    & (Join-Path $repository 'ops\\windows\\Set-RagReleaseAcl.ps1')",
            end,
        )
        grant_block = installer[start:grant_block_end]
        expected = {
            ("profiles", "proxy\\tmp", "caddy"),
            ("profiles", "web\\tmp", "web"),
            ("profiles", "api\\tmp", "api"),
            ("profiles", "ingestion\\tmp", "ingestion"),
            ("profiles", "deletion\\tmp", "deletion"),
            ("profiles", "inference\\tmp", "inference"),
            ("profiles", "inference\\cache\\huggingface\\hub", "inference"),
            ("profiles", "inference\\cache\\transformers", "inference"),
            ("profiles", "ocr\\tmp", "ocr"),
            ("profiles", "ocr\\cache\\huggingface\\hub", "ocr"),
            ("profiles", "ocr\\cache\\transformers", "ocr"),
            ("workRoot", "ingestion\\objects", "ingestion"),
            ("workRoot", "ingestion\\ocr", "ingestion"),
            ("workRoot", "ocr", "ocr"),
        }
        actual = {
            (base, relative, service)
            for base, relative, service in re.findall(
                r"path=\(Join-Path \$(\w+) '([^']+)'\); service='([^']+)'",
                block,
            )
        }
        self.assertEqual(actual, expected)
        self.assertIn(
            "/inheritance:r /grant:r `\n            '*S-1-5-18:(OI)(CI)(F)'",
            grant_block,
        )
        self.assertIn('"*${sid}:(OI)(CI)(M)"', grant_block)
        self.assertNotIn("$release", grant_block)
        self.assertNotIn("$signedAssets", grant_block)

        metadata_start = installer.index("$ocrMetadataParents = @(")
        metadata_end = installer.index(
            "    # requiredDirectories deliberately remove inheritance",
            metadata_start,
        )
        metadata_block = installer[metadata_start:metadata_end]
        self.assertIn("@($programData,$workRoot)", metadata_block)
        self.assertIn(
            '"*$($serviceSid[\'ocr\']):(RX)"',
            metadata_block,
        )
        self.assertNotIn("(OI)", metadata_block)
        self.assertNotIn("(CI)", metadata_block)

        # Every target was previously locked with inheritance removed.  Keep
        # that invariant explicit so a future edit cannot silently rely on a
        # parent grant for these descendants again.
        required_directories = installer[
            installer.index("$requiredDirectories = @(") : installer.index(
                "    foreach ($path in $requiredDirectories)",
                installer.index("$requiredDirectories = @("),
            )
        ]
        for base, relative, _service in expected:
            self.assertIn(f"Join-Path ${base} '{relative}'", required_directories)

    def test_managed_windows_installer_contract_is_fail_closed(self) -> None:
        installer = (WINDOWS / "Install-RagWindows.ps1").read_text(encoding="utf-8")
        uninstaller = (WINDOWS / "Uninstall-RagWindows.ps1").read_text(encoding="utf-8")
        service_host = (WINDOWS / "RagSupervisorService.cs").read_text(encoding="utf-8")
        rights = (WINDOWS / "Set-RagAccountRights.ps1").read_text(encoding="utf-8")
        firewall = (WINDOWS / "Set-RagFirewall.ps1").read_text(encoding="utf-8")
        for required in (
            "start= delayed-auto",
            "type= own",
            "sidtype RagSupervisor unrestricted",
            "RandomNumberGenerator",
            "identity-secrets",
            "Refusing to reuse pre-existing account",
            "EnvironmentSourceRoot is required",
            "backup='not_configured'",
            "updates='not_configured'",
            "Existing or partial Local RAG installation detected",
            "Signed release verification failed",
            "Full supervised graph did not become ready",
            "Get-RagStartupDiagnosticSummary",
            "startup-failure.json",
            "no bounded startup diagnostic was produced",
            "api_mtls_readiness_passed=$true",
            "RagPreparedEnvironmentSnapshots",
            "preparedEnvironmentBytes",
            "WriteAllBytes",
            "Assert-RagPreparedEnvironmentContract",
            "validated_service_secret_sets -ne 7",
            "passwords_exposed -cne $false",
            "$serviceBinaryPath = '\"' + $serviceExe + '\"'",
            "New-Service -Name RagSupervisor",
            "-BinaryPathName $serviceBinaryPath",
            "-ExistingSignedStage $verifiedStage -ExistingReleaseRoot $release",
            "Installed release immutable RX ACL application failed",
            "Verified update stage remains after strict cleanup",
            "Verified release root remains after strict cleanup",
            "-B -m apps.supervisor validate --manifest",
            "-B -m apps.supervisor validate-secrets",
        ):
            self.assertIn(required, installer)
        for managed_update_artifact in (
            "'updates'",
            "'installed-release-evidence.json'",
            "'installed-release-state.json'",
        ):
            self.assertGreaterEqual(uninstaller.count(managed_update_artifact), 2)
        second_dependency_call = installer[
            installer.index("$freshDependencyEvidence =")
            : installer.index("Start-Service -Name RagSupervisor")
        ]
        self.assertNotIn("-SignedUpdateManifest", second_dependency_call)
        self.assertNotIn("-SignedArtifactStageRoot", second_dependency_call)
        self.assertNotIn("Expand-RagVerifiedRelease", second_dependency_call)
        self.assertNotIn("& sc.exe create RagSupervisor", installer)
        env_validation = installer.index("Assert-RagPreparedEnvironmentContract")
        env_copy = installer.index("$preparedEnvironmentBytes =", env_validation)
        self.assertLess(env_validation, env_copy)
        self.assertIn("$preparedEnvironmentBytes[$entry.Key]", installer)
        self.assertLess(
            installer.index("Root DACL lockdown failed"),
            installer.index("RAG_WINDOWS_ACCOUNT_PASSWORD=$password"),
        )
        self.assertIn("executable_sha256", installer)
        self.assertIn("local-rag-release.zip", installer)
        self.assertIn("RagSupervisorService.exe", installer)
        self.assertIn("Join-Path $verifiedStage 'deployment.json'", installer)
        self.assertNotIn("Get-Content -Raw $manifestSource", installer)
        hosts_rollback = installer.index(
            "$hostsRollback = & (Join-Path $repository "
            "'ops\\windows\\Set-RagHostsEntry.ps1')"
        )
        managed_root_removal = installer.index(
            "foreach ($artifact in @($programData,$programFilesRoot))"
        )
        self.assertLess(hosts_rollback, managed_root_removal)
        rollback_contract = installer[hosts_rollback:managed_root_removal]
        self.assertNotIn("SilentlyContinue", rollback_contract)
        self.assertIn(
            "Exact hosts-byte rollback did not report success", rollback_contract
        )
        self.assertIn(
            "'*S-1-5-32-544:(F)' /T /C",
            installer[managed_root_removal:],
        )
        self.assertIn(
            "/F $artifact /A /R /D Y",
            installer[managed_root_removal:],
        )
        self.assertIn(
            "administrator ownership recovery failed",
            installer[managed_root_removal:],
        )
        self.assertIn("[IO.Directory]::Delete($artifact, $true)", installer)
        self.assertIn("managed root remains after recursive deletion", installer)
        self.assertIn("managed-root rollback failed", installer)
        for required_directory in (
            "profiles 'proxy\\tmp'",
            "stateRoot 'inference'",
            "stateRoot 'ocr'",
            "workRoot 'ingestion\\objects'",
            "workRoot 'ocr'",
            "signedAssets 'bge-reranker-v2-m3'",
            "signedAssets 'paddleocr-vl-1.6'",
        ):
            self.assertIn(required_directory, installer)
        for right in (
            "SeServiceLogonRight",
            "SeDenyInteractiveLogonRight",
            "SeDenyRemoteInteractiveLogonRight",
            "SeDenyBatchLogonRight",
            "SeDenyNetworkLogonRight",
        ):
            self.assertIn(right, rights)
        self.assertIn(
            "$resolvedAccount.StartsWith('.\\', [StringComparison]::Ordinal)",
            rights,
        )
        self.assertIn("[Environment]::MachineName", rights)
        self.assertIn(
            "[RagLsaRights]::Apply($resolvedAccount, $required, @())",
            rights,
        )
        self.assertIn(
            "[RagLsaRights]::Apply($resolvedAccount, @(), $required)",
            rights,
        )
        self.assertIn("-Service Any", firewall)
        self.assertIn("-InterfaceType Wired,Wireless", firewall)
        self.assertNotIn("-InterfaceType Lan", firewall)
        self.assertIn("RagSupervisor", service_host)
        self.assertIn("Environment.Exit(failure)", service_host)
        self.assertLess(
            uninstaller.index("Disable-NetFirewallRule"),
            uninstaller.index("Remove-Item"),
        )
        for protected_data in (
            "postgres_data_preserved=$true",
            "rustfs_data_preserved=$true",
            "backup_destination_preserved=$true",
        ):
            self.assertIn(protected_data, uninstaller)
        self.assertIn("installed-accounts.json", uninstaller)
        self.assertIn("supervisor-startup-failure.json", uninstaller)
        self.assertIn("$null -ne $owner.PSObject.Properties['ReturnValue']", uninstaller)
        self.assertIn("Installed-account SID mismatch", uninstaller)
        self.assertNotIn(
            "$accounts = @('RagProxySvc'",
            uninstaller,
        )

    def test_certificate_and_ocr_firewall_gates_are_semantic(self) -> None:
        certificate = (WINDOWS / "Test-RagCertificateSet.ps1").read_text(
            encoding="utf-8"
        )
        renewal = (WINDOWS / "Renew-RagCertificates.ps1").read_text(encoding="utf-8")
        installer = (WINDOWS / "Install-RagWindows.ps1").read_text(encoding="utf-8")
        for required in (
            "CA:TRUE",
            "subjectAltName",
            "extendedKeyUsage",
            "Certificate/private-key mismatch",
            "checkend 2592000",
        ):
            self.assertIn(required, certificate)
        for required in (
            "stage-",
            "rollback-",
            "Test-RagCertificateSet.ps1",
            "Test-RagNetwork.ps1",
            "rollback_retained=$true",
        ):
            self.assertIn(required, renewal)
        self.assertIn("Set-RagOcrFirewall.ps1", installer)
        self.assertIn("Test-RagOcrFirewall.ps1", installer)
        ocr_firewall = (WINDOWS / "Set-RagOcrFirewall.ps1").read_text(encoding="utf-8")
        self.assertIn("-Direction Outbound -Action Block", ocr_firewall)
        self.assertIn("0.0.0.0-126.255.255.255", ocr_firewall)
        self.assertIn("::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", ocr_firewall)
        self.assertNotIn("-RemoteAddress Internet", ocr_firewall)

    def test_ocr_firewall_verifier_accepts_empty_ports_for_any_protocol(
        self,
    ) -> None:
        verifier = str(WINDOWS / "Test-RagOcrFirewall.ps1").replace("'", "''")
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            harness = Path(temporary) / "ocr-firewall-harness.ps1"
            harness.write_text(
                rf"""
param([switch]$OmitRemoteRange)
$global:omitRemoteRange = [bool]$OmitRemoteRange
$script:programs = @('C:\mock\service.exe','C:\mock\engine.exe')
$script:rules = @(
    [pscustomobject]@{{
        DisplayName='Local RAG OCR Outbound - Service'; Enabled=$true
        Direction='Outbound'; Action='Block'; Profile='Any'
        Program=$script:programs[0]
    }},
    [pscustomobject]@{{
        DisplayName='Local RAG OCR Outbound - Engine'; Enabled=$true
        Direction='Outbound'; Action='Block'; Profile='Any'
        Program=$script:programs[1]
    }}
)
function global:Get-NetFirewallRule {{
    param($DisplayName,$ErrorAction)
    $script:rules
}}
function global:Get-NetFirewallApplicationFilter {{
    [CmdletBinding()]
    param([Parameter(ValueFromPipeline=$true)]$InputObject)
    process {{ [pscustomobject]@{{ Program=$InputObject.Program }} }}
}}
function global:Get-NetFirewallAddressFilter {{
    [CmdletBinding()]
    param([Parameter(ValueFromPipeline=$true)]$InputObject)
    process {{
        $remote = @(
            '0.0.0.0-126.255.255.255',
            '128.0.0.0-255.255.255.255',
            '::',
            '::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff'
        )
        if ($global:omitRemoteRange) {{ $remote = $remote[0..2] }}
        [pscustomobject]@{{ RemoteAddress=$remote }}
    }}
}}
function global:Get-NetFirewallPortFilter {{
    [CmdletBinding()]
    param([Parameter(ValueFromPipeline=$true)]$InputObject)
    process {{
        [pscustomobject]@{{
            Protocol=256
            LocalPort=@()
            RemotePort=@()
        }}
    }}
}}
. '{verifier}' -ExpectedProgram $script:programs
""",
                encoding="utf-8",
            )
            passed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertEqual(
                json.loads(passed.stdout)["result"],
                "pass",
                passed.stdout,
            )

            missing_range_harness = Path(temporary) / "ocr-firewall-missing-range.ps1"
            missing_range_harness.write_text(
                harness.read_text(encoding="utf-8").replace(
                    "if ($global:omitRemoteRange) { $remote = $remote[0..2] }",
                    "if ($true) { $remote = $remote[0..2] }",
                ),
                encoding="utf-8",
            )
            self.assertIn(
                "if ($true) { $remote = $remote[0..2] }",
                missing_range_harness.read_text(encoding="utf-8"),
            )
            missing_range = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(missing_range_harness),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(json.loads(missing_range.stdout)["result"], "fail")
