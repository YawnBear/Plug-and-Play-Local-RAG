[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Manifest,
    [ValidateSet('Plan', 'Validate', 'Status')]
    [string]$Mode = 'Plan'
)

$ErrorActionPreference = 'Stop'
$resolvedManifest = (Resolve-Path -LiteralPath $Manifest).Path
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$command = $Mode.ToLowerInvariant()
& py -3 -m apps.supervisor $command --manifest $resolvedManifest
if ($LASTEXITCODE -ne 0) {
    throw "Supervisor $Mode failed with exit code $LASTEXITCODE"
}

Write-Verbose "Repository root: $repositoryRoot"
