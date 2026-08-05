[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RenderedHtmlRoot,
    [Parameter(Mandatory)][string]$OutputPath
)

$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputPath) {
    throw 'CSP output path must not already exist'
}
$root = Get-Item -LiteralPath (Resolve-Path -LiteralPath $RenderedHtmlRoot).Path
if (-not $root.PSIsContainer -or ($root.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Rendered HTML root must be a regular directory'
}
$files = @(Get-ChildItem -LiteralPath $root.FullName -Filter '*.html' -File -Recurse)
if ($files.Count -eq 0 -or @($files | Where-Object {
    $_.Attributes -band [IO.FileAttributes]::ReparsePoint
}).Count -ne 0) {
    throw 'Rendered HTML evidence must contain regular HTML files'
}

$scriptHashes = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$styleHashes = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$styleAttributeHashes = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$sha256 = [Security.Cryptography.SHA256]::Create()
try {
foreach ($file in $files) {
    $html = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8)
    foreach ($match in [regex]::Matches(
        $html,
        '<script(?![^>]*\bsrc\s*=)[^>]*>([\s\S]*?)</script>',
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )) {
        $bytes = [Text.Encoding]::UTF8.GetBytes($match.Groups[1].Value)
        $hash = [Convert]::ToBase64String($sha256.ComputeHash($bytes))
        [void]$scriptHashes.Add("'sha256-$hash'")
    }
    foreach ($match in [regex]::Matches(
        $html,
        '<style[^>]*>([\s\S]*?)</style>',
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )) {
        $bytes = [Text.Encoding]::UTF8.GetBytes($match.Groups[1].Value)
        $hash = [Convert]::ToBase64String($sha256.ComputeHash($bytes))
        [void]$styleHashes.Add("'sha256-$hash'")
    }
    foreach ($match in [regex]::Matches(
        $html,
        '\bstyle\s*=\s*(["''])(.*?)\1',
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )) {
        $bytes = [Text.Encoding]::UTF8.GetBytes($match.Groups[2].Value)
        $hash = [Convert]::ToBase64String($sha256.ComputeHash($bytes))
        [void]$styleAttributeHashes.Add("'sha256-$hash'")
    }
}
} finally {
    $sha256.Dispose()
}
if ($scriptHashes.Count -eq 0) {
    throw 'Rendered Next.js HTML contains no inline bootstrap script hashes'
}
$scriptSource = (@("'self'") + @($scriptHashes | Sort-Object)) -join ' '
$styleSourceItems = @("'self'") + @($styleHashes | Sort-Object)
if ($styleAttributeHashes.Count -gt 0) {
    $styleSourceItems += "'unsafe-hashes'"
    $styleSourceItems += @($styleAttributeHashes | Sort-Object)
}
$styleSource = $styleSourceItems -join ' '
$basePolicy = "default-src 'self'; base-uri 'self'; object-src 'none'; form-action 'self'; connect-src 'self'; img-src 'self' blob: data:; style-src $styleSource; script-src $scriptSource; worker-src 'self'"
$defaultPolicy = "frame-ancestors 'none'; $basePolicy"
$documentContentPolicy = "frame-ancestors 'self'; $basePolicy"
if (
    $defaultPolicy -match "'unsafe-inline'" -or
    $defaultPolicy -match "'unsafe-eval'" -or
    $documentContentPolicy -match "'unsafe-inline'" -or
    $documentContentPolicy -match "'unsafe-eval'"
) {
    throw 'Generated CSP must not contain unsafe script or style sources'
}
$content = (
    "header @document_content Content-Security-Policy `"$documentContentPolicy`"`r`n" +
    "header @non_document_content Content-Security-Policy `"$defaultPolicy`"`r`n"
)
[IO.File]::WriteAllText(
    [IO.Path]::GetFullPath($OutputPath),
    $content,
    [Text.UTF8Encoding]::new($false)
)
[pscustomobject]@{
    schema_version = 1
    result = 'pass'
    rendered_html_files = $files.Count
    script_hashes = $scriptHashes.Count
    style_hashes = $styleHashes.Count
    style_attribute_hashes = $styleAttributeHashes.Count
    output_sha256 = (
        Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
} | ConvertTo-Json
