[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Medium')]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'LocalRAG\Personal'),
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'LocalRAGData'),
    [switch]$Plan,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
$apiRoot = Join-Path $sourceRoot 'apps\api'
$webRoot = Join-Path $sourceRoot 'apps\web'
$ocrRoot = Join-Path $sourceRoot '.venv-ocr'
$ocrPython = Join-Path $ocrRoot 'Scripts\python.exe'
$sourceMc = Join-Path $sourceRoot 'runtime\tools\mc\mc.exe'
$mcUri = 'https://dl.min.io/client/mc/release/windows-amd64/mc.RELEASE.2025-08-13T08-35-41Z'
$mcSha256 = 'c8db13ebeda31497f354c0e950809db0ae9b2a2a69b8afee68c128c37300c157'

function Write-RagSourcePhase {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "`n[Local RAG] $Message" -ForegroundColor Cyan
}

function Invoke-RagSourceCommand {
    param(
        [Parameter(Mandatory)][string]$Program,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$FailureMessage
    )
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw $FailureMessage }
}

function Get-RagSourcePnpm {
    $pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if ($null -ne $pnpm) {
        return [pscustomobject]@{ Program=$pnpm.Source; Prefix=@() }
    }
    $corepack = Get-Command corepack.cmd -ErrorAction SilentlyContinue
    if ($null -eq $corepack) {
        throw 'pnpm is unavailable. Install Node.js 20 or newer with Corepack, then run setup again.'
    }
    return [pscustomobject]@{ Program=$corepack.Source; Prefix=@('pnpm') }
}

function Invoke-RagSourcePnpm {
    param(
        [Parameter(Mandatory)][pscustomobject]$Command,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$FailureMessage
    )
    $allArguments = @($Command.Prefix) + @($Arguments)
    Invoke-RagSourceCommand -Program $Command.Program `
        -Arguments $allArguments -FailureMessage $FailureMessage
}

function Copy-RagSourceWebTree {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "The web build did not produce the required folder: $Source"
    }
    if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
        [IO.Directory]::CreateDirectory($Destination) | Out-Null
    }
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Recurse -Force
    }
}

function Install-RagSourceMc {
    if (Test-Path -LiteralPath $sourceMc -PathType Leaf) {
        $existingHash = (Get-FileHash -LiteralPath $sourceMc -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existingHash -ceq $mcSha256) { return }
        throw 'The cached RustFS setup tool has an unexpected hash. Delete runtime\tools\mc\mc.exe and run setup again.'
    }
    $mcDirectory = Split-Path -Parent $sourceMc
    [IO.Directory]::CreateDirectory($mcDirectory) | Out-Null
    $pending = Join-Path $mcDirectory ('mc-' + [guid]::NewGuid().ToString('N') + '.pending')
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $mcUri -OutFile $pending
        $downloadHash = (Get-FileHash -LiteralPath $pending -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($downloadHash -cne $mcSha256) {
            throw 'The downloaded RustFS setup tool failed SHA-256 verification.'
        }
        [IO.File]::Move($pending, $sourceMc)
    }
    finally {
        if (Test-Path -LiteralPath $pending) { [IO.File]::Delete($pending) }
    }
}

function Test-RagSourceRerankerComplete {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    return (
        (Test-Path -LiteralPath (Join-Path $Path 'config.json') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path 'tokenizer_config.json') -PathType Leaf) -and
        (@(Get-ChildItem -LiteralPath $Path -Filter '*.safetensors' -File -ErrorAction SilentlyContinue).Count -gt 0)
    )
}

function Get-RagSourceMissingOcrAssets {
    param([Parameter(Mandatory)][string]$Path)
    $required = @(
        'official_models\PaddleOCR-VL-1.6\inference.yml',
        'official_models\PP-DocLayoutV3\inference.yml',
        'fonts\PingFang-SC-Regular.ttf'
    )
    return @($required | Where-Object {
        -not (Test-Path -LiteralPath (Join-Path $Path $_) -PathType Leaf)
    })
}

function Invoke-RagSourceCommandWithEnvironment {
    param(
        [Parameter(Mandatory)][string]$Program,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$FailureMessage,
        [Parameter(Mandatory)][hashtable]$Environment
    )
    $prior = @{}
    foreach ($name in $Environment.Keys) {
        $prior[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, [string]$Environment[$name], 'Process')
    }
    try {
        Invoke-RagSourceCommand -Program $Program -Arguments $Arguments -FailureMessage $FailureMessage
    }
    finally {
        foreach ($name in $Environment.Keys) {
            if ($null -eq $prior[$name]) {
                [Environment]::SetEnvironmentVariable($name, $null, 'Process')
            }
            else {
                [Environment]::SetEnvironmentVariable($name, $prior[$name], 'Process')
            }
        }
    }
}

function Prepare-RagSourceModelAssets {
    param(
        [Parameter(Mandatory)][string]$DataRootPath,
        [Parameter(Mandatory)][string]$ApiRootPath,
        [Parameter(Mandatory)][string]$SourceRootPath,
        [Parameter(Mandatory)][string]$OcrPythonPath,
        [Parameter(Mandatory)][string]$UvPath
    )
    $modelRoot = Join-Path $DataRootPath 'models'
    $rerankerPath = Join-Path $modelRoot 'bge-reranker-v2-m3'
    $ocrPath = Join-Path $modelRoot 'paddleocr-vl-1.6'
    $rerankerPending = Join-Path $modelRoot 'bge-reranker-v2-m3.pending'
    $ocrPending = Join-Path $modelRoot 'paddleocr-vl-1.6.pending'
    [IO.Directory]::CreateDirectory($modelRoot) | Out-Null

    if (Test-Path -LiteralPath $rerankerPath) {
        if (-not (Test-RagSourceRerankerComplete -Path $rerankerPath)) {
            throw "The local BGE reranker asset root is incomplete: $rerankerPath"
        }
        Write-RagSourcePhase "Using the existing local BGE reranker assets at $rerankerPath"
    }
    elseif (Test-Path -LiteralPath $rerankerPending) {
        throw "A partial BGE reranker acquisition is present at $rerankerPending; remove only that pending folder and resume setup."
    }
    else {
        Write-RagSourcePhase 'Preparing the local BGE reranker assets'
        $resultFile = Join-Path $modelRoot 'reranker-path.txt.pending'
        Invoke-RagSourceCommand -Program $UvPath -Arguments @(
            '--directory',$ApiRootPath,'run','python',
            (Join-Path $SourceRootPath 'scripts\prepare-dev-reranker.py'),
            '--model','BAAI/bge-reranker-v2-m3',
            '--preferred-path',$rerankerPending,
            '--result-file',$resultFile
        ) -FailureMessage 'The BGE reranker model could not be acquired.'
        if (-not (Test-Path -LiteralPath $resultFile -PathType Leaf)) {
            throw 'The BGE reranker preparation did not produce its result file.'
        }
        $resolved = ([IO.File]::ReadAllText($resultFile)).Trim()
        if ([string]::IsNullOrWhiteSpace($resolved) -or
            -not (Test-RagSourceRerankerComplete -Path $resolved)) {
            throw 'The BGE reranker preparation returned an incomplete model path.'
        }
        if (-not ([IO.Path]::GetFullPath($resolved) -ceq [IO.Path]::GetFullPath($rerankerPending))) {
            [IO.Directory]::CreateDirectory($rerankerPending) | Out-Null
            foreach ($item in Get-ChildItem -LiteralPath $resolved -Force) {
                Copy-Item -LiteralPath $item.FullName -Destination $rerankerPending -Recurse -Force
            }
        }
        if (-not (Test-RagSourceRerankerComplete -Path $rerankerPending)) {
            throw 'The staged BGE reranker assets are incomplete.'
        }
        Move-Item -LiteralPath $rerankerPending -Destination $rerankerPath
        Remove-Item -LiteralPath $resultFile -Force -ErrorAction SilentlyContinue
    }
    Protect-RagPersonalPath -Path $rerankerPath -Directory

    if (Test-Path -LiteralPath $ocrPath -PathType Leaf) {
        throw "The local PaddleOCR-VL 1.6 asset path is not a directory: $ocrPath"
    }
    $missingOcr = if (Test-Path -LiteralPath $ocrPath -PathType Container) {
        Get-RagSourceMissingOcrAssets -Path $ocrPath
    }
    else { @() }
    if (Test-Path -LiteralPath $ocrPath -PathType Container) {
        if (@($missingOcr).Count -gt 0) {
            throw "The local PaddleOCR-VL 1.6 asset root is incomplete: $ocrPath (missing $($missingOcr -join ', '))"
        }
        Write-RagSourcePhase "Using the existing local PaddleOCR-VL 1.6 assets at $ocrPath"
    }
    elseif (Test-Path -LiteralPath $ocrPending) {
        throw "A partial PaddleOCR-VL acquisition is present at $ocrPending; remove only that pending folder and resume setup."
    }
    else {
        Write-RagSourcePhase 'Preparing and smoke-testing the local PaddleOCR-VL 1.6 assets'
        [IO.Directory]::CreateDirectory($ocrPending) | Out-Null
        $cacheRoot = Join-Path $DataRootPath 'cache\source-ocr-smoke'
        $fixturePath = Join-Path $cacheRoot 'system-ocr-fixture.pdf'
        $smokeOutput = Join-Path $cacheRoot 'output'
        [IO.Directory]::CreateDirectory($cacheRoot) | Out-Null
        $environment = @{
            PADDLE_PDX_CACHE_HOME = $ocrPending
            PADDLE_HOME = $ocrPending
            OCR_MODEL_ASSET_ROOT = $ocrPending
            HF_HUB_OFFLINE = '0'
            TRANSFORMERS_OFFLINE = '0'
            PYTHONNOUSERSITE = '1'
            PYTHONUTF8 = '1'
            PYTHONPATH = "$SourceRootPath;$ApiRootPath"
        }
        Invoke-RagSourceCommandWithEnvironment -Program $OcrPythonPath -Arguments @(
            '-c',
            'from pathlib import Path; from app.system.fixtures import system_ocr_fixture; import sys; Path(sys.argv[1]).write_bytes(system_ocr_fixture())',
            $fixturePath
        ) -FailureMessage 'The synthetic OCR fixture could not be generated.' -Environment $environment
        Invoke-RagSourceCommandWithEnvironment -Program $OcrPythonPath -Arguments @(
            '-m','paddleocr','doc_parser','-i',$fixturePath,'--save_path',$smokeOutput,
            '--pipeline_version','v1.6','--device','cpu','--cpu_threads','1'
        ) -FailureMessage 'PaddleOCR-VL 1.6 could not parse the synthetic fixture.' -Environment $environment
        $missingOcr = Get-RagSourceMissingOcrAssets -Path $ocrPending
        if (@($missingOcr).Count -gt 0) {
            throw "PaddleOCR-VL 1.6 did not materialize required assets: $($missingOcr -join ', ')"
        }
        if (@(Get-ChildItem -LiteralPath $smokeOutput -Filter '*_res.json' -File -ErrorAction SilentlyContinue).Count -eq 0) {
            throw 'PaddleOCR-VL 1.6 smoke parsing produced no result JSON.'
        }
        Move-Item -LiteralPath $ocrPending -Destination $ocrPath
    }
    Protect-RagPersonalPath -Path $ocrPath -Directory
    return [pscustomobject]@{ reranker_path=$rerankerPath; ocr_model_path=$ocrPath }
}

if ($Plan) {
    [pscustomobject]@{
        result='pass'
        mode='read_only_source_setup_plan'
        user_installs=@('Docker Desktop','Ollama','Node.js 20 or newer','uv')
        automated=@(
            'install locked JavaScript dependencies',
            'install locked API dependencies',
            'create the isolated pinned CPU OCR environment',
            'prepare the exact local BGE reranker model assets',
            'prepare and smoke-test the exact local PaddleOCR-VL 1.6 assets',
            'build and materialize the web application',
            'download and SHA-256 verify the pinned RustFS setup tool',
            'generate secrets and prepare PostgreSQL and RustFS',
            'apply database migrations and acquire the required Ollama models',
            'issue the first-owner setup code and create Start-menu shortcuts'
        )
        install_root=[IO.Path]::GetFullPath($InstallRoot)
        data_root=[IO.Path]::GetFullPath($DataRoot)
        model_roots=[ordered]@{
            reranker=(Join-Path ([IO.Path]::GetFullPath($DataRoot)) 'models\bge-reranker-v2-m3')
            ocr=(Join-Path ([IO.Path]::GetFullPath($DataRoot)) 'models\paddleocr-vl-1.6')
        }
        mutations_performed=$false
    } | ConvertTo-Json -Depth 4
    return
}

if (-not $PSCmdlet.ShouldProcess($sourceRoot, 'Prepare Local RAG from this source clone')) {
    return
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
    -not [Environment]::Is64BitOperatingSystem) {
    throw 'Local RAG Personal requires 64-bit Windows 10 or 11.'
}
foreach ($marker in @('package.json','pnpm-lock.yaml','apps\api\uv.lock')) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $marker) -PathType Leaf)) {
        throw 'Setup must be run from a complete Local RAG source clone.'
    }
}
foreach ($port in @(3000,8000,8100,8101,8102)) {
    if ($null -ne (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
        throw 'Close the running Local RAG application window before refreshing its source checkout.'
    }
}
$node = Get-Command node.exe -ErrorAction SilentlyContinue
if ($null -eq $node) { throw 'Node.js 20 or newer is not installed or not available in PATH.' }
$nodeVersion = (& $node.Source --version).Trim()
if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v([0-9]+)\.' -or [int]$Matches[1] -lt 20) {
    throw "Node.js 20 or newer is required; detected $nodeVersion."
}
$uv = Get-Command uv.exe -ErrorAction SilentlyContinue
if ($null -eq $uv) { throw 'uv is not installed or not available in PATH.' }
$pnpm = Get-RagSourcePnpm

Write-RagSourcePhase 'Checking Windows, memory, disk, Docker Desktop, and Ollama'
Import-Module (Join-Path $PSScriptRoot 'RagPersonal.psm1') -Force
Get-RagPersonalPreflight -DataRoot $DataRoot | Out-Null

Write-RagSourcePhase 'Installing source dependencies (safe to run again)'
Invoke-RagSourcePnpm -Command $pnpm -Arguments @('install','--frozen-lockfile') `
    -FailureMessage 'JavaScript dependency installation failed.'
Invoke-RagSourceCommand -Program $uv.Source `
    -Arguments @('sync','--directory',$apiRoot,'--frozen') `
    -FailureMessage 'API dependency installation failed.'

Write-RagSourcePhase 'Preparing the isolated CPU OCR environment'
$ocrReady = $false
if (Test-Path -LiteralPath $ocrPython -PathType Leaf) {
    & $ocrPython -c "import importlib.metadata as m; assert m.version('paddlepaddle') == '3.2.1'; assert m.version('paddleocr') == '3.7.0'" 2>$null
    $ocrReady = $LASTEXITCODE -eq 0
}
if (-not $ocrReady) {
    Invoke-RagSourceCommand -Program $uv.Source `
        -Arguments @('venv','--python','3.13','--clear',$ocrRoot) `
        -FailureMessage 'The isolated OCR Python environment could not be created.'
    Invoke-RagSourceCommand -Program $uv.Source `
        -Arguments @('pip','install','--python',$ocrPython,'--index-url','https://www.paddlepaddle.org.cn/packages/stable/cpu/','paddlepaddle==3.2.1') `
        -FailureMessage 'The pinned CPU Paddle runtime could not be installed.'
    Invoke-RagSourceCommand -Program $uv.Source `
        -Arguments @('pip','install','--python',$ocrPython,'paddleocr[doc-parser]==3.7.0') `
        -FailureMessage 'The pinned PaddleOCR environment could not be installed.'
}
Invoke-RagSourceCommand -Program $ocrPython `
    -Arguments @('-c',"import paddle, paddleocr; print('PaddleOCR CPU environment ready')") `
    -FailureMessage 'The isolated OCR environment failed its import check.'

$preparedModels = Prepare-RagSourceModelAssets -DataRootPath ([IO.Path]::GetFullPath($DataRoot)) `
    -ApiRootPath $apiRoot -SourceRootPath $sourceRoot -OcrPythonPath $ocrPython -UvPath $uv.Source

Write-RagSourcePhase 'Building the local web application'
Invoke-RagSourcePnpm -Command $pnpm -Arguments @('build') `
    -FailureMessage 'The Local RAG web build failed.'
$standaloneWeb = Join-Path $webRoot '.next\standalone\apps\web'
if (-not (Test-Path -LiteralPath (Join-Path $standaloneWeb 'server.js') -PathType Leaf)) {
    throw 'The web build did not produce its standalone server.'
}
Copy-RagSourceWebTree -Source (Join-Path $webRoot '.next\static') `
    -Destination (Join-Path $standaloneWeb '.next\static')
Copy-RagSourceWebTree -Source (Join-Path $webRoot 'public') `
    -Destination (Join-Path $standaloneWeb 'public')

Write-RagSourcePhase 'Preparing the local data services and first owner'
Install-RagSourceMc
& (Join-Path $PSScriptRoot 'Install-RagPersonal.ps1') `
    -InstallRoot $InstallRoot -DataRoot $DataRoot -ReleaseRoot $sourceRoot `
    -DevelopmentSource
if ($LASTEXITCODE -ne 0) { throw 'Local RAG Personal preparation failed.' }

if (-not $NoStart) {
    Write-RagSourcePhase 'Starting Local RAG in its own window'
    $launcher = Join-Path $PSScriptRoot 'Start-Local-RAG.cmd'
    Start-Process -FilePath $env:ComSpec -WorkingDirectory $sourceRoot `
        -ArgumentList @('/d','/k',('"' + $launcher + '"')) | Out-Null
    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:3000/setup' `
                -MaximumRedirection 0 -TimeoutSec 2 -ErrorAction Stop
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                $ready = $true
                break
            }
        }
        catch {
            if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -lt 400) {
                $ready = $true
                break
            }
        }
    }
    if (-not $ready) {
        throw 'Local RAG was prepared, but the web application did not become ready. Review the Local RAG window.'
    }
    Start-Process 'http://127.0.0.1:3000/setup' | Out-Null
}

[pscustomobject]@{
    result='pass'
    mode='source_clone'
    install_root=[IO.Path]::GetFullPath($InstallRoot)
    data_root=[IO.Path]::GetFullPath($DataRoot)
    started=(-not $NoStart)
    next_action=if ($NoStart) { 'Open Start Local RAG from the Start menu.' } else { 'Complete first-owner setup in the browser.' }
} | ConvertTo-Json -Depth 3
