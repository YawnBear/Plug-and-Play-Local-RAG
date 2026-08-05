[CmdletBinding()]
param(
    [string]$PinnedCaddyProgram = 'C:\ProgramData\LocalRAG\signed-stage\release-67514bc0449ae9b1465cf3d59ab269cb451e8ed88d991e461b24d1337b67f536\caddy.exe',
    [Parameter(Mandatory)]
    [ValidateCount(2, 2)]
    [string[]]$ExpectedLocalAddresses
)

$ErrorActionPreference = 'Stop'
$families = @($ExpectedLocalAddresses | ForEach-Object {
    ([Net.IPAddress]::Parse($_)).AddressFamily.ToString()
} | Sort-Object -Unique)
if (($families -join ',') -cne 'InterNetwork,InterNetworkV6') {
    throw 'ExpectedLocalAddresses must contain one IPv4 and one IPv6 address'
}
[pscustomobject]@{
    schema_version = 1
    changes_applied = $false
    desired_rule = [pscustomobject]@{
        display_name = 'Local RAG HTTPS'
        direction = 'Inbound'
        action = 'Allow'
        profile = 'Private'
        protocol = 'TCP'
        local_port = 443
        program = [IO.Path]::GetFullPath($PinnedCaddyProgram)
        service = 'Any'
        local_addresses = $ExpectedLocalAddresses
        remote_address = 'LocalSubnet'
        interface_type = 'Wired,Wireless'
        edge_traversal = 'Block'
    }
    prohibited = @(
        'Public-profile allow rule',
        'HTTP port 80 fallback',
        'router port forwarding',
        'LAN listeners other than Caddy TCP 443'
    )
    attended_admin_command = 'New-NetFirewallRule (constructed only after manifest, executable hash, and administrator approval are verified)'
} | ConvertTo-Json -Depth 6
