[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateCount(2,2)][string[]]$ExpectedAddress,
    [ValidateSet('DnsServer','HostOnly')][string]$Mode = 'DnsServer'
)
$ErrorActionPreference = 'Stop'
$expected = @(
    $ExpectedAddress |
        ForEach-Object { ([Net.IPAddress]::Parse($_)).ToString() } |
        Sort-Object
)
$answers = if ($Mode -ceq 'DnsServer') {
    @(
        @(
            Resolve-DnsName -Name 'rag.home.arpa' -Type A -DnsOnly -ErrorAction Stop
            Resolve-DnsName -Name 'rag.home.arpa' -Type AAAA -DnsOnly -ErrorAction Stop
        ) |
            Where-Object { $_.Type -in @('A','AAAA') } |
            ForEach-Object { ([Net.IPAddress]::Parse($_.IPAddress)).ToString() } |
            Sort-Object -Unique
    )
} else {
    @(
        [Net.Dns]::GetHostAddresses('rag.home.arpa') |
            ForEach-Object ToString |
            Sort-Object -Unique
    )
}
$result = if (($answers -join ',') -ceq ($expected -join ',')) { 'pass' } else { 'fail' }
[pscustomobject]@{
    schema_version = 1
    mode = 'read_only'
    dns_scope = if ($Mode -ceq 'DnsServer') { 'lan_dns' } else { 'host_only' }
    result = $result
    host = 'rag.home.arpa'
    expected_addresses = $expected
    observed_addresses = $answers
    second_lan_device = 'unverified'
} | ConvertTo-Json -Depth 4
if ($result -cne 'pass') { throw 'rag.home.arpa resolution does not match the exact expected addresses' }
