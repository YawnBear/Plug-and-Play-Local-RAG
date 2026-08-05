[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param(
    [ValidateSet('Guided','Check','Install','Recover')][string]$Mode = 'Guided',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'LocalRAG\Personal'),
    [string]$DevelopmentArtifactRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagPersonal.psm1') -Force

$channel = 'https://github.com/YawnBear/Plug-and-Play-Local-RAG/releases/latest/download'
$pinnedSigners = 'c3dec800e21c240031dff4ab9d5e22625dd1841ac8a536b56f9267c97d06acb2'
$artifactNames = @(
    'Local-RAG-Personal.zip', 'SBOM.cdx.json', 'SHA256SUMS',
    'release-trust-metadata.json', 'Verify-and-Install-Local-RAG.ps1',
    'Install-Local-RAG.cmd'
)

function Write-RagPersonalUpdateJson {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        Write-RagPersonalUtf8File -Path $temporary -Protect `
            -Value (($Value | ConvertTo-Json -Depth 8 -Compress) + "`n")
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [IO.File]::Replace($temporary, $Path, $null, $true)
        }
        else { [IO.File]::Move($temporary, $Path) }
        Protect-RagPersonalPath -Path $Path
    }
    finally {
        if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) }
    }
}

function Copy-RagPersonalUpdateAsset {
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Target)
    if ($Name -notin @('update-manifest.json','update-manifest.json.sig') + $artifactNames) {
        throw 'Update asset name is not allowlisted.'
    }
    if (-not [string]::IsNullOrWhiteSpace($DevelopmentArtifactRoot)) {
        $sourceRoot = Assert-RagPersonalPathSafe -Path $DevelopmentArtifactRoot
        $source = [IO.Path]::GetFullPath((Join-Path $sourceRoot $Name))
        if ([IO.Path]::GetDirectoryName($source) -cne $sourceRoot) {
            throw 'Development update asset escapes its root.'
        }
        [IO.File]::Copy($source, $Target, $true)
        return
    }
    $uri = "$channel/$Name"
    Import-Module BitsTransfer -ErrorAction Stop
    Start-BitsTransfer -Source $uri -Destination $Target -DisplayName 'Local RAG update' `
        -Description "Downloading $Name" -ErrorAction Stop
}

function Assert-RagPersonalManifestSignature {
    param(
        [Parameter(Mandatory)][string]$Manifest,
        [Parameter(Mandatory)][string]$Signature,
        [Parameter(Mandatory)][string]$AllowedSigners
    )
    if ((Get-FileHash -LiteralPath $AllowedSigners -Algorithm SHA256).Hash.ToLowerInvariant() `
        -cne $pinnedSigners) {
        throw 'The Personal update signing key does not match the pinned trust root.'
    }
    $ssh = Join-Path ([Environment]::SystemDirectory) 'OpenSSH\ssh-keygen.exe'
    if (-not (Test-Path -LiteralPath $ssh -PathType Leaf)) {
        throw 'Windows OpenSSH signature verification is unavailable.'
    }
    $bytes = [IO.File]::ReadAllBytes($Manifest)
    $process = [Diagnostics.Process]::new()
    $process.StartInfo.FileName = $ssh
    $process.StartInfo.Arguments = "-Y verify -f `"$AllowedSigners`" -I rag-release -n file -s `"$Signature`""
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardInput = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    if (-not $process.Start()) { throw 'The signature verifier could not start.' }
    $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $process.StandardInput.Close()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw 'The Personal update signature is invalid.' }
}

function Get-RagPersonalManifest {
    param([Parameter(Mandatory)][string]$DownloadRoot, [Parameter(Mandatory)][string]$ReleaseRoot)
    $manifest = Join-Path $DownloadRoot 'update-manifest.json'
    $signature = "$manifest.sig"
    Copy-RagPersonalUpdateAsset -Name 'update-manifest.json' -Target $manifest
    Copy-RagPersonalUpdateAsset -Name 'update-manifest.json.sig' -Target $signature
    $signers = Join-Path $ReleaseRoot 'ops\windows\release-allowed-signers'
    Assert-RagPersonalManifestSignature -Manifest $manifest -Signature $signature `
        -AllowedSigners $signers
    $document = Read-RagPersonalJson -Path $manifest
    $fields = @($document.PSObject.Properties.Name | Sort-Object)
    if ($document.schema_version -ne 1 -or ($fields -join ',') -cne `
        'artifacts,schema_version,version' -or [string]$document.version -cnotmatch `
        '^[A-Za-z0-9._-]{1,64}$') {
        throw 'The signed Personal update manifest is invalid.'
    }
    $names = @($document.artifacts | ForEach-Object { [string]$_.filename } | Sort-Object)
    if (($names -join ',') -cne (($artifactNames | Sort-Object) -join ',')) {
        throw 'The signed Personal update artifact set is not exact.'
    }
    foreach ($artifact in @($document.artifacts)) {
        $artifactFields = @($artifact.PSObject.Properties.Name | Sort-Object)
        if (($artifactFields -join ',') -cne 'filename,sha256,size' -or
            [string]$artifact.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            [int64]$artifact.size -lt 1) {
            throw 'The signed Personal update artifact entry is invalid.'
        }
    }
    return $document
}

function Get-RagPersonalVerifiedBackup {
    param(
        [Parameter(Mandatory)][string]$DataRoot,
        [Parameter(Mandatory)][ValidatePattern('^[0-9]{4}_[a-z0-9_]+$')]
        [string]$ExpectedRevision
    )
    $catalog = Read-RagPersonalJson -Path (Join-Path $DataRoot 'backup-catalog.json')
    if ($catalog.schema_version -ne 1 -or [string]$catalog.retention.mode -cne 'keep_all') {
        throw 'A restore-verified backup catalog is required for this update.'
    }
    foreach ($entry in @($catalog.entries)) {
        if ([string]$entry.database_revision -cne $ExpectedRevision) { continue }
        $bundle = Assert-RagPersonalPathSafe -Path ([string]$entry.bundle_path)
        $dump = Join-Path $bundle 'database.dump'
        $evidence = Join-Path $bundle 'restore-verification.json'
        if ((Test-Path -LiteralPath $dump -PathType Leaf) -and
            (Test-Path -LiteralPath $evidence -PathType Leaf) -and
            (Get-FileHash -LiteralPath $dump -Algorithm SHA256).Hash.ToLowerInvariant() `
                -ceq [string]$entry.database_sha256 -and
            (Get-FileHash -LiteralPath $evidence -Algorithm SHA256).Hash.ToLowerInvariant() `
                -ceq [string]$entry.restore_verification_sha256) {
            return [pscustomobject]@{
                bundle=$bundle; dump=$dump
                dump_sha256=[string]$entry.database_sha256
            }
        }
    }
    throw 'No intact restore-verified backup is available for this schema update.'
}

function Assert-RagPersonalRuntimeStopped {
    if ($null -eq (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        throw 'Windows listener inspection is unavailable; the update cannot start safely.'
    }
    foreach ($port in @(3000, 8000, 8100, 8101, 8102)) {
        if ($null -ne (Get-NetTCPConnection -State Listen -LocalPort $port `
                -ErrorAction SilentlyContinue)) {
            throw 'Close the Local RAG application window, then run the update again.'
        }
    }
}

function Invoke-RagPersonalMigration {
    param([Parameter(Mandatory)][string]$ReleaseRoot, [Parameter(Mandatory)][string]$ConfigRoot)
    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines((Join-Path $ConfigRoot 'migration.env'))) {
        $separator = $line.IndexOf('=')
        if ($separator -le 0) { throw 'The migration environment is invalid.' }
        $values[$line.Substring(0,$separator)] = $line.Substring($separator + 1)
    }
    $prior = @{}
    try {
        foreach ($name in $values.Keys) {
            $prior[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
        }
        $python = Join-Path $ReleaseRoot 'runtimes\api-python\python.exe'
        Push-Location (Join-Path $ReleaseRoot 'apps\api')
        try { & $python -m alembic upgrade head }
        finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw 'Candidate database migration failed.' }
    }
    finally {
        foreach ($name in $values.Keys) {
            [Environment]::SetEnvironmentVariable($name, $prior[$name], 'Process')
        }
    }
}

function Restore-RagPersonalDatabase {
    param(
        [Parameter(Mandatory)]$Journal,
        [Parameter(Mandatory)][string]$Dump,
        [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')]
        [string]$ExpectedSha256
    )
    $dumpItem = Get-Item -LiteralPath $Dump -Force -ErrorAction Stop
    if ($dumpItem.PSIsContainer -or
        ($dumpItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        (Get-FileHash -LiteralPath $Dump -Algorithm SHA256).Hash.ToLowerInvariant() `
            -cne $ExpectedSha256) {
        throw 'The verified pre-update database backup is no longer intact.'
    }
    $docker = (Get-Command docker.exe -ErrorAction Stop).Source
    $compose = Join-Path $Journal.install_root 'config\compose.personal.yaml'
    $environment = Join-Path $Journal.install_root 'config\compose.env'
    & $docker compose -p $Journal.compose_project --env-file $environment -f $compose `
        cp $Dump 'postgres:/tmp/localrag-update-rollback.dump'
    if ($LASTEXITCODE -ne 0) { throw 'Could not stage the rollback database.' }
    & $docker compose -p $Journal.compose_project --env-file $environment -f $compose `
        exec -T postgres dropdb -U rag_cluster_admin --maintenance-db=postgres `
        --if-exists --force rag
    if ($LASTEXITCODE -ne 0) { throw 'Could not reset the failed candidate database.' }
    & $docker compose -p $Journal.compose_project --env-file $environment -f $compose `
        exec -T postgres createdb -U rag_cluster_admin --maintenance-db=postgres `
        --template=template0 --owner=rag_cluster_admin rag
    if ($LASTEXITCODE -ne 0) { throw 'Could not recreate the rollback database.' }
    & $docker compose -p $Journal.compose_project --env-file $environment -f $compose `
        exec -T postgres pg_restore -U rag_cluster_admin -d rag `
        --exit-on-error '/tmp/localrag-update-rollback.dump'
    if ($LASTEXITCODE -ne 0) { throw 'Database rollback restore failed.' }
}

function Set-RagPersonalReleasePaths {
    param([Parameter(Mandatory)][string]$ConfigRoot, [Parameter(Mandatory)][string]$From,
        [Parameter(Mandatory)][string]$To)
    foreach ($name in @('api.env','ingestion.env','deletion.env','inference.env','ocr.env')) {
        $path = Join-Path $ConfigRoot $name
        $text = [IO.File]::ReadAllText($path)
        $updated = $text.Replace($From, $To)
        if ($updated -ne $text) {
            Write-RagPersonalUtf8File -Path $path -Value $updated -Protect
        }
    }
}

function Invoke-RagPersonalCandidateReadiness {
    param([Parameter(Mandatory)][string]$Install, [Parameter(Mandatory)][string]$Data,
        [Parameter(Mandatory)][string]$Candidate)
    $python = Join-Path $Candidate 'runtimes\api-python\python.exe'
    $priorPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH','Process')
    try {
        [Environment]::SetEnvironmentVariable('PYTHONPATH',$Candidate,'Process')
        & $python -m apps.supervisor.personal_runtime --install-root $Install `
            --data-root $Data --release-root $Candidate --readiness-once
        if ($LASTEXITCODE -ne 0) { throw 'Candidate runtime readiness failed.' }
    }
    finally {
        [Environment]::SetEnvironmentVariable('PYTHONPATH',$priorPythonPath,'Process')
    }
}

function Set-RagPersonalUpdateShortcuts {
    param([Parameter(Mandatory)][string]$Release)
    $menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Local RAG'
    if (-not (Test-Path -LiteralPath $menu -PathType Container) -or
        ((Get-Item -LiteralPath $menu -Force).Attributes -band `
            [IO.FileAttributes]::ReparsePoint)) {
        throw 'The Local RAG Start-menu folder is unavailable or unsafe.'
    }
    $shell = New-Object -ComObject WScript.Shell
    $cmd = Join-Path ([Environment]::SystemDirectory) 'cmd.exe'
    $launchers = [ordered]@{
        'Start Local RAG.lnk' = 'Start-Local-RAG.cmd'
        'Check for updates.lnk' = 'Check-for-Updates.cmd'
        'Recovery - issue setup code.lnk' = 'Issue-New-Setup-Code.cmd'
        'Uninstall Local RAG.lnk' = 'Uninstall-Local-RAG.cmd'
    }
    foreach ($entry in $launchers.GetEnumerator()) {
        $target = Join-Path $Release "ops\windows\v8a\$($entry.Value)"
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "The candidate launcher is missing: $($entry.Value)"
        }
        $shortcut = $shell.CreateShortcut((Join-Path $menu $entry.Key))
        $shortcut.TargetPath = $cmd
        $shortcut.Arguments = "/d /c `"`"$target`"`""
        $shortcut.WorkingDirectory = $Release
        $shortcut.Description = $entry.Key.Replace('.lnk', '')
        $shortcut.Save()
    }
}

$root = Assert-RagPersonalPathSafe -Path $InstallRoot
$journalPath = Join-Path $root 'state\installation-journal.json'
$journal = Read-RagPersonalJson -Path $journalPath
Assert-RagPersonalJournal -Journal $journal
$releaseRoot = Assert-RagPersonalPathSafe -Path ([string]$journal.release_root)
$dataRoot = Assert-RagPersonalPathSafe -Path ([string]$journal.data_root)
$updateRoot = Join-Path $root 'cache\updates'
if (-not (Test-Path -LiteralPath $updateRoot)) {
    [IO.Directory]::CreateDirectory($updateRoot) | Out-Null
    Protect-RagPersonalPath -Path $updateRoot -Directory
}
$transactionPath = Join-Path $root 'state\update-transaction.json'

if ($Mode -ceq 'Recover') {
    Assert-RagPersonalRuntimeStopped
    $transaction = Read-RagPersonalJson -Path $transactionPath
    if ([string]$transaction.state -cnotin @('prepared','migrated','candidate_failed')) {
        throw 'The Personal update transaction is not recoverable.'
    }
    Set-RagPersonalReleasePaths -ConfigRoot (Join-Path $root 'config') `
        -From ([string]$transaction.candidate_root) -To ([string]$transaction.prior_release_root)
    Copy-Item -LiteralPath ([string]$transaction.prior_release_contract) `
        -Destination (Join-Path $root 'config\personal-release.json') -Force
    Copy-Item -LiteralPath ([string]$transaction.prior_compose_contract) `
        -Destination (Join-Path $root 'config\compose.personal.yaml') -Force
    if ($transaction.schema_changing -eq $true) {
        Restore-RagPersonalDatabase -Journal $journal `
            -Dump ([string]$transaction.backup_dump) `
            -ExpectedSha256 ([string]$transaction.backup_dump_sha256)
    }
    $journal.release_root = [string]$transaction.prior_release_root
    Save-RagPersonalJournal -Journal $journal -Path $journalPath
    Set-RagPersonalUpdateShortcuts -Release ([string]$transaction.prior_release_root)
    $transaction.state = 'rolled_back'
    Write-RagPersonalUpdateJson -Path $transactionPath -Value $transaction
    [pscustomobject]@{result='rolled_back';release_root=$journal.release_root} | ConvertTo-Json
    return
}

$downloadRoot = Join-Path $updateRoot ([guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($downloadRoot) | Out-Null
Protect-RagPersonalPath -Path $downloadRoot -Directory
$manifest = Get-RagPersonalManifest -DownloadRoot $downloadRoot -ReleaseRoot $releaseRoot
$currentStatePath = Join-Path $root 'state\release-state.json'
$currentState = if (Test-Path -LiteralPath $currentStatePath -PathType Leaf) {
    Read-RagPersonalJson -Path $currentStatePath
} else { [pscustomobject]@{release_sequence=0;release_id=$null} }
$python = Join-Path $releaseRoot 'runtimes\api-python\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
}

if ($Mode -ceq 'Check') {
    $trustPath = Join-Path $downloadRoot 'release-trust-metadata.json'
    Copy-RagPersonalUpdateAsset -Name 'release-trust-metadata.json' -Target $trustPath
    $trustEntry = @(
        $manifest.artifacts | Where-Object {
            [string]$_.filename -ceq 'release-trust-metadata.json'
        }
    )[0]
    $trustItem = Get-Item -LiteralPath $trustPath -Force -ErrorAction Stop
    if ($trustItem.PSIsContainer -or
        ($trustItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $trustItem.Length -ne [int64]$trustEntry.size -or
        (Get-FileHash -LiteralPath $trustPath -Algorithm SHA256).Hash.ToLowerInvariant() `
            -cne [string]$trustEntry.sha256) {
        throw 'The signed Personal update metadata download is invalid.'
    }
    $priorPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH','Process')
    try {
        [Environment]::SetEnvironmentVariable('PYTHONPATH',$releaseRoot,'Process')
        $metadataText = & $python -m apps.supervisor.personal_update_check_cli `
            --trust-metadata $trustPath --installed-state $currentStatePath
        if ($LASTEXITCODE -ne 0) { throw 'The signed update metadata is not acceptable.' }
    }
    finally {
        [Environment]::SetEnvironmentVariable('PYTHONPATH',$priorPythonPath,'Process')
    }
    $metadata = ($metadataText -join "`n") | ConvertFrom-Json
    [pscustomobject]@{
        result=[string]$metadata.result
        current_release_sequence=[int]$currentState.release_sequence
        available_release_sequence=[int]$metadata.release_sequence
        available_version=[string]$manifest.version; signature_verified=$true
        package_downloaded=$false
    } | ConvertTo-Json
    return
}
Assert-RagPersonalRuntimeStopped
if ($Mode -ceq 'Guided') {
    $choice = Read-Host "Signed update $($manifest.version) is available. Type INSTALL to download and install it"
    if ($choice -cne 'INSTALL') {
        [pscustomobject]@{result='cancelled';data_preserved=$true} | ConvertTo-Json
        return
    }
}
foreach ($name in $artifactNames) {
    Copy-RagPersonalUpdateAsset -Name $name -Target (Join-Path $downloadRoot $name)
}
$candidate = Join-Path $updateRoot ('candidate-' + [guid]::NewGuid().ToString('N'))
$stage = Join-Path $updateRoot ('verified-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($stage) | Out-Null
Protect-RagPersonalPath -Path $stage -Directory
$priorPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH','Process')
try {
    [Environment]::SetEnvironmentVariable('PYTHONPATH',$releaseRoot,'Process')
    $verificationText = & $python -m apps.supervisor.personal_update_cli `
        --manifest (Join-Path $downloadRoot 'update-manifest.json') `
        --signature (Join-Path $downloadRoot 'update-manifest.json.sig') `
        --artifact-root $downloadRoot `
        --allowed-signers (Join-Path $releaseRoot 'ops\windows\release-allowed-signers') `
        --allowed-signers-sha256 $pinnedSigners --stage-root $stage `
        --candidate-root $candidate --installed-state $currentStatePath
    if ($LASTEXITCODE -ne 0) { throw 'The downloaded Personal update failed verification.' }
}
finally {
    [Environment]::SetEnvironmentVariable('PYTHONPATH',$priorPythonPath,'Process')
}
$verified = ($verificationText -join "`n") | ConvertFrom-Json
if ($verified.result -cne 'verified') { throw 'The Personal update was not verified.' }
if ([int]$verified.release_sequence -le [int]$currentState.release_sequence) {
    throw 'No newer Personal release is available.'
}
$installedRelease = Read-RagPersonalJson -Path (Join-Path $root 'config\personal-release.json')
$installedCompose = Join-Path $root 'config\compose.personal.yaml'
$candidateCompose = Join-Path $verified.candidate_root 'ops\windows\v8a\compose.personal.yaml'
if ((Get-FileHash -LiteralPath $installedCompose -Algorithm SHA256).Hash `
        -cne (Get-FileHash -LiteralPath $candidateCompose -Algorithm SHA256).Hash) {
    throw 'This Personal updater cannot change the storage service contract in place.'
}
$schemaChanging = [string]$installedRelease.expected_alembic_revision -cne `
    [string]$verified.expected_alembic_revision
$backup = if ($schemaChanging) {
    Get-RagPersonalVerifiedBackup -DataRoot $dataRoot `
        -ExpectedRevision ([string]$installedRelease.expected_alembic_revision)
} else { $null }
if (-not $PSCmdlet.ShouldProcess($root, "Install verified Personal release $($verified.version)")) {
    return
}
$contractBackupRoot = Join-Path $downloadRoot 'installed-contracts'
[IO.Directory]::CreateDirectory($contractBackupRoot) | Out-Null
$priorReleaseContract = Join-Path $contractBackupRoot 'personal-release.json'
$priorComposeContract = Join-Path $contractBackupRoot 'compose.personal.yaml'
[IO.File]::Copy((Join-Path $root 'config\personal-release.json'),$priorReleaseContract,$false)
[IO.File]::Copy((Join-Path $root 'config\compose.personal.yaml'),$priorComposeContract,$false)
$transaction = [ordered]@{
    schema_version=1; state='prepared'; prior_release_root=$releaseRoot
    candidate_root=[string]$verified.candidate_root
    release_id=[string]$verified.release_id
    release_sequence=[int]$verified.release_sequence
    expected_alembic_revision=[string]$verified.expected_alembic_revision
    schema_changing=$schemaChanging
    backup_dump=if ($null -eq $backup) { $null } else { [string]$backup.dump }
    backup_dump_sha256=if ($null -eq $backup) { $null } else { [string]$backup.dump_sha256 }
    prior_release_contract=$priorReleaseContract
    prior_compose_contract=$priorComposeContract
    manifest_sha256=[string]$verified.manifest_sha256
}
Write-RagPersonalUpdateJson -Path $transactionPath -Value $transaction
$configRoot = Join-Path $root 'config'
try {
    if ($schemaChanging) {
        Invoke-RagPersonalMigration -ReleaseRoot ([string]$verified.candidate_root) `
            -ConfigRoot $configRoot
        $transaction.state = 'migrated'
        Write-RagPersonalUpdateJson -Path $transactionPath -Value $transaction
    }
    Set-RagPersonalReleasePaths -ConfigRoot $configRoot -From $releaseRoot `
        -To ([string]$verified.candidate_root)
    Invoke-RagPersonalCandidateReadiness -Install $root -Data $dataRoot `
        -Candidate ([string]$verified.candidate_root)
    Copy-Item -LiteralPath (Join-Path $verified.candidate_root 'ops\windows\v8a\personal-release.json') `
        -Destination (Join-Path $configRoot 'personal-release.json') -Force
    Copy-Item -LiteralPath (Join-Path $verified.candidate_root 'ops\windows\v8a\compose.personal.yaml') `
        -Destination (Join-Path $configRoot 'compose.personal.yaml') -Force
    Protect-RagPersonalPath -Path (Join-Path $configRoot 'personal-release.json')
    Protect-RagPersonalPath -Path (Join-Path $configRoot 'compose.personal.yaml')
    $journal.release_root = [string]$verified.candidate_root
    Save-RagPersonalJournal -Journal $journal -Path $journalPath
    Set-RagPersonalUpdateShortcuts -Release ([string]$verified.candidate_root)
    Write-RagPersonalUpdateJson -Path $currentStatePath -Value ([ordered]@{
        schema_version=1; release_id=[string]$verified.release_id
        release_sequence=[int]$verified.release_sequence
        trust_metadata_sha256=[string]$verified.trust_metadata_sha256
    })
    $transaction.state = 'committed'
    Write-RagPersonalUpdateJson -Path $transactionPath -Value $transaction
}
catch {
    $transaction.state = 'candidate_failed'
    Write-RagPersonalUpdateJson -Path $transactionPath -Value $transaction
    Set-RagPersonalReleasePaths -ConfigRoot $configRoot `
        -From ([string]$verified.candidate_root) -To $releaseRoot
    Copy-Item -LiteralPath $priorReleaseContract `
        -Destination (Join-Path $configRoot 'personal-release.json') -Force
    Copy-Item -LiteralPath $priorComposeContract `
        -Destination (Join-Path $configRoot 'compose.personal.yaml') -Force
    $journal.release_root = $releaseRoot
    Save-RagPersonalJournal -Journal $journal -Path $journalPath
    Set-RagPersonalUpdateShortcuts -Release $releaseRoot
    if ($schemaChanging) {
        Restore-RagPersonalDatabase -Journal $journal -Dump $backup.dump `
            -ExpectedSha256 $backup.dump_sha256
    }
    Invoke-RagPersonalCandidateReadiness -Install $root -Data $dataRoot -Candidate $releaseRoot
    $transaction.state = 'rolled_back'
    Write-RagPersonalUpdateJson -Path $transactionPath -Value $transaction
    throw
}
[pscustomobject]@{
    result='updated'; version=[string]$verified.version
    release_sequence=[int]$verified.release_sequence
    candidate_readiness='pass'; rollback_ready=$true
    schema_backup_enforced=$schemaChanging
} | ConvertTo-Json
