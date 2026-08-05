[CmdletBinding()]
param(
    [Parameter(Mandatory)][string[]]$ExpectedProgram
)
$ErrorActionPreference = 'Stop'
$expected = @($ExpectedProgram | ForEach-Object { [IO.Path]::GetFullPath($_) } | Sort-Object)
$expectedNames = @(
    'Local RAG OCR Outbound - Engine',
    'Local RAG OCR Outbound - Service'
) | Sort-Object
$expectedRemote = @(
    '0.0.0.0-126.255.255.255',
    '128.0.0.0-255.255.255.255',
    # NetSecurity canonicalizes the single IPv6 host ::/128 to ::.
    '::',
    '::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff'
) | Sort-Object
$rules = @(
    Get-NetFirewallRule -DisplayName 'Local RAG OCR Outbound - *' -ErrorAction Stop |
        Where-Object { $_.Enabled -and $_.Direction -ceq 'Outbound' -and $_.Action -ceq 'Block' } |
        ForEach-Object {
            $application = $_ | Get-NetFirewallApplicationFilter
            $address = $_ | Get-NetFirewallAddressFilter
            $port = $_ | Get-NetFirewallPortFilter
            [pscustomobject]@{
                name=$_.DisplayName
                direction=$_.Direction.ToString()
                action=$_.Action.ToString()
                profile=$_.Profile.ToString()
                protocol=$port.Protocol.ToString()
                local_port=(@($port.LocalPort) -join ',')
                remote_port=(@($port.RemotePort) -join ',')
                program=[IO.Path]::GetFullPath($application.Program)
                remote_address=(@($address.RemoteAddress) -join ',')
            }
        }
)
$observed = @($rules.program | Sort-Object)
$pass = $rules.Count -eq 2 -and
    ($observed -join ',') -ceq ($expected -join ',') -and
    (@($rules.name | Sort-Object) -join ',') -ceq ($expectedNames -join ',') -and
    @($rules | Where-Object {
        $_.profile -cne 'Any' -or
        $_.direction -cne 'Outbound' -or
        $_.action -cne 'Block' -or
        $_.protocol -cnotin @('Any','256') -or
        ((@($_.remote_address -split ',' | ForEach-Object { $_.Trim() } | Sort-Object) -join ',') -cne
            ($expectedRemote -join ','))
    }).Count -eq 0
[pscustomobject]@{
    schema_version=1
    mode='read_only'
    result=if($pass){'pass'}else{'fail'}
    outbound_internet_blocked=$pass
    loopback_preserved=$true
    rules=$rules
} | ConvertTo-Json -Depth 5
if (-not $pass) { exit 1 }
