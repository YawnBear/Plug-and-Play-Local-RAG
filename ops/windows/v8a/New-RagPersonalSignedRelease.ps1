[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$PayloadRoot,
    [Parameter(Mandatory)][string]$OutputRoot,
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._-]{1,64}$')][string]$Version,
    [Parameter(Mandatory)][ValidateRange(1,2147483647)][int]$ReleaseSequence,
    [Parameter(Mandatory)][string]$PrivateKeyPath,
    [Parameter(Mandatory)][string]$ValidationPython,
    [Parameter(Mandatory)][string]$SignedArtifactStageRoot,
    [ValidateRange(1,30)][int]$MetadataLifetimeDays = 14
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
Import-Module (Join-Path $PSScriptRoot 'RagPersonalPayload.psm1') -Force
$payload = (Resolve-Path -LiteralPath $PayloadRoot).Path
$output = [IO.Path]::GetFullPath($OutputRoot)
$privateKey = (Resolve-Path -LiteralPath $PrivateKeyPath).Path
if (Test-Path -LiteralPath (Join-Path $payload '.git')) {
    throw 'PayloadRoot must be an assembled package, not a source checkout.'
}
$forbiddenPayload = @(Get-ChildItem -LiteralPath $payload -Recurse -Force -File |
    Where-Object {
        $_.Name -ceq '.env' -or $_.Name -like '*.key' -or
        $_.Name -like '*.pfx' -or
        $_.Name -ceq 'installation-secrets.json' -or
        $_.Name -ceq 'installation-journal.json'
    } | Select-Object -First 1)
if ($forbiddenPayload.Count -gt 0) {
    throw "The assembled payload contains forbidden private state: $($forbiddenPayload[0].Name)"
}
$assembledManifestPath = Join-Path $payload 'ops\windows\v8a\personal-release.json'
if (-not (Test-Path -LiteralPath $assembledManifestPath -PathType Leaf)) {
    throw 'The assembled Personal payload contract is missing.'
}
$assembledManifest = Get-Content -Raw -LiteralPath $assembledManifestPath | ConvertFrom-Json
if ($assembledManifest.payload_state -cne 'assembled_unsigned') {
    throw 'Personal signing requires an assembled_unsigned payload.'
}
$null = Test-RagPersonalPayloadInventory -Root $payload
if ($privateKey.StartsWith($repositoryRoot + '\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'The offline release private key must not reside in the repository.'
}
$keyItem = Get-Item -LiteralPath $privateKey -Force
$keyAcl = Get-Acl -LiteralPath $privateKey
if ($keyItem.PSIsContainer -or
    ($keyItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    -not $keyAcl.AreAccessRulesProtected -or
    $keyAcl.Owner -cnotin @('NT AUTHORITY\SYSTEM','BUILTIN\Administrators') -or
    @($keyAcl.Access | Where-Object {
        $_.IsInherited -or $_.AccessControlType -cne 'Allow' -or
        $_.IdentityReference.Value -cnotin @(
            'NT AUTHORITY\SYSTEM','BUILTIN\Administrators'
        )
    }).Count -ne 0) {
    throw 'The offline Personal signing key is not a protected regular file.'
}
if (Test-Path -LiteralPath $output) {
    if (@(Get-ChildItem -LiteralPath $output -Force).Count -gt 0) {
        throw 'The Personal signed-release output folder must be empty.'
    }
}
else { [IO.Directory]::CreateDirectory($output) | Out-Null }

$requiredPayload = @(
    'LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','MODEL_LICENSES.md',
    'ops\windows\release-allowed-signers',
    'ops\windows\v8a\personal-release.json',
    'ops\windows\v8a\product-profiles.json',
    'ops\windows\v8a\capability-profiles.json',
    'ops\windows\v8a\Install-Local-RAG.cmd',
    'ops\windows\v8a\Verify-and-Install-Local-RAG.ps1',
    'ops\windows\v8a\Check-for-Updates.cmd',
    'ops\windows\v8a\Uninstall-Local-RAG.cmd',
    'personal-payload-inventory.json'
)
foreach ($relative in $requiredPayload) {
    if (-not (Test-Path -LiteralPath (Join-Path $payload $relative) -PathType Leaf)) {
        throw "The assembled Personal payload is missing: $relative"
    }
}
$signers = Join-Path $payload 'ops\windows\release-allowed-signers'
if ((Get-FileHash $signers -Algorithm SHA256).Hash.ToLowerInvariant() -cne
    'c3dec800e21c240031dff4ab9d5e22625dd1841ac8a536b56f9267c97d06acb2') {
    throw 'The assembled Personal payload has an unexpected signing trust root.'
}
$staging = Join-Path $output ('.payload-' + [guid]::NewGuid().ToString('N'))
if (-not $PSCmdlet.ShouldProcess($output,"Build and sign Local RAG Personal $Version")) {
    return
}
try {
    [IO.Directory]::CreateDirectory($staging) | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $payload -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination $staging -Recurse -Force
    }
    $contractRoot = Join-Path $staging 'ops\windows\v8a'
    $personalPath = Join-Path $contractRoot 'personal-release.json'
    $personal = Get-Content -Raw -LiteralPath $personalPath | ConvertFrom-Json
    $personal.payload_state = 'packaged'
    [IO.File]::WriteAllText(
        $personalPath,
        (($personal | ConvertTo-Json -Depth 12) + "`n"),
        [Text.UTF8Encoding]::new($false)
    )
    foreach ($artifact in @($personal.artifacts)) {
        $artifactPath = Join-Path $staging ([string]$artifact.relative_path)
        $exists = if ([string]$artifact.kind -ceq 'directory') {
            Test-Path -LiteralPath $artifactPath -PathType Container
        } else { Test-Path -LiteralPath $artifactPath -PathType Leaf }
        if (-not $exists) {
            throw "The assembled Personal payload artifact is missing: $($artifact.artifact_id)"
        }
    }
    & $ValidationPython -B (Join-Path $contractRoot 'validate_contracts.py')
    if ($LASTEXITCODE -ne 0) { throw 'Packaged Personal contracts failed validation.' }

    $issued = [DateTimeOffset]::UtcNow
    $expires = $issued.AddDays($MetadataLifetimeDays)
    $trust = [ordered]@{
        schema_version=1; policy_id='local-rag-v8-release-trust'; root_id='rag-root-v8'
        release_id=('personal-' + $Version.ToLowerInvariant())
        release_sequence=$ReleaseSequence
        issued_at=$issued.ToString('yyyy-MM-ddTHH:mm:ssZ')
        expires_at=$expires.ToString('yyyy-MM-ddTHH:mm:ssZ')
        artifacts_sha256=[ordered]@{
            'personal-release.json'=(Get-FileHash $personalPath -Algorithm SHA256).Hash.ToLowerInvariant()
            'product-profiles.json'=(Get-FileHash (Join-Path $contractRoot 'product-profiles.json') -Algorithm SHA256).Hash.ToLowerInvariant()
            'capability-profiles.json'=(Get-FileHash (Join-Path $contractRoot 'capability-profiles.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        revoked_release_ids=@(); revoked_profile_ids=@()
    }
    $trustInside = Join-Path $staging 'release-trust-metadata.json'
    [IO.File]::WriteAllText($trustInside,(($trust | ConvertTo-Json -Depth 6 -Compress)+"`n"),
        [Text.UTF8Encoding]::new($false))
    & $ValidationPython -B (Join-Path $repositoryRoot 'ops\windows\validate_json_schema.py') `
        (Join-Path $contractRoot 'release-trust-metadata.schema.json') $trustInside
    if ($LASTEXITCODE -ne 0) { throw 'Release trust metadata failed schema validation.' }
    $null = New-RagPersonalPayloadInventory -Root $staging
    Test-RagPersonalPayloadInventory -Root $staging | Out-Null

    & $ValidationPython -B (Join-Path $repositoryRoot 'ops\release\generate_v8f_artifacts.py') --check
    if ($LASTEXITCODE -ne 0) { throw 'The checked-in release SBOM is stale.' }
    Copy-Item (Join-Path $repositoryRoot 'SBOM.cdx.json') (Join-Path $output 'SBOM.cdx.json')
    Copy-Item $trustInside (Join-Path $output 'release-trust-metadata.json')
    Copy-Item (Join-Path $contractRoot 'Verify-and-Install-Local-RAG.ps1') `
        (Join-Path $output 'Verify-and-Install-Local-RAG.ps1')
    Copy-Item (Join-Path $contractRoot 'Install-Local-RAG.cmd') `
        (Join-Path $output 'Install-Local-RAG.cmd')
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = Join-Path $output 'Local-RAG-Personal.zip'
    [IO.Compression.ZipFile]::CreateFromDirectory(
        $staging,$archive,[IO.Compression.CompressionLevel]::Optimal,$false
    )
    & $ValidationPython (Join-Path $repositoryRoot 'ops\release\generate_v8f_artifacts.py') `
        --release-root $output
    if ($LASTEXITCODE -ne 0) { throw 'Release checksum generation failed.' }

    $names = @('Local-RAG-Personal.zip','SBOM.cdx.json','SHA256SUMS',
        'release-trust-metadata.json','Verify-and-Install-Local-RAG.ps1',
        'Install-Local-RAG.cmd')
    $artifacts = @($names | ForEach-Object {
        $path = Join-Path $output $_
        [ordered]@{filename=$_;sha256=(Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant();size=(Get-Item $path).Length}
    })
    $manifestPath = Join-Path $output 'update-manifest.json'
    [IO.File]::WriteAllText($manifestPath,(([ordered]@{
        schema_version=1;version=$Version;artifacts=$artifacts
    } | ConvertTo-Json -Depth 6 -Compress)+"`n"),[Text.UTF8Encoding]::new($false))
    & $ValidationPython -B (Join-Path $repositoryRoot 'ops\windows\validate_json_schema.py') `
        (Join-Path $repositoryRoot 'ops\windows\update-manifest.schema.json') $manifestPath
    if ($LASTEXITCODE -ne 0) { throw 'Personal update manifest failed schema validation.' }
    $ssh = Join-Path ([Environment]::SystemDirectory) 'OpenSSH\ssh-keygen.exe'
    & $ssh -Y sign -f $privateKey -n file $manifestPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$manifestPath.sig" -PathType Leaf)) {
        throw 'Offline Personal release signing failed.'
    }
    $verification = & (Join-Path $repositoryRoot 'ops\windows\Test-RagUpdate.ps1') `
        -Manifest $manifestPath -Signature "$manifestPath.sig" -ArtifactRoot $output `
        -SignedArtifactStageRoot $SignedArtifactStageRoot -CleanupOnSuccess -CleanupOnFailure |
        ConvertFrom-Json
    if ($verification.result -cne 'pass') { throw 'Signed Personal release self-check failed.' }
    [pscustomobject]@{
        result='signed';profile='personal';version=$Version
        release_sequence=$ReleaseSequence;artifact_root=$output
        signature_verified=$true;sbom='SBOM.cdx.json';checksums='SHA256SUMS'
    } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $staging -PathType Container) {
        [IO.Directory]::Delete($staging,$true)
    }
}
