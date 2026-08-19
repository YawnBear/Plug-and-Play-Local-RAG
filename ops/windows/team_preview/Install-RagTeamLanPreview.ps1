[CmdletBinding(SupportsShouldProcess,ConfirmImpact='High')]
param(
    [Parameter(Mandatory)][string]$PayloadRoot,
    [string]$LocalAddress,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagTeamLanPreview.psm1') -Force
$programData = 'C:\ProgramData\LocalRAG'
$programFiles = 'C:\Program Files\LocalRAG'
$release = Join-Path $programFiles 'current'
$serviceRoot = Join-Path $programFiles 'service'
$payload = (Resolve-Path -LiteralPath $PayloadRoot).Path.TrimEnd('\')
if ([string]::IsNullOrWhiteSpace($LocalAddress)) {
    $LocalAddress = Read-Host 'Enter this workstation''s reserved RFC1918 IPv4 address'
}
$address = Assert-RagTeamPreviewRfc1918Address -Address $LocalAddress
if ($Plan) {
    & (Join-Path $PSScriptRoot 'Prepare-RagTeamLanPreview.ps1') `
        -ReleaseRoot (Join-Path $payload 'release') -ProgramDataRoot $programData `
        -LocalAddress $address -Plan
    return
}
$inventory = Test-RagTeamPreviewInventory -Root $payload
$contract = Get-Content -Raw -LiteralPath (Join-Path $payload 'team-preview-release.json') |
    ConvertFrom-Json
if ([string]$contract.profile -cne 'team_lan_preview_unsigned' -or
    [string]$contract.payload_state -cne 'assembled_unsigned' -or
    [string]$contract.authenticity -cne 'unverified_unsigned' -or
    [string]$contract.alembic_revision -cne '0014_restart_without_backup' -or
    $contract.automatic_updates_available -isnot [bool] -or
    $contract.automatic_updates_available) {
    throw 'The payload is not an assembled unsigned Team/LAN preview.'
}
$resumeProvisioning = $false
if (Test-Path -LiteralPath $programData) {
    Assert-RagTeamPreviewProfileState -ProgramDataRoot $programData -Expected FreshInstall
    $resumeJournal = Join-Path $programData 'state\team-preview-provisioning.json'
    if (-not (Test-Path -LiteralPath $resumeJournal -PathType Leaf)) {
        throw 'Fresh preview install refuses unrelated ProgramData\LocalRAG content.'
    }
    $resumeState = Get-Content -Raw -LiteralPath $resumeJournal | ConvertFrom-Json
    if ($resumeState.schema_version -ne 1 -or $resumeState.provisioned -ne $true -or
        [string]$resumeState.installation_id -cnotmatch '^[0-9a-f-]{36}$') {
        throw 'Team preview resume journal is invalid or predates durable store provisioning.'
    }
    $resumeProvisioning = $true
}
if (Test-Path -LiteralPath $programFiles) {
    if (-not $resumeProvisioning) {
        throw 'Fresh preview install refuses unrelated Program Files\LocalRAG content.'
    }
}
& (Join-Path $PSScriptRoot 'Test-RagTeamPreviewPrerequisites.ps1') `
    -LocalAddress $address -PullModels | Out-Null
Write-Warning 'UNSIGNED TEAM/LAN PREVIEW: Windows cannot verify the publisher of this package.'
Write-Warning 'The SHA-256 inventory detects corruption only; it does not prove publisher authenticity.'
Write-Warning 'Install only on a trusted private LAN. Public networks and router port forwarding are unsupported.'
if (-not $PSCmdlet.ShouldProcess($programFiles,'Install unsigned Team/LAN preview')) { return }
$createdAccounts = [Collections.Generic.List[string]]::new()
$serviceCreated = $false
$firewallCreated = $false
$storesProvisioned = $false
$setupCode = $null
$rightsGranted = [Collections.Generic.List[string]]::new()
$importedCaThumbprint = $null
$hostsPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$priorHostsBytes = [IO.File]::ReadAllBytes($hostsPath)
$hostsMutated = $false
try {
    [IO.Directory]::CreateDirectory($programFiles) | Out-Null
    [IO.Directory]::CreateDirectory($programData) | Out-Null
    [IO.Directory]::CreateDirectory($serviceRoot) | Out-Null
    $copyRelease = Test-RagTeamPreviewReleaseCopyRequired `
        -CurrentReleaseRoot $release -DurableStoreResume $resumeProvisioning
    if ($copyRelease) {
        $stage = Join-Path $programFiles ('team-preview-'+$inventory.tree_sha256)
        if (Test-Path -LiteralPath $stage) {
            throw 'Preview release staging path already exists; attended cleanup is required.'
        }
        Copy-Item -LiteralPath (Join-Path $payload 'release') -Destination $stage -Recurse
        Test-RagTeamPreviewInstalledRelease -PayloadRoot $payload `
            -InstalledReleaseRoot $stage -ExpectedTreeSha256 $inventory.tree_sha256 | Out-Null
        [IO.Directory]::Move($stage,$release)
    }
    Test-RagTeamPreviewInstalledRelease -PayloadRoot $payload `
        -InstalledReleaseRoot $release -ExpectedTreeSha256 $inventory.tree_sha256 | Out-Null
    $provisioning = (& (Join-Path $PSScriptRoot 'Prepare-RagTeamLanPreview.ps1') `
        -ReleaseRoot $release -ProgramDataRoot $programData -LocalAddress $address) |
        ConvertFrom-Json
    if ($provisioning.result -cne 'provisioned' -or
        [string]$provisioning.alembic_revision -cne '0014_restart_without_backup') {
        throw 'Team preview provisioning did not reach the exact release contract.'
    }
    $storesProvisioned = $true
    $setupCode = [string]$provisioning.setup_code
    $installationId = [string]$provisioning.installation_id
    $secrets = Join-Path $programData 'secrets'
    $identitySecrets = Join-Path $programData 'identity-secrets'
    $certificates = Join-Path $programData 'certificates'
    $environments = [string]$provisioning.environment_root
    $profiles = Join-Path $programData 'profiles'
    $work = Join-Path $programData 'work'
    $stateRoot = Join-Path $programData 'state'
    $runtimeDirectories = @(
        $profiles,$work,(Join-Path $profiles 'proxy\tmp'),
        (Join-Path $profiles 'web\tmp'),(Join-Path $profiles 'api\tmp'),
        (Join-Path $profiles 'ingestion\tmp'),(Join-Path $profiles 'deletion\tmp'),
        (Join-Path $profiles 'inference\tmp'),
        (Join-Path $profiles 'inference\cache\huggingface\hub'),
        (Join-Path $profiles 'inference\cache\transformers'),
        (Join-Path $profiles 'ocr\tmp'),
        (Join-Path $profiles 'ocr\cache\huggingface\hub'),
        (Join-Path $profiles 'ocr\cache\transformers'),
        (Join-Path $work 'ingestion\objects'),(Join-Path $work 'ingestion\ocr'),
        (Join-Path $work 'ocr'),(Join-Path $stateRoot 'inference'),
        (Join-Path $stateRoot 'ocr')
    )
    foreach ($path in @($secrets,$identitySecrets,$certificates,$environments)+$runtimeDirectories) {
        [IO.Directory]::CreateDirectory($path) | Out-Null
    }
    $accounts = [ordered]@{
        caddy='RagProxySvc';web='RagWebSvc';api='RagApiSvc';ingestion='RagIngestionSvc'
        deletion='RagDeletionSvc';inference='RagInferenceSvc';ocr='RagOcrSvc'
    }
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        foreach ($entry in $accounts.GetEnumerator()) {
            if ($null -ne (Get-LocalUser -Name $entry.Value -ErrorAction SilentlyContinue)) {
                throw "Preview refuses a pre-existing runtime account: $($entry.Value)"
            }
            $bytes = [byte[]]::new(48);$rng.GetBytes($bytes)
            $passwordText = [Convert]::ToBase64String($bytes)
            $password = ConvertTo-SecureString $passwordText -AsPlainText -Force
            New-LocalUser -Name $entry.Value -Password $password -AccountNeverExpires `
                -PasswordNeverExpires -UserMayNotChangePassword | Out-Null
            $createdAccounts.Add($entry.Value)
            [IO.File]::WriteAllText((Join-Path $identitySecrets ($entry.Key+'.logon.env')),
                "RAG_WINDOWS_ACCOUNT_PASSWORD=$passwordText`r`n",[Text.UTF8Encoding]::new($false))
        }
    } finally { $rng.Dispose() }
    foreach ($account in $accounts.Values) {
        $rightsGranted.Add($account)
        & (Join-Path (Split-Path $PSScriptRoot -Parent) 'Set-RagAccountRights.ps1') `
            -Account $account -Action GrantRequired | Out-Null
    }
    $serviceSids = @{}
    foreach ($entry in $accounts.GetEnumerator()) {
        $sid = (New-Object Security.Principal.NTAccount(
            $env:COMPUTERNAME,$entry.Value
        )).Translate([Security.Principal.SecurityIdentifier]).Value
        $serviceSids[$entry.Key] = $sid
        $environmentFile = Join-Path $environments ($entry.Key+'.env')
        & icacls.exe $environmentFile /inheritance:r /grant:r '*S-1-5-18:(F)' `
            '*S-1-5-32-544:(F)' "*$sid`:(R)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Preview service environment ACL failed: $($entry.Key)" }
        $profileName = if ($entry.Key -ceq 'caddy') { 'proxy' } else { $entry.Key }
        & icacls.exe (Join-Path $profiles $profileName) /inheritance:r /grant:r `
            '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' `
            "*$sid`:(OI)(CI)(M)" /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Preview profile ACL failed: $($entry.Key)" }
        & icacls.exe $release /grant:r "*$sid`:(OI)(CI)(RX)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Preview release ACL failed: $($entry.Key)" }
    }
    foreach ($grant in @(
        @((Join-Path $work 'ingestion'),$serviceSids.ingestion),
        @((Join-Path $work 'ocr'),$serviceSids.ocr),
        @((Join-Path $stateRoot 'inference'),$serviceSids.inference),
        @((Join-Path $stateRoot 'ocr'),$serviceSids.ocr)
    )) {
        & icacls.exe $grant[0] /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' `
            '*S-1-5-32-544:(OI)(CI)(F)' "*$($grant[1]):(OI)(CI)(M)" /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Preview runtime ACL failed: $($grant[0])" }
    }
    & icacls.exe $identitySecrets /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' `
        '*S-1-5-32-544:(OI)(CI)(F)' /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Preview identity-secret ACL lockdown failed.' }
    $openssl = Join-Path $release 'tools\openssl\openssl.exe'
    & (Join-Path $PSScriptRoot 'Install-RagTeamPreviewCertificates.ps1') `
        -OpenSslPath $openssl -OpenSslSha256 ([string]$contract.openssl_sha256) `
        -CertificateRoot $certificates -SecretRoot $secrets
    $installedCa = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
        (Join-Path $certificates 'local-rag-ca.crt')
    )
    $importedCaThumbprint = $installedCa.Thumbprint
    Copy-Item -LiteralPath (Join-Path $certificates 'rag-api-loopback.crt') `
        -Destination (Join-Path $certificates 'api-loopback.crt')
    foreach ($grant in @(
        @((Join-Path $secrets 'rag.home.arpa.key'),$serviceSids.caddy),
        @((Join-Path $certificates 'rag.home.arpa.crt'),$serviceSids.caddy),
        @((Join-Path $secrets 'caddy-api-client.key'),$serviceSids.caddy),
        @((Join-Path $certificates 'caddy-api-client.crt'),$serviceSids.caddy),
        @((Join-Path $certificates 'loopback-ca.crt'),$serviceSids.caddy),
        @((Join-Path $secrets 'api-loopback.key'),$serviceSids.api),
        @((Join-Path $certificates 'api-loopback.crt'),$serviceSids.api),
        @((Join-Path $certificates 'local-rag-ca.crt'),$serviceSids.api)
    )) {
        & icacls.exe $grant[0] /grant:r "*$($grant[1]):(R)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Preview certificate ACL failed: $($grant[0])" }
    }
    $caddyEnv = Join-Path $environments 'caddy.env'
    $sourceManifest = Join-Path $release 'deployment.json'
    $manifest = Get-Content -Raw -LiteralPath $sourceManifest | ConvertFrom-Json
    $manifest | Add-Member -NotePropertyName product_profile -NotePropertyValue 'team_lan_preview_unsigned'
    $manifest.deployment_readiness.prerequisite_checks = @(
        @($manifest.deployment_readiness.prerequisite_checks | Where-Object { $_ -cne 'signed_release_chain' }) +
        @('unsigned_inventory','preview_profile_isolation','rfc1918_ipv4')
    )
    $caddy = @($manifest.services | Where-Object name -eq 'caddy')[0]
    $caddy.executable = Join-Path $release 'caddy.exe'
    $caddy.working_directory = $release
    $caddy.arguments = @('run','--config',(Join-Path $release 'Caddyfile'))
    $caddy.environment_file = $caddyEnv
    $caddy.environment_keys = @($caddy.environment_keys | Where-Object { $_ -cne 'RAG_LAN_IPV6' })
    foreach ($service in @($manifest.services | Where-Object name -ne 'caddy')) {
        $service.environment_file = Join-Path $environments ($service.name+'.env')
    }
    $apiService = @($manifest.services | Where-Object name -eq 'api')[0]
    $apiService.environment_keys = @($apiService.environment_keys) + @(
        'PRODUCT_PROFILE','RAG_LAN_IPV4'
    )
    $manifest.deployment_readiness.state = 'installed'
    Write-RagTeamPreviewJsonAtomic -Path (Join-Path $programData 'installed-deployment.json') -Value $manifest
    Copy-Item -LiteralPath (Join-Path $release 'RagSupervisorService.exe') `
        -Destination (Join-Path $serviceRoot 'RagSupervisorService.exe')
    New-Service -Name RagSupervisor -BinaryPathName ('"'+(Join-Path $serviceRoot 'RagSupervisorService.exe')+'"') `
        -StartupType Automatic | Out-Null
    $serviceCreated = $true
    & sc.exe sidtype RagSupervisor unrestricted | Out-Null
    & (Join-Path $PSScriptRoot 'Set-RagTeamPreviewHosts.ps1') -LocalAddress $address -Confirm:$false
    $hostsMutated = $true
    & (Join-Path $PSScriptRoot 'Set-RagTeamPreviewFirewall.ps1') -Action Apply `
        -LocalAddress $address -PinnedCaddyProgram (Join-Path $release 'caddy.exe') -Confirm:$false
    $firewallCreated = $true
    $startupAttempt = [DateTimeOffset]::UtcNow
    Start-Service RagSupervisor
    Wait-RagTeamPreviewGraphReady -ProgramDataRoot $programData `
        -LocalAddress $address -AttemptStartedAtUtc $startupAttempt `
        -TimeoutSeconds 300 | Out-Null
    & (Join-Path $PSScriptRoot 'Test-RagTeamPreviewNetwork.ps1') -LocalAddress $address `
        -PinnedCaddyProgram (Join-Path $release 'caddy.exe') `
        -PinnedCaddySha256 ([string]$contract.caddy_sha256) | Out-Null
    $state = [ordered]@{
        schema_version=1;profile='team_lan_preview_unsigned';authenticity='unverified_unsigned'
        automatic_updates_available=$false;release_tree_sha256=$inventory.tree_sha256
        alembic_revision=[string]$contract.alembic_revision;local_address=$address
        installation_id=$installationId;connector_generation=1
        installed_at=[DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    }
    Write-RagTeamPreviewJsonAtomic -Path (Join-Path $programData 'team-preview-state.json') -Value $state
    Invoke-RagTeamPreviewConnector -LocalAddress $address `
        -InstallationId $installationId -ConnectorGeneration 1 `
        -CaCertificate (Join-Path $certificates 'local-rag-ca.crt') `
        -OutputRoot (Join-Path $programData 'connectors\generation-1')
    Write-Host ''
    Write-Host 'One-time Team owner setup code (expires in 15 minutes):'
    Write-Host $setupCode
    Start-Process 'https://rag.home.arpa/setup'
    $setupCode = $null
    $state | ConvertTo-Json -Depth 3
} catch {
    if ($serviceCreated) { Stop-Service RagSupervisor -Force -ErrorAction SilentlyContinue;& sc.exe delete RagSupervisor | Out-Null }
    if ($firewallCreated) { & (Join-Path $PSScriptRoot 'Set-RagTeamPreviewFirewall.ps1') -Action Remove -LocalAddress $address -PinnedCaddyProgram (Join-Path $release 'caddy.exe') -Confirm:$false -ErrorAction SilentlyContinue }
    if ($hostsMutated) {
        $temporaryHosts = "$hostsPath.team-preview-rollback-$([guid]::NewGuid().ToString('N')).tmp"
        try {
            [IO.File]::WriteAllBytes($temporaryHosts,$priorHostsBytes)
            [IO.File]::Replace($temporaryHosts,$hostsPath,$null,$true)
        }
        finally {
            if (Test-Path -LiteralPath $temporaryHosts) { [IO.File]::Delete($temporaryHosts) }
        }
    }
    if ($null -ne $importedCaThumbprint) {
        $store = [Security.Cryptography.X509Certificates.X509Store]::new('Root','LocalMachine')
        try {
            $store.Open('ReadWrite')
            foreach ($certificate in @($store.Certificates | Where-Object {
                $_.Thumbprint -ceq $importedCaThumbprint
            })) { $store.Remove($certificate) }
        }
        finally { $store.Close() }
    }
    foreach ($account in $rightsGranted) {
        & (Join-Path (Split-Path $PSScriptRoot -Parent) 'Set-RagAccountRights.ps1') `
            -Account $account -Action RemoveRequired -ErrorAction SilentlyContinue | Out-Null
    }
    foreach ($account in $createdAccounts) {
        Remove-LocalUser -Name $account -ErrorAction SilentlyContinue
    }
    $provisioningJournal = Join-Path $programData 'state\team-preview-provisioning.json'
    if (-not $storesProvisioned -and (Test-Path -LiteralPath $provisioningJournal)) {
        try {
            $failedProvisioning = Get-Content -Raw -LiteralPath $provisioningJournal |
                ConvertFrom-Json
            $storesProvisioned = $failedProvisioning.provisioned -eq $true
        }
        catch { $storesProvisioned = $false }
    }
    if (-not $storesProvisioned) {
        if (Test-Path -LiteralPath $programFiles) { [IO.Directory]::Delete($programFiles,$true) }
        if (Test-Path -LiteralPath $programData) { [IO.Directory]::Delete($programData,$true) }
    }
    else {
        foreach ($path in @(
            (Join-Path $programData 'identity-secrets'),
            (Join-Path $programData 'certificates'),
            (Join-Path $programData 'connectors')
        )) {
            if (Test-Path -LiteralPath $path) { [IO.Directory]::Delete($path,$true) }
        }
        foreach ($path in @(
            (Join-Path $programData 'installed-deployment.json'),
            (Join-Path $programData 'team-preview-state.json')
        )) {
            if (Test-Path -LiteralPath $path) { [IO.File]::Delete($path) }
        }
        foreach ($name in @(
            'local-rag-ca.key','rag.home.arpa.key','api-loopback.key',
            'caddy-api-client.key','supervisor-api-client.key'
        )) {
            $path = Join-Path $programData ('secrets\'+$name)
            if (Test-Path -LiteralPath $path) { [IO.File]::Delete($path) }
        }
        Write-Warning 'Provisioned Team preview data, protected secrets, and the resume journal were preserved.'
    }
    $setupCode = $null
    throw
}
