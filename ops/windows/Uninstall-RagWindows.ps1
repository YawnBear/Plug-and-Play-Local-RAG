[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param()
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Uninstall-RagWindows.ps1 must run elevated'
}
if (-not $PSCmdlet.ShouldProcess('Local RAG managed Windows runtime', 'Uninstall while preserving data')) { return }
. (Join-Path $PSScriptRoot 'RagProcessSafety.ps1')
. (Join-Path $PSScriptRoot 'RagManagedRootSafety.ps1')
# Validate protected ledgers and exact hosts bytes before any irreversible
# service, firewall, account, or payload removal.
# Keep both OCR outbound blocks enabled until supervisor/descendant/account
# process stop proof succeeds.
$service = Get-CimInstance Win32_Service -Filter "Name='RagSupervisor'" -ErrorAction Stop
if ($null -eq $service) {
    throw 'RagSupervisor SCM service is missing; descendant shutdown cannot be confirmed'
}
$trackedProcessIds = if ([int]$service.ProcessId -gt 0) {
    @(Get-RagProcessTreeIds -RootProcessId ([int]$service.ProcessId))
} else {
    @()
}
& sc.exe config RagSupervisor start= disabled | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not disable RagSupervisor startup' }
Stop-Service -Name RagSupervisor -Force -ErrorAction Stop
$serviceDeadline = [DateTime]::UtcNow.AddSeconds(45)
do {
    $serviceState = (Get-Service -Name RagSupervisor -ErrorAction Stop).Status
    if ($serviceState -eq [ServiceProcess.ServiceControllerStatus]::Stopped) { break }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $serviceDeadline)
if ($serviceState -ne [ServiceProcess.ServiceControllerStatus]::Stopped) {
    throw 'RagSupervisor SCM service did not reach Stopped'
}
$processDeadline = [DateTime]::UtcNow.AddSeconds(30)
do {
    try {
        Assert-RagProcessSetStopped -ProcessId $trackedProcessIds
        $descendantsStopped = $true
    } catch {
        $descendantsStopped = $false
        Start-Sleep -Milliseconds 250
    }
} while (-not $descendantsStopped -and [DateTime]::UtcNow -lt $processDeadline)
Assert-RagProcessSetStopped -ProcessId $trackedProcessIds

$programData = Join-Path $env:ProgramData 'LocalRAG'
$accountLedger = Join-Path $programData 'installed-accounts.json'
$hostsLedger = Join-Path $programData 'installed-hosts.json'
if (-not (Test-Path -LiteralPath $accountLedger -PathType Leaf)) {
    throw 'Protected installed-account ledger is required for uninstall'
}
$ledger = Get-Content -Raw -LiteralPath $accountLedger | ConvertFrom-Json
$expectedNames = @('RagApiSvc','RagDeletionSvc','RagInferenceSvc','RagIngestionSvc','RagOcrSvc','RagProxySvc','RagWebSvc')
$ledgerNames = @($ledger.accounts.name | Sort-Object)
if ($ledger.schema_version -ne 2 -or $ledger.phase -cne 'installed' -or
    $ledger.accounts.Count -ne 7 -or
    ($ledgerNames -join ',') -cne ($expectedNames -join ',')) {
    throw 'Installed-account ledger contract is invalid'
}
foreach ($account in $ledger.accounts) {
    if ($account.sid -cnotmatch '^S-1-5-21-(?:[0-9]+-){3}[0-9]+$') {
        throw "Installed-account SID is invalid: $($account.name)"
    }
    $current = Get-LocalUser -Name $account.name -ErrorAction Stop
    if ($current.SID.Value -cne $account.sid) {
        throw "Installed-account SID mismatch: $($account.name)"
    }
}
$hosts = $null
if (Test-Path -LiteralPath $hostsLedger -PathType Leaf) {
    $hosts = Get-Content -Raw -LiteralPath $hostsLedger | ConvertFrom-Json
    if ($hosts.schema_version -ne 1 -or
        $hosts.dns_scope -cnotin @('lan_dns','host_only') -or
        @($hosts.addresses).Count -ne 2 -or
        $hosts.managed_block -isnot [bool] -or
        ($hosts.managed_block -and
            ($hosts.prior_content_path -cne (Join-Path $programData 'hosts-prior.bin') -or
             $hosts.prior_content_sha256 -cnotmatch '^[0-9a-f]{64}$'))) {
        throw 'Installed hosts ledger contract is invalid'
    }
}
$hostsRollbackReady = $false
if ($null -ne $hosts -and $hosts.managed_block) {
    if ((Get-FileHash -LiteralPath $hosts.prior_content_path -Algorithm SHA256).Hash.ToLowerInvariant() -cne
        $hosts.prior_content_sha256) {
        throw 'Prior hosts-byte ledger hash mismatch'
    }
    & (Join-Path $PSScriptRoot 'Set-RagHostsEntry.ps1') `
        -Action Remove -Address @($hosts.addresses) `
        -PriorContentPath $hosts.prior_content_path -WhatIf | Out-Null
    $hostsRollbackReady = $true
}
$managedRelative = @(
    'identity-secrets','secrets','certificates','signed-stage','verified-release',
    'profiles','state','updates','work\ingestion','work\ocr',
    'installed-deployment.json','installed-caddy.json','installed-accounts.json',
    'installed-hosts.json','installation-dependency-evidence.json',
    'installation-network-evidence.json','installed-release-evidence.json',
    'installed-release-state.json','supervisor-startup-failure.json',
    'hosts-prior.bin'
)
Assert-RagUninstallManagedRoot -Root $programData `
    -ManagedRelativePath $managedRelative
$programFilesLocalRag = Join-Path $env:ProgramFiles 'LocalRAG'
if (Test-Path -LiteralPath $programFilesLocalRag) {
    Assert-RagPathComponentsNotReparse -Path $programFilesLocalRag
}
$caPath = Join-Path $programData 'certificates\local-rag-ca.crt'
if (Test-Path -LiteralPath $caPath) {
    $caItem = Get-Item -LiteralPath $caPath -Force
    if ($caItem.PSIsContainer -or ($caItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Managed CA certificate path is unsafe'
    }
}
$accountProcesses = [Collections.Generic.List[int]]::new()
foreach ($process in @(Get-CimInstance Win32_Process)) {
    $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwner -ErrorAction SilentlyContinue
    if ($null -ne $owner -and
        $null -ne $owner.PSObject.Properties['ReturnValue'] -and
        $null -ne $owner.PSObject.Properties['Domain'] -and
        $null -ne $owner.PSObject.Properties['User'] -and
        $owner.ReturnValue -eq 0 -and $owner.Domain -ceq $env:COMPUTERNAME -and
        $ledgerNames -ccontains $owner.User) {
        $accountProcesses.Add([int]$process.ProcessId)
    }
}
Assert-RagProcessSetStopped -ProcessId $accountProcesses.ToArray()

# Ingress and OCR egress are disabled only after all supervised and
# account-owned processes have been proven stopped.
Get-NetFirewallRule -DisplayName 'Local RAG HTTPS' -ErrorAction SilentlyContinue |
    Disable-NetFirewallRule | Out-Null

& sc.exe delete RagSupervisor | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not delete stopped RagSupervisor service' }
Get-NetFirewallRule -DisplayName 'Local RAG HTTPS' -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
Get-NetFirewallRule -DisplayName 'Local RAG OCR Outbound - *' -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
if ($null -ne $hosts -and $hosts.managed_block) {
    if ((Get-FileHash -LiteralPath $hosts.prior_content_path -Algorithm SHA256).Hash.ToLowerInvariant() -cne
        $hosts.prior_content_sha256) {
        throw 'Prior hosts-byte ledger hash mismatch'
    }
    & (Join-Path $PSScriptRoot 'Set-RagHostsEntry.ps1') `
        -Action Remove -Address @($hosts.addresses) `
        -PriorContentPath $hosts.prior_content_path | Out-Null
}

foreach ($account in $ledger.accounts) {
    & (Join-Path $PSScriptRoot 'Set-RagAccountRights.ps1') -Account ".\$($account.name)" -Action RemoveRequired
    Remove-LocalUser -Name $account.name
}
$caPath = Join-Path $programData 'certificates\local-rag-ca.crt'
if (Test-Path -LiteralPath $caPath) {
    $thumbprint = (New-Object Security.Cryptography.X509Certificates.X509Certificate2 `
        -ArgumentList $caPath).Thumbprint
    Get-ChildItem Cert:\LocalMachine\Root | Where-Object Thumbprint -ceq $thumbprint |
        Remove-Item -Force
}

# PostgreSQL volumes, RustFS data, originals, OCR work, and backup destinations
# are deliberately outside this removal list and are never deleted.
foreach ($path in @(
    (Join-Path $programData 'identity-secrets'),
    (Join-Path $programData 'secrets'),
    (Join-Path $programData 'certificates'),
    (Join-Path $programData 'signed-stage'),
    (Join-Path $programData 'updates'),
    (Join-Path $programData 'installed-deployment.json'),
    (Join-Path $programData 'installed-caddy.json'),
    (Join-Path $programData 'installed-accounts.json'),
    (Join-Path $programData 'installed-hosts.json'),
    (Join-Path $programData 'installation-dependency-evidence.json'),
    (Join-Path $programData 'installation-network-evidence.json'),
    (Join-Path $programData 'installed-release-evidence.json'),
    (Join-Path $programData 'installed-release-state.json'),
    (Join-Path $programData 'supervisor-startup-failure.json'),
    (Join-Path $env:ProgramFiles 'LocalRAG\current'),
    (Join-Path $env:ProgramFiles 'LocalRAG\service')
)) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}
$programFilesLocalRag = Join-Path $env:ProgramFiles 'LocalRAG'
if (Test-Path -LiteralPath $programFilesLocalRag) {
    if (@(Get-ChildItem -LiteralPath $programFilesLocalRag -Force).Count -ne 0) {
        throw 'Program Files LocalRAG root still contains unexpected content'
    }
    Remove-Item -LiteralPath $programFilesLocalRag -Force
}
foreach ($path in @(
    (Join-Path $programData 'profiles'), (Join-Path $programData 'state'),
    (Join-Path $programData 'verified-release'), (Join-Path $programData 'signed-stage'),
    (Join-Path $programData 'identity-secrets'), (Join-Path $programData 'secrets'),
    (Join-Path $programData 'certificates'), (Join-Path $programData 'work\ingestion')
    ,(Join-Path $programData 'work\ocr'), (Join-Path $programData 'work')
)) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}
$remainingTopLevel = @(Get-ChildItem -LiteralPath $programData -Force)
if ($remainingTopLevel.Count -ne 0) {
    throw 'ProgramData LocalRAG root still contains unexpected content'
}
if (Test-Path -LiteralPath $programData) { Remove-Item -LiteralPath $programData -Recurse -Force }
[pscustomobject]@{
    result='uninstalled'; data_preserved=$true; postgres_data_preserved=$true
    rustfs_data_preserved=$true; backup_destination_preserved=$true
} | ConvertTo-Json
