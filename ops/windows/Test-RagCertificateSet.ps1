[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OpenSslPath,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$OpenSslSha256,
    [Parameter(Mandatory)][string]$CertificateRoot,
    [Parameter(Mandatory)][string]$SecretRoot
)
$ErrorActionPreference = 'Stop'

function Get-RagOpenSslExtensionValues {
    param(
        [Parameter(Mandatory)][string]$Certificate,
        [Parameter(Mandatory)][string]$Extension
    )
    $output = @(& $openssl x509 -in $Certificate -noout -ext $Extension)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read certificate extension ${Extension}: $Certificate"
    }
    $valueLines = @(
        $output |
            Where-Object { $_ -notmatch '^\s*X509v3\s+' -and $_ -notmatch '^\s*$' } |
            ForEach-Object { $_.Trim() }
    )
    if ($valueLines.Count -eq 0) {
        throw "Certificate extension is missing ${Extension}: $Certificate"
    }
    return @(
        (($valueLines -join ' ') -split ',') |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ } |
            Sort-Object
    )
}

$openssl = (Resolve-Path -LiteralPath $OpenSslPath).Path
$opensslConfig = Join-Path (Split-Path -Parent $openssl) 'openssl.cnf'
if (-not (Test-Path -LiteralPath $opensslConfig -PathType Leaf)) {
    throw 'Pinned OpenSSL runtime is missing its adjacent openssl.cnf'
}
if ((Get-FileHash -LiteralPath $openssl -Algorithm SHA256).Hash.ToLowerInvariant() -cne $OpenSslSha256) {
    throw 'Pinned OpenSSL SHA-256 verification failed'
}
$ca = Join-Path $CertificateRoot 'local-rag-ca.crt'
$caKey = Join-Path $SecretRoot 'local-rag-ca.key'
$caText = & $openssl x509 -in $ca -noout -text
if ($LASTEXITCODE -ne 0 -or ($caText -join "`n") -cnotmatch 'CA:TRUE') {
    throw 'Private CA basic constraints are invalid'
}
& $openssl x509 -in $ca -noout -checkend 31536000
if ($LASTEXITCODE -ne 0) { throw 'Private CA lifetime is less than one year' }
& $openssl verify -CAfile $ca $ca
if ($LASTEXITCODE -ne 0) { throw 'Private CA self-signature verification failed' }

$leaves = @(
    @{ Cert='rag.home.arpa.crt'; Key='rag.home.arpa.key'; Usage='TLS Web Server Authentication'; San=@('DNS:rag.home.arpa') },
    @{ Cert='api-loopback.crt'; Key='api-loopback.key'; Usage='TLS Web Server Authentication'; San=@('DNS:rag-api-loopback','IP Address:127.0.0.1','IP Address:0:0:0:0:0:0:0:1') },
    @{ Cert='caddy-api-client.crt'; Key='caddy-api-client.key'; Usage='TLS Web Client Authentication'; San=@('DNS:caddy-api-client') },
    @{ Cert='supervisor-api-client.crt'; Key='supervisor-api-client.key'; Usage='TLS Web Client Authentication'; San=@('DNS:supervisor-api-client') }
)
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $caKeyPublic = (& $openssl pkey -in $caKey -pubout) -join "`n"
    $caCertPublic = (& $openssl x509 -in $ca -pubkey -noout) -join "`n"
    $caKeyHash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($caKeyPublic))).Replace('-','')
    $caCertHash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($caCertPublic))).Replace('-','')
    if ($caKeyHash -cne $caCertHash) { throw 'Private CA certificate/private-key mismatch' }
    foreach ($leaf in $leaves) {
        $certificate = Join-Path $CertificateRoot $leaf.Cert
        $key = Join-Path $SecretRoot $leaf.Key
        & $openssl verify -CAfile $ca -purpose any $certificate
        if ($LASTEXITCODE -ne 0) { throw "Certificate chain verification failed: $($leaf.Cert)" }
        & $openssl x509 -in $certificate -noout -checkend 2592000
        if ($LASTEXITCODE -ne 0) { throw "Certificate lifetime is less than 30 days: $($leaf.Cert)" }
        $x509 = [Security.Cryptography.X509Certificates.X509Certificate2]::new($certificate)
        try {
            $endDate = $x509.NotAfter.ToUniversalTime()
        } finally {
            $x509.Dispose()
        }
        if ($endDate -gt [DateTime]::UtcNow.AddDays(826)) {
            throw "Certificate lifetime exceeds the 825-day policy: $($leaf.Cert)"
        }
        $observedSans = @(Get-RagOpenSslExtensionValues $certificate 'subjectAltName')
        if (($observedSans -join ',') -cne (@($leaf.San | Sort-Object) -join ',')) {
            throw "Certificate SAN set is not exact: $($leaf.Cert)"
        }
        $observedUsage = @(Get-RagOpenSslExtensionValues $certificate 'extendedKeyUsage')
        if (($observedUsage -join ',') -cne (@($leaf.Usage | Sort-Object) -join ',')) {
            throw "Certificate EKU set is not exact: $($leaf.Cert)"
        }
        $constraintsText = (& $openssl x509 -in $certificate -noout -ext basicConstraints) -join ' '
        if ($constraintsText -cnotmatch 'CA:FALSE' -or $constraintsText -cnotmatch 'critical') {
            throw "Leaf basic constraints are invalid: $($leaf.Cert)"
        }
        $keyPublic = (& $openssl pkey -in $key -pubout) -join "`n"
        $certPublic = (& $openssl x509 -in $certificate -pubkey -noout) -join "`n"
        $keyHash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($keyPublic))).Replace('-','')
        $certHash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($certPublic))).Replace('-','')
        if ($keyHash -cne $certHash) { throw "Certificate/private-key mismatch: $($leaf.Cert)" }
    }
} finally {
    $sha.Dispose()
}
[pscustomobject]@{
    schema_version=1
    result='pass'
    ca_constraints=$true
    minimum_lifetime_days=30
    exact_san_eku_and_key_pairing=$true
    changes_applied=$false
} | ConvertTo-Json
