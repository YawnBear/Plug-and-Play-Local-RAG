[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RenderedHtmlRoot,
    [Parameter(Mandatory)][string]$CspArtifact
)

$ErrorActionPreference = 'Stop'
$artifact = Get-Item -LiteralPath (Resolve-Path -LiteralPath $CspArtifact).Path
if ($artifact.PSIsContainer -or ($artifact.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'CSP artifact must be a regular file'
}
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('rag-csp-' + [guid]::NewGuid().ToString('N') + '.caddy')
try {
    $result = & (Join-Path $PSScriptRoot 'New-RagCspArtifact.ps1') `
        -RenderedHtmlRoot $RenderedHtmlRoot `
        -OutputPath $temporary | ConvertFrom-Json
    $expected = [IO.File]::ReadAllBytes($temporary)
    $actual = [IO.File]::ReadAllBytes($artifact.FullName)
    $matches = (
        $expected.Length -eq $actual.Length -and
        [Convert]::ToBase64String($expected) -ceq [Convert]::ToBase64String($actual)
    )
    $resultObject = [pscustomobject]@{
        schema_version = 1
        mode = 'read_only'
        result = if ($result.result -ceq 'pass' -and $matches) { 'pass' } else { 'fail' }
        unsafe_inline_absent = (
            [Text.Encoding]::UTF8.GetString($actual) -cnotmatch "'unsafe-inline'"
        )
    }
    $resultObject | ConvertTo-Json
    if ($resultObject.result -cne 'pass' -or -not $resultObject.unsafe_inline_absent) {
        exit 1
    }
} finally {
    if (Test-Path -LiteralPath $temporary) {
        [IO.File]::Delete($temporary)
    }
}
