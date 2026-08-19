[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$LocalAddress,
    [Parameter(Mandatory)][string]$PinnedCaddyProgram,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$PinnedCaddySha256
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'RagTeamLanPreview.psm1') -Force
$address = Assert-RagTeamPreviewRfc1918Address -Address $LocalAddress
$caddy = (Resolve-Path -LiteralPath $PinnedCaddyProgram).Path
if ((Get-FileHash -LiteralPath $caddy -Algorithm SHA256).Hash.ToLowerInvariant() -cne $PinnedCaddySha256) {
    throw 'Preview Caddy executable hash differs from its inventoried contract.'
}
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
$private = @($profiles | Where-Object Name -eq 'Private')
$public = @($profiles | Where-Object Name -eq 'Public')
if ($private.Count -ne 1 -or -not $private[0].Enabled -or
    [string]$private[0].DefaultInboundAction -ne 'Block' -or
    $public.Count -ne 1 -or -not $public[0].Enabled -or
    [string]$public[0].DefaultInboundAction -ne 'Block') {
    throw 'Private and Public firewall profiles must be enabled with default inbound Block.'
}
$rule = @(Get-NetFirewallRule -DisplayName 'Local RAG Team LAN Preview HTTPS' `
    -PolicyStore ActiveStore -ErrorAction Stop)
if ($rule.Count -ne 1 -or $rule[0].Enabled -ne 'True' -or
    $rule[0].Direction -ne 'Inbound' -or $rule[0].Action -ne 'Allow' -or
    [string]$rule[0].Profile -ne 'Private') {
    throw 'The exact Private preview ingress firewall rule is absent.'
}
$port = $rule[0] | Get-NetFirewallPortFilter
$app = $rule[0] | Get-NetFirewallApplicationFilter
$service = $rule[0] | Get-NetFirewallServiceFilter
$addressFilter = $rule[0] | Get-NetFirewallAddressFilter
$interface = $rule[0] | Get-NetFirewallInterfaceTypeFilter
if ([string]$port.Protocol -notin @('TCP','6') -or [string]$port.LocalPort -cne '443' -or
    [IO.Path]::GetFullPath([string]$app.Program) -cne $caddy -or
    [string]$service.Service -cne 'Any' -or
    [string]$addressFilter.LocalAddress -cne $address -or
    [string]$addressFilter.RemoteAddress -cne 'LocalSubnet' -or
    (@([string]$interface.InterfaceType -split ',') | ForEach-Object { $_.Trim() } | Sort-Object) -join ',' -cne 'Wired,Wireless' -or
    [string]$rule[0].EdgeTraversalPolicy -cne 'Block') {
    throw 'The preview ingress rule is broader than exact Private/LocalSubnet/Caddy/443 scope.'
}
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'RagFirewallClassification.ps1')
$competingRules = @(Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True `
    -Direction Inbound -Action Allow | Where-Object DisplayName -ne $rule[0].DisplayName |
    ForEach-Object {
        $candidatePort = $_ | Get-NetFirewallPortFilter
        $candidateApp = $_ | Get-NetFirewallApplicationFilter
        $candidateAddress = $_ | Get-NetFirewallAddressFilter
        [pscustomobject]@{
            package_family_name=$_.PackageFamilyName
            protocol=[string]$candidatePort.Protocol
            local_port=(@($candidatePort.LocalPort) -join ',')
            program=[string]$candidateApp.Program
            local_address=(@($candidateAddress.LocalAddress) -join ',')
        }
    } | Where-Object { Test-RagRuleCanAdmitPinnedCaddy443 $_ $caddy })
if ($competingRules.Count -gt 0) {
    throw 'Another inbound allow rule could admit the preview Caddy listener on TCP 443.'
}
$listeners = @(Get-NetTCPConnection -State Listen)
$https = @($listeners | Where-Object LocalPort -eq 443)
if ($https.Count -ne 1 -or [string]$https[0].LocalAddress -cne $address) {
    throw 'Exactly one attended IPv4 TCP 443 listener is required.'
}
$caddyProcess = Get-Process -Id ([int]$https[0].OwningProcess) -ErrorAction Stop
if ([IO.Path]::GetFullPath([string]$caddyProcess.Path) -cne $caddy) {
    throw 'The attended TCP 443 listener is not owned by the inventoried Caddy executable.'
}
foreach ($portNumber in @(3000,5432,8000,8443,8100,8101,8102,9000,11434)) {
    $exposed = @($listeners | Where-Object {
        $_.LocalPort -eq $portNumber -and $_.LocalAddress -notin @('127.0.0.1','::1')
    })
    if ($exposed.Count -gt 0) { throw "Internal Local RAG port is exposed: $portNumber" }
}
$resolved = @([Net.Dns]::GetHostAddresses('rag.home.arpa') | ForEach-Object { $_.ToString() })
if ($resolved.Count -ne 1 -or $resolved[0] -cne $address) {
    throw 'rag.home.arpa must resolve to exactly the attended preview IPv4 address.'
}
[pscustomobject]@{
    result='pass';profile='team_lan_preview_unsigned';local_address=$address
    canonical_origin='https://rag.home.arpa';https_port=443;ipv6_enabled=$false
    internal_listeners='loopback_only';api_transport='loopback_mtls'
    firewall_profile='Private';remote_scope='LocalSubnet'
} | ConvertTo-Json -Depth 3
