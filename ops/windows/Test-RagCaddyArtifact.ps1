[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Artifact,
    [Parameter(Mandatory)]
    [string]$ReleaseManifest,
    [Parameter(Mandatory)]
    [string]$ExtractedExecutable
)

$ErrorActionPreference = 'Stop'
$release = Get-Content -LiteralPath $ReleaseManifest -Raw | ConvertFrom-Json
if ($release.schema_version -ne 2 -or $release.state -ne 'configured') {
    throw 'Caddy release is not configured'
}
if ($release.archive_sha512 -notmatch '^[0-9a-f]{128}$' -or
    $release.executable_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]::IsNullOrWhiteSpace($release.version)) {
    throw 'Caddy release pin is invalid'
}
$actualArchive = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA512).Hash.ToLowerInvariant()
if ($actualArchive -cne $release.archive_sha512) {
    throw 'Caddy archive SHA-512 mismatch'
}
$actualExecutable = (Get-FileHash -LiteralPath $ExtractedExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualExecutable -cne $release.executable_sha256) {
    throw 'Extracted Caddy executable SHA-256 mismatch'
}
[pscustomobject]@{
    result = 'pass'
    version = $release.version
    archive_sha512 = $actualArchive
    executable_sha256 = $actualExecutable
    changes_applied = $false
} | ConvertTo-Json
