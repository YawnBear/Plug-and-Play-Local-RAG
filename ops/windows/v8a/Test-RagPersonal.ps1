[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'LocalRAG\Personal')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagPersonal.psm1') -Force

$root = Assert-RagPersonalPathSafe -Path $InstallRoot
$journalPath = Join-Path $root 'state\installation-journal.json'
$journal = Read-RagPersonalJson -Path $journalPath
Assert-RagPersonalJournal -Journal $journal
$docker = (Get-Command docker.exe -ErrorAction Stop).Source
$containers = @(& $docker ps --filter `
    "label=com.docker.compose.project=$($journal.compose_project)" --format '{{.ID}}')
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Personal containers.' }
$validated = 0
foreach ($container in $containers) {
    if ([string]::IsNullOrWhiteSpace($container)) { continue }
    $label = (& $docker inspect --format `
        '{{ index .Config.Labels "com.localrag.installation-id" }}' $container).Trim()
    if ($LASTEXITCODE -ne 0 -or $label -cne $journal.installation_id) {
        throw 'A Personal container does not match the installation ledger.'
    }
    $validated++
}
[pscustomobject]@{
    result = if ($validated -eq 2) { 'pass' } else { 'fail' }
    profile = 'personal'
    state = $journal.state
    validated_containers = $validated
    setup_required = $journal.state -ceq 'setup_required'
    mutations_performed = $false
} | ConvertTo-Json -Compress
if ($validated -ne 2) { exit 2 }
