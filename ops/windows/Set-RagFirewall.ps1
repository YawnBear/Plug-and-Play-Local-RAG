[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$CaddyPath,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$CaddySha256,
    [Parameter(Mandatory)][ValidateCount(2,2)][string[]]$LocalAddress
)
$ErrorActionPreference = 'Stop'
$program = (Resolve-Path -LiteralPath $CaddyPath).Path
if ((Get-FileHash -LiteralPath $program -Algorithm SHA256).Hash.ToLowerInvariant() -cne $CaddySha256) {
    throw 'Staged Caddy SHA-256 verification failed'
}
$parsed = @($LocalAddress | ForEach-Object { [Net.IPAddress]::Parse($_) })
$ipv6Bytes = $parsed[1].GetAddressBytes()
if ($parsed[0].AddressFamily -ne 'InterNetwork' -or $parsed[1].AddressFamily -ne 'InterNetworkV6' -or
    $parsed[0].ToString() -notmatch '^(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)' -or
    (($ipv6Bytes[0] -band 0xFE) -ne 0xFC)) {
    throw 'LocalAddress must be one RFC1918 IPv4 followed by one ULA IPv6 address'
}
Get-NetFirewallRule -DisplayName 'Local RAG HTTPS' -ErrorAction SilentlyContinue |
    Disable-NetFirewallRule | Out-Null
Get-NetFirewallRule -DisplayName 'Local RAG HTTPS' -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'Local RAG HTTPS' -Direction Inbound -Action Allow `
    -Profile Private -Protocol TCP -LocalPort 443 -Program $program -Service Any `
    -LocalAddress $LocalAddress -RemoteAddress LocalSubnet -InterfaceType Wired,Wireless `
    -EdgeTraversalPolicy Block | Out-Null
