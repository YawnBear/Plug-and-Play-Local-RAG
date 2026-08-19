[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OpenSslPath,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$OpenSslSha256,
    [string]$CertificateRoot = 'C:\ProgramData\LocalRAG\certificates',
    [string]$SecretRoot = 'C:\ProgramData\LocalRAG\secrets'
)
$ErrorActionPreference = 'Stop'
$openssl = (Resolve-Path -LiteralPath $OpenSslPath).Path
if ((Get-FileHash -LiteralPath $openssl -Algorithm SHA256).Hash.ToLowerInvariant() -cne $OpenSslSha256) {
    throw 'Pinned preview OpenSSL SHA-256 verification failed.'
}
$opensslConfig = Join-Path (Split-Path -Parent $openssl) 'openssl.cnf'
if (-not (Test-Path -LiteralPath $opensslConfig -PathType Leaf)) {
    throw 'Pinned preview OpenSSL is missing adjacent openssl.cnf.'
}
[IO.Directory]::CreateDirectory($CertificateRoot) | Out-Null
[IO.Directory]::CreateDirectory($SecretRoot) | Out-Null
$caKey = Join-Path $SecretRoot 'local-rag-ca.key'
$caCert = Join-Path $CertificateRoot 'local-rag-ca.crt'
$targets = @(
    [pscustomobject]@{name='rag.home.arpa';san='DNS:rag.home.arpa';usage='serverAuth';key='rag.home.arpa.key'},
    [pscustomobject]@{name='rag-api-loopback';san='DNS:rag-api-loopback,IP:127.0.0.1';usage='serverAuth';key='api-loopback.key'},
    [pscustomobject]@{name='caddy-api-client';san='DNS:caddy-api-client';usage='clientAuth';key='caddy-api-client.key'},
    [pscustomobject]@{name='supervisor-api-client';san='DNS:supervisor-api-client';usage='clientAuth';key='supervisor-api-client.key'}
)
foreach ($path in @($caKey,$caCert) + @($targets | ForEach-Object { Join-Path $SecretRoot $_.key })) {
    if (Test-Path -LiteralPath $path) { throw "Fresh preview certificate setup refuses existing material: $path" }
}
& $openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out $caKey
if ($LASTEXITCODE -ne 0) { throw 'Preview CA key generation failed.' }
& $openssl req -config $opensslConfig -x509 -new -sha256 -days 3650 -key $caKey `
    -subj '/CN=Local RAG Private CA' -addext 'basicConstraints=critical,CA:TRUE' `
    -addext 'keyUsage=critical,keyCertSign,cRLSign' -out $caCert
if ($LASTEXITCODE -ne 0) { throw 'Preview CA certificate generation failed.' }
Copy-Item -LiteralPath $caCert -Destination (Join-Path $CertificateRoot 'loopback-ca.crt')
$extension = Join-Path $CertificateRoot 'preview-leaf.cnf'
try {
    foreach ($target in $targets) {
        $key = Join-Path $SecretRoot $target.key
        $csr = Join-Path $CertificateRoot ($target.name+'.csr')
        $cert = Join-Path $CertificateRoot ($target.name+'.crt')
        & $openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out $key
        if ($LASTEXITCODE -ne 0) { throw "Preview leaf key generation failed: $($target.name)" }
        & $openssl req -config $opensslConfig -new -key $key -subj ("/CN="+$target.name) -out $csr
        if ($LASTEXITCODE -ne 0) { throw "Preview leaf request failed: $($target.name)" }
        @('basicConstraints=critical,CA:FALSE',('subjectAltName='+$target.san),
            ('extendedKeyUsage='+$target.usage),'keyUsage=digitalSignature,keyEncipherment') |
            Set-Content -LiteralPath $extension -Encoding ascii
        & $openssl x509 -req -sha256 -days 825 -in $csr -CA $caCert -CAkey $caKey `
            -CAcreateserial -extfile $extension -out $cert
        if ($LASTEXITCODE -ne 0) { throw "Preview leaf signing failed: $($target.name)" }
        Remove-Item -LiteralPath $csr -Force
    }
} finally {
    Remove-Item -LiteralPath $extension -Force -ErrorAction SilentlyContinue
}
& icacls.exe $SecretRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' `
    '*S-1-5-32-544:(OI)(CI)(F)' /T /C | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Preview private-key ACL lockdown failed.' }
Import-Certificate -FilePath $caCert -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
