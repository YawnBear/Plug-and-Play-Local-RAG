[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OcrServicePython,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$OcrServicePythonSha256,
    [Parameter(Mandatory)][string]$OcrEnginePython,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$OcrEnginePythonSha256
)
$ErrorActionPreference = 'Stop'
$nonLoopbackRemoteAddresses = @(
    '0.0.0.0-126.255.255.255',
    '128.0.0.0-255.255.255.255',
    '::/128',
    '::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff'
)
$programs = @(
    @{
        Name='Local RAG OCR Outbound - Service'
        Path=(Resolve-Path -LiteralPath $OcrServicePython).Path
        Hash=$OcrServicePythonSha256
    },
    @{
        Name='Local RAG OCR Outbound - Engine'
        Path=(Resolve-Path -LiteralPath $OcrEnginePython).Path
        Hash=$OcrEnginePythonSha256
    }
)
foreach ($program in $programs) {
    if ((Get-FileHash -LiteralPath $program.Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $program.Hash) {
        throw "OCR executable SHA-256 mismatch: $($program.Name)"
    }
    Get-NetFirewallRule -DisplayName $program.Name -ErrorAction SilentlyContinue |
        Disable-NetFirewallRule | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $program.Name -Direction Outbound -Action Block `
        -Profile Any -Protocol Any -LocalPort Any -RemotePort Any -Program $program.Path `
        -RemoteAddress $nonLoopbackRemoteAddresses `
        | Out-Null
}
