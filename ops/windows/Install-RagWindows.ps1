[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param(
    [Parameter(Mandatory)][string]$RepositoryRoot,
    [Parameter(Mandatory)][string]$SignedReleaseManifest,
    [Parameter(Mandatory)][string]$SignedReleaseSignature,
    [Parameter(Mandatory)][string]$ReleaseArtifactRoot,
    [Parameter(Mandatory)][ValidateCount(2,2)][string[]]$LocalAddress,
    [Parameter(Mandatory)][string]$OcrFixture,
    [Parameter(Mandatory)][string]$OcrTempRoot,
    [Parameter(Mandatory)][string]$PinnedDockerProgram,
    [Parameter(Mandatory)][string]$PostgresContainer,
    [Parameter(Mandatory)][string]$PostgresUser,
    [Parameter(Mandatory)][string]$PostgresDatabase,
    [Parameter(Mandatory)][string]$RustfsContainer,
    [Parameter(Mandatory)][uri]$RustfsEndpoint,
    [Parameter(Mandatory)][string]$RustfsApiCredentials,
    [Parameter(Mandatory)][string]$RustfsIngestionCredentials,
    [Parameter(Mandatory)][string]$RustfsDeletionCredentials,
    [Parameter(Mandatory)][string]$RustfsMaintenanceCredentials,
    [string]$EnvironmentSourceRoot
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'RagHostBinding.ps1')
$script:RagPreparedEnvironmentSnapshots = @{}

function Read-RagPreparedEnvironment {
    param([Parameter(Mandatory)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.Length -gt 64KB) { throw "Prepared environment file is not a bounded regular file: $Path" }
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    $script:RagPreparedEnvironmentSnapshots[$item.FullName] = $bytes
    $values = [ordered]@{}
    $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    foreach ($line in ($text -split "`r?`n")) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith('#')) { continue }
        $separator = $line.IndexOf('=')
        if ($separator -le 0) { throw "Prepared environment file is invalid: $Path" }
        $key = $line.Substring(0, $separator)
        if ($values.Contains($key)) { throw "Prepared environment file contains duplicate keys: $Path" }
        $values[$key] = $line.Substring($separator + 1)
    }
    return $values
}

function Assert-RagPreparedEnvironmentContract {
    param(
        [Parameter(Mandatory)][string]$EnvironmentRoot,
        [Parameter(Mandatory)][string]$ReleaseRoot,
        [Parameter(Mandatory)][pscustomobject]$ReleaseEvidence,
        [Parameter(Mandatory)][uri]$RustfsEndpoint,
        [Parameter(Mandatory)][string[]]$RustfsCredentialFiles
    )
    $files = @{}
    foreach ($name in @('caddy','web','api','ingestion','deletion','inference','ocr')) {
        $path = Join-Path $EnvironmentRoot "$name.env"
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing prepared environment file: $path" }
        $files[$name] = Read-RagPreparedEnvironment -Path $path
    }
    if ($RustfsCredentialFiles.Count -ne 4) { throw 'Exactly four RustFS credential files are required' }
    $credentialValues = @()
    foreach ($path in $RustfsCredentialFiles) {
        $credential = Read-RagPreparedEnvironment -Path $path
        if ((@($credential.Keys | Sort-Object) -join ',') -cne
            'OBJECT_STORAGE_ACCESS_KEY_ID,OBJECT_STORAGE_SECRET_ACCESS_KEY') {
            throw 'RustFS credential file must contain exactly the two object-storage credential fields'
        }
        $credentialValues += ,$credential
    }
    $endpoint = $RustfsEndpoint.AbsoluteUri.TrimEnd('/')
    $bucket = [string]$ReleaseEvidence.rustfs.bucket
    foreach ($name in @('api','ingestion','deletion')) {
        $values = $files[$name]
        if ($values['OBJECT_STORAGE_ENDPOINT_URL'].TrimEnd('/') -cne $endpoint -or
            $values['OBJECT_STORAGE_BUCKET'] -cne $bucket -or
            $values['OBJECT_STORAGE_USE_TLS'] -cne ($RustfsEndpoint.Scheme -ceq 'https').ToString().ToLowerInvariant()) {
            throw "Prepared $name RustFS endpoint or bucket does not match the verified contract"
        }
        $credential = $credentialValues[ @{api=0;ingestion=1;deletion=2}[$name] ]
        if ($values['OBJECT_STORAGE_ACCESS_KEY_ID'] -cne $credential['OBJECT_STORAGE_ACCESS_KEY_ID'] -or
            $values['OBJECT_STORAGE_SECRET_ACCESS_KEY'] -cne $credential['OBJECT_STORAGE_SECRET_ACCESS_KEY']) {
            throw "Prepared $name RustFS credentials do not match its verified credential file"
        }
    }
    foreach ($name in @('ingestion','deletion')) {
        $blockingConcurrency = 0
        if (-not [int]::TryParse(
                $files[$name]['OBJECT_STORAGE_BLOCKING_CONCURRENCY'],
                [ref]$blockingConcurrency
            ) -or
            $blockingConcurrency -lt 1 -or
            $blockingConcurrency -gt 4) {
            throw (
                "Prepared $name object-storage blocking concurrency must " +
                'be from 1 to 4'
            )
        }
    }
    foreach ($token in @(
        $files['api']['COORDINATOR_SERVICE_TOKEN'],
        $files['api']['CONTROLLER_SERVICE_TOKEN'],
        $files['ingestion']['COORDINATOR_SERVICE_TOKEN'],
        $files['inference']['COORDINATOR_SERVICE_TOKEN'],
        $files['ingestion']['OCR_SERVICE_TOKEN'],
        $files['ocr']['OCR_SERVICE_TOKEN']
    )) {
        if ([Text.Encoding]::UTF8.GetByteCount($token) -lt 32 -or $token -match '\s') {
            throw 'Prepared service tokens must contain at least 32 UTF-8 bytes and no whitespace'
        }
    }
    if (
        $files['api']['COORDINATOR_SERVICE_TOKEN'] -cne $files['ingestion']['COORDINATOR_SERVICE_TOKEN'] -or
        $files['api']['COORDINATOR_SERVICE_TOKEN'] -cne $files['inference']['COORDINATOR_SERVICE_TOKEN'] -or
        $files['ingestion']['OCR_SERVICE_TOKEN'] -cne $files['ocr']['OCR_SERVICE_TOKEN']) {
        throw 'Prepared shared service tokens do not match exactly'
    }
    if ($files['api']['CONTROLLER_BASE_URL'].TrimEnd('/') -cne 'http://127.0.0.1:8102') {
        throw 'Prepared controller endpoint must be the fixed loopback origin'
    }
    if ($files['api']['CONTROLLER_SERVICE_TOKEN'] -ceq $files['api']['COORDINATOR_SERVICE_TOKEN'] -or
        $files['api']['CONTROLLER_SERVICE_TOKEN'] -ceq $files['ingestion']['OCR_SERVICE_TOKEN']) {
        throw 'Prepared controller service token must be distinct'
    }
    if ($files['api']['CANONICAL_HOST'] -cne 'rag.home.arpa' -or
        $files['api']['CANONICAL_ORIGIN'] -cne 'https://rag.home.arpa') {
        throw 'Prepared API canonical host/origin does not match the managed deployment'
    }
    if ($files['inference']['OLLAMA_BASE_URL'].TrimEnd('/') -cne 'http://127.0.0.1:11434' -or
        $files['inference']['GENERATION_MODEL'] -cne 'qwen3:8b' -or
        $files['inference']['EMBEDDING_MODEL'] -cne 'qwen3-embedding:0.6b') {
        throw 'Prepared inference model or Ollama contract does not match the verified release'
    }
    foreach ($setting in @(
        'MAXIMUM_GENERATION_CONTEXT',
        'MAXIMUM_GENERATION_OUTPUT',
        'GENERATION_TIMEOUT_SECONDS'
    )) {
        $positiveValue = 0
        if (-not [int]::TryParse($files['inference'][$setting], [ref]$positiveValue) -or
            $positiveValue -le 0) {
            throw "Prepared inference $setting must be a positive integer"
        }
    }
    if (-not $ReleaseEvidence.ollama_models.'qwen3:8b' -or
        -not $ReleaseEvidence.ollama_models.'qwen3-embedding:0.6b') {
        throw 'Verified release is missing exact Ollama model digests'
    }
    $expectedReranker = Join-Path $ReleaseRoot 'signed-assets\bge-reranker-v2-m3'
    if ([IO.Path]::GetFullPath($files['inference']['RERANKER_MODEL_PATH']) -cne [IO.Path]::GetFullPath($expectedReranker)) {
        throw 'Prepared inference reranker path does not match the signed model tree'
    }
    $expectedOcrModel = Join-Path $ReleaseRoot 'signed-assets\paddleocr-vl-1.6'
    $expectedOcrPython = Join-Path $ReleaseRoot 'runtimes\ocr-python\python.exe'
    if ([IO.Path]::GetFullPath($files['api']['OCR_PYTHON_EXECUTABLE']) -cne [IO.Path]::GetFullPath($expectedOcrPython) -or
        [IO.Path]::GetFullPath($files['ocr']['OCR_MODEL_ASSET_ROOT']) -cne [IO.Path]::GetFullPath($expectedOcrModel) -or
        [IO.Path]::GetFullPath($files['ocr']['OCR_PYTHON_EXECUTABLE']) -cne [IO.Path]::GetFullPath($expectedOcrPython) -or
        $files['ocr']['OCR_SERVICE_TOKEN'] -ne $files['ingestion']['OCR_SERVICE_TOKEN'] -or
        $files['ingestion']['OCR_SERVICE_BASE_URL'].TrimEnd('/') -cne 'http://127.0.0.1:8101') {
        throw 'Prepared OCR paths or service URL do not match the signed production contract'
    }
}

function Get-RagStartupDiagnosticSummary {
    param(
        [Parameter(Mandatory)][string]$ProgramDataRoot,
        [Parameter(Mandatory)][string]$ProfilesRoot
    )
    $candidates = @(
        [pscustomobject]@{
            path=(Join-Path $ProgramDataRoot 'supervisor-startup-failure.json')
            service=$null
        },
        [pscustomobject]@{ path=(Join-Path $ProfilesRoot 'api\tmp\startup-failure.json'); service='api' },
        [pscustomobject]@{ path=(Join-Path $ProfilesRoot 'ingestion\tmp\startup-failure.json'); service='ingestion' },
        [pscustomobject]@{ path=(Join-Path $ProfilesRoot 'deletion\tmp\startup-failure.json'); service='deletion' },
        [pscustomobject]@{ path=(Join-Path $ProfilesRoot 'inference\tmp\startup-failure.json'); service='inference' },
        [pscustomobject]@{ path=(Join-Path $ProfilesRoot 'ocr\tmp\startup-failure.json'); service='ocr' }
    )
    $allowedServices = @('caddy','web','api','ingestion','deletion','inference','ocr')
    $summaries = [Collections.Generic.List[string]]::new()
    foreach ($candidate in $candidates) {
        $item = Get-Item -LiteralPath $candidate.path -Force -ErrorAction SilentlyContinue
        if ($null -eq $item -or $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $item.Length -gt 64KB) { continue }
        try {
            $bytes = [IO.File]::ReadAllBytes($item.FullName)
            $document = (
                [Text.UTF8Encoding]::new($false, $true).GetString($bytes) |
                    ConvertFrom-Json
            )
        } catch {
            continue
        }
        $service = [string]$document.service
        if ($document.schema_version -ne 1 -or
            $service -notin $allowedServices -or
            ($null -ne $candidate.service -and $service -cne $candidate.service)) {
            continue
        }
        $details = [Collections.Generic.List[string]]::new()
        $startupStage = [string]$document.startup_stage
        if ($startupStage -match '^[a-z][a-z0-9_]{0,63}$') {
            $details.Add("stage=$startupStage")
        }
        foreach ($entry in @($document.exception_chain) | Select-Object -First 8) {
            $type = [string]$entry.type
            if ($type -notmatch '^[A-Za-z_][A-Za-z0-9_.-]{0,127}$') { continue }
            $detail = $type
            foreach ($numberName in @('errno','winerror','exit_code')) {
                if ($null -ne $entry.$numberName -and
                    ([string]$entry.$numberName) -match '^-?[0-9]{1,10}$') {
                    $detail += ":$numberName=$($entry.$numberName)"
                }
            }
            $validationDetails = [Collections.Generic.List[string]]::new()
            foreach ($validation in @($entry.validation) | Select-Object -First 32) {
                $validationType = [string]$validation.type
                $location = @($validation.location) -join '.'
                if ($validationType -match '^[A-Za-z_][A-Za-z0-9_.-]{0,127}$' -and
                    $location -match '^[A-Za-z0-9_.-]{1,256}$') {
                    $validationDetails.Add("$location=$validationType")
                }
            }
            if ($validationDetails.Count -gt 0) {
                $detail += ':validation=' + ($validationDetails -join ',')
            }
            $details.Add($detail)
        }
        if ($details.Count -gt 0) {
            $summaries.Add("$service[" + ($details -join ' <- ') + ']')
        }
    }
    return $summaries -join '; '
}

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Install-RagWindows.ps1 must run elevated'
}
$repository = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$placeholderReleaseAnchorSha256 = '9fab4947813e743127d1e09f50d1521f3d2e4aea9a7d6013615c54335ece828f'
if ((Get-FileHash -LiteralPath (
    Join-Path $repository 'ops\windows\release-allowed-signers'
) -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $placeholderReleaseAnchorSha256) {
    throw 'Release signing identity is still the non-operational placeholder'
}
$caddyVersion = '2.11.3'
$caddyExecutablePin = '67514bc0449ae9b1465cf3d59ab269cb451e8ed88d991e461b24d1337b67f536'
$programData = Join-Path $env:ProgramData 'LocalRAG'
$programFilesRoot = Join-Path $env:ProgramFiles 'LocalRAG'
$serviceRoot = Join-Path $programFilesRoot 'service'
$release = Join-Path $programFilesRoot 'current'
$signedStage = Join-Path $programData "signed-stage\release-$caddyExecutablePin"
$secrets = Join-Path $programData 'secrets'
$identitySecrets = Join-Path $programData 'identity-secrets'
$certificates = Join-Path $programData 'certificates'
$profiles = Join-Path $programData 'profiles'
$stateRoot = Join-Path $programData 'state'
$workRoot = Join-Path $programData 'work'
$signedAssets = Join-Path $release 'signed-assets'
$verifiedReleaseRoot = Join-Path $programData 'verified-release'
$installedManifest = Join-Path $programData 'installed-deployment.json'
$accountLedger = Join-Path $programData 'installed-accounts.json'
$hostsLedger = Join-Path $programData 'installed-hosts.json'
$hostsPriorBytes = Join-Path $programData 'hosts-prior.bin'
$serviceExe = Join-Path $serviceRoot 'RagSupervisorService.exe'
$ocrEnginePython = Join-Path $release 'runtimes\ocr-python\python.exe'
$serviceNames = [ordered]@{
    caddy='RagProxySvc'; web='RagWebSvc'; api='RagApiSvc';
    ingestion='RagIngestionSvc'; deletion='RagDeletionSvc';
    inference='RagInferenceSvc'; ocr='RagOcrSvc'
}
$createdAccounts = [Collections.Generic.List[string]]::new()
$serviceCreated = $false
$hostEntryApplied = $false
$dnsScope = 'lan_dns'
$previousInstallation = $false
. (Join-Path $PSScriptRoot 'RagManagedRootSafety.ps1')
Assert-RagFreshManagedRoots -ProgramDataRoot $programData -ProgramFilesRoot $programFilesRoot
$existingManagedState = @(
    Test-Path -LiteralPath $accountLedger -PathType Leaf
    $null -ne (Get-Service -Name RagSupervisor -ErrorAction SilentlyContinue)
    $null -ne (Get-NetFirewallRule -DisplayName 'Local RAG HTTPS' -ErrorAction SilentlyContinue)
    @($serviceNames.Values | Where-Object { $null -ne (Get-LocalUser -Name $_ -ErrorAction SilentlyContinue) }).Count -gt 0
)
if ($existingManagedState -contains $true) {
    throw 'Existing or partial Local RAG installation detected; in-place mutation is refused until atomic rollback is implemented'
}

if (-not $PSCmdlet.ShouldProcess('Local RAG Windows deployment', 'Install')) { return }
try {
    Get-NetFirewallRule -DisplayName 'Local RAG HTTPS' -ErrorAction SilentlyContinue |
        Disable-NetFirewallRule | Out-Null
    Stop-Service -Name RagSupervisor -Force -ErrorAction SilentlyContinue
    $requiredDirectories = @(
        $programData,$serviceRoot,$signedStage,$secrets,$identitySecrets,$certificates,$profiles,
        $stateRoot,$workRoot,$verifiedReleaseRoot,
        (Join-Path $stateRoot 'inference'),(Join-Path $stateRoot 'ocr'),
        (Join-Path $workRoot 'ingestion\objects'),(Join-Path $workRoot 'ingestion\ocr'),
        (Join-Path $workRoot 'ocr'),
        (Join-Path $profiles 'proxy\tmp'),(Join-Path $profiles 'web\tmp'),
        (Join-Path $profiles 'api\tmp'),(Join-Path $profiles 'ingestion\tmp'),
        (Join-Path $profiles 'deletion\tmp'),
        (Join-Path $profiles 'inference\tmp'),
        (Join-Path $profiles 'inference\cache\huggingface\hub'),
        (Join-Path $profiles 'inference\cache\transformers'),
        (Join-Path $profiles 'ocr\tmp'),
        (Join-Path $profiles 'ocr\cache\huggingface\hub'),
        (Join-Path $profiles 'ocr\cache\transformers')
    )
    foreach ($path in $requiredDirectories) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
    foreach ($path in $requiredDirectories) {
        icacls.exe $path /setowner '*S-1-5-18' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Owner lockdown failed: $path" }
        icacls.exe $path /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Root DACL lockdown failed: $path" }
    }
    $dependencyArtifactName = 'dependency-evidence.json'
    $releaseManifestPath = (Resolve-Path -LiteralPath $SignedReleaseManifest).Path
    $releaseSignaturePath = (Resolve-Path -LiteralPath $SignedReleaseSignature).Path
    $artifactRootPath = (Resolve-Path -LiteralPath $ReleaseArtifactRoot).Path
    $signedVerification = & (Join-Path $repository 'ops\windows\Test-RagUpdate.ps1') `
        -Manifest $releaseManifestPath -Signature $releaseSignaturePath `
        -ArtifactRoot $artifactRootPath -SignedArtifactStageRoot $verifiedReleaseRoot |
        Out-String
    if ($LASTEXITCODE -ne 0) { throw 'Signed release verification failed' }
    $signedVerificationDocument = $signedVerification | ConvertFrom-Json
    if ($signedVerificationDocument.result -cne 'pass') { throw 'Signed release verifier did not pass' }
    $verifiedStage = (Resolve-Path -LiteralPath $signedVerificationDocument.stage_directory).Path
    $signedReleaseDocument = Get-Content -Raw -LiteralPath (
        Join-Path $verifiedStage 'update-manifest.json'
    ) | ConvertFrom-Json
    $dependencyArtifact = @($signedReleaseDocument.artifacts | Where-Object filename -ceq $dependencyArtifactName)
    $appReleaseArtifact = @(
        $signedReleaseDocument.artifacts | Where-Object filename -ceq 'local-rag-release.zip'
    )
    $releaseEvidenceArtifact = @(
        $signedReleaseDocument.artifacts | Where-Object filename -ceq 'release-evidence.json'
    )
    if ($dependencyArtifact.Count -ne 1 -or $appReleaseArtifact.Count -ne 1 -or
        $releaseEvidenceArtifact.Count -ne 1) {
        throw 'Signed release lacks the fixed application, release evidence, or dependency evidence artifact'
    }
    $requiredRuntimeArtifacts = @(
        'deployment.json', 'caddy.exe', 'Caddyfile', 'csp-header.caddy',
        'RagSupervisorService.exe'
    )
    foreach ($runtimeArtifact in $requiredRuntimeArtifacts) {
        if (@($signedReleaseDocument.artifacts | Where-Object filename -ceq $runtimeArtifact).Count -ne 1) {
            throw "Signed release lacks required runtime artifact: $runtimeArtifact"
        }
    }
    $dependencyEvidencePath = Join-Path $verifiedStage $dependencyArtifactName
    $dependencyEvidenceDocument = Get-Content -Raw -LiteralPath $dependencyEvidencePath | ConvertFrom-Json
    if ($dependencyEvidenceDocument.result -cne 'pass' -or
        @($dependencyEvidenceDocument.checks | Where-Object result -cne 'pass').Count -ne 0 -or
        $dependencyEvidenceDocument.release_evidence_sha256 -cne (
            Get-FileHash -LiteralPath (Join-Path $verifiedStage 'release-evidence.json') `
                -Algorithm SHA256
        ).Hash.ToLowerInvariant()) {
        throw 'Dependency evidence is not a passing verifier result'
    }
    $releaseEvidenceDocument = Get-Content -Raw -LiteralPath (
        Join-Path $verifiedStage 'release-evidence.json'
    ) | ConvertFrom-Json
    Assert-RagFreshHostEvidence -Evidence $dependencyEvidenceDocument `
        -MaxAgeSeconds $releaseEvidenceDocument.max_evidence_age_seconds
    . (Join-Path $repository 'ops\windows\Expand-RagVerifiedRelease.ps1')
    Expand-RagVerifiedRelease -Archive (Join-Path $verifiedStage 'local-rag-release.zip') `
        -Destination $release
    Test-RagInstalledReleaseBinding -ReleaseRoot $release `
        -ReleaseEvidence $releaseEvidenceDocument
    $installedReleaseTreeSha256 = Get-RagTreeSha256 $release
    if ($installedReleaseTreeSha256 -cne
        (Get-RagZipTreeSha256 (Join-Path $verifiedStage 'local-rag-release.zip'))) {
        throw 'Installed complete release tree does not match the signed release ZIP tree'
    }
    if ([string]::IsNullOrWhiteSpace($EnvironmentSourceRoot)) {
        throw 'EnvironmentSourceRoot is required; prepared secret files are never synthesized from placeholders'
    }
    $environmentSource = (Resolve-Path -LiteralPath $EnvironmentSourceRoot).Path
    Assert-RagPreparedEnvironmentContract -EnvironmentRoot $environmentSource `
        -ReleaseRoot $release -ReleaseEvidence $releaseEvidenceDocument `
        -RustfsEndpoint $RustfsEndpoint `
        -RustfsCredentialFiles @(
            $RustfsApiCredentials,$RustfsIngestionCredentials,
            $RustfsDeletionCredentials,$RustfsMaintenanceCredentials
        )
    $preparedEnvironmentBytes = @{}
    foreach ($entry in $serviceNames.GetEnumerator()) {
        $sourcePath = (Resolve-Path -LiteralPath (Join-Path $environmentSource "$($entry.Key).env")).Path
        if (-not $script:RagPreparedEnvironmentSnapshots.ContainsKey($sourcePath)) {
            throw "Prepared environment snapshot is missing: $sourcePath"
        }
        $preparedEnvironmentBytes[$entry.Key] = $script:RagPreparedEnvironmentSnapshots[$sourcePath]
    }
    icacls.exe $release /setowner '*S-1-5-18' /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Fixed current release owner lockdown failed' }
    icacls.exe $release /inheritance:r /grant:r `
        '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-18:(F)' `
        '*S-1-5-32-544:(OI)(CI)(F)' '*S-1-5-32-544:(F)' /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Fixed current release DACL lockdown failed' }
    $standaloneWeb = Join-Path $release 'apps\web\.next\standalone\apps\web'
    $installedDependencyEvidence = Join-Path $programData 'installation-dependency-evidence.json'
    Copy-Item -LiteralPath $dependencyEvidencePath -Destination $installedDependencyEvidence -Force
    icacls.exe $installedDependencyEvidence /setowner '*S-1-5-18' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Dependency-evidence owner lockdown failed' }
    icacls.exe $installedDependencyEvidence /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Dependency-evidence DACL lockdown failed' }
    $installedReleaseEvidence = Join-Path $programData 'installed-release-evidence.json'
    Copy-Item -LiteralPath (Join-Path $verifiedStage 'release-evidence.json') `
        -Destination $installedReleaseEvidence
    $installedReleaseState = Join-Path $programData 'installed-release-state.json'
    [IO.File]::WriteAllText($installedReleaseState,([ordered]@{
        schema_version=1; version=$signedReleaseDocument.version
        final_manifest_sha256=(Get-FileHash -LiteralPath (Join-Path $verifiedStage 'update-manifest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        release_evidence_sha256=(Get-FileHash -LiteralPath $installedReleaseEvidence -Algorithm SHA256).Hash.ToLowerInvariant()
        release_tree_sha256=$installedReleaseTreeSha256
    } | ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    foreach ($artifact in @($installedReleaseEvidence,$installedReleaseState)) {
        icacls.exe $artifact /setowner '*S-1-5-18' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Installed release-state owner lockdown failed: $artifact" }
        icacls.exe $artifact /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Installed release-state DACL lockdown failed: $artifact" }
    }

    foreach ($runtimeArtifact in @('caddy.exe','Caddyfile','csp-header.caddy')) {
        Copy-Item -LiteralPath (Join-Path $verifiedStage $runtimeArtifact) `
            -Destination (Join-Path $signedStage $runtimeArtifact)
    }
    $caddyExecutableSha256 = (
        Get-FileHash -LiteralPath (Join-Path $signedStage 'caddy.exe') -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($caddyExecutableSha256 -cne $caddyExecutablePin) {
        throw 'Signed Caddy executable does not match the approved release pin'
    }

    foreach ($entry in $serviceNames.GetEnumerator()) {
        $account = $entry.Value
        $existing = Get-LocalUser -Name $account -ErrorAction SilentlyContinue
        $logonSecret = Join-Path $identitySecrets "$($entry.Key).logon.env"
        if ($null -eq $existing) {
            $passwordBytes = New-Object byte[] 48
            $passwordGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $passwordGenerator.GetBytes($passwordBytes) } finally { $passwordGenerator.Dispose() }
            $password = 'Aa1!' + [Convert]::ToBase64String($passwordBytes)
            $securePassword = ConvertTo-SecureString $password -AsPlainText -Force
            New-LocalUser -Name $account -Password $securePassword -PasswordNeverExpires `
                -AccountNeverExpires -UserMayNotChangePassword | Out-Null
            $createdAccounts.Add($account)
        } else {
            throw "Refusing to reuse pre-existing account $account without its independently generated installer credential"
        }
        & (Join-Path $repository 'ops\windows\Set-RagAccountRights.ps1') -Account ".\$account" -Action GrantRequired
        [IO.File]::WriteAllText(
            $logonSecret,
            "RAG_WINDOWS_ACCOUNT_PASSWORD=$password`n",
            [Text.UTF8Encoding]::new($false)
        )
        icacls.exe $logonSecret /setowner '*S-1-5-18' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Logon-secret owner lockdown failed for $account" }
        icacls.exe $logonSecret /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Logon-secret DACL lockdown failed for $account" }
        $password = $null
        $securePassword = $null
        $profileName = if ($entry.Key -ceq 'caddy') { 'proxy' } else { $entry.Key }
        $profile = Join-Path $profiles $profileName
        New-Item -ItemType Directory -Path $profile -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $profile 'tmp') -Force | Out-Null
    }
    $preExposureAccounts = @($serviceNames.Values | ForEach-Object {
        [pscustomobject]@{
            name = $_
            sid = (New-Object Security.Principal.NTAccount(
                $env:COMPUTERNAME,$_
            )).Translate([Security.Principal.SecurityIdentifier]).Value
        }
    })
    [IO.File]::WriteAllText(
        $accountLedger,
        (@{
            schema_version=2
            phase='pre_exposure'
            accounts=$preExposureAccounts
        } | ConvertTo-Json -Depth 4),
        [Text.UTF8Encoding]::new($false)
    )
    icacls.exe $accountLedger /setowner '*S-1-5-18' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Pre-exposure account-ledger owner lockdown failed' }
    icacls.exe $accountLedger /inheritance:r /grant:r `
        '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Pre-exposure account-ledger DACL lockdown failed' }

    foreach ($entry in $serviceNames.GetEnumerator()) {
        $source = Join-Path $environmentSource "$($entry.Key).env"
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing prepared environment file: $source" }
        [IO.File]::WriteAllBytes(
            (Join-Path $secrets "$($entry.Key).env"),
            $preparedEnvironmentBytes[$entry.Key]
        )
    }

    icacls.exe $programData /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'ProgramData ACL application failed' }
    foreach ($entry in $serviceNames.GetEnumerator()) {
        $sid = (New-Object Security.Principal.NTAccount($env:COMPUTERNAME,$entry.Value)).Translate([Security.Principal.SecurityIdentifier]).Value
        $environmentFile = Join-Path $secrets "$($entry.Key).env"
        icacls.exe $environmentFile /setowner '*S-1-5-18' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Environment owner lockdown failed for $($entry.Key)" }
        icacls.exe $environmentFile /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' "*${sid}:(R)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Environment ACL application failed for $($entry.Key)" }
        $profileName = if ($entry.Key -ceq 'caddy') { 'proxy' } else { $entry.Key }
        $profile = Join-Path $profiles $profileName
        icacls.exe $profile /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' "*${sid}:(OI)(CI)(M)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Profile/work/log ACL application failed for $($entry.Key)" }
    }
    $serviceSid = @{}
    foreach ($entry in $serviceNames.GetEnumerator()) {
        $serviceSid[$entry.Key] = (
            New-Object Security.Principal.NTAccount($env:COMPUTERNAME,$entry.Value)
        ).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    # The OCR workspace rejects reparse points in every existing ancestor.
    # These parents are intentionally non-inheriting, so grant only the OCR
    # identity enough direct access to inspect their metadata and traverse to
    # its separately writable work\ocr descendant.
    $ocrMetadataParents = @($programData,$workRoot)
    foreach ($path in $ocrMetadataParents) {
        icacls.exe $path /grant:r "*$($serviceSid['ocr']):(RX)" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "OCR workspace-parent metadata ACL application failed: $path"
        }
    }
    # requiredDirectories deliberately remove inheritance before any service
    # accounts exist.  Re-apply Modify only to the exact runtime-owned
    # descendants after resolving each service SID; immutable release/model
    # trees are handled separately by Set-RagReleaseAcl and never appear here.
    $writableDirectoryGrants = @(
        [pscustomobject]@{ path=(Join-Path $profiles 'proxy\tmp'); service='caddy' },
        [pscustomobject]@{ path=(Join-Path $profiles 'web\tmp'); service='web' },
        [pscustomobject]@{ path=(Join-Path $profiles 'api\tmp'); service='api' },
        [pscustomobject]@{ path=(Join-Path $profiles 'ingestion\tmp'); service='ingestion' },
        [pscustomobject]@{ path=(Join-Path $profiles 'deletion\tmp'); service='deletion' },
        [pscustomobject]@{ path=(Join-Path $profiles 'inference\tmp'); service='inference' },
        [pscustomobject]@{ path=(Join-Path $profiles 'inference\cache\huggingface\hub'); service='inference' },
        [pscustomobject]@{ path=(Join-Path $profiles 'inference\cache\transformers'); service='inference' },
        [pscustomobject]@{ path=(Join-Path $profiles 'ocr\tmp'); service='ocr' },
        [pscustomobject]@{ path=(Join-Path $profiles 'ocr\cache\huggingface\hub'); service='ocr' },
        [pscustomobject]@{ path=(Join-Path $profiles 'ocr\cache\transformers'); service='ocr' },
        [pscustomobject]@{ path=(Join-Path $workRoot 'ingestion\objects'); service='ingestion' },
        [pscustomobject]@{ path=(Join-Path $workRoot 'ingestion\ocr'); service='ingestion' },
        [pscustomobject]@{ path=(Join-Path $workRoot 'ocr'); service='ocr' }
    )
    foreach ($grant in $writableDirectoryGrants) {
        $sid = $serviceSid[$grant.service]
        icacls.exe $grant.path /inheritance:r /grant:r `
            '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' "*${sid}:(OI)(CI)(M)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Writable descendant ACL application failed: $($grant.path)" }
    }
    & (Join-Path $repository 'ops\windows\Set-RagReleaseAcl.ps1') `
        -ReleaseRoot $release -ServiceSid $serviceSid
    $proxySid = (New-Object Security.Principal.NTAccount($env:COMPUTERNAME,'RagProxySvc')).Translate([Security.Principal.SecurityIdentifier]).Value
    icacls.exe $signedStage /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' "*${proxySid}:(OI)(CI)(RX)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Signed Caddy stage ACL application failed' }
    $inferenceSid = (New-Object Security.Principal.NTAccount($env:COMPUTERNAME,'RagInferenceSvc')).Translate([Security.Principal.SecurityIdentifier]).Value
    $ocrSid = (New-Object Security.Principal.NTAccount($env:COMPUTERNAME,'RagOcrSvc')).Translate([Security.Principal.SecurityIdentifier]).Value
    $ingestionSid = (New-Object Security.Principal.NTAccount($env:COMPUTERNAME,'RagIngestionSvc')).Translate([Security.Principal.SecurityIdentifier]).Value
    foreach ($grant in @(
        @((Join-Path $stateRoot 'inference'),$inferenceSid,'M'),
        @((Join-Path $stateRoot 'ocr'),$ocrSid,'M'),
        @((Join-Path $workRoot 'ingestion'),$ingestionSid,'M'),
        @((Join-Path $workRoot 'ocr'),$ocrSid,'M'),
        @((Join-Path $signedAssets 'bge-reranker-v2-m3'),$inferenceSid,'RX'),
        @((Join-Path $signedAssets 'paddleocr-vl-1.6'),$ocrSid,'RX')
    )) {
        icacls.exe $grant[0] /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' "*$($grant[1]):(OI)(CI)($($grant[2]))" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "State/work/asset ACL application failed: $($grant[0])" }
    }

    $signedOpenSsl = Join-Path $release 'tools\openssl\openssl.exe'
    $signedOpenSslSha256 = (
        Get-FileHash -LiteralPath $signedOpenSsl -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    & (Join-Path $repository 'ops\windows\Install-RagCertificates.ps1') `
        -OpenSslPath $signedOpenSsl -OpenSslSha256 $signedOpenSslSha256 `
        -CertificateRoot $certificates -SecretRoot $secrets
    & (Join-Path $repository 'ops\windows\Test-RagCertificateSet.ps1') `
        -OpenSslPath $signedOpenSsl -OpenSslSha256 $signedOpenSslSha256 `
        -CertificateRoot $certificates -SecretRoot $secrets
    if ($LASTEXITCODE -ne 0) { throw 'Certificate semantic validation failed' }
    Get-ChildItem -LiteralPath $secrets -File | ForEach-Object {
        icacls.exe $_.FullName /setowner '*S-1-5-18' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Secret owner lockdown failed: $($_.FullName)" }
    }
    icacls.exe $secrets /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Secret tree ACL application failed' }
    $apiSid = (New-Object Security.Principal.NTAccount($env:COMPUTERNAME,'RagApiSvc')).Translate([Security.Principal.SecurityIdentifier]).Value
    foreach ($key in @('rag.home.arpa.key','caddy-api-client.key')) {
        icacls.exe (Join-Path $secrets $key) /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' "*${proxySid}:(R)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Proxy private-key ACL failed: $key" }
    }
    icacls.exe (Join-Path $secrets 'api-loopback.key') /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' "*${apiSid}:(R)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'API private-key ACL failed' }
    icacls.exe $certificates /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' "*${proxySid}:(OI)(CI)(R)" "*${apiSid}:(OI)(CI)(R)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Certificate ACL application failed' }

    $manifest = Get-Content -Raw (Join-Path $verifiedStage 'deployment.json') | ConvertFrom-Json
    $manifest.deployment_readiness.state = 'installed'
    [IO.File]::WriteAllText(
        $installedManifest,
        ($manifest | ConvertTo-Json -Depth 20),
        [Text.UTF8Encoding]::new($false)
    )
    icacls.exe $installedManifest /setowner '*S-1-5-18' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Installed-manifest owner lockdown failed' }
    icacls.exe $installedManifest /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Installed-manifest DACL lockdown failed' }
    Push-Location $release
    try {
        & (Join-Path $release 'runtimes\api-python\python.exe') `
            -B -m apps.supervisor validate --manifest $installedManifest
        if ($LASTEXITCODE -ne 0) { throw 'Installed manifest validation failed' }
        $secretValidationText = @(
            & (Join-Path $release 'runtimes\api-python\python.exe') `
                -B -m apps.supervisor validate-secrets `
                --manifest $installedManifest
        ) | Out-String
        if ($LASTEXITCODE -ne 0) {
            throw (
                'Protected environment/logon-secret validation failed: ' +
                $secretValidationText.Trim()
            )
        }
        $secretValidation = $secretValidationText | ConvertFrom-Json
        if ($secretValidation.result -cne 'pass' -or
            $secretValidation.validated_service_secret_sets -ne 7 -or
            $secretValidation.passwords_exposed -cne $false) {
            throw 'Protected environment/logon-secret validator contract failed'
        }
    } finally {
        Pop-Location
    }

    Copy-Item -LiteralPath (Join-Path $verifiedStage 'RagSupervisorService.exe') `
        -Destination $serviceExe
    $serviceExeSha256 = (
        Get-FileHash -LiteralPath $serviceExe -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $serviceBinaryPath = '"' + $serviceExe + '"'
    New-Service -Name RagSupervisor `
        -BinaryPathName $serviceBinaryPath `
        -DisplayName 'Local RAG Supervisor' `
        -StartupType Automatic `
        -ErrorAction Stop | Out-Null
    $serviceCreated = $true
    $serviceConfigurationText = & sc.exe config RagSupervisor `
        type= own start= delayed-auto obj= LocalSystem 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw (
            'RagSupervisor service configuration failed: ' +
            $serviceConfigurationText.Trim()
        )
    }
    & sc.exe sidtype RagSupervisor unrestricted | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'RagSupervisor service SID configuration failed' }
    $supervisorSid = (New-Object Security.Principal.NTAccount('NT SERVICE','RagSupervisor')).Translate([Security.Principal.SecurityIdentifier]).Value
    icacls.exe $identitySecrets /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' "*${supervisorSid}:(RX)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Supervisor service-SID logon-secret ACL failed' }
    foreach ($artifact in @($installedManifest,(Join-Path $programData 'installation-dependency-evidence.json'))) {
        icacls.exe $artifact /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' "*${supervisorSid}:(R)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Supervisor service-SID artifact ACL failed: $artifact" }
    }
    icacls.exe (Join-Path $secrets 'supervisor-api-client.key') /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' "*${supervisorSid}:(R)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Supervisor mTLS private-key ACL failed' }
    icacls.exe (Join-Path $certificates 'supervisor-api-client.crt') /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' "*${supervisorSid}:(R)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Supervisor mTLS certificate ACL failed' }
    & sc.exe failure RagSupervisor reset= 86400 actions= restart/5000/restart/15000/restart/30000 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'RagSupervisor recovery configuration failed' }

    & (Join-Path $repository 'ops\windows\Set-RagFirewall.ps1') `
        -CaddyPath (Join-Path $signedStage 'caddy.exe') -CaddySha256 $caddyExecutableSha256 `
        -LocalAddress $LocalAddress
    [IO.File]::WriteAllText(
        (Join-Path $programData 'installed-caddy.json'),
        (@{
            version=$caddyVersion
            executable_path=(Join-Path $signedStage 'caddy.exe')
            executable_sha256=$caddyExecutableSha256
        } | ConvertTo-Json),
        [Text.UTF8Encoding]::new($false)
    )
    icacls.exe (Join-Path $programData 'installed-caddy.json') /setowner '*S-1-5-18' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Installed Caddy evidence owner lockdown failed' }
    icacls.exe (Join-Path $programData 'installed-caddy.json') /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Installed Caddy evidence DACL lockdown failed' }
    $ocrEngineSha256 = (Get-FileHash -LiteralPath $ocrEnginePython -Algorithm SHA256).Hash.ToLowerInvariant()
    $ocrServicePython = Join-Path $release 'runtimes\api-python\python.exe'
    $ocrServiceSha256 = (
        Get-FileHash -LiteralPath $ocrServicePython -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    & (Join-Path $repository 'ops\windows\Set-RagOcrFirewall.ps1') `
        -OcrServicePython $ocrServicePython -OcrServicePythonSha256 $ocrServiceSha256 `
        -OcrEnginePython $ocrEnginePython -OcrEnginePythonSha256 $ocrEngineSha256
    $ocrFirewallEvidence = & (Join-Path $repository 'ops\windows\Test-RagOcrFirewall.ps1') `
        -ExpectedProgram @($ocrServicePython,$ocrEnginePython) | Out-String
    if ($LASTEXITCODE -ne 0 -or ($ocrFirewallEvidence | ConvertFrom-Json).result -cne 'pass') {
        throw (
            'OCR outbound firewall verification failed: ' +
            $ocrFirewallEvidence.Trim()
        )
    }
    try {
        & (Join-Path $repository 'ops\windows\Test-RagLanDns.ps1') `
            -ExpectedAddress $LocalAddress -Mode DnsServer | Out-Null
    } catch {
        & (Join-Path $repository 'ops\windows\Set-RagHostsEntry.ps1') `
            -Action Apply -Address $LocalAddress -PriorContentPath $hostsPriorBytes | Out-Null
        $hostEntryApplied = $true
        $dnsScope = 'host_only'
        & (Join-Path $repository 'ops\windows\Test-RagLanDns.ps1') `
            -ExpectedAddress $LocalAddress -Mode HostOnly | Out-Null
    }
    [IO.File]::WriteAllText(
        $hostsLedger,
        (@{
            schema_version=1; dns_scope=$dnsScope; addresses=@($LocalAddress)
            managed_block=$hostEntryApplied
            prior_content_path=if($hostEntryApplied){$hostsPriorBytes}else{$null}
            prior_content_sha256=if($hostEntryApplied){
                (Get-FileHash -LiteralPath $hostsPriorBytes -Algorithm SHA256).Hash.ToLowerInvariant()
            }else{$null}
        } | ConvertTo-Json -Depth 3),
        [Text.UTF8Encoding]::new($false)
    )
    icacls.exe $hostsLedger /setowner '*S-1-5-18' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Hosts ledger owner lockdown failed' }
    icacls.exe $hostsLedger /inheritance:r /grant:r `
        '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Hosts ledger DACL lockdown failed' }
    $freshOcrOutput = Join-Path (
        (Resolve-Path -LiteralPath $OcrTempRoot).Path
    ) ("install-verification-" + [Guid]::NewGuid().ToString('N'))
    icacls.exe $release /grant:r `
        '*S-1-5-18:(OI)(CI)(RX)' '*S-1-5-18:(RX)' `
        '*S-1-5-32-544:(OI)(CI)(RX)' '*S-1-5-32-544:(RX)' /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Installed release immutable RX ACL application failed'
    }
    $freshDependencyEvidence = & (
        Join-Path $repository 'ops\windows\Test-RagDependencies.ps1'
    ) -ExistingSignedStage $verifiedStage -ExistingReleaseRoot $release `
        -OcrFixture $OcrFixture -OcrTempRoot $OcrTempRoot -OcrOutput $freshOcrOutput `
        -PinnedDockerProgram $PinnedDockerProgram -PostgresContainer $PostgresContainer `
        -PostgresUser $PostgresUser -PostgresDatabase $PostgresDatabase `
        -RustfsContainer $RustfsContainer -RustfsEndpoint $RustfsEndpoint `
        -RustfsApiCredentials $RustfsApiCredentials `
        -RustfsIngestionCredentials $RustfsIngestionCredentials `
        -RustfsDeletionCredentials $RustfsDeletionCredentials `
        -RustfsMaintenanceCredentials $RustfsMaintenanceCredentials | Out-String
    if ($LASTEXITCODE -ne 0 -or
        ($freshDependencyEvidence | ConvertFrom-Json).result -cne 'pass') {
        throw 'Immediate current-host dependency revalidation failed'
    }
    Assert-RagFreshHostEvidence -Evidence ($freshDependencyEvidence | ConvertFrom-Json) `
        -MaxAgeSeconds $releaseEvidenceDocument.max_evidence_age_seconds
    $verifiedStageItem = Get-Item -LiteralPath $verifiedStage
    if (-not $verifiedStageItem.PSIsContainer -or
        ($verifiedStageItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        [IO.Path]::GetDirectoryName($verifiedStage) -cne $verifiedReleaseRoot) {
        throw 'Verified update stage cleanup target is unsafe'
    }
    $cleanupSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & (Join-Path ([Environment]::SystemDirectory) 'takeown.exe') `
        /F $verifiedStage /R /D Y | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Verified update stage ownership recovery failed'
    }
    & (Join-Path ([Environment]::SystemDirectory) 'icacls.exe') `
        $verifiedStage /grant:r `
        "*$cleanupSid`:(OI)(CI)(F)" "*$cleanupSid`:(F)" /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Verified update stage cleanup ACL failed'
    }
    [IO.Directory]::Delete($verifiedStage, $true)
    if (Test-Path -LiteralPath $verifiedStage) {
        throw 'Verified update stage remains after strict cleanup'
    }
    if (@(Get-ChildItem -LiteralPath $verifiedReleaseRoot -Force).Count -ne 0) {
        throw 'Verified release root is not empty after update-stage cleanup'
    }
    [IO.Directory]::Delete($verifiedReleaseRoot, $false)
    if (Test-Path -LiteralPath $verifiedReleaseRoot) {
        throw 'Verified release root remains after strict cleanup'
    }
    Start-Service -Name RagSupervisor
    $startupDeadline = [DateTime]::UtcNow.AddMinutes(5)
    do {
        $serviceState = Get-Service -Name RagSupervisor
        if ($serviceState.Status -cne 'Running') {
            $startupDiagnostic = Get-RagStartupDiagnosticSummary `
                -ProgramDataRoot $programData -ProfilesRoot $profiles
            $diagnosticSuffix = if ([string]::IsNullOrWhiteSpace($startupDiagnostic)) {
                '; no bounded startup diagnostic was produced'
            } else {
                "; startup diagnostic: $startupDiagnostic"
            }
            throw "RagSupervisor stopped during graph startup$diagnosticSuffix"
        }
        $caddyListeners = @(Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue |
            Where-Object LocalAddress -in $LocalAddress)
        if ($caddyListeners.Count -eq 2 -and
            @($caddyListeners.OwningProcess | Sort-Object -Unique).Count -eq 1) { break }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $startupDeadline)
    if ($caddyListeners.Count -ne 2) {
        $startupDiagnostic = Get-RagStartupDiagnosticSummary `
            -ProgramDataRoot $programData -ProfilesRoot $profiles
        $diagnosticSuffix = if ([string]::IsNullOrWhiteSpace($startupDiagnostic)) {
            '; no bounded startup diagnostic was produced'
        } else {
            "; startup diagnostic: $startupDiagnostic"
        }
        throw "Full supervised graph did not become ready within five minutes$diagnosticSuffix"
    }
    $caddyPid = @($caddyListeners.OwningProcess | Sort-Object -Unique)[0]
    $networkEvidence = & (Join-Path $repository 'ops\windows\Test-RagNetwork.ps1') `
        -PinnedCaddyProgram (Join-Path $signedStage 'caddy.exe') `
        -PinnedCaddySha256 $caddyExecutableSha256 `
        -PinnedServiceHostProgram $serviceExe -PinnedServiceHostSha256 $serviceExeSha256 `
        -PinnedSupervisorPython (Join-Path $release 'runtimes\api-python\python.exe') `
        -DeploymentId 'rag-v4-local' `
        -ExpectedLocalAddresses $LocalAddress | Out-String
    $networkEvidenceDocument = $networkEvidence | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $networkEvidenceDocument.result -cne 'pass') {
        $networkFindings = @($networkEvidenceDocument.findings) -join '; '
        if ([string]::IsNullOrWhiteSpace($networkFindings)) {
            $networkFindings = 'network evidence exited without findings'
        }
        throw "Post-start Caddy/firewall/supervision evidence failed: $networkFindings"
    }
    $installedNetworkEvidence = Join-Path $programData 'installation-network-evidence.json'
    [IO.File]::WriteAllText(
        $installedNetworkEvidence,
        $networkEvidence,
        [Text.UTF8Encoding]::new($false)
    )
    icacls.exe $installedNetworkEvidence /setowner '*S-1-5-18' | Out-Null
    icacls.exe $installedNetworkEvidence /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Network-evidence lockdown failed' }
    $installedAccountLedger = Get-Content -Raw -LiteralPath $accountLedger | ConvertFrom-Json
    $installedAccountLedger.phase = 'installed'
    [IO.File]::WriteAllText(
        $accountLedger,
        ($installedAccountLedger | ConvertTo-Json -Depth 4),
        [Text.UTF8Encoding]::new($false)
    )
    [pscustomobject]@{
        result='installed'; service='RagSupervisor'; service_sid='unrestricted'
        service_start='automatic-delayed'; child_accounts=$serviceNames.Values
        full_graph_ready=$true; api_mtls_readiness_passed=$true; network_evidence='pass'
        backup='not_configured'; updates='not_configured'; dns_scope=$dnsScope
        second_lan_device='unverified'
    } | ConvertTo-Json -Depth 4
} catch {
    $installFailure = $_.Exception
    $rollbackRootFailures = [Collections.Generic.List[string]]::new()
    Get-NetFirewallRule -DisplayName 'Local RAG HTTPS' -ErrorAction SilentlyContinue | Disable-NetFirewallRule | Out-Null
    . (Join-Path $repository 'ops\windows\RagProcessSafety.ps1')
    Stop-RagManagedProcesses -AccountName @($createdAccounts)
    if ($serviceCreated) { & sc.exe delete RagSupervisor | Out-Null }
    Get-NetFirewallRule -DisplayName 'Local RAG HTTPS' -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    Get-NetFirewallRule -DisplayName 'Local RAG OCR Outbound - *' -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    foreach ($account in $createdAccounts) {
        & (Join-Path $repository 'ops\windows\Set-RagAccountRights.ps1') -Account ".\$account" -Action RemoveRequired -ErrorAction SilentlyContinue
        Remove-LocalUser -Name $account -ErrorAction SilentlyContinue
    }
    if (-not $previousInstallation) {
        if (Test-Path -LiteralPath (Join-Path $certificates 'local-rag-ca.crt')) {
            $rollbackCa = New-Object Security.Cryptography.X509Certificates.X509Certificate2 `
                -ArgumentList (Join-Path $certificates 'local-rag-ca.crt')
            Get-ChildItem Cert:\LocalMachine\Root |
                Where-Object Thumbprint -ceq $rollbackCa.Thumbprint |
                Remove-Item -Force -ErrorAction SilentlyContinue
        }
        if ($hostEntryApplied) {
            $hostsRollback = & (Join-Path $repository 'ops\windows\Set-RagHostsEntry.ps1') `
                -Action Remove -Address $LocalAddress -PriorContentPath $hostsPriorBytes |
                ConvertFrom-Json
            if ($hostsRollback.result -cne 'changed' -or
                $hostsRollback.action -cne 'Remove') {
                throw 'Exact hosts-byte rollback did not report success'
            }
            $hostEntryApplied = $false
        }
        # Fresh-root preflight proves these roots were absent, so rollback owns
        # exactly these two created trees and never follows a reparse point.
        foreach ($artifact in @($programData,$programFilesRoot)) {
            if (Test-Path -LiteralPath $artifact) {
                try {
                    Assert-RagPathComponentsNotReparse -Path $artifact
                    & (Join-Path ([Environment]::SystemDirectory) 'takeown.exe') `
                        /F $artifact /A /R /D Y | Out-Null
                    if ($LASTEXITCODE -ne 0) {
                        throw 'administrator ownership recovery failed'
                    }
                    & icacls.exe $artifact /grant:r `
                        '*S-1-5-32-544:(OI)(CI)(F)' `
                        '*S-1-5-32-544:(F)' /T /C | Out-Null
                    if ($LASTEXITCODE -ne 0) {
                        throw 'administrator recovery ACL failed'
                    }
                    [IO.Directory]::Delete($artifact, $true)
                    if (Test-Path -LiteralPath $artifact) {
                        throw 'managed root remains after recursive deletion'
                    }
                } catch {
                    $rollbackRootFailures.Add(
                        "$artifact`: $($_.Exception.Message)"
                    )
                }
            }
        }
    }
    if ($rollbackRootFailures.Count -ne 0) {
        throw [InvalidOperationException]::new(
            (
                "Installation failed: $($installFailure.Message); " +
                "managed-root rollback failed: " +
                ($rollbackRootFailures -join '; ')
            ),
            $installFailure
        )
    }
    throw $installFailure
}
