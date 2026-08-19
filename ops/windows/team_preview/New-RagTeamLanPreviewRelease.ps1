[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$PayloadRoot,
    [Parameter(Mandatory)][string]$OutputRoot
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagTeamLanPreview.psm1') -Force

$payload = (Resolve-Path -LiteralPath $PayloadRoot).Path.TrimEnd('\')
$output = [IO.Path]::GetFullPath($OutputRoot).TrimEnd('\')
if ($output -ieq $payload -or
    $output.StartsWith($payload+'\',[StringComparison]::OrdinalIgnoreCase) -or
    $payload.StartsWith($output+'\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Team/LAN preview output and payload roots must not overlap.'
}
foreach ($name in @(
    'Install-Local-RAG-LAN.cmd','Update-Local-RAG-LAN.cmd',
    'Repair-Local-RAG-LAN.cmd','Uninstall-Local-RAG-LAN.cmd'
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $payload $name) -PathType Leaf)) {
        throw "Team/LAN preview payload is missing its root launcher: $name"
    }
}
$inventory = Test-RagTeamPreviewInventory -Root $payload
if (Test-Path -LiteralPath $output) {
    $item = Get-Item -LiteralPath $output -Force
    if (-not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        @(Get-ChildItem -LiteralPath $output -Force).Count -ne 0) {
        throw 'Team/LAN preview output must be absent or an empty regular directory.'
    }
}
if (-not $PSCmdlet.ShouldProcess($output,'Create unsigned Team/LAN preview ZIP')) { return }
[IO.Directory]::CreateDirectory($output) | Out-Null
$archive = Join-Path $output 'Local-RAG-Team-LAN-Preview.zip'
$temporary = Join-Path $output ('.team-preview-'+[guid]::NewGuid().ToString('N')+'.zip')
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory(
        $payload,$temporary,[IO.Compression.CompressionLevel]::Fastest,$false
    )
    Assert-RagTeamPreviewArchive -Archive $temporary
    [IO.File]::Move($temporary,$archive)
    [pscustomobject]@{
        result='packaged_unsigned_preview'
        profile='team_lan_preview_unsigned'
        authenticity='unverified_unsigned'
        automatic_updates_available=$false
        archive=$archive
        sha256=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        size=[int64](Get-Item -LiteralPath $archive).Length
        payload_tree_sha256=$inventory.tree_sha256
    } | ConvertTo-Json -Depth 3
} finally {
    if (Test-Path -LiteralPath $temporary -PathType Leaf) { [IO.File]::Delete($temporary) }
}
