[CmdletBinding()]
param([string]$LocalAddress,[switch]$PullModels)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Team/LAN preview requires an elevated administrator session.'
}
$os = Get-CimInstance Win32_OperatingSystem
$product = Get-CimInstance Win32_ComputerSystem
$version = [version]$os.Version
if ($version.Major -ne 10 -or $product.PCSystemType -notin @(1,2)) {
    throw 'Team/LAN preview supports Windows 10/11 workstations only.'
}
$networkEvidence = $null
if (-not [string]::IsNullOrWhiteSpace($LocalAddress)) {
    Import-Module (Join-Path $PSScriptRoot 'RagTeamLanPreview.psm1') -Force
    $normalizedAddress = Assert-RagTeamPreviewRfc1918Address -Address $LocalAddress
    $assigned = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $normalizedAddress `
        -ErrorAction SilentlyContinue | Where-Object AddressState -eq 'Preferred')
    if ($assigned.Count -ne 1) {
        throw 'The reserved preview IPv4 must be assigned in Preferred state exactly once.'
    }
    $adapter = Get-NetAdapter -InterfaceIndex $assigned[0].InterfaceIndex -ErrorAction Stop
    $profile = @(Get-NetConnectionProfile -InterfaceIndex $assigned[0].InterfaceIndex `
        -ErrorAction Stop)
    if ($adapter.Status -ne 'Up' -or $adapter.Virtual -or $profile.Count -ne 1 -or
        $profile[0].NetworkCategory -ne 'Private') {
        throw 'The reserved preview IPv4 must use an active physical adapter on a Private network.'
    }
    $networkEvidence = [pscustomobject]@{
        local_address=$normalizedAddress
        prefix_length=[int]$assigned[0].PrefixLength
        interface_index=[int]$assigned[0].InterfaceIndex
        interface_guid=[string]$adapter.InterfaceGuid
        adapter_mac=[string]$adapter.MacAddress
        network_category='Private'
    }
}
$docker = Get-Command docker.exe -ErrorAction Stop
& $docker.Source version --format '{{.Server.Version}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop must be installed and running.' }
$ollama = Get-Command ollama.exe -ErrorAction Stop
& $ollama.Source list | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Ollama must be installed and running.' }
$models = @('qwen3:8b','qwen3-embedding:0.6b')
if ($PullModels) {
    foreach ($model in $models) {
        & $ollama.Source pull $model
        if ($LASTEXITCODE -ne 0) { throw "Ollama model pull failed: $model" }
    }
}
$installed = @(& $ollama.Source list | Select-Object -Skip 1 | ForEach-Object {
    ($_ -split '\s+')[0]
})
foreach ($model in $models) {
    if (@($installed | Where-Object { $_ -eq $model -or $_ -like "$model-*" }).Count -eq 0) {
        throw "Required Ollama model is not installed: $model"
    }
}
[pscustomobject]@{
    result='pass';elevated=$true;workstation=$true;docker_running=$true
    ollama_running=$true;models=$models;network=$networkEvidence
} | ConvertTo-Json -Depth 3
