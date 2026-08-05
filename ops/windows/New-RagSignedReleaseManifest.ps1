[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidateSet('Preliminary','Final')][string]$Mode,
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._-]{1,64}$')][string]$Version,
    [Parameter(Mandatory)][string]$ArtifactRoot,
    [Parameter(Mandatory)][string]$PrivateKeyPath,
    [Parameter(Mandatory)][string]$ValidationPython,
    [Parameter(Mandatory)][string]$SignedArtifactStageRoot,
    [string]$PreliminaryManifestPath
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$privateKey = (Resolve-Path -LiteralPath $PrivateKeyPath).Path
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if ($privateKey.StartsWith($repositoryRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Offline release private key must not reside in the repository'
}
$keyItem = Get-Item -LiteralPath $privateKey
if ($keyItem.PSIsContainer -or
    ($keyItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Offline release private key must be a regular non-reparse file'
}
$preliminaryNames = @(
    'Caddyfile','RagSupervisorService.exe','caddy.exe','csp-header.caddy',
    'deployment.json','local-rag-release.zip','release-evidence.json',
    'verify_dependencies.py'
) | Sort-Object
$finalNames = @(
    'Caddyfile','RagSupervisorService.exe','caddy.exe','csp-header.caddy',
    'dependency-evidence.json','deployment.json','local-rag-release.zip',
    'release-evidence.json','verify_dependencies.py'
) | Sort-Object
$expectedNames = if ($Mode -ceq 'Preliminary') { $preliminaryNames } else { $finalNames }
if ($Mode -ceq 'Final' -and [string]::IsNullOrWhiteSpace($PreliminaryManifestPath)) {
    throw 'Final signing requires the exact preliminary complete-release manifest'
}
if ($Mode -ceq 'Preliminary' -and -not [string]::IsNullOrWhiteSpace($PreliminaryManifestPath)) {
    throw 'PreliminaryManifestPath is valid only for Final signing'
}
$actualNames = @(
    Get-ChildItem -LiteralPath $root -File -Force |
        Where-Object Name -notin @('update-manifest.json','update-manifest.json.sig') |
        ForEach-Object Name |
        Sort-Object
)
if (($actualNames -join "`n") -cne ($expectedNames -join "`n")) {
    throw "$Mode release artifact set is not exact"
}
$keyAcl = Get-Acl -LiteralPath $privateKey
if (-not $keyAcl.AreAccessRulesProtected -or
    $keyAcl.Owner -cnotin @('NT AUTHORITY\SYSTEM','BUILTIN\Administrators') -or
    @($keyAcl.Access | Where-Object {
        $_.IsInherited -or $_.AccessControlType -cne 'Allow' -or
        $_.IdentityReference.Value -cnotin @(
            'NT AUTHORITY\SYSTEM','BUILTIN\Administrators'
        )
    }).Count -ne 0) {
    throw 'Offline release private key ACL is not protected'
}
foreach ($name in $expectedNames) {
    $path = Join-Path $root $name
    $item = Get-Item -LiteralPath $path
    if ($item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.Length -lt 1 -or
        $name -cmatch '(?i)(^|[._-])(secret|credentials?|password|private[-_]?key)([._-]|$)') {
        throw "Release signing input is unsafe: $name"
    }
}
$evidencePath = Join-Path $root 'release-evidence.json'
& $ValidationPython (Join-Path $PSScriptRoot 'validate_json_schema.py') `
    (Join-Path $PSScriptRoot 'release-evidence.schema.json') $evidencePath
if ($LASTEXITCODE -ne 0) { throw 'Release evidence failed schema validation before signing' }
$evidenceText = Get-Content -Raw -LiteralPath $evidencePath
if ($Mode -ceq 'Final' -and $evidenceText -match '(?<![0-9a-f])0{64}(?![0-9a-f])') {
    throw 'Final release evidence contains a silent zero digest placeholder'
}
if ($Mode -ceq 'Final') {
    $preliminaryManifest = (Resolve-Path -LiteralPath $PreliminaryManifestPath).Path
    $preliminaryManifestItem = Get-Item -LiteralPath $preliminaryManifest
    if ($preliminaryManifestItem.PSIsContainer -or
        ($preliminaryManifestItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Preliminary complete-release manifest must be a regular non-reparse file'
    }
    & $ValidationPython (Join-Path $PSScriptRoot 'validate_json_schema.py') `
        (Join-Path $PSScriptRoot 'update-manifest.schema.json') $preliminaryManifest
    if ($LASTEXITCODE -ne 0) {
        throw 'Preliminary complete-release manifest failed schema validation'
    }
    $preliminaryDocument = Get-Content -Raw -LiteralPath $preliminaryManifest |
        ConvertFrom-Json
    $preliminaryArtifacts = @($preliminaryDocument.artifacts)
    if ($preliminaryArtifacts.Count -ne $preliminaryNames.Count) {
        throw 'Preliminary complete-release manifest does not contain the exact eight-artifact set'
    }
    foreach ($name in $preliminaryNames) {
        $preliminaryArtifact = @(
            $preliminaryArtifacts | Where-Object filename -ceq $name
        )
        if ($preliminaryArtifact.Count -ne 1) {
            throw "Preliminary complete-release manifest artifact set is not exact: $name"
        }
        $currentPath = Join-Path $root $name
        if ($preliminaryArtifact[0].sha256 -cne (
                Get-FileHash -LiteralPath $currentPath -Algorithm SHA256
            ).Hash.ToLowerInvariant() -or
            [int64]$preliminaryArtifact[0].size -ne (Get-Item -LiteralPath $currentPath).Length) {
            throw "Final base artifact differs from preliminary complete-release manifest: $name"
        }
    }
    $dependencyEvidence = Get-Content -Raw -LiteralPath (
        Join-Path $root 'dependency-evidence.json'
    ) | ConvertFrom-Json
    $dependencyFields = @($dependencyEvidence.PSObject.Properties.Name | Sort-Object)
    $expectedDependencyFields = @(
        'captured_at','changes_applied','checks','machine_fingerprint','mode','mutation_scope',
        'preliminary_manifest_sha256','release_evidence_sha256','result',
        'schema_version','verifier_sha256'
    ) | Sort-Object
    if ($dependencyEvidence.result -cne 'pass' -or
        ($dependencyFields -join ',') -cne ($expectedDependencyFields -join ',') -or
        @($dependencyEvidence.checks | Where-Object result -cne 'pass').Count -ne 0 -or
        $dependencyEvidence.release_evidence_sha256 -cne (
            Get-FileHash -LiteralPath $evidencePath -Algorithm SHA256
        ).Hash.ToLowerInvariant() -or
        $dependencyEvidence.verifier_sha256 -cne (
            Get-FileHash -LiteralPath (Join-Path $root 'verify_dependencies.py') `
                -Algorithm SHA256
        ).Hash.ToLowerInvariant() -or
        $dependencyEvidence.preliminary_manifest_sha256 -cne (
            Get-FileHash -LiteralPath $preliminaryManifest -Algorithm SHA256
        ).Hash.ToLowerInvariant()) {
        throw 'Final release requires passing dependency evidence'
    }
    . (Join-Path $PSScriptRoot 'RagHostBinding.ps1')
    $releaseDocument = Get-Content -Raw -LiteralPath $evidencePath | ConvertFrom-Json
    Assert-RagFreshHostEvidence -Evidence $dependencyEvidence `
        -MaxAgeSeconds $releaseDocument.max_evidence_age_seconds
}
$artifacts = @(
    foreach ($name in $expectedNames) {
        $path = Join-Path $root $name
        [ordered]@{
            filename=$name
            sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            size=(Get-Item -LiteralPath $path).Length
        }
    }
)
$manifest = [ordered]@{
    schema_version=1
    version=$Version
    artifacts=$artifacts
}
$manifestPath = Join-Path $root 'update-manifest.json'
$signaturePath = "$manifestPath.sig"
foreach ($path in @($manifestPath,$signaturePath)) {
    if (Test-Path -LiteralPath $path) { throw "Refusing to overwrite signed release output: $path" }
}
if (-not $PSCmdlet.ShouldProcess($manifestPath, "Create and sign $Mode release manifest")) {
    return
}
[IO.File]::WriteAllText(
    $manifestPath,
    (($manifest | ConvertTo-Json -Depth 6 -Compress) + "`n"),
    [Text.UTF8Encoding]::new($false)
)
& $ValidationPython (Join-Path $PSScriptRoot 'validate_json_schema.py') `
    (Join-Path $PSScriptRoot 'update-manifest.schema.json') $manifestPath
if ($LASTEXITCODE -ne 0) { throw 'Update manifest failed schema validation' }
$sshKeygen = Join-Path ([Environment]::SystemDirectory) 'OpenSSH\ssh-keygen.exe'
& $sshKeygen -Y sign -f $privateKey -n file $manifestPath
if ($LASTEXITCODE -ne 0 -or
    -not (Test-Path -LiteralPath $signaturePath -PathType Leaf)) {
    throw 'Offline Ed25519 manifest signing failed'
}
$verification = & (Join-Path $PSScriptRoot 'Test-RagUpdate.ps1') `
    -Manifest $manifestPath -Signature $signaturePath -ArtifactRoot $root `
    -SignedArtifactStageRoot $SignedArtifactStageRoot `
    -CleanupOnFailure -CleanupOnSuccess | ConvertFrom-Json
if ($verification.result -cne 'pass') {
    throw 'Signed release self-verification failed'
}
[pscustomobject]@{
    result='signed'
    mode=$Mode.ToLowerInvariant()
    manifest=$manifestPath
    signature=$signaturePath
    self_verification_stage_cleaned=$true
} | ConvertTo-Json
