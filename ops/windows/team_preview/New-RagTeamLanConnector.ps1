[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$OutputRoot,
    [Parameter(Mandatory = $true)] [string]$CaCertificate,
    [Parameter(Mandatory = $true)] [string]$LocalAddress,
    [ValidatePattern('^[0-9a-fA-F-]{36}$')]
    [string]$InstallationId = ([guid]::NewGuid().ToString('D')),
    [ValidateRange(1, 2147483647)] [int]$ConnectorGeneration = 1,
    [string]$ZipPath,
    [switch]$Plan
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'New-RagTeamConnector.ps1') -OutputDirectory $OutputRoot -CertificatePath $CaCertificate -LanIPv4 $LocalAddress -InstallationId $InstallationId -ConnectorGeneration $ConnectorGeneration -ZipPath $ZipPath -Plan:$Plan
