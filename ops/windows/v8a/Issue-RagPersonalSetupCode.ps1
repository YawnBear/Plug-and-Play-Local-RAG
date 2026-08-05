[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'LocalRAG\Personal'),
    [string]$ReleaseRoot,
    [switch]$DevelopmentSource,
    [switch]$PassThru
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagPersonal.psm1') -Force
if ([string]::IsNullOrWhiteSpace($ReleaseRoot)) {
    $ReleaseRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
}

$root = Assert-RagPersonalPathSafe -Path $InstallRoot
$release = Assert-RagPersonalPathSafe -Path $ReleaseRoot
$journal = Read-RagPersonalJson -Path (Join-Path $root 'state\installation-journal.json')
Assert-RagPersonalJournal -Journal $journal
if (-not (Test-RagPersonalStepComplete -Journal $journal -Step 'schema_migrated')) {
    throw 'Personal setup code cannot be issued before the database migration completes.'
}
foreach ($port in @(3000, 8000, 8100, 8101, 8102)) {
    if ($null -ne (Get-NetTCPConnection -State Listen -LocalPort $port `
        -ErrorAction SilentlyContinue)) {
        throw 'Stop the Personal application before issuing a replacement setup code.'
    }
}

$environmentPath = Join-Path $root 'config\maintenance.env'
$values = [ordered]@{}
$raw = [Text.UTF8Encoding]::new($false, $true).GetString(
    [IO.File]::ReadAllBytes($environmentPath)
)
foreach ($line in ($raw -split "`r?`n")) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $separator = $line.IndexOf('=')
    if ($separator -le 0) { throw 'The protected maintenance environment is invalid.' }
    $key = $line.Substring(0, $separator)
    if ($values.Contains($key)) { throw 'The protected maintenance environment is invalid.' }
    $values[$key] = $line.Substring($separator + 1)
}

$prior = @{}
try {
    foreach ($key in $values.Keys) {
        $prior[$key] = [Environment]::GetEnvironmentVariable(
            [string]$key, [EnvironmentVariableTarget]::Process
        )
        [Environment]::SetEnvironmentVariable(
            [string]$key, [string]$values[$key], [EnvironmentVariableTarget]::Process
        )
    }
    $apiRoot = Join-Path $release 'apps\api'
    if ($DevelopmentSource) {
        $uv = Get-Command uv.exe -ErrorAction Stop
        $output = & $uv.Source --directory $apiRoot run python `
            -m app.maintenance_cli --confirm-stopped setup-code-issue 2>&1
    }
    else {
        $python = Join-Path $release 'runtimes\api-python\python.exe'
        Push-Location $apiRoot
        try {
            $output = & $python -m app.maintenance_cli `
                --confirm-stopped setup-code-issue 2>&1
        }
        finally { Pop-Location }
    }
    if ($LASTEXITCODE -ne 0) { throw 'The one-time Personal setup code could not be issued.' }
    $code = [string](@($output | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })[-1])
    if ($code -cnotmatch '^[A-Za-z0-9_-]{32,128}$') {
        throw 'The setup-code command returned an invalid bounded result.'
    }
    if ($PassThru) {
        Write-Output $code
    }
    else {
        & (Join-Path $PSScriptRoot 'Show-RagPersonalSetupCode.ps1') -Code $code
    }
}
finally {
    foreach ($key in $values.Keys) {
        [Environment]::SetEnvironmentVariable(
            [string]$key, $prior[$key], [EnvironmentVariableTarget]::Process
        )
    }
    $code = $null
    $output = $null
}
