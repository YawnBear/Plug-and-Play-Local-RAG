[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$LocalAddress,
    [string]$HostsPath = (Join-Path $env:SystemRoot 'System32\drivers\etc\hosts')
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'RagTeamLanPreview.psm1') -Force
$address = Assert-RagTeamPreviewRfc1918Address -Address $LocalAddress
$begin = '# BEGIN LOCAL RAG TEAM PREVIEW HOST'
$end = '# END LOCAL RAG TEAM PREVIEW HOST'
$content = [IO.File]::ReadAllText($HostsPath,[Text.UTF8Encoding]::new($false,$true))
$pattern = "(?ms)^$([regex]::Escape($begin))\r?\n.*?^$([regex]::Escape($end))(?:\r?\n)?"
if ([regex]::Matches($content,$pattern).Count -gt 1) { throw 'Duplicate preview hosts blocks are refused.' }
$outside = [regex]::Replace($content,$pattern,'')
if (@($outside -split '\r?\n' | Where-Object {
    ($_ -replace '#.*$','').Trim() -match '(^|\s)rag\.home\.arpa(\s|$)'
}).Count -gt 0) { throw 'An unmanaged rag.home.arpa hosts entry is present.' }
$newline = if ($content.Contains("`r`n")) { "`r`n" } else { "`n" }
$updated = [regex]::Replace($content,$pattern,'')
if ($updated.Length -gt 0 -and -not $updated.EndsWith($newline)) { $updated += $newline }
$updated += @($begin,"$address rag.home.arpa",$end,'') -join $newline
if (-not $PSCmdlet.ShouldProcess($HostsPath,'Set exact IPv4 preview hosts block')) { return }
$temporary = "$HostsPath.team-preview-$([guid]::NewGuid().ToString('N')).tmp"
[IO.File]::WriteAllText($temporary,$updated,[Text.UTF8Encoding]::new($false))
[IO.File]::Replace($temporary,$HostsPath,$null,$true)
