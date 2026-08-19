[CmdletBinding(SupportsShouldProcess,ConfirmImpact='High')]
param([Parameter(Mandatory)][string]$LocalAddress)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagTeamLanPreview.psm1') -Force
$programData = 'C:\ProgramData\LocalRAG'
$programFiles = 'C:\Program Files\LocalRAG'
$statePath = Join-Path $programData 'team-preview-state.json'
$state = Assert-RagTeamPreviewProfileState -ProgramDataRoot $programData -Expected InstalledPreview
$newAddress = Assert-RagTeamPreviewRfc1918Address -Address $LocalAddress
& (Join-Path $PSScriptRoot 'Test-RagTeamPreviewPrerequisites.ps1') `
    -LocalAddress $newAddress | Out-Null
$oldAddress = [string]$state.local_address
$newConnectorGeneration = [int]$state.connector_generation + 1
if ($newAddress -ceq $oldAddress) { throw 'The preview is already configured for this IPv4 address.' }
if (-not $PSCmdlet.ShouldProcess('rag.home.arpa',"Repair preview address to $newAddress")) { return }
$id = [guid]::NewGuid().ToString('N')
$transaction = Join-Path (Join-Path $programData 'repairs') $id
[IO.Directory]::CreateDirectory($transaction) | Out-Null
$hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$caddyEnvironment = Join-Path $programData 'environments\caddy.env'
$apiEnvironment = Join-Path $programData 'environments\api.env'
$stateBackup = Join-Path $transaction 'team-preview-state.json'
$hostsBackup = Join-Path $transaction 'hosts'
$caddyEnvironmentBackup = Join-Path $transaction 'caddy.env'
$apiEnvironmentBackup = Join-Path $transaction 'api.env'
[IO.File]::Copy($statePath,$stateBackup)
[IO.File]::Copy($hosts,$hostsBackup)
[IO.File]::Copy($caddyEnvironment,$caddyEnvironmentBackup)
[IO.File]::Copy($apiEnvironment,$apiEnvironmentBackup)
$journal = [ordered]@{
    schema_version=1;profile='team_lan_preview_unsigned';repair_id=$id;state='prepared'
    old_address=$oldAddress;new_address=$newAddress
    created_at=[DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ');failure=$null
}
$journalPath = Join-Path $transaction 'repair-journal.json'
function Set-PreviewRepairState([string]$Value,[AllowNull()][string]$Failure) {
    $journal.state=$Value;$journal.failure=$Failure
    Write-RagTeamPreviewJsonAtomic -Path $journalPath -Value $journal
}
function Set-EnvironmentValue([string]$Path,[string]$Name,[string]$Value) {
    $lines = @([IO.File]::ReadAllLines($Path))
    $matches = @($lines | Where-Object { $_.StartsWith($Name+'=',[StringComparison]::Ordinal) })
    if ($matches.Count -ne 1) { throw "Environment file must contain exactly one $Name entry: $Path" }
    $updated = @($lines | ForEach-Object {
        if ($_.StartsWith($Name+'=',[StringComparison]::Ordinal)) { "$Name=$Value" } else { $_ }
    })
    [IO.File]::WriteAllText($Path,(($updated -join "`r`n")+"`r`n"),[Text.UTF8Encoding]::new($false))
}
Set-PreviewRepairState 'prepared' $null
try {
    Stop-Service RagSupervisor -Force
    Set-EnvironmentValue -Path $caddyEnvironment -Name RAG_LAN_IPV4 -Value $newAddress
    Set-EnvironmentValue -Path $apiEnvironment -Name RAG_LAN_IPV4 -Value $newAddress
    & (Join-Path $PSScriptRoot 'Set-RagTeamPreviewHosts.ps1') `
        -LocalAddress $newAddress -Confirm:$false
    & (Join-Path $PSScriptRoot 'Set-RagTeamPreviewFirewall.ps1') -Action Apply `
        -LocalAddress $newAddress -PinnedCaddyProgram (Join-Path $programFiles 'current\caddy.exe') `
        -Confirm:$false
    $state.local_address=$newAddress
    $state.connector_generation=$newConnectorGeneration
    Write-RagTeamPreviewJsonAtomic -Path $statePath -Value $state
    Set-PreviewRepairState 'configuration_switched' $null
    $startupAttempt = [DateTimeOffset]::UtcNow
    Start-Service RagSupervisor
    Wait-RagTeamPreviewGraphReady -ProgramDataRoot $programData `
        -LocalAddress $newAddress -AttemptStartedAtUtc $startupAttempt `
        -TimeoutSeconds 300 | Out-Null
    $contractPath = Join-Path $programFiles 'current\team-preview-release.json'
    if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) {
        throw 'Installed Team/LAN preview release contract is missing.'
    }
    $releaseContract = Get-Content -Raw -LiteralPath $contractPath | ConvertFrom-Json
    $caddyHash = [string]$releaseContract.caddy_sha256
    if ($caddyHash -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$releaseContract.profile -cne 'team_lan_preview_unsigned') {
        throw 'Installed Team/LAN preview release contract is invalid.'
    }
    & (Join-Path $PSScriptRoot 'Test-RagTeamPreviewNetwork.ps1') `
        -LocalAddress $newAddress -PinnedCaddyProgram (Join-Path $programFiles 'current\caddy.exe') `
        -PinnedCaddySha256 $caddyHash | Out-Null
    Invoke-RagTeamPreviewConnector -LocalAddress $newAddress `
        -CaCertificate (Join-Path $programData 'certificates\local-rag-ca.crt') `
        -OutputRoot (Join-Path $programData "connectors\generation-$newConnectorGeneration") `
        -InstallationId ([string]$state.installation_id) `
        -ConnectorGeneration $newConnectorGeneration
    Set-PreviewRepairState 'committed' $null
    [pscustomobject]@{result='repaired';old_address=$oldAddress;local_address=$newAddress} |
        ConvertTo-Json
} catch {
    $failure=$_.Exception.Message
    Stop-Service RagSupervisor -Force -ErrorAction SilentlyContinue
    [IO.File]::Copy($hostsBackup,$hosts,$true)
    [IO.File]::Copy($caddyEnvironmentBackup,$caddyEnvironment,$true)
    [IO.File]::Copy($apiEnvironmentBackup,$apiEnvironment,$true)
    [IO.File]::Copy($stateBackup,$statePath,$true)
    & (Join-Path $PSScriptRoot 'Set-RagTeamPreviewFirewall.ps1') -Action Apply `
        -LocalAddress $oldAddress -PinnedCaddyProgram (Join-Path $programFiles 'current\caddy.exe') `
        -Confirm:$false -ErrorAction SilentlyContinue
    Start-Service RagSupervisor -ErrorAction SilentlyContinue
    Set-PreviewRepairState 'rolled_back' $failure
    throw "Preview address repair failed and prior configuration was restored: $failure"
}
