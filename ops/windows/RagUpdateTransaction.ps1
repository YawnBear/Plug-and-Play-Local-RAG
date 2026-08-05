$ErrorActionPreference = 'Stop'

$script:RagUpdateJournalStates = @(
    'prepared', 'service_stopped', 'release_switched', 'files_switched',
    'candidate_started', 'verified', 'commit_cleanup', 'rollback_started',
    'rollback_cleanup', 'rolled_back', 'recovery_failed', 'committed'
)

function ConvertTo-RagUpdateAddress {
    param([Parameter(Mandatory)][string]$Address)
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$parsed)) {
        throw "Installed update address is invalid: $Address"
    }
    $parsed
}

function Get-RagFileSha256OrNull {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-RagUpdatePath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][ValidateSet('candidate','previous','transaction')]
        [string]$Kind,
        [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{32}$')][string]$UpdateId
    )
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $expectedName = switch ($Kind) {
        candidate { "candidate-$UpdateId" }
        previous { "previous-$UpdateId" }
        transaction { "update-$UpdateId" }
    }
    $expected = [IO.Path]::GetFullPath((Join-Path $rootFull $expectedName)).TrimEnd('\')
    if (-not [string]::Equals($full, $expected, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([IO.Path]::GetDirectoryName($full), $rootFull,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Update journal contains an unsafe $Kind path"
    }
    $full
}

function Assert-RagUpdateJournal {
    param(
        [Parameter(Mandatory)]$Journal,
        [Parameter(Mandatory)][string]$ProgramFilesRoot,
        [Parameter(Mandatory)][string]$UpdatesRoot
    )
    $expected = @(
        'schema_version','update_id','state','created_at','original_service_running',
        'original_start_mode','current_path','candidate_path','previous_path',
        'transaction_path','verified_stage_path','verified_root_created',
        'replacements','failure'
    ) | Sort-Object
    $actual = @($Journal.PSObject.Properties.Name | Sort-Object)
    if ($Journal.schema_version -ne 1 -or ($actual -join ',') -cne ($expected -join ',') -or
        [string]$Journal.update_id -cnotmatch '^[0-9a-f]{32}$' -or
        [string]$Journal.state -cnotin $script:RagUpdateJournalStates -or
        $Journal.original_service_running -isnot [bool] -or
        [string]$Journal.original_start_mode -cnotin @('Auto','Manual','Disabled')) {
        throw 'Update journal contract is invalid'
    }
    $current = [IO.Path]::GetFullPath((Join-Path $ProgramFilesRoot 'current')).TrimEnd('\')
    if (-not [string]::Equals([IO.Path]::GetFullPath([string]$Journal.current_path).TrimEnd('\'),
            $current, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Update journal current path is unsafe'
    }
    Assert-RagUpdatePath -Path $Journal.candidate_path -Root $ProgramFilesRoot `
        -Kind candidate -UpdateId $Journal.update_id | Out-Null
    Assert-RagUpdatePath -Path $Journal.previous_path -Root $ProgramFilesRoot `
        -Kind previous -UpdateId $Journal.update_id | Out-Null
    Assert-RagUpdatePath -Path $Journal.transaction_path -Root $UpdatesRoot `
        -Kind transaction -UpdateId $Journal.update_id | Out-Null
    if ($Journal.verified_root_created -isnot [bool]) {
        throw 'Update journal verified-root ownership contract is invalid'
    }
    $verifiedRoot = Join-Path ([IO.Path]::GetDirectoryName($UpdatesRoot)) 'verified-release'
    $verifiedStage = [IO.Path]::GetFullPath([string]$Journal.verified_stage_path).TrimEnd('\')
    if (-not [string]::Equals([IO.Path]::GetDirectoryName($verifiedStage),
            [IO.Path]::GetFullPath($verifiedRoot).TrimEnd('\'),
            [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($verifiedStage) -cnotmatch '^update-[0-9a-f]{32}$') {
        throw 'Update journal verified-stage path is unsafe'
    }
    $transaction = [IO.Path]::GetFullPath([string]$Journal.transaction_path).TrimEnd('\')
    $allowedTargets = @(
        'installed-deployment.json','installation-dependency-evidence.json',
        'installed-release-evidence.json','installed-release-state.json',
        'installation-network-evidence.json'
    )
    foreach ($replacement in @($Journal.replacements)) {
        $names = @($replacement.PSObject.Properties.Name | Sort-Object)
        if (($names -join ',') -cne 'backup_path,new_sha256,old_sha256,staged_path,target_relative' -or
            ([string]$replacement.target_relative -cnotin $allowedTargets -and
                [string]$replacement.target_relative -cnotmatch
                    '^signed-stage\\release-[0-9a-f]{64}\\(?:Caddyfile|csp-header\.caddy)$') -or
            ($null -ne $replacement.old_sha256 -and
                [string]$replacement.old_sha256 -cnotmatch '^[0-9a-f]{64}$') -or
            ($null -ne $replacement.new_sha256 -and
                [string]$replacement.new_sha256 -cnotmatch '^[0-9a-f]{64}$')) {
            throw 'Update journal replacement contract is invalid'
        }
        foreach ($property in @('backup_path','staged_path')) {
            $value = [IO.Path]::GetFullPath([string]$replacement.$property)
            if (-not $value.StartsWith($transaction + '\', [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Update journal replacement path escapes its transaction root'
            }
        }
    }
}

function Write-RagUpdateJsonAtomic {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)
    $temporary = "$Path.tmp"
    Assert-RagPathComponentsNotReparse -Path $Path
    Assert-RagPathComponentsNotReparse -Path $temporary
    [IO.File]::WriteAllText(
        $temporary, ($Value | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false)
    )
    if (Test-Path -LiteralPath $Path) {
        [IO.File]::Replace($temporary, $Path, $null, $true)
    } else {
        [IO.File]::Move($temporary, $Path)
    }
}

function Set-RagProtectedUpdateAcl {
    param([Parameter(Mandatory)][string]$Path, [switch]$Recursive)
    $arguments = if ($Recursive) { @('/T','/C') } else { @() }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    & icacls.exe $Path /setowner '*S-1-5-18' @arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Update path owner lockdown failed: $Path" }
    $grants = if ($item.PSIsContainer) {
        @('*S-1-5-18:(OI)(CI)(F)','*S-1-5-18:(F)',
            '*S-1-5-32-544:(OI)(CI)(F)','*S-1-5-32-544:(F)')
    } else {
        @('*S-1-5-18:(F)','*S-1-5-32-544:(F)')
    }
    & icacls.exe $Path /inheritance:r /grant:r $grants @arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Update path DACL lockdown failed: $Path" }
}

function Set-RagJournalState {
    param([Parameter(Mandatory)]$Journal, [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$State, [AllowNull()][string]$Failure)
    if ($State -cnotin $script:RagUpdateJournalStates) { throw 'Unknown update journal state' }
    $persisted = $Journal | ConvertTo-Json -Depth 20 | ConvertFrom-Json
    $persisted.state = $State
    $persisted.failure = $Failure
    Write-RagUpdateJsonAtomic -Path $Path -Value $persisted
    Set-RagProtectedUpdateAcl -Path $Path
    $Journal.state = $State
    $Journal.failure = $Failure
}

function Install-RagAtomicFile {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Target)
    $pending = "$Target.update-pending"
    Assert-RagPathComponentsNotReparse -Path $Source
    Assert-RagPathComponentsNotReparse -Path $Target
    Assert-RagPathComponentsNotReparse -Path $pending
    Copy-Item -LiteralPath $Source -Destination $pending
    if (Test-Path -LiteralPath $Target) {
        [IO.File]::Replace($pending, $Target, $null, $true)
    } else {
        [IO.File]::Move($pending, $Target)
    }
}

function Assert-RagReplacementSourceHash {
    param([Parameter(Mandatory)]$Replacement)
    if ($null -eq $Replacement.new_sha256 -or
        (Get-RagFileSha256OrNull $Replacement.staged_path) -cne $Replacement.new_sha256) {
        throw "Staged update hash mismatch: $($Replacement.target_relative)"
    }
}

function Assert-RagRollbackPreflight {
    param(
        [Parameter(Mandatory)]$Journal,
        [Parameter(Mandatory)][string]$ProgramDataRoot
    )
    foreach ($replacement in @($Journal.replacements)) {
        $target = Join-Path $ProgramDataRoot $replacement.target_relative
        foreach ($path in @($target,$replacement.backup_path,$replacement.staged_path)) {
            Assert-RagPathComponentsNotReparse -Path $path
        }
        if ($null -ne $replacement.old_sha256 -and
            (Get-RagFileSha256OrNull $replacement.backup_path) -cne $replacement.old_sha256) {
            throw "Update recovery backup is missing or corrupt: $($replacement.target_relative)"
        }
    }
}
