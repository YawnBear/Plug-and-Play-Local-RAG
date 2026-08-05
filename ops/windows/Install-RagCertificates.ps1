[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OpenSslPath,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$OpenSslSha256,
    [Parameter(Mandatory)][string]$CertificateRoot,
    [Parameter(Mandatory)][string]$SecretRoot
)

$ErrorActionPreference = 'Stop'
$openssl = (Resolve-Path -LiteralPath $OpenSslPath).Path
$opensslConfig = Join-Path (Split-Path -Parent $openssl) 'openssl.cnf'
if (-not (Test-Path -LiteralPath $opensslConfig -PathType Leaf)) {
    throw 'Pinned OpenSSL runtime is missing its adjacent openssl.cnf'
}
if ((Get-FileHash -LiteralPath $openssl -Algorithm SHA256).Hash.ToLowerInvariant() -cne $OpenSslSha256) {
    throw 'Pinned OpenSSL SHA-256 verification failed'
}
New-Item -ItemType Directory -Path $CertificateRoot,$SecretRoot -Force | Out-Null
$caKey = Join-Path $SecretRoot 'local-rag-ca.key'
$caCert = Join-Path $CertificateRoot 'local-rag-ca.crt'
$serverKey = Join-Path $SecretRoot 'rag.home.arpa.key'
$serverCsr = Join-Path $CertificateRoot 'rag.home.arpa.csr'
$serverCert = Join-Path $CertificateRoot 'rag.home.arpa.crt'
$apiKey = Join-Path $SecretRoot 'api-loopback.key'
$apiCsr = Join-Path $CertificateRoot 'api-loopback.csr'
$apiCert = Join-Path $CertificateRoot 'api-loopback.crt'
$clientKey = Join-Path $SecretRoot 'caddy-api-client.key'
$clientCsr = Join-Path $CertificateRoot 'caddy-api-client.csr'
$clientCert = Join-Path $CertificateRoot 'caddy-api-client.crt'
$supervisorKey = Join-Path $SecretRoot 'supervisor-api-client.key'
$supervisorCsr = Join-Path $CertificateRoot 'supervisor-api-client.csr'
$supervisorCert = Join-Path $CertificateRoot 'supervisor-api-client.crt'
$extensions = Join-Path $CertificateRoot 'leaf-extensions.cnf'

foreach ($path in @(
    $caKey,$caCert,$serverKey,$serverCsr,$serverCert,$apiKey,$apiCsr,$apiCert,
    $clientKey,$clientCsr,$clientCert,$supervisorKey,$supervisorCsr,$supervisorCert
)) {
    if (Test-Path -LiteralPath $path) {
        throw "Fresh certificate installation refuses pre-existing material: $path"
    }
}
& $openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out $caKey
if ($LASTEXITCODE -ne 0) { throw 'OpenSSL CA key generation failed' }
& $openssl req -config $opensslConfig -x509 -new -sha256 -days 3650 -key $caKey -subj '/CN=Local RAG Private CA' `
    -addext 'basicConstraints=critical,CA:TRUE' `
    -addext 'keyUsage=critical,keyCertSign,cRLSign' -out $caCert
if ($LASTEXITCODE -ne 0) { throw 'OpenSSL CA generation failed' }
Copy-Item -LiteralPath $caCert -Destination (Join-Path $CertificateRoot 'loopback-ca.crt') -Force

function New-RagLeaf {
    param([string]$Key,[string]$Csr,[string]$Cert,[string]$CommonName,[string]$San,[string]$Usage)
    & $openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out $Key
    if ($LASTEXITCODE -ne 0) { throw "OpenSSL key generation failed for $CommonName" }
    & $openssl req -config $opensslConfig -new -key $Key -subj "/CN=$CommonName" -out $Csr
    if ($LASTEXITCODE -ne 0) { throw "OpenSSL CSR generation failed for $CommonName" }
    @("basicConstraints=critical,CA:FALSE","subjectAltName=$San","extendedKeyUsage=$Usage","keyUsage=digitalSignature,keyEncipherment") |
        Set-Content -LiteralPath $extensions -Encoding ascii
    & $openssl x509 -req -sha256 -days 825 -in $Csr -CA $caCert -CAkey $caKey -CAcreateserial -extfile $extensions -out $Cert
    if ($LASTEXITCODE -ne 0) { throw "OpenSSL signing failed for $CommonName" }
}
New-RagLeaf $serverKey $serverCsr $serverCert 'rag.home.arpa' 'DNS:rag.home.arpa' 'serverAuth'
New-RagLeaf $apiKey $apiCsr $apiCert 'rag-api-loopback' 'DNS:rag-api-loopback,IP:127.0.0.1,IP:::1' 'serverAuth'
New-RagLeaf $clientKey $clientCsr $clientCert 'caddy-api-client' 'DNS:caddy-api-client' 'clientAuth'
New-RagLeaf $supervisorKey $supervisorCsr $supervisorCert 'supervisor-api-client' 'DNS:supervisor-api-client' 'clientAuth'

Import-Certificate -FilePath $caCert -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
Remove-Item -LiteralPath $serverCsr,$apiCsr,$clientCsr,$supervisorCsr,$extensions -Force -ErrorAction SilentlyContinue
