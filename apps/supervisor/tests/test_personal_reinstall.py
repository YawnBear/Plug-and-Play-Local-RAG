from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from apps.api.app.processes.settings import (
    CoordinatorProcessSettings,
    OcrProcessSettings,
)


ROOT = Path(__file__).resolve().parents[3]
V8A = ROOT / "ops" / "windows" / "v8a"
MODULE = V8A / "RagPersonal.psm1"


def _powershell(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".ps1", encoding="utf-8-sig", delete=False, dir=ROOT
    ) as handle:
        handle.write(script)
        path = Path(handle.name)
    try:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(path),
                *arguments,
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
    finally:
        path.unlink(missing_ok=True)


CAPSULE_HARNESS = r"""
param([string]$Root,[string]$Module,[string]$Release,[string]$Action,[string]$Mode)
Import-Module $Module -Force
$install = Join-Path $Root 'install'
$data = Join-Path $Root 'data'
foreach ($path in @($install,$data,(Join-Path $install 'state'),
    (Join-Path $install 'config'),(Join-Path $install 'secrets'))) {
    [IO.Directory]::CreateDirectory($path) | Out-Null
}
Protect-RagPersonalPath -Path $data -Directory
$journalPath = Join-Path $install 'state\installation-journal.json'
$journal = New-RagPersonalJournal -Path $journalPath -InstallRoot $install `
    -DataRoot $data -ReleaseRoot $Release
$journal.owned_paths = @('cache','config','logs','secrets','state') | ForEach-Object {
    Join-Path $install $_
}
foreach ($step in @('contracts_validated','prerequisites_validated','roots_created',
    'secrets_created','stores_started','postgres_provisioned','rustfs_provisioned',
    'schema_migrated','storage_bootstrapped','models_acquired','setup_code_issued')) {
    Start-RagPersonalStep -Journal $journal -Step $step -JournalPath $journalPath
    Complete-RagPersonalStep -Journal $journal -Step $step -JournalPath $journalPath
}
[IO.File]::Copy((Join-Path $Release 'ops\windows\v8a\personal-release.json'),
    (Join-Path $install 'config\personal-release.json'),$false)
[IO.File]::Copy((Join-Path $Release 'ops\windows\v8a\compose.personal.yaml'),
    (Join-Path $install 'config\compose.personal.yaml'),$false)
$secrets = New-RagPersonalSecretDocument -InstallationId $journal.installation_id
$sentinel = 'RAW_SECRET_SENTINEL_0123456789abcdef'
$secrets.values.csrf_signing_secret = $sentinel
Write-RagPersonalUtf8File -Path (Join-Path $install 'secrets\installation-secrets.json') `
    -Value ($secrets | ConvertTo-Json -Depth 6) -Protect
$capsule = New-RagPersonalReinstallCapsule -DataAction $Action -InstallRoot $install `
    -DataRoot $data -ReleaseRoot $Release -DevelopmentSource
$rawCapsule = [IO.File]::ReadAllBytes($capsule.path)
$rawContainsSecret = [Text.Encoding]::ASCII.GetString($rawCapsule).Contains($sentinel)
[Array]::Clear($rawCapsule,0,$rawCapsule.Length)
if ($Mode -ceq 'Tamper') {
    $tampered = [IO.File]::ReadAllBytes($capsule.path)
    $tampered[[Math]::Floor($tampered.Length / 2)] = `
        $tampered[[Math]::Floor($tampered.Length / 2)] -bxor 1
    [IO.File]::WriteAllBytes($capsule.path,$tampered)
    [Array]::Clear($tampered,0,$tampered.Length)
    Protect-RagPersonalPath -Path $capsule.path
    try {
        Read-RagPersonalReinstallCapsule -DataRoot $data -InstallRoot $install `
            -ReleaseRoot $Release -DevelopmentSource | Out-Null
        throw 'tampered capsule was accepted'
    } catch {
        if ($_.Exception.Message -eq 'tampered capsule was accepted') { throw }
        [pscustomobject]@{rejected=$true;message=$_.Exception.Message} |
            ConvertTo-Json -Compress
    }
    exit 0
}
if ($Mode -ceq 'Unknown') {
    [IO.File]::WriteAllText((Join-Path $data 'unknown.txt'),'unknown')
    try {
        Read-RagPersonalReinstallCapsule -DataRoot $data -InstallRoot $install `
            -ReleaseRoot $Release -DevelopmentSource | Out-Null
        throw 'unknown data was accepted'
    } catch {
        if ($_.Exception.Message -eq 'unknown data was accepted') { throw }
        [pscustomobject]@{rejected=$true} | ConvertTo-Json -Compress
    }
    exit 0
}
if ($Mode -ceq 'WrongSid') {
    Add-Type -AssemblyName System.Security
    $entropy=[Text.UTF8Encoding]::new($false).GetBytes(
        'LocalRAG.Personal.ReinstallCapsule.v1')
    $cipher=[IO.File]::ReadAllBytes($capsule.path)
    $plain=[Security.Cryptography.ProtectedData]::Unprotect($cipher,$entropy,
        [Security.Cryptography.DataProtectionScope]::CurrentUser)
    $document=[Text.UTF8Encoding]::new($false,$true).GetString($plain) |
        ConvertFrom-Json
    $document.windows_sid='S-1-5-18'
    $changed=[Text.UTF8Encoding]::new($false).GetBytes(
        (($document | ConvertTo-Json -Depth 7 -Compress)+"`n"))
    $protected=[Security.Cryptography.ProtectedData]::Protect($changed,$entropy,
        [Security.Cryptography.DataProtectionScope]::CurrentUser)
    [IO.File]::WriteAllBytes($capsule.path,$protected)
    Protect-RagPersonalPath -Path $capsule.path
    foreach($value in @($entropy,$cipher,$plain,$changed,$protected)) {
        [Array]::Clear($value,0,$value.Length)
    }
    try {
        Read-RagPersonalReinstallCapsule -DataRoot $data -InstallRoot $install `
            -ReleaseRoot $Release -DevelopmentSource | Out-Null
        throw 'wrong SID capsule was accepted'
    } catch {
        if ($_.Exception.Message -eq 'wrong SID capsule was accepted') { throw }
        [pscustomobject]@{rejected=$true} | ConvertTo-Json -Compress
    }
    exit 0
}
if ($Mode -ceq 'InvalidAcl') {
    & (Join-Path ([Environment]::SystemDirectory) 'icacls.exe') $capsule.path `
        /inheritance:e | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'test could not weaken capsule ACL' }
    try {
        Read-RagPersonalReinstallCapsule -DataRoot $data -InstallRoot $install `
            -ReleaseRoot $Release -DevelopmentSource | Out-Null
        throw 'invalid capsule ACL was accepted'
    } catch {
        if ($_.Exception.Message -eq 'invalid capsule ACL was accepted') { throw }
        [pscustomobject]@{rejected=$true} | ConvertTo-Json -Compress
    }
    exit 0
}
[IO.Directory]::Delete($install,$true)
Restore-RagPersonalReinstallCapsule -Capsule $capsule -InstallRoot $install
# A failed reinstall retains both the protected capsule and recovery marker;
# the exact same recovery can be retried transactionally.
Restore-RagPersonalReinstallCapsule -Capsule $capsule -InstallRoot $install
$restored = Read-RagPersonalJson -Path (Join-Path $install `
    'state\installation-journal.json')
$retainedBeforeRuntime = (Test-Path -LiteralPath $capsule.path -PathType Leaf) -and
    (Test-Path -LiteralPath (Join-Path $install 'state\reinstall-recovery.json') `
        -PathType Leaf)
Complete-RagPersonalReinstallRecovery -InstallRoot $install -DataRoot $data `
    -ReleaseRoot $Release | Out-Null
[pscustomobject]@{
    action=$capsule.document.data_action
    installation_id=$restored.installation_id
    capsule_removed=(-not (Test-Path -LiteralPath $capsule.path))
    recovery_marker_removed=(-not (Test-Path -LiteralPath (Join-Path $install `
        'state\reinstall-recovery.json')))
    retained_before_runtime=$retainedBeforeRuntime
    raw_contains_secret=$rawContainsSecret
    setup_step_count=@($restored.completed_steps).Count
} | ConvertTo-Json -Compress
"""


@pytest.mark.parametrize("action", ["Preserve", "Export"])
def test_preserve_and_export_capsules_restore_same_installation(action: str) -> None:
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        result = _powershell(
            CAPSULE_HARNESS,
            "-Root",
            temporary,
            "-Module",
            str(MODULE),
            "-Release",
            str(ROOT),
            "-Action",
            action,
            "-Mode",
            "Recover",
        )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["action"] == action.lower()
    assert len(payload["installation_id"]) == 32
    assert payload["setup_step_count"] == 11
    assert payload["capsule_removed"] is True
    assert payload["recovery_marker_removed"] is True
    assert payload["retained_before_runtime"] is True
    assert payload["raw_contains_secret"] is False


@pytest.mark.parametrize("mode", ["Tamper", "Unknown", "WrongSid", "InvalidAcl"])
def test_reinstall_capsule_rejects_tamper_and_unknown_data(mode: str) -> None:
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        result = _powershell(
            CAPSULE_HARNESS,
            "-Root",
            temporary,
            "-Module",
            str(MODULE),
            "-Release",
            str(ROOT),
            "-Action",
            "Preserve",
            "-Mode",
            mode,
        )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])["rejected"] is True


def test_packaged_capsule_binds_archive_and_authenticated_release_state() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        result = _powershell(
            r"""
param([string]$Root,[string]$Module,[string]$Contracts)
Import-Module $Module -Force
$archive=('a' * 64); $release=Join-Path (Join-Path $Root 'releases') $archive
$contractRoot=Join-Path $release 'ops\windows\v8a'
$install=Join-Path $Root 'install'; $data=Join-Path $Root 'data'
foreach($path in @($contractRoot,$install,$data,(Join-Path $install 'state'),
    (Join-Path $install 'config'),(Join-Path $install 'secrets'))) {
    [IO.Directory]::CreateDirectory($path) | Out-Null
}
Protect-RagPersonalPath -Path $data -Directory
foreach($name in @('personal-release.json','compose.personal.yaml')) {
    [IO.File]::Copy((Join-Path $Contracts $name),(Join-Path $contractRoot $name),$false)
}
$packaged=Get-Content -Raw -LiteralPath (Join-Path $contractRoot `
    'personal-release.json') | ConvertFrom-Json
$packaged.payload_state='packaged'
[IO.File]::WriteAllText((Join-Path $contractRoot 'personal-release.json'),
    ($packaged | ConvertTo-Json -Depth 10))
[IO.File]::WriteAllText((Join-Path $release '.verified-archive-sha256'),$archive+"`n")
$trust=[ordered]@{schema_version=1;policy_id='local-rag-v8-release-trust';
    root_id='rag-root-v8';release_id='release-00000001';release_sequence=1}
$trustPath=Join-Path $release 'release-trust-metadata.json'
[IO.File]::WriteAllText($trustPath,($trust | ConvertTo-Json -Compress))
$journalPath=Join-Path $install 'state\installation-journal.json'
$journal=New-RagPersonalJournal -Path $journalPath -InstallRoot $install `
    -DataRoot $data -ReleaseRoot $release
$journal.owned_paths=@('cache','config','logs','secrets','state') | ForEach-Object {
    Join-Path $install $_
}
foreach($step in @('contracts_validated','prerequisites_validated','roots_created',
    'secrets_created','stores_started','postgres_provisioned','rustfs_provisioned',
    'schema_migrated','storage_bootstrapped','models_acquired','setup_code_issued')) {
    Start-RagPersonalStep -Journal $journal -Step $step -JournalPath $journalPath
    Complete-RagPersonalStep -Journal $journal -Step $step -JournalPath $journalPath
}
[IO.File]::Copy((Join-Path $contractRoot 'personal-release.json'),
    (Join-Path $install 'config\personal-release.json'),$false)
[IO.File]::Copy((Join-Path $contractRoot 'compose.personal.yaml'),
    (Join-Path $install 'config\compose.personal.yaml'),$false)
$secrets=New-RagPersonalSecretDocument -InstallationId $journal.installation_id
Write-RagPersonalUtf8File -Path (Join-Path $install `
    'secrets\installation-secrets.json') -Protect `
    -Value ($secrets | ConvertTo-Json -Depth 6)
$releaseState=[ordered]@{schema_version=1;release_id='release-00000001';
    release_sequence=1;trust_metadata_sha256=(Get-FileHash -LiteralPath $trustPath `
        -Algorithm SHA256).Hash.ToLowerInvariant()}
Write-RagPersonalUtf8File -Path (Join-Path $install 'state\release-state.json') `
    -Protect -Value ($releaseState | ConvertTo-Json -Compress)
$capsule=New-RagPersonalReinstallCapsule -DataAction Preserve -InstallRoot $install `
    -DataRoot $data -ReleaseRoot $release
$verified=Read-RagPersonalReinstallCapsule -DataRoot $data -InstallRoot $install `
    -ReleaseRoot $release
[pscustomobject]@{archive=$verified.document.release_identity.archive_sha256;
    release_state=$verified.document.release_identity.release_state_sha256} |
    ConvertTo-Json -Compress
""",
            "-Root",
            temporary,
            "-Module",
            str(MODULE),
            "-Contracts",
            str(V8A),
        )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["archive"] == "a" * 64
    assert len(payload["release_state"]) == 64


def test_runtime_active_refuses_maintenance() -> None:
    result = _powershell(
        r"""
param([string]$Module)
function global:Get-NetTCPConnection {
    param($State,$LocalPort,$ErrorAction)
    if ($LocalPort -eq 3000) { [pscustomobject]@{LocalPort=3000} }
}
Import-Module $Module -Force
try {
    Assert-RagPersonalRuntimeStopped
    throw 'active runtime was accepted'
} catch {
    if ($_.Exception.Message -eq 'active runtime was accepted') { throw }
    [pscustomobject]@{rejected=$true;message=$_.Exception.Message} |
        ConvertTo-Json -Compress
}
""",
        "-Module",
        str(MODULE),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["rejected"] is True
    assert "Close the Local RAG application window" in payload["message"]


def _env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    )


def test_generated_runtime_environments_satisfy_process_settings() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        result = _powershell(
            r"""
param([string]$Root,[string]$Module,[string]$Release)
Import-Module $Module -Force
$install=Join-Path $Root 'install'; $data=Join-Path $Root 'data'
$config=Join-Path $install 'config'
foreach($path in @($install,$data,$config,(Join-Path $install 'cache'),
    (Join-Path $install 'state'),(Join-Path $data 'application'),
    (Join-Path $data 'ocr-work'))) {
    [IO.Directory]::CreateDirectory($path) | Out-Null
}
$secrets=New-RagPersonalSecretDocument -InstallationId ('a' * 32)
Write-RagPersonalRuntimeEnvironments -Secrets $secrets -ConfigurationRoot $config `
    -PersonalDataRoot $data -InstallRoot $install -ReleaseRoot $Release `
    -DevelopmentSource
[pscustomobject]@{config=$config;install=$install;data=$data} |
    ConvertTo-Json -Compress
""",
            "-Root",
            temporary,
            "-Module",
            str(MODULE),
            "-Release",
            str(ROOT),
        )
        assert result.returncode == 0, result.stderr
        paths = json.loads(result.stdout.strip().splitlines()[-1])
        inference = _env(Path(paths["config"]) / "inference.env")
        ocr = _env(Path(paths["config"]) / "ocr.env")
        with pytest.MonkeyPatch.context() as patch:
            for key, value in inference.items():
                patch.setenv(key, value)
            coordinator = CoordinatorProcessSettings()
        with pytest.MonkeyPatch.context() as patch:
            for key, value in ocr.items():
                patch.setenv(key, value)
            ocr_settings = OcrProcessSettings()
        assert coordinator.hf_home != ocr_settings.hf_home
        assert coordinator.reranker_model_path == (
            Path(paths["data"]) / "models" / "bge-reranker-v2-m3"
        )
        assert ocr_settings.ocr_model_asset_root == (
            Path(paths["data"]) / "models" / "paddleocr-vl-1.6"
        )
        assert inference["HF_HUB_OFFLINE"] == "true"
        assert inference["TOKENIZERS_PARALLELISM"] == "false"
        assert ocr["TRANSFORMERS_OFFLINE"] == "true"
        assert ocr["PADDLE_PDX_CACHE_HOME"] == ocr["OCR_MODEL_ASSET_ROOT"]


@pytest.mark.parametrize(
    ("version", "product_type", "supported"),
    [
        ("10.0.10240.0", 1, True),
        ("10.0.26100.0", 1, True),
        ("10.0.10239.0", 1, False),
        ("10.0.26100.0", 3, False),
        ("6.3.9600.0", 1, False),
    ],
)
def test_windows_classifier(version: str, product_type: int, supported: bool) -> None:
    result = _powershell(
        r"""
param([string]$Module,[string]$Version,[int]$ProductType)
Import-Module $Module -Force
[pscustomobject]@{supported=(Test-RagPersonalSupportedWindows `
    -Version ([Version]$Version) -ProductType $ProductType)} |
    ConvertTo-Json -Compress
""",
        "-Module",
        str(MODULE),
        "-Version",
        version,
        "-ProductType",
        str(product_type),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["supported"] is supported


@pytest.mark.parametrize(
    ("action", "required"),
    [("Preserve", True), ("Export", True), ("Delete", False)],
)
def test_delete_flow_creates_no_capsule_contract(action: str, required: bool) -> None:
    result = _powershell(
        r"""
param([string]$Module,[string]$Action)
Import-Module $Module -Force
[pscustomobject]@{required=(Test-RagPersonalReinstallCapsuleRequired `
    -DataAction $Action)} | ConvertTo-Json -Compress
""",
        "-Module",
        str(MODULE),
        "-Action",
        action,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["required"] is required
