[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][hashtable]$ServiceSid,
    [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'

$apiReaders = @('api','ingestion','deletion','inference','ocr')
$plan = @(
    [pscustomobject]@{ relative='apps'; services=@('web') + $apiReaders; recursive=$false },
    [pscustomobject]@{ relative='runtimes'; services=@('web') + $apiReaders; recursive=$false },
    [pscustomobject]@{ relative='signed-assets'; services=@('inference','ocr'); recursive=$false },
    [pscustomobject]@{ relative='runtimes\api-python'; services=$apiReaders; recursive=$true },
    [pscustomobject]@{ relative='apps\api'; services=$apiReaders; recursive=$true },
    [pscustomobject]@{ relative='runtimes\node'; services=@('web'); recursive=$true },
    [pscustomobject]@{ relative='apps\web'; services=@('web'); recursive=$true },
    # The API readiness endpoint verifies this executable before the OCR
    # service is admitted. Keep the immutable runtime read/execute-only for
    # both identities; only RagOcrSvc receives writable OCR work directories.
    [pscustomobject]@{ relative='runtimes\ocr-python'; services=@('api','ocr'); recursive=$true },
    [pscustomobject]@{ relative='signed-assets\bge-reranker-v2-m3'; services=@('inference'); recursive=$true },
    [pscustomobject]@{ relative='signed-assets\paddleocr-vl-1.6'; services=@('ocr'); recursive=$true }
)
$expectedKeys = @('api','caddy','deletion','inference','ingestion','ocr','web')
if ((@($ServiceSid.Keys | Sort-Object) -join ',') -cne ($expectedKeys -join ',')) {
    throw 'Release ACL service SID map is not exact'
}
foreach ($entry in $plan) {
    if (-not (Test-Path -LiteralPath (Join-Path $ReleaseRoot $entry.relative))) {
        throw "Release ACL target is missing: $($entry.relative)"
    }
}
if ($PlanOnly) {
    $plan | ConvertTo-Json -Depth 4
    return
}

# Non-inheriting traverse/read on the managed release root; descendants are
# protected independently below with only their relevant service identities.
foreach ($service in @('web') + $apiReaders) {
    icacls.exe $ReleaseRoot /grant:r "*$($ServiceSid[$service]):(RX)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Release-root traverse grant failed: $service" }
}
foreach ($entry in $plan) {
    $target = Join-Path $ReleaseRoot $entry.relative
    $grants = @(
        '*S-1-5-18:(OI)(CI)(F)',
        '*S-1-5-18:(F)',
        '*S-1-5-32-544:(OI)(CI)(F)',
        '*S-1-5-32-544:(F)'
    ) + @($entry.services | ForEach-Object {
        "*$($ServiceSid[$_]):(OI)(CI)(RX)"
        "*$($ServiceSid[$_]):(RX)"
    })
    $recursiveArguments = if ($entry.recursive) { @('/T','/C') } else { @() }
    & icacls.exe $target /setowner '*S-1-5-18' @recursiveArguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Release ACL owner failed: $($entry.relative)" }
    & icacls.exe $target /inheritance:r /grant:r $grants @recursiveArguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Release ACL application failed: $($entry.relative)" }
}
