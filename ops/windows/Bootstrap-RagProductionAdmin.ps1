[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$principal = [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)) {
    throw 'Administrator bootstrap requires an elevated PowerShell session.'
}
if (-not $PSCmdlet.ShouldProcess(
    'fresh rag-prod database', 'Interactively create the first website administrator'
)) { return }

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
. (Join-Path $PSScriptRoot 'RagManagedRootSafety.ps1')
$environmentRoot = 'C:\ProgramData\LocalRAG-Preparation\environments'
foreach ($name in @('api', 'maintenance')) {
    $path = Join-Path $environmentRoot "$name.env"
    Assert-RagPathComponentsNotReparse -Path $path
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Prepared environment path is not a regular file: $path"
    }
    foreach ($line in [IO.File]::ReadAllLines($path)) {
        if ($line -cnotmatch '^([A-Z0-9_]+)=(.*)$') {
            throw "Prepared environment file is invalid: $path"
        }
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}

$service = Get-Service -Name RagSupervisor -ErrorAction SilentlyContinue
if ($null -ne $service -and $service.Status -ne 'Stopped') {
    throw 'RagSupervisor must be stopped before administrator bootstrap.'
}
$python = Join-Path $repository 'apps\api\.venv\Scripts\python.exe'
$previousLocation = Get-Location
try {
    Set-Location (Join-Path $repository 'apps\api')
    & $python -m app.maintenance_cli --confirm-stopped bootstrap-admin
    if ($LASTEXITCODE -ne 0) { throw 'First-administrator bootstrap failed.' }
}
finally {
    Set-Location $previousLocation
}
