[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'LocalRAG\Personal'),
    [string]$ReleaseRoot,
    [switch]$DevelopmentSource
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagPersonal.psm1') -Force

$resolvedInstallRoot = Assert-RagPersonalPathSafe -Path $InstallRoot
$journal = Read-RagPersonalJson -Path (Join-Path $resolvedInstallRoot 'state\installation-journal.json')
Assert-RagPersonalJournal -Journal $journal
if ($journal.state -notin @('setup_required','ready')) {
    throw "Personal runtime cannot start while installation state is $($journal.state)."
}
if ([string]::IsNullOrWhiteSpace($ReleaseRoot)) {
    $ReleaseRoot = [string]$journal.release_root
}
$resolvedReleaseRoot = Assert-RagPersonalPathSafe -Path $ReleaseRoot
if ($resolvedReleaseRoot -cne (Assert-RagPersonalPathSafe -Path ([string]$journal.release_root))) {
    throw 'The selected release root does not match the installed Personal journal.'
}

$configRoot = Join-Path $resolvedInstallRoot 'config'
$composeFile = Join-Path $configRoot 'compose.personal.yaml'
$composeEnvironment = Join-Path $configRoot 'compose.env'
$docker = (Get-Command docker.exe -ErrorAction Stop).Source
& $docker compose -p ([string]$journal.compose_project) --env-file $composeEnvironment `
    -f $composeFile up -d --wait
if ($LASTEXITCODE -ne 0) {
    throw 'Personal PostgreSQL/RustFS startup failed.'
}

$priorPythonPath = [Environment]::GetEnvironmentVariable(
    'PYTHONPATH', [EnvironmentVariableTarget]::Process
)
try {
    [Environment]::SetEnvironmentVariable(
        'PYTHONPATH', $resolvedReleaseRoot, [EnvironmentVariableTarget]::Process
    )
    $arguments = @(
        '-m','apps.supervisor.personal_runtime',
        '--install-root',$resolvedInstallRoot,
        '--data-root',([string]$journal.data_root),
        '--release-root',$resolvedReleaseRoot
    )
    if ($DevelopmentSource) {
        $uv = (Get-Command uv.exe -ErrorAction Stop).Source
        & $uv --directory (Join-Path $resolvedReleaseRoot 'apps\api') run python `
            @arguments --development-source
    }
    else {
        $python = Join-Path $resolvedReleaseRoot 'runtimes\api-python\python.exe'
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw 'The packaged API Python runtime is missing.'
        }
        & $python @arguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'The Personal foreground runtime stopped unexpectedly.'
    }
}
finally {
    [Environment]::SetEnvironmentVariable(
        'PYTHONPATH', $priorPythonPath, [EnvironmentVariableTarget]::Process
    )
}
