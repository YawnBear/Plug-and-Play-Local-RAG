[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidateSet('Apply','Remove')][string]$Action,
    [Parameter(Mandatory)][string]$LocalAddress,
    [Parameter(Mandatory)][string]$PinnedCaddyProgram
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'RagTeamLanPreview.psm1') -Force
$address = Assert-RagTeamPreviewRfc1918Address -Address $LocalAddress
$program = (Resolve-Path -LiteralPath $PinnedCaddyProgram).Path
$name = 'Local RAG Team LAN Preview HTTPS'
if (-not $PSCmdlet.ShouldProcess($name,"$Action preview firewall rule")) { return }
Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($Action -ceq 'Apply') {
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow `
        -Profile Private -Protocol TCP -LocalPort 443 -LocalAddress $address `
        -RemoteAddress LocalSubnet -InterfaceType Wired,Wireless -Program $program `
        -Service Any -EdgeTraversalPolicy Block | Out-Null
}
