[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'LocalRAG\Personal'),
    [ValidateSet('Preserve','Export','Delete')][string]$DataAction = 'Preserve',
    [string]$VerifiedBackupBundle,
    [string]$DeleteConfirmation
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagPersonal.psm1') -Force

$root = Assert-RagPersonalPathSafe -Path $InstallRoot
$journalPath = Join-Path $root 'state\installation-journal.json'
$journal = Read-RagPersonalJson -Path $journalPath
Assert-RagPersonalJournal -Journal $journal
$dataRoot = Assert-RagPersonalPathSafe -Path ([string]$journal.data_root)
$releaseRoot = Assert-RagPersonalPathSafe -Path ([string]$journal.release_root)
$installedRelease = Read-RagPersonalJson -Path (Join-Path $root `
    'config\personal-release.json')
$developmentSource = [string]$installedRelease.payload_state -ceq 'development_template'
if ($dataRoot.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Preserved Personal data unexpectedly resides under the removable install root.'
}
$expectedOwned = @('cache','config','logs','secrets','state') | ForEach-Object {
    Join-Path $root $_
}
$actualOwned = @($journal.owned_paths)
if (
    $actualOwned.Count -ne $expectedOwned.Count -or
    (@($actualOwned | Sort-Object) -join ',') -cne (@($expectedOwned | Sort-Object) -join ',')
) {
    throw 'Personal uninstall ledger does not contain the exact owned path set.'
}
if ($DataAction -ceq 'Export') {
    if ([string]::IsNullOrWhiteSpace($VerifiedBackupBundle)) {
        throw 'Export requires the folder of an existing restore-verified backup.'
    }
    $bundle = Assert-RagPersonalPathSafe -Path $VerifiedBackupBundle
    $envelope = Read-RagPersonalJson -Path (Join-Path $bundle 'backup-bundle.json')
    $verification = Read-RagPersonalJson -Path (Join-Path $bundle 'restore-verification.json')
    $catalog = Read-RagPersonalJson -Path (Join-Path $dataRoot 'backup-catalog.json')
    $catalogEntry = @($catalog.entries | Where-Object {
        [string]::Equals(
            [IO.Path]::GetFullPath([string]$_.bundle_path), $bundle,
            [StringComparison]::OrdinalIgnoreCase
        )
    })
    if (
        [string]$envelope.backup_run_id -cnotmatch `
            '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
        [string]$verification.backup_run_id -cne [string]$envelope.backup_run_id -or
        [string]$verification.manifest_sha256 -cne [string]$envelope.manifest_sha256 -or
        [string]$verification.database_security -cne 'pass' -or
        [string]$verification.storage_inventory -cne 'pass' -or
        $catalog.schema_version -ne 1 -or $catalogEntry.Count -ne 1 -or
        (Get-FileHash -LiteralPath (Join-Path $bundle 'database.dump') `
            -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            [string]$catalogEntry[0].database_sha256 -or
        (Get-FileHash -LiteralPath (Join-Path $bundle 'restore-verification.json') `
            -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            [string]$catalogEntry[0].restore_verification_sha256
    ) {
        throw 'The selected backup does not contain matching restore-verification evidence.'
    }
}
if ($DataAction -ceq 'Delete' -and $DeleteConfirmation -cne 'DELETE LOCAL RAG DATA') {
    throw 'Data deletion requires the exact confirmation: DELETE LOCAL RAG DATA'
}
foreach ($path in $actualOwned) {
    $resolved = Assert-RagPersonalPathSafe -Path ([string]$path)
    if (-not $resolved.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Personal uninstall ledger contains a path outside the install root.'
    }
}
Assert-RagPersonalRuntimeStopped
$description = if ($DataAction -ceq 'Delete') {
    'Uninstall Local RAG Personal and permanently delete its data'
} elseif ($DataAction -ceq 'Export') {
    'Uninstall Local RAG Personal after confirming a restore-verified export'
} else {
    'Uninstall Local RAG Personal while preserving data'
}
if (-not $PSCmdlet.ShouldProcess($root, $description)) {
    return
}

if (Test-RagPersonalReinstallCapsuleRequired -DataAction $DataAction) {
    New-RagPersonalReinstallCapsule -DataAction $DataAction `
        -InstallRoot $root -DataRoot $dataRoot -ReleaseRoot $releaseRoot `
        -DevelopmentSource:$developmentSource | Out-Null
}

$menuMarker = Join-Path $root 'state\start-menu.json'
if (Test-Path -LiteralPath $menuMarker -PathType Leaf) {
    $menuLedger = Read-RagPersonalJson -Path $menuMarker
    $expectedEntries = @(
        'Start Local RAG.lnk', 'Check for updates.lnk',
        'Recovery - issue setup code.lnk', 'Uninstall Local RAG.lnk'
    )
    if (
        $menuLedger.schema_version -ne 1 -or
        (@($menuLedger.entries | Sort-Object) -join ',') -cne
            (@($expectedEntries | Sort-Object) -join ',')
    ) {
        throw 'The Start-menu ownership ledger is invalid.'
    }
    $expectedMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Local RAG'
    $menu = [IO.Path]::GetFullPath([string]$menuLedger.menu_path)
    if ($menu -cne [IO.Path]::GetFullPath($expectedMenu)) {
        throw 'The Start-menu ownership ledger points outside the expected location.'
    }
    if (Test-Path -LiteralPath $menu -PathType Container) {
        $menuItem = Get-Item -LiteralPath $menu -Force
        if ($menuItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'The Local RAG Start-menu folder is a reparse point.'
        }
        foreach ($entry in $expectedEntries) {
            $link = Join-Path $menu $entry
            if (Test-Path -LiteralPath $link -PathType Leaf) {
                [IO.File]::Delete($link)
            }
        }
        if (@(Get-ChildItem -LiteralPath $menu -Force).Count -eq 0) {
            [IO.Directory]::Delete($menu, $false)
        }
    }
}

$docker = Get-Command docker.exe -ErrorAction SilentlyContinue
if (
    $null -eq $docker -and
    (Test-RagPersonalStepComplete -Journal $journal -Step 'stores_started')
) {
    throw 'Docker Desktop is required to stop Personal stores before uninstall.'
}
if ($null -ne $docker) {
    $containers = @(& $docker.Source ps -a --filter `
        "label=com.docker.compose.project=$($journal.compose_project)" --format '{{.ID}}')
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Personal containers before uninstall.' }
    foreach ($container in $containers) {
        if ([string]::IsNullOrWhiteSpace($container)) { continue }
        $label = (& $docker.Source inspect --format `
            '{{ index .Config.Labels "com.localrag.installation-id" }}' $container).Trim()
        if ($LASTEXITCODE -ne 0 -or $label -cne $journal.installation_id) {
            throw 'Unknown container prevents Personal uninstall.'
        }
    }
    $compose = Join-Path $root 'config\compose.personal.yaml'
    $composeEnvironment = Join-Path $root 'config\compose.env'
    if (
        (Test-Path -LiteralPath $compose -PathType Leaf) -and
        (Test-Path -LiteralPath $composeEnvironment -PathType Leaf)
    ) {
        & $docker.Source compose -p $journal.compose_project `
            --env-file $composeEnvironment -f $compose down --remove-orphans
        if ($LASTEXITCODE -ne 0) {
            throw 'Personal containers could not be stopped and removed safely.'
        }
    }
    elseif ($containers.Count -gt 0) {
        throw 'Personal Compose ledger is missing; containers were not removed.'
    }
}

if ($DataAction -ceq 'Delete' -and (Test-Path -LiteralPath $dataRoot -PathType Container)) {
    $dataItem = Get-Item -LiteralPath $dataRoot -Force
    if ($dataItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'The Personal data root became a reparse point; data was preserved.'
    }
    [IO.Directory]::Delete($dataRoot, $true)
}

# Remove only the exact installer-owned directories. Docker bind-mounted
# PostgreSQL and RustFS data, originals, and backup destinations are outside
# this set. Data is preserved unless the operator selected the separately
# confirmed V8F deletion mode.
foreach ($relative in @('cache','logs','secrets','config','state')) {
    $path = Join-Path $root $relative
    if (Test-Path -LiteralPath $path) {
        Assert-RagPersonalPathSafe -Path $path | Out-Null
        [IO.Directory]::Delete($path, $true)
    }
}
if ((Test-Path -LiteralPath $root) -and @(Get-ChildItem -LiteralPath $root -Force).Count -eq 0) {
    [IO.Directory]::Delete($root, $false)
}
[pscustomobject]@{
    result='pass'
    profile='personal'
    application_removed=$true
    data_root=$dataRoot
    data_action=$DataAction.ToLowerInvariant()
    verified_export=($DataAction -ceq 'Export')
    data_preserved=(Test-Path -LiteralPath $dataRoot -PathType Container)
    docker_volumes_removed=$false
    destructive_data_mode_available=$true
} | ConvertTo-Json -Compress
