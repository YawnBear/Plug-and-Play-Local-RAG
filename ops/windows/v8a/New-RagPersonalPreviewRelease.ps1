[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$PayloadRoot,
    [Parameter(Mandatory)][string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagPersonalPayload.psm1') -Force

$payload = (Resolve-Path -LiteralPath $PayloadRoot).Path.TrimEnd('\')
$output = [IO.Path]::GetFullPath($OutputRoot).TrimEnd('\')
$payloadItem = Get-Item -LiteralPath $payload -Force
if (-not $payloadItem.PSIsContainer -or
    ($payloadItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Preview payload input must be a regular directory.'
}
if ($output -ieq $payload -or
    $output.StartsWith($payload + '\',[StringComparison]::OrdinalIgnoreCase) -or
    $payload.StartsWith($output + '\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Preview release output must not overlap its payload input.'
}
$manifestPath = Join-Path $payload 'ops\windows\v8a\personal-release.json'
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
if ([string]$manifest.profile_id -cne 'personal' -or
    [string]$manifest.payload_state -cne 'assembled_unsigned') {
    throw 'Preview release packaging requires an assembled_unsigned Personal payload.'
}
if (-not (Test-Path -LiteralPath (Join-Path $payload 'Install-Local-RAG.cmd') `
        -PathType Leaf)) {
    throw 'The unsigned Personal payload has no root one-click installer.'
}
$evidence = Test-RagPersonalPayloadInventory -Root $payload
if (Test-Path -LiteralPath $output) {
    $outputItem = Get-Item -LiteralPath $output -Force
    if (-not $outputItem.PSIsContainer -or
        ($outputItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        @(Get-ChildItem -LiteralPath $output -Force).Count -gt 0) {
        throw 'Preview release output must be absent or an empty regular directory.'
    }
}
if (-not $PSCmdlet.ShouldProcess($output,'Create unsigned Local RAG Personal preview ZIP')) {
    return
}

$createdOutput = -not (Test-Path -LiteralPath $output)
[IO.Directory]::CreateDirectory($output) | Out-Null
$temporary = Join-Path $output ('.Local-RAG-Personal-Preview-' +
    [guid]::NewGuid().ToString('N') + '.zip')
$archive = Join-Path $output 'Local-RAG-Personal-Preview.zip'
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory(
        $payload,$temporary,[IO.Compression.CompressionLevel]::Fastest,$false
    )
    if ((Get-Item -LiteralPath $temporary).Length -lt 1) {
        throw 'Unsigned Personal preview ZIP is empty.'
    }
    [IO.File]::Move($temporary,$archive)
    [pscustomobject]@{
        result='packaged_unsigned_preview'
        distribution='unsigned_preview'
        archive=$archive
        sha256=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        size=[int64](Get-Item -LiteralPath $archive).Length
        payload_tree_sha256=[string]$evidence.tree_sha256
        signing_required=$false
        automatic_updates_available=$false
        user_action='Extract the ZIP, then double-click Install-Local-RAG.cmd.'
    } | ConvertTo-Json -Depth 3
}
finally {
    if (Test-Path -LiteralPath $temporary -PathType Leaf) {
        [IO.File]::Delete($temporary)
    }
    if ($createdOutput -and (Test-Path -LiteralPath $output -PathType Container) -and
        @(Get-ChildItem -LiteralPath $output -Force).Count -eq 0) {
        [IO.Directory]::Delete($output,$false)
    }
}
