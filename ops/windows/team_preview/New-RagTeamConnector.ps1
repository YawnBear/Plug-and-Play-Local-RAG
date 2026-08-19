[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [Alias('OutputRoot')] [string]$OutputDirectory,
    [Parameter(Mandatory = $true)] [Alias('CaCertificate')] [string]$CertificatePath,
    [Parameter(Mandatory = $true)] [Alias('LocalAddress')] [string]$LanIPv4,
    [ValidatePattern('^[0-9a-fA-F-]{36}$')]
    [string]$InstallationId = ([guid]::NewGuid().ToString('D')),
    [ValidateRange(1, 2147483647)] [int]$ConnectorGeneration = 1,
    [string]$ExpectedSubject = 'CN=Local RAG Private CA',
    [string]$ZipPath,
    [switch]$Plan
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Normalize-Rfc1918IPv4([string]$Value) {
    $address = $null
    if (-not [System.Net.IPAddress]::TryParse($Value, [ref]$address) -or
        $address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        throw 'LanIPv4 must be an IPv4 address.'
    }
    $bytes = $address.GetAddressBytes()
    $private = (($bytes[0] -eq 10) -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168))
    if (-not $private -or $address.Equals([System.Net.IPAddress]::Loopback) -or
        ($bytes[0] -eq 169 -and $bytes[1] -eq 254)) {
        throw 'LanIPv4 must be an RFC1918 private IPv4 address.'
    }
    return $address.ToString()
}

function Get-RelativeFiles([string]$Root) {
    $resolvedPath = Resolve-Path -LiteralPath $Root
    if ($resolvedPath.Provider.Name -cne 'FileSystem') { throw 'Connector root must use the filesystem provider.' }
    $resolvedRoot = [IO.Path]::GetFullPath($resolvedPath.ProviderPath)
    $rootPrefix = $resolvedRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $seen = @{}
    foreach ($item in Get-ChildItem -LiteralPath $resolvedRoot -Recurse -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point is not allowed: $($item.FullName)" }
        if (-not $item.PSIsContainer) {
            $fullPath = [IO.Path]::GetFullPath($item.FullName)
            if (-not $fullPath.StartsWith(
                    $rootPrefix,
                    [StringComparison]::OrdinalIgnoreCase
                )) {
                throw "Connector file escapes its root: $($item.FullName)"
            }
            $relative = $fullPath.Substring($rootPrefix.Length).Replace('\', '/')
            if ($relative -ieq 'inventory.json') { continue }
            $key = $relative.ToLowerInvariant()
            if ($seen.ContainsKey($key)) { throw "Case-colliding connector path: $relative" }
            $seen[$key] = $true
            if ($relative -match '(?i)(private[_-]?key|secret|\.pfx$|\.p12$|\.key$)') { throw "Private material is not allowed: $relative" }
            $bytes = [IO.File]::ReadAllBytes($fullPath)
            $text = [Text.Encoding]::ASCII.GetString($bytes)
            if ($text -match '-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----') { throw "Private material is not allowed: $relative" }
            [pscustomobject]@{ Path = $relative; Length = $bytes.Length; Sha256 = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant() }
        }
    }
}

function Write-Inventory([string]$Root) {
    $entries = @(Get-RelativeFiles -Root $Root | Sort-Object Path)
    $lines = @($entries | ForEach-Object { "$($_.Path)|$($_.Length)|$($_.Sha256)" })
    $tree = [Security.Cryptography.SHA256]::Create()
    $treeHash = ([BitConverter]::ToString($tree.ComputeHash([Text.Encoding]::UTF8.GetBytes(($lines -join "`n") + "`n")))).Replace('-', '').ToLowerInvariant()
    $manifest = [ordered]@{ version = 1; files = $entries; tree_sha256 = $treeHash }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Root 'inventory.json') -Encoding UTF8
}

$normalizedIp = Normalize-Rfc1918IPv4 $LanIPv4
$resolvedCertificate = (Resolve-Path -LiteralPath $CertificatePath).Path
$certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new($resolvedCertificate)
if ($certificate.HasPrivateKey) { throw 'The connector certificate must not contain a private key.' }
if ($certificate.Subject -ne $ExpectedSubject) { throw "Unexpected CA subject: $($certificate.Subject)" }
$basic = $certificate.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.19' }
if (-not $basic -or -not ([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]$basic).CertificateAuthority) { throw 'The connector certificate must be a CA certificate.' }

$scriptRoot = Split-Path -Parent $PSCommandPath
$target = [IO.Path]::GetFullPath($OutputDirectory)
$zip = if ($ZipPath) { [IO.Path]::GetFullPath($ZipPath) } else { $null }
$certificateSha256 = (Get-FileHash -LiteralPath $resolvedCertificate -Algorithm SHA256).Hash.ToLowerInvariant()
$normalizedInstallationId = ([guid]::Parse($InstallationId)).ToString('D')
$publicFiles = @('connector.json', 'rag-local-ca.cer', 'inventory.json', 'Install-RagTeamConnector.ps1', 'Uninstall-RagTeamConnector.ps1', 'Install-RagTeamConnector.cmd', 'Uninstall-RagTeamConnector.cmd', 'Connect-to-Local-RAG.cmd', 'Disconnect-from-Local-RAG.cmd')
$planResult = [pscustomobject]@{ OutputDirectory = $target; ZipPath = $zip; LanIPv4 = $normalizedIp; InstallationId = $normalizedInstallationId; ConnectorGeneration = $ConnectorGeneration; Subject = $certificate.Subject; CertificateSha256 = $certificateSha256; Thumbprint = $certificate.Thumbprint.ToUpperInvariant(); Files = $publicFiles }
if ($Plan) { $planResult | ConvertTo-Json -Depth 4; return }

if (Test-Path -LiteralPath $target) { throw "Output directory already exists: $target" }
New-Item -ItemType Directory -Path $target -Force | Out-Null
Copy-Item -LiteralPath $resolvedCertificate -Destination (Join-Path $target 'rag-local-ca.cer')
foreach ($name in @('Install-RagTeamConnector.ps1', 'Uninstall-RagTeamConnector.ps1', 'Install-RagTeamConnector.cmd', 'Uninstall-RagTeamConnector.cmd', 'Connect-to-Local-RAG.cmd', 'Disconnect-from-Local-RAG.cmd')) { Copy-Item -LiteralPath (Join-Path $scriptRoot $name) -Destination (Join-Path $target $name) }
[ordered]@{ version = 1; installation_id = $normalizedInstallationId; connector_generation = $ConnectorGeneration; host = 'rag.home.arpa'; lan_ipv4 = $normalizedIp; ca_subject = $certificate.Subject; ca_sha256 = $certificateSha256; ca_thumbprint = $certificate.Thumbprint.ToUpperInvariant(); unsigned_connector = $true } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target 'connector.json') -Encoding UTF8
Write-Inventory -Root $target
if ($zip) {
    if (Test-Path -LiteralPath $zip) { throw "ZIP output already exists: $zip" }
    $archiveFiles = @(Get-ChildItem -LiteralPath $target -File | ForEach-Object FullName)
    Compress-Archive -Path $archiveFiles -DestinationPath $zip -CompressionLevel Optimal
}
$planResult | ConvertTo-Json -Depth 4
