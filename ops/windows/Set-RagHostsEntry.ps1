[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidateSet('Apply','Remove')][string]$Action,
    [Parameter(Mandatory)][ValidateCount(2,2)][string[]]$Address,
    [string]$HostsPath = (Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'),
    [Parameter(Mandatory)][string]$PriorContentPath
)
$ErrorActionPreference = 'Stop'
$begin = '# BEGIN LOCAL RAG MANAGED HOST'
$end = '# END LOCAL RAG MANAGED HOST'
$normalized = @($Address | ForEach-Object { ([Net.IPAddress]::Parse($_)).ToString() })
if (@($normalized | Where-Object { ([Net.IPAddress]$_).AddressFamily -eq 'InterNetwork' }).Count -ne 1 -or
    @($normalized | Where-Object { ([Net.IPAddress]$_).AddressFamily -eq 'InterNetworkV6' }).Count -ne 1) {
    throw 'Managed hosts entry requires exactly one IPv4 and one IPv6 address'
}
$utf8 = [Text.UTF8Encoding]::new($false, $true)
$contentBytes = [IO.File]::ReadAllBytes($HostsPath)
$content = $utf8.GetString($contentBytes)
$newline = if ($content.Contains("`r`n")) { "`r`n" } else { "`n" }
$pattern = "(?ms)^$([regex]::Escape($begin))\r?\n.*?^$([regex]::Escape($end))(?:\r?\n)?"
$managedMatches = [regex]::Matches($content, $pattern)
if ($managedMatches.Count -gt 1) { throw 'Hosts file contains duplicate Local RAG managed blocks' }
if ($Action -ceq 'Apply' -and $managedMatches.Count -ne 0) {
    throw 'Hosts file already contains a Local RAG managed block'
}
$outside = [regex]::Replace($content, $pattern, '')
$conflicts = @(
    $outside -split '\r?\n' |
        Where-Object {
            ($_ -replace '#.*$','').Trim() -match '(^|\s)rag\.home\.arpa(\s|$)'
        }
)
if ($conflicts.Count -gt 0) {
    throw 'Hosts file contains an unmanaged rag.home.arpa entry'
}
$replacement = ''
if ($Action -ceq 'Apply') {
    $replacement = @(
        $begin
        "$($normalized[0]) rag.home.arpa"
        "$($normalized[1]) rag.home.arpa"
        $end
        ''
    ) -join $newline
}
$updated = if ($Action -ceq 'Apply') {
    $prefix = if ($content.Length -gt 0 -and -not $content.EndsWith($newline)) { $newline } else { '' }
    $content + $prefix + $replacement
} else {
    if (-not (Test-Path -LiteralPath $PriorContentPath -PathType Leaf)) {
        throw 'Exact prior hosts bytes are required for removal'
    }
    $priorBytes = [IO.File]::ReadAllBytes($PriorContentPath)
    $prior = $utf8.GetString($priorBytes)
    $priorNewline = if ($prior.Contains("`r`n")) { "`r`n" } else { "`n" }
    $priorPrefix = if ($prior.Length -gt 0 -and -not $prior.EndsWith($priorNewline)) {
        $priorNewline
    } else { '' }
    $expectedCurrent = $prior + $priorPrefix + (@(
        $begin
        "$($normalized[0]) rag.home.arpa"
        "$($normalized[1]) rag.home.arpa"
        $end
        ''
    ) -join $priorNewline)
    if ($content -cne $expectedCurrent) {
        throw 'Hosts file changed after managed block installation; exact rollback is refused'
    }
    $prior
}
if (-not $PSCmdlet.ShouldProcess($HostsPath, "$Action Local RAG managed hosts block")) { return }
$temporary = "$HostsPath.local-rag-$([Guid]::NewGuid().ToString('N')).tmp"
$backup = "$HostsPath.local-rag-$([Guid]::NewGuid().ToString('N')).bak"
try {
    if ($Action -ceq 'Apply') {
        if (Test-Path -LiteralPath $PriorContentPath) {
            throw 'Prior hosts-byte ledger already exists'
        }
        [IO.File]::WriteAllBytes($PriorContentPath, $contentBytes)
    }
    $updatedBytes = if ($Action -ceq 'Remove') {
        [IO.File]::ReadAllBytes($PriorContentPath)
    } else {
        [Text.UTF8Encoding]::new($false).GetBytes($updated)
    }
    [IO.File]::WriteAllBytes($temporary, $updatedBytes)
    [IO.File]::Replace($temporary, $HostsPath, $backup)
    Remove-Item -LiteralPath $backup -Force
    if ($Action -ceq 'Remove') {
        Remove-Item -LiteralPath $PriorContentPath -Force
    }
} finally {
    foreach ($path in @($temporary,$backup)) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        }
    }
}
[pscustomobject]@{ result='changed'; action=$Action; hosts_path=$HostsPath } |
    ConvertTo-Json
