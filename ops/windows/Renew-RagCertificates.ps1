[CmdletBinding(SupportsShouldProcess,ConfirmImpact='High')]
param(
    [Parameter(Mandatory)][string]$OpenSslPath,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$OpenSslSha256,
    [Parameter(Mandatory)][string]$CaddyPath,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$CaddySha256,
    [Parameter(Mandatory)][ValidateCount(2,2)][string[]]$LocalAddress
)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Renew-RagCertificates.ps1 must run elevated'
}
if (-not $PSCmdlet.ShouldProcess('Local RAG TLS leaf set','Stage, validate, switch, restart, and verify')) { return }

$programData = Join-Path $env:ProgramData 'LocalRAG'
$certificates = Join-Path $programData 'certificates'
$secrets = Join-Path $programData 'secrets'
$serviceExe = Join-Path $env:ProgramFiles 'LocalRAG\service\RagSupervisorService.exe'
$serviceExeSha256 = (Get-FileHash -LiteralPath $serviceExe -Algorithm SHA256).Hash.ToLowerInvariant()
$supervisorPython = Join-Path $env:ProgramFiles 'LocalRAG\current\runtimes\api-python\python.exe'
$openssl = (Resolve-Path -LiteralPath $OpenSslPath).Path
$opensslConfig = Join-Path (Split-Path -Parent $openssl) 'openssl.cnf'
if (-not (Test-Path -LiteralPath $opensslConfig -PathType Leaf)) {
    throw 'Pinned OpenSSL runtime is missing its adjacent openssl.cnf'
}
if ((Get-FileHash -LiteralPath $openssl -Algorithm SHA256).Hash.ToLowerInvariant() -cne $OpenSslSha256) {
    throw 'Pinned OpenSSL SHA-256 verification failed'
}
$runId = [Guid]::NewGuid().ToString('N')
$stage = Join-Path $programData "certificate-renewal\stage-$runId"
$stageCertificates = Join-Path $stage 'certificates'
$stageSecrets = Join-Path $stage 'secrets'
$rollback = Join-Path $programData "certificate-renewal\rollback-$runId"
$rollbackCertificates = Join-Path $rollback 'certificates'
$rollbackSecrets = Join-Path $rollback 'secrets'
. (Join-Path $PSScriptRoot 'RagCertificateTransaction.ps1')
foreach ($path in @($stage,$stageCertificates,$stageSecrets,$rollback,$rollbackCertificates,$rollbackSecrets)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    icacls.exe $path /setowner '*S-1-5-18' | Out-Null
    icacls.exe $path /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Renewal staging ACL failed: $path" }
}
Copy-Item -LiteralPath (Join-Path $certificates 'local-rag-ca.crt') -Destination $stageCertificates
Copy-Item -LiteralPath (Join-Path $secrets 'local-rag-ca.key') -Destination $stageSecrets
$caCert = Join-Path $certificates 'local-rag-ca.crt'
$caKey = Join-Path $secrets 'local-rag-ca.key'
$extensions = Join-Path $stage 'extensions.cnf'
$leaves = @(
    @{ Name='rag.home.arpa'; San='DNS:rag.home.arpa'; Usage='serverAuth' },
    @{ Name='api-loopback'; CommonName='rag-api-loopback'; San='DNS:rag-api-loopback,IP:127.0.0.1,IP:::1'; Usage='serverAuth' },
    @{ Name='caddy-api-client'; San='DNS:caddy-api-client'; Usage='clientAuth' },
    @{ Name='supervisor-api-client'; San='DNS:supervisor-api-client'; Usage='clientAuth' }
)
foreach ($leaf in $leaves) {
    $commonName = if ($leaf.CommonName) { $leaf.CommonName } else { $leaf.Name }
    $key = Join-Path $stageSecrets "$($leaf.Name).key"
    $csr = Join-Path $stageCertificates "$($leaf.Name).csr"
    $cert = Join-Path $stageCertificates "$($leaf.Name).crt"
    & $openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out $key
    if ($LASTEXITCODE -ne 0) { throw "Renewal key generation failed: $($leaf.Name)" }
    & $openssl req -config $opensslConfig -new -key $key -subj "/CN=$commonName" -out $csr
    if ($LASTEXITCODE -ne 0) { throw "Renewal CSR failed: $($leaf.Name)" }
    @("basicConstraints=critical,CA:FALSE","subjectAltName=$($leaf.San)","extendedKeyUsage=$($leaf.Usage)","keyUsage=digitalSignature,keyEncipherment") |
        Set-Content -LiteralPath $extensions -Encoding ascii
    & $openssl x509 -req -sha256 -days 825 -in $csr -CA $caCert -CAkey $caKey `
        -CAcreateserial -extfile $extensions -out $cert
    if ($LASTEXITCODE -ne 0) { throw "Renewal signing failed: $($leaf.Name)" }
}
& (Join-Path $PSScriptRoot 'Test-RagCertificateSet.ps1') -OpenSslPath $openssl `
    -OpenSslSha256 $OpenSslSha256 -CertificateRoot $stageCertificates -SecretRoot $stageSecrets
if ($LASTEXITCODE -ne 0) { throw 'Staged certificate set failed semantic validation' }
Remove-Item -LiteralPath (Join-Path $stageSecrets 'local-rag-ca.key') -Force

$mutationJournal = @()
$httpsRule = Get-NetFirewallRule -DisplayName 'Local RAG HTTPS' -ErrorAction Stop
$ingressWasEnabled = @($httpsRule | Where-Object Enabled -eq 'True').Count -gt 0
try {
    Stop-Service -Name RagSupervisor -Force
    $mutationJournal = @(
        Invoke-RagCertificateLeafSetSwitch `
            -LiveCertificateRoot $certificates -LiveSecretRoot $secrets `
            -StageCertificateRoot $stageCertificates -StageSecretRoot $stageSecrets `
            -RollbackCertificateRoot $rollbackCertificates -RollbackSecretRoot $rollbackSecrets `
            -LeafNames @($leaves.Name)
    )
    $proxySid = (New-Object Security.Principal.NTAccount($env:COMPUTERNAME,'RagProxySvc')).Translate([Security.Principal.SecurityIdentifier]).Value
    $apiSid = (New-Object Security.Principal.NTAccount($env:COMPUTERNAME,'RagApiSvc')).Translate([Security.Principal.SecurityIdentifier]).Value
    $supervisorSid = (New-Object Security.Principal.NTAccount('NT SERVICE','RagSupervisor')).Translate([Security.Principal.SecurityIdentifier]).Value
    foreach ($acl in @(
        @((Join-Path $secrets 'rag.home.arpa.key'),$proxySid),
        @((Join-Path $secrets 'caddy-api-client.key'),$proxySid),
        @((Join-Path $secrets 'api-loopback.key'),$apiSid),
        @((Join-Path $secrets 'supervisor-api-client.key'),$supervisorSid)
    )) {
        icacls.exe $acl[0] /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' "*$($acl[1]):(R)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Renewed private-key ACL failed: $($acl[0])" }
    }
    foreach ($acl in @(
        @((Join-Path $certificates 'rag.home.arpa.crt'),$proxySid),
        @((Join-Path $certificates 'caddy-api-client.crt'),$proxySid),
        @((Join-Path $certificates 'api-loopback.crt'),$apiSid)
    )) {
        icacls.exe $acl[0] /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' "*$($acl[1]):(R)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Renewed certificate ACL failed: $($acl[0])" }
    }
    icacls.exe (Join-Path $certificates 'supervisor-api-client.crt') /inheritance:r `
        /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' "*${supervisorSid}:(R)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Renewed supervisor certificate ACL failed' }
    Start-Service -Name RagSupervisor
    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    do {
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue |
            Where-Object LocalAddress -in $LocalAddress)
        if ($listeners.Count -eq 2 -and @($listeners.OwningProcess | Sort-Object -Unique).Count -eq 1) { break }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($listeners.Count -ne 2) { throw 'Renewed service graph did not restore both Caddy listeners' }
    & (Join-Path $PSScriptRoot 'Test-RagNetwork.ps1') -PinnedCaddyProgram $CaddyPath `
        -PinnedCaddySha256 $CaddySha256 -PinnedServiceHostProgram $serviceExe `
        -PinnedServiceHostSha256 $serviceExeSha256 -PinnedSupervisorPython $supervisorPython `
        -DeploymentId 'rag-v4-local' `
        -ExpectedLocalAddresses $LocalAddress
    if ($LASTEXITCODE -ne 0) { throw 'Post-renewal network evidence failed' }
    [pscustomobject]@{ result='renewed'; previous_leaf_set=$rollback; rollback_retained=$true } |
        ConvertTo-Json
} catch {
    $renewalFailure = $_
    Get-NetFirewallRule -DisplayName 'Local RAG HTTPS' -ErrorAction SilentlyContinue |
        Disable-NetFirewallRule | Out-Null
    Stop-Service -Name RagSupervisor -Force -ErrorAction SilentlyContinue
    try {
        if ($mutationJournal.Count -gt 0) {
            Undo-RagCertificateLeafSetSwitch -Journal $mutationJournal
        }
        & (Join-Path $PSScriptRoot 'Test-RagCertificateSet.ps1') -OpenSslPath $openssl `
            -OpenSslSha256 $OpenSslSha256 -CertificateRoot $certificates -SecretRoot $secrets |
            Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Restored certificate set failed semantic validation' }
        Start-Service -Name RagSupervisor -ErrorAction Stop
        $rollbackDeadline = [DateTime]::UtcNow.AddMinutes(5)
        do {
            $rollbackListeners = @(Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue |
                Where-Object LocalAddress -in $LocalAddress)
            if ($rollbackListeners.Count -eq 2 -and
                @($rollbackListeners.OwningProcess | Sort-Object -Unique).Count -eq 1) { break }
            Start-Sleep -Seconds 1
        } while ([DateTime]::UtcNow -lt $rollbackDeadline)
        if ($rollbackListeners.Count -ne 2) {
            throw 'Rolled-back service graph did not restore both Caddy listeners'
        }
        & (Join-Path $PSScriptRoot 'Test-RagNetwork.ps1') -PinnedCaddyProgram $CaddyPath `
            -PinnedCaddySha256 $CaddySha256 -PinnedServiceHostProgram $serviceExe `
            -PinnedServiceHostSha256 $serviceExeSha256 -PinnedSupervisorPython $supervisorPython `
            -DeploymentId 'rag-v4-local' `
            -ExpectedLocalAddresses $LocalAddress | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Post-rollback network evidence failed' }
        if ($ingressWasEnabled) {
            Get-NetFirewallRule -DisplayName 'Local RAG HTTPS' -ErrorAction Stop |
                Enable-NetFirewallRule | Out-Null
        }
    } catch {
        throw "$($renewalFailure.Exception.Message); rollback acceptance failed and ingress remains disabled: $($_.Exception.Message)"
    }
    throw $renewalFailure
}
