[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$SourceRoot,
    [Parameter(Mandatory)][string]$ApiRuntimeRoot,
    [Parameter(Mandatory)][string]$OcrRuntimeRoot,
    [Parameter(Mandatory)][string]$NodeRuntimeRoot,
    [Parameter(Mandatory)][string]$OpenSslRuntimeRoot,
    [Parameter(Mandatory)][string]$RerankerModelRoot,
    [Parameter(Mandatory)][string]$OcrModelRoot,
    [Parameter(Mandatory)][string]$CaddyExecutable,
    [Parameter(Mandatory)][string]$CspArtifact,
    [Parameter(Mandatory)][string]$ServiceHostExecutable,
    [Parameter(Mandatory)][ValidatePattern('^sha256:[0-9a-f]{64}$')][string]$PostgresImageDigest,
    [Parameter(Mandatory)][ValidatePattern('^sha256:[0-9a-f]{64}$')][string]$RustfsImageDigest,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$RustfsProbeObjectSha256,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$QwenGenerationDigest,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$QwenEmbeddingDigest,
    [Parameter(Mandatory)][string]$PaddleOcrVersion,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$OcrFixtureSha256,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$OcrOutputSha256,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$OcrStructuredSha256,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$OcrTextSha256,
    [Parameter(Mandatory)][ValidateRange(1,10000)][int]$OcrPageCount,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$DockerExecutableSha256,
    [Parameter(Mandatory)][string]$OutputRoot
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Expand-RagVerifiedRelease.ps1')

function Test-RagDeniedReleaseName {
    param([Parameter(Mandatory)][string]$Name)
    return (
        $Name -ceq '.env' -or
        $Name -clike '.env.*' -or
        $Name -cmatch '(?i)^(credentials|secrets?|password)\.(json|txt|ini|yaml|yml)$' -or
        $Name -cmatch '(?i)^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$' -or
        $Name -cmatch '(?i)\.(key|pfx|p12)$'
    )
}

function Assert-RagReleaseSourceTree {
    param([Parameter(Mandatory)][string]$Root)
    $item = Get-Item -LiteralPath (Resolve-Path -LiteralPath $Root).Path
    if (-not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Release source must be a regular directory: $Root"
    }
    foreach ($entry in Get-ChildItem -LiteralPath $item.FullName -Recurse -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Release source contains a reparse point: $($entry.FullName)"
        }
        if (-not $entry.PSIsContainer -and (Test-RagDeniedReleaseName $entry.Name)) {
            throw "Release source contains a denied secret-shaped filename: $($entry.FullName)"
        }
    }
}

function Copy-RagReleaseTree {
    param([Parameter(Mandatory)][string]$Source,[Parameter(Mandatory)][string]$Destination)
    Assert-RagReleaseSourceTree $Source
    $resolvedSource = (Resolve-Path -LiteralPath $Source).Path
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($entry in Get-ChildItem -LiteralPath $resolvedSource -Recurse -Force) {
        $relative = $entry.FullName.Substring($resolvedSource.Length).TrimStart('\')
        if (@($relative -split '\\') -match '^(__pycache__|\.pytest_cache|\.mypy_cache)$' -or
            $entry.Extension -cin @('.pyc','.pyo')) {
            continue
        }
        $target = Join-Path $Destination $relative
        if ($entry.PSIsContainer) {
            New-Item -ItemType Directory -Path $target -Force | Out-Null
        } else {
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath $entry.FullName -Destination $target
        }
    }
}

function Copy-RagMaterializedTree {
    param([Parameter(Mandatory)][string]$Source,[Parameter(Mandatory)][string]$Destination)
    $visited = @{}
    function Copy-RagMaterializedTreeInner {
        param([string]$CurrentSource,[string]$CurrentDestination)
        $sourceItem = Get-Item -LiteralPath (Resolve-Path -LiteralPath $CurrentSource).Path -Force
        $sourceKey = [IO.Path]::GetFullPath($sourceItem.FullName) + '|' + [IO.Path]::GetFullPath($CurrentDestination)
        if ($visited.ContainsKey($sourceKey)) { return }
        $visited[$sourceKey] = $true
        if (-not $sourceItem.PSIsContainer -or ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Materialized release source must be a regular directory: $CurrentSource"
        }
        New-Item -ItemType Directory -Path $CurrentDestination -Force | Out-Null
        foreach ($entry in Get-ChildItem -LiteralPath $sourceItem.FullName -Force) {
            $target = Join-Path $CurrentDestination $entry.Name
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                $resolved = Get-Item -LiteralPath $entry.FullName -Force
                $targetPath = @($resolved.Target)[0]
                if ([string]::IsNullOrWhiteSpace($targetPath)) { throw "Reparse target is unavailable: $($entry.FullName)" }
                $targetPath = (Resolve-Path -LiteralPath $targetPath).Path
                if ($resolved.PSIsContainer) {
                    Copy-RagMaterializedTreeInner $targetPath $target
                    # pnpm places a package's dependency links beside the
                    # package directory.  Materialize that sibling closure at
                    # the destination package's node_modules parent so Node's
                    # upward resolution works without junctions.
                    $targetParent = Split-Path -Parent $targetPath
                    $destinationParent = Split-Path -Parent $target
                    foreach ($sibling in Get-ChildItem -LiteralPath $targetParent -Force) {
                        if ($sibling.Name -ceq (Split-Path -Leaf $targetPath)) { continue }
                        $siblingDestination = Join-Path $destinationParent $sibling.Name
                        if ($sibling.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                            $siblingItem = Get-Item -LiteralPath $sibling.FullName -Force
                            $siblingTarget = @($siblingItem.Target)[0]
                            if ([string]::IsNullOrWhiteSpace($siblingTarget)) { throw "Reparse target is unavailable: $($sibling.FullName)" }
                            Copy-RagMaterializedTreeInner (Resolve-Path -LiteralPath $siblingTarget).Path $siblingDestination
                        } elseif ($sibling.PSIsContainer) {
                            Copy-RagMaterializedTreeInner $sibling.FullName $siblingDestination
                        } else {
                            New-Item -ItemType Directory -Path (Split-Path -Parent $siblingDestination) -Force | Out-Null
                            Copy-Item -LiteralPath $sibling.FullName -Destination $siblingDestination -Force
                        }
                    }
                } else {
                    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
                    Copy-Item -LiteralPath $targetPath -Destination $target -Force
                }
                continue
            }
            if (Test-RagDeniedReleaseName $entry.Name) {
                throw "Materialized Next tree contains a denied secret-shaped filename: $($entry.FullName)"
            }
            if ($entry.PSIsContainer) {
                Copy-RagMaterializedTreeInner $entry.FullName $target
            } else {
                New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
                Copy-Item -LiteralPath $entry.FullName -Destination $target -Force
            }
        }
    }
    Copy-RagMaterializedTreeInner $Source $Destination
    foreach ($entry in Get-ChildItem -LiteralPath $Destination -Recurse -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Materialized Next tree contains a reparse point: $($entry.FullName)"
        }
    }
}

function New-RagDeterministicZip {
    param([Parameter(Mandatory)][string]$Root,[Parameter(Mandatory)][string]$Archive)
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    Initialize-RagUtf8PathComparer
    $stream = [IO.File]::Open($Archive, [IO.FileMode]::CreateNew)
    try {
        $zip = [IO.Compression.ZipArchive]::new(
            $stream, [IO.Compression.ZipArchiveMode]::Create, $false
        )
        try {
            $resolved = (Resolve-Path -LiteralPath $Root).Path
            $relativeToFile = [Collections.Generic.Dictionary[string,object]]::new(
                [StringComparer]::Ordinal
            )
            foreach ($file in Get-ChildItem -LiteralPath $resolved -Recurse -File -Force) {
                $relative = $file.FullName.Substring($resolved.Length).TrimStart('\').Replace('\','/')
                $relativeToFile[$relative] = $file
            }
            $relativePaths = [string[]]@($relativeToFile.Keys)
            [Array]::Sort($relativePaths, [RagUtf8PathComparer]::new())
            foreach ($relative in $relativePaths) {
                $file = $relativeToFile[$relative]
                $entry = $zip.CreateEntry(
                    $relative,
                    [IO.Compression.CompressionLevel]::NoCompression
                )
                $entry.LastWriteTime = [DateTimeOffset]::new(1980,1,1,0,0,0,[TimeSpan]::Zero)
                $input = [IO.File]::OpenRead($file.FullName)
                $output = $entry.Open()
                try { $input.CopyTo($output) } finally { $output.Dispose(); $input.Dispose() }
            }
        } finally {
            $zip.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$output = [IO.Path]::GetFullPath($OutputRoot)
foreach ($inputRoot in @(
    $source,$ApiRuntimeRoot,$OcrRuntimeRoot,$NodeRuntimeRoot,$OpenSslRuntimeRoot,
    $RerankerModelRoot,$OcrModelRoot
)) {
    $resolvedInputRoot = (Resolve-Path -LiteralPath $inputRoot).Path
    if ($output -ceq $resolvedInputRoot -or
        $output.StartsWith($resolvedInputRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Release output must be outside every source/runtime/model tree'
    }
}
if (Test-Path -LiteralPath $output) {
    if (@(Get-ChildItem -LiteralPath $output -Force).Count -gt 0) {
        throw 'Release output directory must be absent or empty'
    }
} else {
    New-Item -ItemType Directory -Path $output | Out-Null
}
if (-not $PSCmdlet.ShouldProcess($output, 'Build unsigned deterministic Local RAG release artifacts')) {
    return
}
$stage = Join-Path $output ('.release-stage-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    foreach ($mapping in @(
        @((Join-Path $source 'apps\api\app'),(Join-Path $stage 'apps\api\app')),
        @((Join-Path $source 'apps\api\alembic'),(Join-Path $stage 'apps\api\alembic')),
        @($ApiRuntimeRoot,(Join-Path $stage 'runtimes\api-python')),
        @($OcrRuntimeRoot,(Join-Path $stage 'runtimes\ocr-python')),
        @($NodeRuntimeRoot,(Join-Path $stage 'runtimes\node')),
        @($OpenSslRuntimeRoot,(Join-Path $stage 'tools\openssl')),
        @($RerankerModelRoot,(Join-Path $stage 'signed-assets\bge-reranker-v2-m3')),
        @($OcrModelRoot,(Join-Path $stage 'signed-assets\paddleocr-vl-1.6'))
    )) {
        Copy-RagReleaseTree $mapping[0] $mapping[1]
    }
    $supervisorDestination = Join-Path $stage 'apps\supervisor'
    New-Item -ItemType Directory -Path $supervisorDestination | Out-Null
    foreach ($supervisorFile in Get-ChildItem -LiteralPath (
        Join-Path $source 'apps\supervisor'
    ) -File -Filter '*.py') {
        Copy-Item -LiteralPath $supervisorFile.FullName -Destination $supervisorDestination
    }
    foreach ($file in @('alembic.ini','pyproject.toml','uv.lock')) {
        Copy-Item -LiteralPath (Join-Path $source "apps\api\$file") `
            -Destination (Join-Path $stage "apps\api\$file")
    }
    $standaloneSource = Join-Path $source 'apps\web\.next\standalone'
    Copy-RagMaterializedTree $standaloneSource (
        Join-Path $stage 'apps\web\.next\standalone'
    )
    Copy-RagReleaseTree (Join-Path $source 'apps\web\.next\static') (
        Join-Path $stage 'apps\web\.next\standalone\apps\web\.next\static'
    )
    $publicSource = Join-Path $source 'apps\web\public'
    if (-not (Test-Path -LiteralPath $publicSource -PathType Container)) {
        throw 'Production web public tree is missing'
    }
    Copy-RagReleaseTree $publicSource (
        Join-Path $stage 'apps\web\.next\standalone\apps\web\public'
    )
    foreach ($entry in Get-ChildItem -LiteralPath $stage -Recurse -File -Force) {
        if (Test-RagDeniedReleaseName $entry.Name) {
            throw "Release staging contains a denied secret-shaped filename: $($entry.FullName)"
        }
        if ($entry.Name -ceq 'pyvenv.cfg') {
            throw "Release staging contains a non-relocatable external-base configuration: $($entry.FullName)"
        }
    }
    $archive = Join-Path $output 'local-rag-release.zip'
    New-RagDeterministicZip $stage $archive

    foreach ($artifact in @(
        @((Join-Path $source 'ops\windows\deployment.json'),'deployment.json'),
        @((Join-Path $source 'ops\windows\Caddyfile'),'Caddyfile'),
        @($CaddyExecutable,'caddy.exe'),
        @($CspArtifact,'csp-header.caddy'),
        @($ServiceHostExecutable,'RagSupervisorService.exe'),
        @((Join-Path $source 'ops\windows\verify_dependencies.py'),'verify_dependencies.py')
    )) {
        $resolvedArtifact = (Resolve-Path -LiteralPath $artifact[0]).Path
        $item = Get-Item -LiteralPath $resolvedArtifact
        if ($item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            (Test-RagDeniedReleaseName $artifact[1])) {
            throw "Release artifact is not a regular file: $resolvedArtifact"
        }
        Copy-Item -LiteralPath $resolvedArtifact -Destination (Join-Path $output $artifact[1])
    }

    $fixedRlsTables = @(
        'access_grants','acl_previews','audit_events','backup_runs','chat_scopes',
        'chat_turns','chats','chunks','documents','effective_document_access',
        'folder_create_grants',
        'ingestion_jobs','library_nodes','login_throttles','object_deletions',
        'pre_auth_challenges','security_epochs','service_leases','sessions',
        'team_members','teams','turn_citations','turn_sources',
        'upload_reservations','users'
    )
    $evidence = [ordered]@{
        schema_version=1
        alembic_revision='0006_versioned_claim'
        force_rls_tables=$fixedRlsTables
        containers=[ordered]@{
            postgres_image_digest=$PostgresImageDigest
            rustfs_image_digest=$RustfsImageDigest
        }
        rustfs=[ordered]@{
            bucket='rag-originals'
            probe_object_key='_release-probe/v4.bin'
            probe_object_sha256=$RustfsProbeObjectSha256
        }
        ollama_models=[ordered]@{
            'qwen3:8b'=$QwenGenerationDigest
            'qwen3-embedding:0.6b'=$QwenEmbeddingDigest
        }
        reranker=[ordered]@{
            identity='BAAI/bge-reranker-v2-m3'; device='cpu'
            model_assets_sha256=(Get-RagTreeSha256 (
                Join-Path $stage 'signed-assets\bge-reranker-v2-m3'
            ))
        }
        ocr=[ordered]@{
            paddleocr_version=$PaddleOcrVersion; pipeline_version='1.6'
            fixture_sha256=$OcrFixtureSha256
            expected_output_sha256=$OcrOutputSha256
            expected_structured_sha256=$OcrStructuredSha256
            expected_text_sha256=$OcrTextSha256
            expected_page_count=$OcrPageCount
            model_assets_sha256=(Get-RagTreeSha256 (
                Join-Path $stage 'signed-assets\paddleocr-vl-1.6'
            ))
        }
        runtimes=[ordered]@{
            api_python_sha256=(
                Get-FileHash (Join-Path $stage 'runtimes\api-python\python.exe') -Algorithm SHA256
            ).Hash.ToLowerInvariant()
            ocr_python_sha256=(
                Get-FileHash (Join-Path $stage 'runtimes\ocr-python\python.exe') -Algorithm SHA256
            ).Hash.ToLowerInvariant()
            api_python_tree_sha256=(Get-RagTreeSha256 (Join-Path $stage 'runtimes\api-python'))
            ocr_python_tree_sha256=(Get-RagTreeSha256 (Join-Path $stage 'runtimes\ocr-python'))
            node_tree_sha256=(Get-RagTreeSha256 (Join-Path $stage 'runtimes\node'))
            openssl_tree_sha256=(Get-RagTreeSha256 (Join-Path $stage 'tools\openssl'))
            docker_executable_sha256=$DockerExecutableSha256
        }
        verifier_sha256=(
            Get-FileHash (Join-Path $output 'verify_dependencies.py') -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        max_evidence_age_seconds=900
    }
    $evidencePath = Join-Path $output 'release-evidence.json'
    [IO.File]::WriteAllText(
        $evidencePath,
        ($evidence | ConvertTo-Json -Depth 20),
        [Text.UTF8Encoding]::new($false)
    )
    & (Join-Path $stage 'runtimes\api-python\python.exe') -B `
        (Join-Path $source 'ops\windows\validate_json_schema.py') `
        (Join-Path $source 'ops\windows\release-evidence.schema.json') $evidencePath
    if ($LASTEXITCODE -ne 0) { throw 'Draft release evidence failed schema validation' }
    Test-RagInstalledReleaseBinding -ReleaseRoot $stage -ReleaseEvidence $evidence
    [pscustomobject]@{
        result='built_unsigned'
        archive=$archive
        release_evidence=$evidencePath
        signing_required=$true
        live_evidence_required=$true
    } | ConvertTo-Json
} finally {
    if (Test-Path -LiteralPath $stage) {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
