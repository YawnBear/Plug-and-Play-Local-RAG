[CmdletBinding(DefaultParameterSetName='Update', SupportsShouldProcess, ConfirmImpact='High')]
param(
    [Parameter(Mandatory, ParameterSetName='Update')][string]$RepositoryRoot,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$SignedReleaseManifest,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$SignedReleaseSignature,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$ReleaseArtifactRoot,
    [Parameter(Mandatory, ParameterSetName='Update')][ValidateCount(2,2)][string[]]$LocalAddress,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$OcrFixture,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$OcrTempRoot,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$PinnedDockerProgram,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$PostgresContainer,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$PostgresUser,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$PostgresDatabase,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$RustfsContainer,
    [Parameter(Mandatory, ParameterSetName='Update')][uri]$RustfsEndpoint,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$RustfsApiCredentials,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$RustfsIngestionCredentials,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$RustfsDeletionCredentials,
    [Parameter(Mandatory, ParameterSetName='Update')][string]$RustfsMaintenanceCredentials,
    [Parameter(ParameterSetName='Update')][string]$CurrentSignedReleaseManifest,
    [Parameter(ParameterSetName='Update')][string]$CurrentSignedReleaseSignature,
    [Parameter(ParameterSetName='Update')][string]$CurrentReleaseArtifactRoot,
    [Parameter(Mandatory, ParameterSetName='Recover')][switch]$Recover
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'RagUpdateTransaction.ps1')
. (Join-Path $PSScriptRoot 'RagManagedRootSafety.ps1')
. (Join-Path $PSScriptRoot 'RagProcessSafety.ps1')
. (Join-Path $PSScriptRoot 'RagHostBinding.ps1')
. (Join-Path $PSScriptRoot 'Expand-RagVerifiedRelease.ps1')

function Assert-RagAdminProtectedPath {
    param([Parameter(Mandatory)][string]$Path, [switch]$Leaf)
    Assert-RagPathComponentsNotReparse -Path $Path
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($Leaf -and $item.PSIsContainer) -or (-not $Leaf -and -not $item.PSIsContainer) -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Managed update prerequisite has the wrong type: $Path"
    }
    $acl = Get-Acl -LiteralPath $item.FullName
    if (-not $acl.AreAccessRulesProtected -or
        $acl.Owner -cnotin @('NT AUTHORITY\SYSTEM','BUILTIN\Administrators')) {
        throw "Managed update prerequisite is not ACL protected: $Path"
    }
    $trustedWriters = @('NT AUTHORITY\SYSTEM','BUILTIN\Administrators')
    $writeCapableRights = [long](
        0x2 -bor 0x4 -bor 0x10 -bor 0x40 -bor 0x100 -bor
        0x10000 -bor 0x40000 -bor 0x80000
    )
    foreach ($rule in @($acl.Access)) {
        if ($rule.IsInherited -or
            ($rule.AccessControlType -ceq 'Allow' -and
                $trustedWriters -cnotcontains $rule.IdentityReference.Value -and
                (([long]$rule.FileSystemRights -band $writeCapableRights) -ne 0))) {
            throw "Managed update prerequisite grants untrusted write access: $Path"
        }
    }
}

function Get-RagInstalledUpdateAddresses {
    param([Parameter(Mandatory)][string]$HostsLedgerPath)
    Assert-RagAdminProtectedPath -Path $HostsLedgerPath -Leaf
    $hosts = Get-Content -Raw -LiteralPath $HostsLedgerPath | ConvertFrom-Json
    $addresses = @($hosts.addresses | ForEach-Object { ConvertTo-RagUpdateAddress $_ })
    $families = @($addresses | ForEach-Object { $_.AddressFamily.ToString() } | Sort-Object)
    if ($hosts.schema_version -ne 1 -or $addresses.Count -ne 2 -or
        ($families -join ',') -cne 'InterNetwork,InterNetworkV6') {
        throw 'Installed hosts ledger does not contain exactly one IPv4 and one IPv6 address'
    }
    @($addresses | ForEach-Object { $_.ToString() } | Sort-Object)
}

function Set-RagInstalledUpdateArtifactAcl {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$TargetRelative,
        [Parameter(Mandatory)][string]$SupervisorSid,
        [Parameter(Mandatory)][string]$ProxySid
    )
    & icacls.exe $Path /setowner '*S-1-5-18' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Installed update artifact owner failed: $TargetRelative" }
    $grants = @('*S-1-5-18:(F)','*S-1-5-32-544:(F)')
    if ($TargetRelative -cin @(
        'installed-deployment.json','installation-dependency-evidence.json'
    )) {
        $grants += "*$SupervisorSid`:(R)"
    } elseif ($TargetRelative -cmatch
        '^signed-stage\\release-[0-9a-f]{64}\\(?:Caddyfile|csp-header\.caddy)$') {
        $grants += "*$ProxySid`:(R)"
    } elseif ($TargetRelative -cnotin @(
        'installed-release-evidence.json','installed-release-state.json',
        'installation-network-evidence.json'
    )) {
        throw "Installed update artifact ACL target is unknown: $TargetRelative"
    }
    & icacls.exe $Path /inheritance:r /grant:r $grants | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Installed update artifact DACL failed: $TargetRelative" }
}

function Stop-RagUpdateGraph {
    param([Parameter(Mandatory)][string[]]$AccountName)
    Stop-RagManagedProcesses -AccountName $AccountName -ServiceName 'RagSupervisor'
    if ((Get-Service -Name RagSupervisor -ErrorAction Stop).Status -ne 'Stopped') {
        throw 'RagSupervisor did not stop'
    }
}

function Start-RagUpdateGraph {
    param([Parameter(Mandatory)][string[]]$Addresses)
    Start-Service -Name RagSupervisor
    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    do {
        $service = Get-Service -Name RagSupervisor -ErrorAction Stop
        if ($service.Status -ne 'Running') { throw 'RagSupervisor stopped during update readiness' }
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue |
            Where-Object LocalAddress -in $Addresses)
        if ($listeners.Count -eq 2 -and
            @($listeners.OwningProcess | Sort-Object -Unique).Count -eq 1) { return }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Updated supervised graph did not become ready within five minutes'
}

function Restore-RagUpdate {
    param([Parameter(Mandatory)]$Journal, [Parameter(Mandatory)][string]$JournalPath,
        [Parameter(Mandatory)][string]$ProgramDataRoot,
        [Parameter(Mandatory)][string]$ProgramFilesRoot,
        [Parameter(Mandatory)][string[]]$AccountName,
        [Parameter(Mandatory)][string[]]$Addresses,
        [Parameter(Mandatory)][string]$SupervisorSid,
        [Parameter(Mandatory)][string]$ProxySid)
    Assert-RagUpdateJournal -Journal $Journal -ProgramFilesRoot $ProgramFilesRoot `
        -UpdatesRoot (Join-Path $ProgramDataRoot 'updates')
    $current = [string]$Journal.current_path
    $candidate = [string]$Journal.candidate_path
    $previous = [string]$Journal.previous_path
    foreach ($path in @($current,$candidate,$previous,[string]$Journal.transaction_path) +
        @([string]$Journal.verified_stage_path) +
        @($Journal.replacements | ForEach-Object { $_.backup_path; $_.staged_path })) {
        Assert-RagPathComponentsNotReparse -Path $path
    }
    Assert-RagRollbackPreflight -Journal $Journal -ProgramDataRoot $ProgramDataRoot
    $hasCurrent = Test-Path -LiteralPath $current -PathType Container
    $hasPrevious = Test-Path -LiteralPath $previous -PathType Container
    $hasCandidate = Test-Path -LiteralPath $candidate -PathType Container
    if ((-not $hasCurrent -and -not $hasPrevious) -or
        ($hasCurrent -and $hasPrevious -and $hasCandidate)) {
        throw 'Update recovery directory topology is not recoverable'
    }
    Set-RagJournalState -Journal $Journal -Path $JournalPath -State rollback_started -Failure $Journal.failure
    Stop-RagUpdateGraph -AccountName $AccountName
    if (Test-Path -LiteralPath $previous -PathType Container) {
        if (Test-Path -LiteralPath $current) {
            if (Test-Path -LiteralPath $candidate) { throw 'Candidate recovery destination is occupied' }
            [IO.Directory]::Move($current, $candidate)
        }
        [IO.Directory]::Move($previous, $current)
    }
    foreach ($replacement in @($Journal.replacements)) {
        $target = Join-Path $ProgramDataRoot $replacement.target_relative
        if ($null -eq $replacement.old_sha256) {
            if (Test-Path -LiteralPath $target) { [IO.File]::Delete($target) }
            continue
        }
        if ((Get-RagFileSha256OrNull $replacement.backup_path) -cne $replacement.old_sha256) {
            throw "Update recovery backup hash mismatch: $($replacement.target_relative)"
        }
        Install-RagAtomicFile -Source $replacement.backup_path -Target $target
        if ((Get-RagFileSha256OrNull $target) -cne $replacement.old_sha256) {
            throw "Update recovery target hash mismatch: $($replacement.target_relative)"
        }
        Set-RagInstalledUpdateArtifactAcl -Path $target `
            -TargetRelative $replacement.target_relative `
            -SupervisorSid $SupervisorSid -ProxySid $ProxySid
    }
    if ($Journal.original_service_running) { Start-RagUpdateGraph -Addresses $Addresses }
    else { Stop-RagUpdateGraph -AccountName $AccountName }
    Set-RagJournalState -Journal $Journal -Path $JournalPath -State rollback_cleanup -Failure $Journal.failure
    Invoke-RagUpdateCleanup -Journal $Journal -Mode Rollback
    Set-RagJournalState -Journal $Journal -Path $JournalPath -State rolled_back -Failure $Journal.failure
}

function Invoke-RagUpdateCleanup {
    param([Parameter(Mandatory)]$Journal,[Parameter(Mandatory)][ValidateSet('Commit','Rollback')][string]$Mode)
    Assert-RagUpdateJournal -Journal $Journal `
        -ProgramFilesRoot ([IO.Path]::GetDirectoryName([string]$Journal.current_path)) `
        -UpdatesRoot ([IO.Path]::GetDirectoryName([string]$Journal.transaction_path))
    $ownedPaths = if ($Mode -ceq 'Commit') {
        @([string]$Journal.previous_path,[string]$Journal.candidate_path,[string]$Journal.transaction_path)
    } else {
        @([string]$Journal.candidate_path,[string]$Journal.previous_path,[string]$Journal.transaction_path)
    }
    foreach ($owned in $ownedPaths) {
        Assert-RagPathComponentsNotReparse -Path $owned
        if (Test-Path -LiteralPath $owned) {
            Remove-Item -LiteralPath $owned -Recurse -Force
        }
    }
    Assert-RagPathComponentsNotReparse -Path $Journal.verified_stage_path
    if (Test-Path -LiteralPath $Journal.verified_stage_path) {
        & (Join-Path $PSScriptRoot 'Test-RagUpdate.ps1') -ExistingSignedStage $Journal.verified_stage_path `
            -CleanupOnSuccess -CleanupOnFailure | Out-Null
    }
    $verifiedRoot = Split-Path -Parent $Journal.verified_stage_path
    Assert-RagPathComponentsNotReparse -Path $verifiedRoot
    if ($Journal.verified_root_created -and (Test-Path -LiteralPath $verifiedRoot) -and
        @(Get-ChildItem -LiteralPath $verifiedRoot -Force).Count -eq 0) {
        Remove-Item -LiteralPath $verifiedRoot -Force
    }
}

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Update-RagWindows.ps1 must run elevated'
}
$programData = Join-Path $env:ProgramData 'LocalRAG'
$programFilesRoot = Join-Path $env:ProgramFiles 'LocalRAG'
$currentReleaseRoot = Join-Path $programFilesRoot 'current'
$serviceRoot = Join-Path $programFilesRoot 'service'
$updatesRoot = Join-Path $programData 'updates'
Assert-RagPathComponentsNotReparse -Path $updatesRoot
$journalPath = Join-Path $updatesRoot 'active-update.json'
Assert-RagPathComponentsNotReparse -Path $journalPath
$serviceNames = [ordered]@{
    caddy='RagProxySvc'; web='RagWebSvc'; api='RagApiSvc';
    ingestion='RagIngestionSvc'; deletion='RagDeletionSvc';
    inference='RagInferenceSvc'; ocr='RagOcrSvc'
}
$mutexSecurity = [Security.AccessControl.MutexSecurity]::new()
$mutexSecurity.SetAccessRuleProtection($true,$false)
foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
    $mutexSecurity.AddAccessRule([Security.AccessControl.MutexAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new($sid),
        [Security.AccessControl.MutexRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow
    ))
}
$mutexCreated = $false
$mutex = [Threading.Mutex]::new(
    $false, 'Global\LocalRAG.Update.v1', [ref]$mutexCreated, $mutexSecurity
)
$mutexAcl = $mutex.GetAccessControl()
$mutexRules = @($mutexAcl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
if (-not $mutexAcl.AreAccessRulesProtected -or $mutexRules.Count -ne 2 -or
    @($mutexRules | Where-Object {
        $_.IsInherited -or $_.AccessControlType -cne 'Allow' -or
        $_.IdentityReference.Value -cnotin @('S-1-5-18','S-1-5-32-544') -or
        $_.MutexRights -cne [Security.AccessControl.MutexRights]::FullControl
    }).Count -ne 0) {
    $mutex.Dispose()
    throw 'Global Local RAG update mutex ACL is not exact and protected'
}
$ownsMutex = $false
$journalWritten = $false
$verifiedStage = $null
$candidate = $null
$transaction = $null
$verifiedReleaseRootCreated = $false
try {
    try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw 'Another Local RAG update or recovery is active' }
    Assert-RagAdminProtectedPath -Path $programData
    Assert-RagPathComponentsNotReparse -Path $programFilesRoot
    foreach ($root in @($currentReleaseRoot,$serviceRoot)) {
        Assert-RagAdminProtectedPath -Path $root
    }
    $ledgerPath = Join-Path $programData 'installed-accounts.json'
    Assert-RagAdminProtectedPath -Path $ledgerPath -Leaf
    $ledger = Get-Content -Raw -LiteralPath $ledgerPath | ConvertFrom-Json
    $expectedNames = @($serviceNames.Values | Sort-Object)
    if ($ledger.schema_version -ne 2 -or $ledger.phase -cne 'installed' -or
        $ledger.accounts.Count -ne 7 -or
        (@($ledger.accounts.name | Sort-Object) -join ',') -cne ($expectedNames -join ',')) {
        throw 'Installed account ledger is not the exact installed seven-identity contract'
    }
    foreach ($account in $ledger.accounts) {
        $local = Get-LocalUser -Name $account.name -ErrorAction Stop
        if ($account.sid -cnotmatch '^S-1-5-21-(?:[0-9]+-){3}[0-9]+$' -or
            $local.SID.Value -cne $account.sid) { throw "Installed identity mismatch: $($account.name)" }
    }
    $installedAddresses = Get-RagInstalledUpdateAddresses -HostsLedgerPath (
        Join-Path $programData 'installed-hosts.json'
    )
    $service = Get-CimInstance Win32_Service -Filter "Name='RagSupervisor'" -ErrorAction Stop
    $serviceExe = Join-Path $programFilesRoot 'service\RagSupervisorService.exe'
    if ($null -eq $service -or $service.StartName -cne 'LocalSystem' -or
        [IO.Path]::GetFullPath(([string]$service.PathName).Trim('"')) -cne $serviceExe) {
        throw 'Existing RagSupervisor service contract is not exact'
    }
    if ($service.StartMode -ceq 'Disabled') {
        throw 'RagSupervisor Disabled start mode cannot satisfy mandatory candidate startup verification'
    }
    $serviceSids = @{}
    foreach ($entry in $serviceNames.GetEnumerator()) {
        $serviceSids[$entry.Key] = (New-Object Security.Principal.NTAccount(
            $env:COMPUTERNAME,$entry.Value
        )).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    $supervisorSid = (New-Object Security.Principal.NTAccount(
        'NT SERVICE','RagSupervisor'
    )).Translate([Security.Principal.SecurityIdentifier]).Value
    if (Test-Path -LiteralPath $journalPath -PathType Leaf) {
        $existingJournal = Get-Content -Raw -LiteralPath $journalPath | ConvertFrom-Json
        Assert-RagUpdateJournal -Journal $existingJournal -ProgramFilesRoot $programFilesRoot `
            -UpdatesRoot $updatesRoot
        if ($existingJournal.state -cnotin @('committed','rolled_back')) {
            if (-not $Recover) { throw 'A nonterminal update journal requires explicit -Recover' }
            if (-not $PSCmdlet.ShouldProcess('Local RAG managed Windows release', 'Recover prior release')) { return }
            try {
                if ($existingJournal.state -ceq 'commit_cleanup') {
                    Invoke-RagUpdateCleanup -Journal $existingJournal -Mode Commit
                    Set-RagJournalState $existingJournal $journalPath committed $null
                } elseif ($existingJournal.state -ceq 'rollback_cleanup') {
                    Invoke-RagUpdateCleanup -Journal $existingJournal -Mode Rollback
                    Set-RagJournalState $existingJournal $journalPath rolled_back $existingJournal.failure
                } else {
                    Restore-RagUpdate -Journal $existingJournal -JournalPath $journalPath `
                        -ProgramDataRoot $programData -ProgramFilesRoot $programFilesRoot `
                        -AccountName $expectedNames -Addresses $installedAddresses `
                        -SupervisorSid $supervisorSid -ProxySid $serviceSids.caddy
                }
                [pscustomobject]@{result='recovered';update_id=$existingJournal.update_id;
                    service_running=[bool]$existingJournal.original_service_running;
                    data_preserved=$true;secrets_preserved=$true} | ConvertTo-Json
                return
            } catch {
                if ($existingJournal.state -cin @('commit_cleanup','rollback_cleanup')) {
                    Set-RagJournalState -Journal $existingJournal -Path $journalPath `
                        -State $existingJournal.state -Failure $_.Exception.Message
                    throw
                }
                Set-RagJournalState -Journal $existingJournal -Path $journalPath `
                    -State recovery_failed -Failure $_.Exception.Message
                throw
            }
        }
    }
    if ($Recover) { throw 'There is no nonterminal update journal to recover' }
    if (-not $PSCmdlet.ShouldProcess('Local RAG managed Windows release', 'Install signed same-schema update')) { return }

    $repository = (Resolve-Path -LiteralPath $RepositoryRoot).Path
    $requestedAddresses = @($LocalAddress | ForEach-Object {
        (ConvertTo-RagUpdateAddress $_).ToString()
    } | Sort-Object)
    if (($requestedAddresses -join ',') -cne ($installedAddresses -join ',')) {
        throw 'LocalAddress must exactly match the protected installed-hosts.json addresses'
    }
    $current = $currentReleaseRoot
    $installedDeployment = Join-Path $programData 'installed-deployment.json'
    $installedDependency = Join-Path $programData 'installation-dependency-evidence.json'
    $installedReleaseEvidence = Join-Path $programData 'installed-release-evidence.json'
    $installedReleaseState = Join-Path $programData 'installed-release-state.json'
    $installedCaddy = Join-Path $programData 'installed-caddy.json'
    foreach ($path in @($current,$installedDeployment,$installedDependency,$installedCaddy,
        (Join-Path $programData 'signed-stage'))) { Assert-RagAdminProtectedPath -Path $path -Leaf:($path -ne $current -and $path -notlike '*signed-stage') }
    $dependency = Get-Content -Raw -LiteralPath $installedDependency | ConvertFrom-Json
    $verifiedReleaseRoot = Join-Path $programData 'verified-release'
    Assert-RagPathComponentsNotReparse -Path $verifiedReleaseRoot
    if (-not (Test-Path -LiteralPath $verifiedReleaseRoot)) {
        New-Item -ItemType Directory -Path $verifiedReleaseRoot | Out-Null
        Set-RagProtectedUpdateAcl -Path $verifiedReleaseRoot
        $verifiedReleaseRootCreated = $true
    } else {
        Assert-RagAdminProtectedPath -Path $verifiedReleaseRoot
    }
    if (@(Get-ChildItem -LiteralPath $verifiedReleaseRoot -Force).Count -ne 0) {
        throw 'Verified release staging parent must be empty before update verification'
    }
    $currentEvidencePath = $installedReleaseEvidence
    $currentEvidence = $null
    if (Test-Path -LiteralPath $installedReleaseState -PathType Leaf) {
        Assert-RagAdminProtectedPath -Path $installedReleaseState -Leaf
        Assert-RagAdminProtectedPath -Path $currentEvidencePath -Leaf
        $currentState = Get-Content -Raw -LiteralPath $installedReleaseState | ConvertFrom-Json
        $stateFields = @($currentState.PSObject.Properties.Name | Sort-Object)
        if (($stateFields -join ',') -cne
                'final_manifest_sha256,release_evidence_sha256,release_tree_sha256,schema_version,version' -or
            $currentState.schema_version -ne 1 -or
            $currentState.version -isnot [string] -or
            $currentState.final_manifest_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $currentState.release_tree_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $currentState.release_evidence_sha256 -cne (Get-RagFileSha256OrNull $currentEvidencePath) -or
            $currentState.release_tree_sha256 -cne (Get-RagTreeSha256 $current)) {
            throw 'Protected installed release state does not bind the complete current release tree'
        }
        if ((Get-RagFileSha256OrNull $currentEvidencePath) -cne $dependency.release_evidence_sha256) {
            throw 'Current release evidence is not bound by installed dependency evidence'
        }
        $currentEvidence = Get-Content -Raw -LiteralPath $currentEvidencePath | ConvertFrom-Json
    } else {
        if (@($CurrentSignedReleaseManifest,$CurrentSignedReleaseSignature,$CurrentReleaseArtifactRoot |
            Where-Object { [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) {
            throw 'Legacy bootstrap requires CurrentSignedReleaseManifest, CurrentSignedReleaseSignature, and CurrentReleaseArtifactRoot'
        }
        $legacyText = & (Join-Path $repository 'ops\windows\Test-RagUpdate.ps1') `
            -Manifest $CurrentSignedReleaseManifest -Signature $CurrentSignedReleaseSignature `
            -ArtifactRoot $CurrentReleaseArtifactRoot -SignedArtifactStageRoot $verifiedReleaseRoot `
            -CleanupOnFailure | Out-String
        $legacy = $legacyText | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or $legacy.result -cne 'pass') { throw 'Historical signed release verification failed' }
        $legacyStage = (Resolve-Path -LiteralPath $legacy.stage_directory).Path
        Assert-RagPathComponentsNotReparse -Path $legacyStage
        $currentEvidencePath = Join-Path $legacyStage 'release-evidence.json'
        if ((Get-RagFileSha256OrNull $currentEvidencePath) -cne $dependency.release_evidence_sha256 -or
            (Get-RagZipTreeSha256 (Join-Path $legacyStage 'local-rag-release.zip')) -cne
                (Get-RagTreeSha256 $current)) {
            throw 'Historical signed release does not bind current dependency evidence and complete tree'
        }
        $currentEvidence = Get-Content -Raw -LiteralPath $currentEvidencePath | ConvertFrom-Json
        & (Join-Path $repository 'ops\windows\Test-RagUpdate.ps1') `
            -ExistingSignedStage $legacyStage -CleanupOnSuccess | Out-Null
    }
    Test-RagInstalledReleaseBinding -ReleaseRoot $current -ReleaseEvidence $currentEvidence

    if (-not (Test-Path -LiteralPath $updatesRoot)) { New-Item -ItemType Directory -Path $updatesRoot | Out-Null }
    Set-RagProtectedUpdateAcl -Path $updatesRoot
    $verification = & (Join-Path $repository 'ops\windows\Test-RagUpdate.ps1') `
        -Manifest $SignedReleaseManifest -Signature $SignedReleaseSignature `
        -ArtifactRoot $ReleaseArtifactRoot -SignedArtifactStageRoot $verifiedReleaseRoot `
        -CleanupOnFailure | Out-String
    if ($LASTEXITCODE -ne 0 -or ($verification | ConvertFrom-Json).result -cne 'pass') {
        throw 'Final exact signed release verification failed'
    }
    $verifiedStage = (Resolve-Path -LiteralPath (($verification | ConvertFrom-Json).stage_directory)).Path
    Assert-RagPathComponentsNotReparse -Path $verifiedStage
    $candidateEvidence = Get-Content -Raw -LiteralPath (Join-Path $verifiedStage 'release-evidence.json') | ConvertFrom-Json
    $signedDependencyEvidence = Get-Content -Raw -LiteralPath (
        Join-Path $verifiedStage 'dependency-evidence.json'
    ) | ConvertFrom-Json
    if ($signedDependencyEvidence.result -cne 'pass' -or
        @($signedDependencyEvidence.checks | Where-Object result -cne 'pass').Count -ne 0 -or
        $signedDependencyEvidence.release_evidence_sha256 -cne
            (Get-RagFileSha256OrNull (Join-Path $verifiedStage 'release-evidence.json'))) {
        throw 'Final dependency evidence is not bound to the candidate release evidence'
    }
    Assert-RagFreshHostEvidence -Evidence $signedDependencyEvidence `
        -MaxAgeSeconds $candidateEvidence.max_evidence_age_seconds
    if ([string]$candidateEvidence.alembic_revision -cne [string]$currentEvidence.alembic_revision) {
        throw 'Schema-changing updates are blocked: authenticated restore evidence is unavailable'
    }
    $candidateCaddyHash = Get-RagFileSha256OrNull (Join-Path $verifiedStage 'caddy.exe')
    $caddyLedger = Get-Content -Raw -LiteralPath $installedCaddy | ConvertFrom-Json
    $caddyDirectory = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath(
        [string]$caddyLedger.executable_path
    ))
    $signedStageRoot = [IO.Path]::GetFullPath((Join-Path $programData 'signed-stage')).TrimEnd('\')
    if (-not $caddyDirectory.StartsWith($signedStageRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([IO.Path]::GetDirectoryName($caddyDirectory), $signedStageRoot,
            [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($caddyDirectory) -cnotmatch '^release-[0-9a-f]{64}$') {
        throw 'Installed Caddy ledger path is outside the exact signed-stage release directory'
    }
    Assert-RagPathComponentsNotReparse -Path $caddyDirectory
    Assert-RagPathComponentsNotReparse -Path $caddyLedger.executable_path
    if ((Get-RagFileSha256OrNull $caddyLedger.executable_path) -cne $caddyLedger.executable_sha256 -or
        $candidateCaddyHash -cne $caddyLedger.executable_sha256 -or
        (Get-RagFileSha256OrNull (Join-Path $verifiedStage 'RagSupervisorService.exe')) -cne
            (Get-RagFileSha256OrNull $serviceExe)) {
        throw 'Updater v1 rejects Caddy or RagSupervisor service-host executable changes'
    }
    $candidateDeployment = Get-Content -Raw -LiteralPath (Join-Path $verifiedStage 'deployment.json') | ConvertFrom-Json
    $candidateDeployment.deployment_readiness.state = 'installed'
    $installedDeploymentDocument = Get-Content -Raw -LiteralPath $installedDeployment | ConvertFrom-Json
    if (($candidateDeployment | ConvertTo-Json -Depth 20 -Compress) -cne
        ($installedDeploymentDocument | ConvertTo-Json -Depth 20 -Compress)) {
        throw 'Candidate deployment does not retain the installed deployment/service/environment/provisioning contract'
    }

    $updateId = [Guid]::NewGuid().ToString('N')
    $candidate = Join-Path $programFilesRoot "candidate-$updateId"
    $previous = Join-Path $programFilesRoot "previous-$updateId"
    $transaction = Join-Path $updatesRoot "update-$updateId"
    foreach ($path in @($candidate,$previous,$transaction,(Join-Path $transaction 'backup'),
        (Join-Path $transaction 'staged'))) {
        Assert-RagPathComponentsNotReparse -Path $path
    }
    New-Item -ItemType Directory -Path $transaction,(Join-Path $transaction 'backup'),(Join-Path $transaction 'staged') | Out-Null
    Expand-RagVerifiedRelease -Archive (Join-Path $verifiedStage 'local-rag-release.zip') -Destination $candidate
    $candidateTreeHash = Get-RagZipTreeSha256 (Join-Path $verifiedStage 'local-rag-release.zip')
    if ((Get-RagTreeSha256 $candidate) -cne $candidateTreeHash) {
        throw 'Candidate extracted complete tree does not match the signed release ZIP tree'
    }
    Test-RagInstalledReleaseBinding -ReleaseRoot $candidate -ReleaseEvidence $candidateEvidence
    & (Join-Path $repository 'ops\windows\Set-RagReleaseAcl.ps1') -ReleaseRoot $candidate -ServiceSid $serviceSids
    & icacls.exe $candidate /grant:r `
        '*S-1-5-18:(OI)(CI)(RX)' '*S-1-5-18:(RX)' `
        '*S-1-5-32-544:(OI)(CI)(RX)' '*S-1-5-32-544:(RX)' /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Candidate immutable RX ACL application failed' }
    $freshOcrOutput = Join-Path (Resolve-Path -LiteralPath $OcrTempRoot).Path "update-$updateId"
    Assert-RagPathComponentsNotReparse -Path $freshOcrOutput
    $freshDependencyText = & (Join-Path $repository 'ops\windows\Test-RagDependencies.ps1') `
        -ExistingSignedStage $verifiedStage -ExistingReleaseRoot $candidate `
        -OcrFixture $OcrFixture -OcrTempRoot $OcrTempRoot -OcrOutput $freshOcrOutput `
        -PinnedDockerProgram $PinnedDockerProgram -PostgresContainer $PostgresContainer `
        -PostgresUser $PostgresUser -PostgresDatabase $PostgresDatabase `
        -RustfsContainer $RustfsContainer -RustfsEndpoint $RustfsEndpoint `
        -RustfsApiCredentials $RustfsApiCredentials -RustfsIngestionCredentials $RustfsIngestionCredentials `
        -RustfsDeletionCredentials $RustfsDeletionCredentials -RustfsMaintenanceCredentials $RustfsMaintenanceCredentials | Out-String
    $freshDependency = $freshDependencyText | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $freshDependency.result -cne 'pass') { throw 'Immediate candidate dependency verification failed' }
    Assert-RagFreshHostEvidence -Evidence $freshDependency -MaxAgeSeconds $candidateEvidence.max_evidence_age_seconds

    $caddyRelative = $caddyDirectory.Substring(
        [IO.Path]::GetFullPath($programData).TrimEnd('\').Length
    ).TrimStart('\')
    $stagedDeployment = Join-Path $transaction 'staged\installed-deployment.json'
    Assert-RagPathComponentsNotReparse -Path $stagedDeployment
    [IO.File]::WriteAllText($stagedDeployment,
        ($candidateDeployment | ConvertTo-Json -Depth 20),[Text.UTF8Encoding]::new($false))
    $stagedReleaseState = Join-Path $transaction 'staged\installed-release-state.json'
    Assert-RagPathComponentsNotReparse -Path $stagedReleaseState
    [IO.File]::WriteAllText($stagedReleaseState,([ordered]@{
        schema_version=1; version=(($verification | ConvertFrom-Json).version)
        final_manifest_sha256=(Get-RagFileSha256OrNull (Join-Path $verifiedStage 'update-manifest.json'))
        release_evidence_sha256=(Get-RagFileSha256OrNull (Join-Path $verifiedStage 'release-evidence.json'))
        release_tree_sha256=$candidateTreeHash
    } | ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    $replacementSources = [ordered]@{
        'installed-deployment.json'=$stagedDeployment
        'installation-dependency-evidence.json'=(Join-Path $transaction 'staged\installation-dependency-evidence.json')
        'installed-release-evidence.json'=(Join-Path $verifiedStage 'release-evidence.json')
        'installed-release-state.json'=$stagedReleaseState
        (Join-Path $caddyRelative 'Caddyfile')=(Join-Path $verifiedStage 'Caddyfile')
        (Join-Path $caddyRelative 'csp-header.caddy')=(Join-Path $verifiedStage 'csp-header.caddy')
        'installation-network-evidence.json'=(Join-Path $transaction 'staged\installation-network-evidence.json')
    }
    [IO.File]::WriteAllText($replacementSources['installation-dependency-evidence.json'],
        $freshDependencyText,[Text.UTF8Encoding]::new($false))
    $replacements = @()
    $index = 0
    foreach ($entry in $replacementSources.GetEnumerator()) {
        $target = Join-Path $programData $entry.Key
        $backup = Join-Path $transaction "backup\$index.bin"
        $staged = Join-Path $transaction "staged\$index.bin"
        foreach ($path in @($target,$entry.Value,$backup,$staged)) {
            Assert-RagPathComponentsNotReparse -Path $path
        }
        $oldHash = Get-RagFileSha256OrNull $target
        if ($null -ne $oldHash) { Copy-Item -LiteralPath $target -Destination $backup }
        if (Test-Path -LiteralPath $entry.Value -PathType Leaf) { Copy-Item -LiteralPath $entry.Value -Destination $staged }
        $replacements += [pscustomobject]@{target_relative=$entry.Key;backup_path=$backup;
            staged_path=$staged;old_sha256=$oldHash;new_sha256=(Get-RagFileSha256OrNull $staged)}
        $index++
    }
    $originalRunning = $service.State -ceq 'Running'
    $journal = [pscustomobject]@{schema_version=1;update_id=$updateId;state='prepared';
        created_at=[DateTimeOffset]::UtcNow.ToString('o');original_service_running=$originalRunning;
        original_start_mode=[string]$service.StartMode;current_path=$current;candidate_path=$candidate;
        previous_path=$previous;transaction_path=$transaction;verified_stage_path=$verifiedStage;
        verified_root_created=$verifiedReleaseRootCreated;replacements=$replacements;failure=$null}
    Write-RagUpdateJsonAtomic -Path $journalPath -Value $journal
    Set-RagProtectedUpdateAcl -Path $transaction -Recursive
    Set-RagProtectedUpdateAcl -Path $journalPath
    $journalWritten = $true
    $committed = $false
    try {
        foreach ($path in @($journalPath,$updatesRoot,$current,$candidate,$previous,$transaction,$verifiedStage) +
            @($replacements | ForEach-Object { $_.backup_path; $_.staged_path; (Join-Path $programData $_.target_relative) })) {
            Assert-RagPathComponentsNotReparse -Path $path
        }
        Assert-RagRollbackPreflight -Journal $journal -ProgramDataRoot $programData
        foreach ($replacement in @($replacements | Where-Object {
            $_.target_relative -cne 'installation-network-evidence.json'
        })) { Assert-RagReplacementSourceHash -Replacement $replacement }
        Stop-RagUpdateGraph -AccountName $expectedNames
        Set-RagJournalState $journal $journalPath service_stopped $null
        [IO.Directory]::Move($current,$previous); [IO.Directory]::Move($candidate,$current)
        Set-RagJournalState $journal $journalPath release_switched $null
        foreach ($replacement in $replacements | Where-Object { $null -ne $_.new_sha256 }) {
            Install-RagAtomicFile -Source $replacement.staged_path -Target (Join-Path $programData $replacement.target_relative)
            if ((Get-RagFileSha256OrNull (Join-Path $programData $replacement.target_relative)) -cne
                $replacement.new_sha256) { throw "Installed update target hash mismatch: $($replacement.target_relative)" }
            Set-RagInstalledUpdateArtifactAcl `
                -Path (Join-Path $programData $replacement.target_relative) `
                -TargetRelative $replacement.target_relative `
                -SupervisorSid $supervisorSid -ProxySid $serviceSids.caddy
        }
        Set-RagJournalState $journal $journalPath files_switched $null
        Start-RagUpdateGraph -Addresses $installedAddresses
        Set-RagJournalState $journal $journalPath candidate_started $null
        $networkText = & (Join-Path $repository 'ops\windows\Test-RagNetwork.ps1') `
            -PinnedCaddyProgram $caddyLedger.executable_path -PinnedCaddySha256 $candidateCaddyHash `
            -PinnedServiceHostProgram $serviceExe -PinnedServiceHostSha256 (Get-RagFileSha256OrNull $serviceExe) `
            -PinnedSupervisorPython (Join-Path $current 'runtimes\api-python\python.exe') `
            -DeploymentId $candidateDeployment.deployment_id -ExpectedLocalAddresses $installedAddresses | Out-String
        if ($LASTEXITCODE -ne 0 -or ($networkText | ConvertFrom-Json).result -cne 'pass') { throw 'Post-update network verification failed' }
        $networkReplacement = @($replacements | Where-Object target_relative -ceq 'installation-network-evidence.json')[0]
        [IO.File]::WriteAllText($networkReplacement.staged_path,$networkText,[Text.UTF8Encoding]::new($false))
        $networkReplacement.new_sha256 = Get-RagFileSha256OrNull $networkReplacement.staged_path
        Set-RagJournalState $journal $journalPath candidate_started $null
        Assert-RagReplacementSourceHash -Replacement $networkReplacement
        Install-RagAtomicFile $networkReplacement.staged_path (Join-Path $programData $networkReplacement.target_relative)
        if ((Get-RagFileSha256OrNull (Join-Path $programData $networkReplacement.target_relative)) -cne
            $networkReplacement.new_sha256) { throw 'Installed network evidence hash mismatch' }
        Set-RagInstalledUpdateArtifactAcl `
            -Path (Join-Path $programData $networkReplacement.target_relative) `
            -TargetRelative $networkReplacement.target_relative `
            -SupervisorSid $supervisorSid -ProxySid $serviceSids.caddy
        Set-RagJournalState $journal $journalPath verified $null
        if (-not $originalRunning) { Stop-RagUpdateGraph -AccountName $expectedNames }
        Set-RagJournalState $journal $journalPath commit_cleanup $null
        Invoke-RagUpdateCleanup -Journal $journal -Mode Commit
        Set-RagJournalState $journal $journalPath committed $null
        $committed = $true
        [pscustomobject]@{result='updated';update_id=$updateId;version=(($verification | ConvertFrom-Json).version);
            alembic_revision=$candidateEvidence.alembic_revision;service_start_mode=$service.StartMode;
            service_running=$originalRunning;data_preserved=$true;secrets_preserved=$true;
            automatic_scheduling='not_configured'} | ConvertTo-Json
    } catch {
        $failure = $_.Exception.Message
        if ($committed) {
            throw "Update verified and committed, but strict updater-owned cleanup failed: $failure"
        }
        if ($journal.state -ceq 'commit_cleanup') {
            Set-RagJournalState $journal $journalPath commit_cleanup $failure
            throw "Update is verified with commit cleanup incomplete; use -Recover: $failure"
        }
        $journal.failure = $failure
        try { Restore-RagUpdate -Journal $journal -JournalPath $journalPath -ProgramDataRoot $programData `
                -ProgramFilesRoot $programFilesRoot -AccountName $expectedNames `
                -Addresses $installedAddresses -SupervisorSid $supervisorSid `
                -ProxySid $serviceSids.caddy }
        catch {
            if ($journal.state -ceq 'rollback_cleanup') {
                Set-RagJournalState $journal $journalPath rollback_cleanup `
                    "$failure; rollback cleanup: $($_.Exception.Message)"
                throw "Update failed and rollback cleanup is incomplete; use -Recover: $failure"
            }
            Set-RagJournalState $journal $journalPath recovery_failed "$failure; recovery: $($_.Exception.Message)"
            throw "Update failed and recovery is incomplete; use -Recover: $failure"
        }
        throw "Update failed and the prior release was restored: $failure"
    }
} finally {
    if (-not $journalWritten) {
        foreach ($owned in @($candidate,$transaction)) {
            if (-not [string]::IsNullOrWhiteSpace($owned)) {
                Assert-RagPathComponentsNotReparse -Path $owned
            }
            if (-not [string]::IsNullOrWhiteSpace($owned) -and
                (Test-Path -LiteralPath $owned)) {
                & (Join-Path ([Environment]::SystemDirectory) 'takeown.exe') /F $owned /A /R /D Y | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "Updater-owned cleanup ownership failed: $owned" }
                & icacls.exe $owned /grant:r '*S-1-5-32-544:(OI)(CI)(F)' '*S-1-5-32-544:(F)' /T /C | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "Updater-owned cleanup ACL failed: $owned" }
                Remove-Item -LiteralPath $owned -Recurse -Force
            }
        }
        if (-not [string]::IsNullOrWhiteSpace($verifiedStage)) {
            Assert-RagPathComponentsNotReparse -Path $verifiedStage
        }
        if (-not [string]::IsNullOrWhiteSpace($verifiedStage) -and
            (Test-Path -LiteralPath $verifiedStage) -and
            -not [string]::IsNullOrWhiteSpace($repository)) {
            & (Join-Path $repository 'ops\windows\Test-RagUpdate.ps1') `
                -ExistingSignedStage $verifiedStage -CleanupOnSuccess | Out-Null
        }
        if ($verifiedReleaseRootCreated) {
            Assert-RagPathComponentsNotReparse -Path $verifiedReleaseRoot
        }
        if ($verifiedReleaseRootCreated -and
            (Test-Path -LiteralPath $verifiedReleaseRoot) -and
            @(Get-ChildItem -LiteralPath $verifiedReleaseRoot -Force).Count -eq 0) {
            Remove-Item -LiteralPath $verifiedReleaseRoot -Force
        }
    }
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
