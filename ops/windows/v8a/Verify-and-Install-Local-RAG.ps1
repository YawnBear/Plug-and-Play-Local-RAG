[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Medium')]
param(
    [string]$AssetRoot = $PSScriptRoot,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'LocalRAG\Personal'),
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'LocalRAGData')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$expected = @(
    'Local-RAG-Personal.zip', 'SBOM.cdx.json', 'SHA256SUMS',
    'release-trust-metadata.json', 'Verify-and-Install-Local-RAG.ps1',
    'Install-Local-RAG.cmd'
)
$publicKey = 'rag-release ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILoVXVly/Acr6DVBOx1M7wgYLqEJq06YEjLRTdbolGtQ'

function Get-RegularFile {
    param([Parameter(Mandatory)][string]$Path, [int64]$MaximumBytes = 24GB)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.Length -lt 1 -or $item.Length -gt $MaximumBytes) {
        throw "Release asset is not a bounded regular file: $($item.Name)"
    }
    return $item
}

function Assert-ManifestSignature {
    param([string]$Manifest,[string]$Signature,[string]$AllowedSigners)
    $ssh = Join-Path ([Environment]::SystemDirectory) 'OpenSSH\ssh-keygen.exe'
    if (-not (Test-Path -LiteralPath $ssh -PathType Leaf)) {
        throw 'Windows OpenSSH is required to verify the Local RAG release.'
    }
    $bytes = [IO.File]::ReadAllBytes($Manifest)
    $process = [Diagnostics.Process]::new()
    $process.StartInfo.FileName = $ssh
    $process.StartInfo.Arguments = "-Y verify -f `"$AllowedSigners`" -I rag-release -n file -s `"$Signature`""
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardInput = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    if (-not $process.Start()) { throw 'The release signature verifier could not start.' }
    $process.StandardInput.BaseStream.Write($bytes,0,$bytes.Length)
    $process.StandardInput.Close()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw 'The Local RAG release signature is invalid.' }
}

function Expand-VerifiedArchive {
    param([string]$Archive,[string]$Destination)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        [int64]$total = 0
        $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('/','\')
            if ([string]::IsNullOrWhiteSpace($name) -or [IO.Path]::IsPathRooted($name) -or
                $name.Contains(':') -or $name -match '(^|\\)\.\.(\\|$)' -or
                -not $seen.Add($name.TrimEnd('\'))) {
                throw 'The signed release archive contains an unsafe path.'
            }
            $total += $entry.Length
            if ($total -gt 48GB) { throw 'The signed release archive expands beyond its limit.' }
        }
        if (Test-Path -LiteralPath $Destination) {
            $marker = Join-Path $Destination '.verified-archive-sha256'
            $hash = (Get-FileHash $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
            if (-not (Test-Path -LiteralPath $marker -PathType Leaf) -or
                [IO.File]::ReadAllText($marker).Trim() -cne $hash) {
                throw 'An unknown existing release directory will not be adopted.'
            }
            $expectedFiles = [Collections.Generic.HashSet[string]]::new(
                [StringComparer]::OrdinalIgnoreCase
            )
            foreach ($entry in $zip.Entries) {
                if ([string]::IsNullOrEmpty($entry.Name)) { continue }
                $relative = $entry.FullName.Replace('/','\')
                [void]$expectedFiles.Add($relative)
                $target = [IO.Path]::GetFullPath((Join-Path $Destination $relative))
                $item = Get-Item -LiteralPath $target -Force -ErrorAction Stop
                if ($item.PSIsContainer -or
                    ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                    $item.Length -ne $entry.Length) {
                    throw 'A previously expanded release file was changed.'
                }
                $archiveStream = $entry.Open()
                $fileStream = [IO.File]::OpenRead($target)
                $archiveSha = [Security.Cryptography.SHA256]::Create()
                $fileSha = [Security.Cryptography.SHA256]::Create()
                try {
                    $archiveDigest = [Convert]::ToBase64String(
                        $archiveSha.ComputeHash($archiveStream)
                    )
                    $fileDigest = [Convert]::ToBase64String(
                        $fileSha.ComputeHash($fileStream)
                    )
                }
                finally {
                    $archiveSha.Dispose(); $fileSha.Dispose()
                    $archiveStream.Dispose(); $fileStream.Dispose()
                }
                if ($archiveDigest -cne $fileDigest) {
                    throw 'A previously expanded release file was changed.'
                }
            }
            $actualFiles = @(
                Get-ChildItem -LiteralPath $Destination -File -Recurse -Force |
                    ForEach-Object {
                        $_.FullName.Substring($Destination.Length + 1)
                    } | Where-Object { $_ -cne '.verified-archive-sha256' }
            )
            if ($actualFiles.Count -ne $expectedFiles.Count -or
                @($actualFiles | Where-Object { -not $expectedFiles.Contains($_) }).Count -ne 0) {
                throw 'The existing release directory contains an unexpected file.'
            }
            return
        }
        [IO.Directory]::CreateDirectory($Destination) | Out-Null
        foreach ($entry in $zip.Entries) {
            $target = [IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName))
            if (-not $target.StartsWith($Destination + '\',[StringComparison]::OrdinalIgnoreCase)) {
                throw 'The signed release archive escapes its destination.'
            }
            if ([string]::IsNullOrEmpty($entry.Name)) {
                [IO.Directory]::CreateDirectory($target) | Out-Null
            }
            else {
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
                $input = $entry.Open()
                $output = [IO.File]::Open($target,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
                try { $input.CopyTo($output) }
                finally { $output.Dispose(); $input.Dispose() }
            }
        }
        [IO.File]::WriteAllText(
            (Join-Path $Destination '.verified-archive-sha256'),
            ((Get-FileHash $Archive -Algorithm SHA256).Hash.ToLowerInvariant()+"`n"),
            [Text.UTF8Encoding]::new($false)
        )
    }
    catch {
        if (Test-Path -LiteralPath $Destination -PathType Container) {
            [IO.Directory]::Delete($Destination,$true)
        }
        throw
    }
    finally { $zip.Dispose() }
}

$root = [IO.Path]::GetFullPath($AssetRoot)
$manifestPath = Join-Path $root 'update-manifest.json'
$signaturePath = Join-Path $root 'update-manifest.json.sig'
Get-RegularFile $manifestPath 1MB | Out-Null
Get-RegularFile $signaturePath 1MB | Out-Null
$temporarySigners = Join-Path ([IO.Path]::GetTempPath()) `
    ('local-rag-signers-' + [guid]::NewGuid().ToString('N'))
try {
    [IO.File]::WriteAllText($temporarySigners,$publicKey+"`n",[Text.UTF8Encoding]::new($false))
    Assert-ManifestSignature $manifestPath $signaturePath $temporarySigners
}
finally {
    if (Test-Path -LiteralPath $temporarySigners) { [IO.File]::Delete($temporarySigners) }
}
$manifest = [Text.UTF8Encoding]::new($false,$true).GetString(
    [IO.File]::ReadAllBytes($manifestPath)
) | ConvertFrom-Json
$fields = @($manifest.PSObject.Properties.Name | Sort-Object)
$names = @($manifest.artifacts | ForEach-Object { [string]$_.filename } | Sort-Object)
if ($manifest.schema_version -ne 1 -or ($fields -join ',') -cne `
    'artifacts,schema_version,version' -or ($names -join ',') -cne `
    (($expected | Sort-Object) -join ',')) {
    throw 'The signed Personal release artifact set is invalid.'
}
foreach ($artifact in @($manifest.artifacts)) {
    $artifactFields = @($artifact.PSObject.Properties.Name | Sort-Object)
    $path = Join-Path $root ([string]$artifact.filename)
    $item = Get-RegularFile $path
    if (($artifactFields -join ',') -cne 'filename,sha256,size' -or
        [int64]$artifact.size -ne $item.Length -or
        [string]$artifact.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        (Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne `
            [string]$artifact.sha256) {
        throw "Signed release verification failed: $($artifact.filename)"
    }
}
$trustPath = Join-Path $root 'release-trust-metadata.json'
$trust = [Text.UTF8Encoding]::new($false,$true).GetString(
    [IO.File]::ReadAllBytes($trustPath)
) | ConvertFrom-Json
$trustFields = @($trust.PSObject.Properties.Name | Sort-Object)
$expectedTrustFields = @(
    'artifacts_sha256','expires_at','issued_at','policy_id','release_id',
    'release_sequence','revoked_profile_ids','revoked_release_ids','root_id',
    'schema_version'
) | Sort-Object
$issued = [DateTimeOffset]::MinValue
$expires = [DateTimeOffset]::MinValue
if (
    ($trustFields -join ',') -cne ($expectedTrustFields -join ',') -or
    $trust.schema_version -ne 1 -or
    [string]$trust.policy_id -cne 'local-rag-v8-release-trust' -or
    [string]$trust.root_id -cne 'rag-root-v8' -or
    [string]$trust.release_id -cnotmatch '^[a-z0-9][a-z0-9._-]{5,127}$' -or
    $trust.release_sequence -isnot [int] -or [int]$trust.release_sequence -lt 1 -or
    -not [DateTimeOffset]::TryParseExact([string]$trust.issued_at,
        'yyyy-MM-ddTHH:mm:ssZ',[Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal,[ref]$issued) -or
    -not [DateTimeOffset]::TryParseExact([string]$trust.expires_at,
        'yyyy-MM-ddTHH:mm:ssZ',[Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal,[ref]$expires) -or
    $issued -gt [DateTimeOffset]::UtcNow -or $expires -le [DateTimeOffset]::UtcNow -or
    $expires -le $issued -or ($expires - $issued).TotalDays -gt 30 -or
    @($trust.revoked_release_ids) -contains [string]$trust.release_id
) {
    throw 'The signed Personal release trust metadata is invalid, expired, or revoked.'
}
$archive = Join-Path $root 'Local-RAG-Personal.zip'
$archiveHash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
$releaseParent = Join-Path $env:LOCALAPPDATA 'LocalRAG\Releases'
$releaseRoot = Join-Path $releaseParent $archiveHash
if (-not (Test-Path $releaseParent)) { [IO.Directory]::CreateDirectory($releaseParent) | Out-Null }
if (-not $PSCmdlet.ShouldProcess($releaseRoot,'Verify, expand, and install Local RAG Personal')) {
    return
}
Expand-VerifiedArchive -Archive $archive -Destination $releaseRoot
$contractRoot = Join-Path $releaseRoot 'ops\windows\v8a'
$contractNames = @('personal-release.json','product-profiles.json','capability-profiles.json')
$trustContractNames = @($trust.artifacts_sha256.PSObject.Properties.Name | Sort-Object)
if (($trustContractNames -join ',') -cne (($contractNames | Sort-Object) -join ',')) {
    throw 'The signed Personal release contract set is invalid.'
}
foreach ($name in $contractNames) {
    $path = Join-Path $contractRoot $name
    if ((Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne
        [string]$trust.artifacts_sha256.$name) {
        throw "The verified Personal release contract differs: $name"
    }
}
& (Join-Path ([Environment]::SystemDirectory) 'icacls.exe') $releaseRoot `
    /inheritance:r /grant:r `
    "*$([Security.Principal.WindowsIdentity]::GetCurrent().User.Value):(OI)(CI)(F)" `
    '*S-1-5-18:(OI)(CI)(F)' /T /C | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'The verified release directory could not be protected.' }
& (Join-Path $releaseRoot 'ops\windows\v8a\Install-RagPersonal.ps1') `
    -InstallRoot $InstallRoot -DataRoot $DataRoot -ReleaseRoot $releaseRoot
if ($LASTEXITCODE -ne 0) { throw 'Local RAG installation stopped safely.' }
