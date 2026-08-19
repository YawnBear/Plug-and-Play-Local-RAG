[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][string]$OutputRoot,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$CaddySha256,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$OpenSslSha256,
    [Parameter(Mandatory)][string]$AlembicRevision
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagTeamLanPreview.psm1') -Force
$source = (Resolve-Path -LiteralPath $ReleaseRoot).Path.TrimEnd('\')
$output = [IO.Path]::GetFullPath($OutputRoot).TrimEnd('\')
if ($output -ieq $source -or
    $output.StartsWith($source+'\',[StringComparison]::OrdinalIgnoreCase) -or
    $source.StartsWith($output+'\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Payload output and release input must not overlap.'
}
foreach ($relative in @(
    'runtimes\api-python\python.exe','runtimes\ocr-python\python.exe',
    'runtimes\node\node.exe','tools\openssl\openssl.exe',
    'tools\openssl\openssl.cnf','tools\mc\mc.exe','caddy.exe',
    'RagSupervisorService.exe','deployment.json','csp-header.caddy',
    'apps\api','apps\web','signed-assets\bge-reranker-v2-m3',
    'signed-assets\paddleocr-vl-1.6'
)) {
    $requiredPath = Join-Path $source $relative
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Team/LAN preview release input is incomplete: $relative"
    }
    $requiredItem = Get-Item -LiteralPath $requiredPath -Force
    if (($requiredItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        (-not $requiredItem.PSIsContainer -and $requiredItem.Length -eq 0)) {
        throw "Team/LAN preview release input is empty or unsafe: $relative"
    }
}
$requiredTrees = @(
    'runtimes\api-python','runtimes\ocr-python','runtimes\node',
    'tools\openssl','apps\api','apps\web',
    'signed-assets\bge-reranker-v2-m3','signed-assets\paddleocr-vl-1.6'
)
foreach ($relative in $requiredTrees) {
    $tree = Join-Path $source $relative
    $item = Get-Item -LiteralPath $tree -Force
    if (-not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        @(Get-ChildItem -LiteralPath $tree -Recurse -File -Force).Count -eq 0) {
        throw "Team/LAN preview release input tree is empty or unsafe: $relative"
    }
}
$windowsRoot = Split-Path $PSScriptRoot -Parent
foreach ($name in @('Set-RagAccountRights.ps1','RagFirewallClassification.ps1')) {
    if (-not (Test-Path -LiteralPath (Join-Path $windowsRoot $name) -PathType Leaf)) {
        throw "Team/LAN preview shared installer dependency is missing: $name"
    }
}
$environmentSource = Join-Path $windowsRoot 'environments'
$environmentNames = @('caddy','web','api','ingestion','deletion','inference','ocr')
foreach ($name in $environmentNames) {
    if (-not (Test-Path -LiteralPath (Join-Path $environmentSource "$name.env.example") `
            -PathType Leaf)) {
        throw "Team/LAN preview environment template is missing: $name.env.example"
    }
}
if ((Get-FileHash -LiteralPath (Join-Path $source 'caddy.exe') -Algorithm SHA256).Hash.ToLowerInvariant() -cne $CaddySha256 -or
    (Get-FileHash -LiteralPath (Join-Path $source 'tools\openssl\openssl.exe') -Algorithm SHA256).Hash.ToLowerInvariant() -cne $OpenSslSha256) {
    throw 'Pinned Caddy or OpenSSL input hash does not match.'
}
if (Test-Path -LiteralPath $output) {
    throw 'Team/LAN preview payload output must not already exist.'
}
if (-not $PSCmdlet.ShouldProcess($output,'Assemble unsigned Team/LAN preview payload')) { return }
$stage = "$output.stage-$([guid]::NewGuid().ToString('N'))"
try {
    [IO.Directory]::CreateDirectory($stage) | Out-Null
    Copy-Item -LiteralPath $source -Destination (Join-Path $stage 'release') -Recurse
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Caddyfile') `
        -Destination (Join-Path $stage 'release\Caddyfile') -Force
    [IO.Directory]::CreateDirectory(
        (Join-Path $stage 'release\ops\windows\environments')
    ) | Out-Null
    foreach ($name in $environmentNames) {
        Copy-Item -LiteralPath (Join-Path $environmentSource "$name.env.example") `
            -Destination (Join-Path $stage "release\ops\windows\environments\$name.env.example")
    }
    Copy-Item -LiteralPath $PSScriptRoot `
        -Destination (Join-Path $stage 'release\ops\windows\team_preview') -Recurse
    foreach ($name in @('Set-RagAccountRights.ps1','RagFirewallClassification.ps1')) {
        Copy-Item -LiteralPath (Join-Path $windowsRoot $name) `
            -Destination (Join-Path (Join-Path $stage 'release\ops\windows') $name)
    }
    [IO.Directory]::CreateDirectory((Join-Path $stage 'ops\windows')) | Out-Null
    Copy-Item -LiteralPath $PSScriptRoot -Destination (Join-Path $stage 'ops\windows\team_preview') -Recurse
    foreach ($name in @('Set-RagAccountRights.ps1','RagFirewallClassification.ps1')) {
        $shared = Join-Path $windowsRoot $name
        Copy-Item -LiteralPath $shared `
            -Destination (Join-Path (Join-Path $stage 'ops\windows') $name)
    }
    foreach ($name in @('Install','Update','Repair','Uninstall')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "$name-Local-RAG-LAN.cmd") -Destination $stage
    }
    $contract = [ordered]@{
        schema_version=1;profile='team_lan_preview_unsigned';payload_state='assembled_unsigned'
        authenticity='unverified_unsigned';automatic_updates_available=$false
        alembic_revision=$AlembicRevision;caddy_sha256=$CaddySha256
        openssl_sha256=$OpenSslSha256
    }
    [IO.File]::WriteAllText((Join-Path $stage 'team-preview-release.json'),
        (($contract | ConvertTo-Json -Depth 3)+"`n"),[Text.UTF8Encoding]::new($false))
    Copy-Item -LiteralPath (Join-Path $stage 'team-preview-release.json') `
        -Destination (Join-Path $stage 'release\team-preview-release.json')
    $inventory = New-RagTeamPreviewInventory -Root $stage
    [IO.Directory]::Move($stage,$output)
    [pscustomobject]@{
        result='assembled_unsigned';profile='team_lan_preview_unsigned'
        authenticity='unverified_unsigned';automatic_updates_available=$false
        output=$output;tree_sha256=$inventory.tree_sha256;file_count=$inventory.file_count
    } | ConvertTo-Json -Depth 3
} finally {
    if (Test-Path -LiteralPath $stage -PathType Container) { [IO.Directory]::Delete($stage,$true) }
}
