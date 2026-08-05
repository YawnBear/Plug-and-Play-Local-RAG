$ErrorActionPreference = 'Stop'

function Undo-RagCertificateLeafSetSwitch {
    param([Parameter(Mandatory)][object[]]$Journal)
    $failures = [Collections.Generic.List[string]]::new()
    for ($index = $Journal.Count - 1; $index -ge 0; $index--) {
        $entry = $Journal[$index]
        try {
            if (-not (Test-Path -LiteralPath $entry.Destination -PathType Leaf)) {
                throw "mutation destination is missing: $($entry.Destination)"
            }
            if (Test-Path -LiteralPath $entry.Source) {
                throw "mutation source was unexpectedly recreated: $($entry.Source)"
            }
            Move-Item -LiteralPath $entry.Destination -Destination $entry.Source
        } catch {
            $failures.Add($_.Exception.Message)
        }
    }
    if ($failures.Count -gt 0) {
        throw "Certificate mutation rollback failed: $($failures -join '; ')"
    }
}

function Invoke-RagCertificateLeafSetSwitch {
    param(
        [Parameter(Mandatory)][string]$LiveCertificateRoot,
        [Parameter(Mandatory)][string]$LiveSecretRoot,
        [Parameter(Mandatory)][string]$StageCertificateRoot,
        [Parameter(Mandatory)][string]$StageSecretRoot,
        [Parameter(Mandatory)][string]$RollbackCertificateRoot,
        [Parameter(Mandatory)][string]$RollbackSecretRoot,
        [Parameter(Mandatory)][string[]]$LeafNames,
        [ValidateRange(0, 16)][int]$FaultAfterMutation = 0
    )
    $journal = [Collections.Generic.List[object]]::new()
    $mutations = [Collections.Generic.List[object]]::new()
    foreach ($leafName in $LeafNames) {
        $mutations.Add([pscustomobject]@{
            Source = Join-Path $LiveCertificateRoot "$leafName.crt"
            Destination = Join-Path $RollbackCertificateRoot "$leafName.crt"
        })
        $mutations.Add([pscustomobject]@{
            Source = Join-Path $LiveSecretRoot "$leafName.key"
            Destination = Join-Path $RollbackSecretRoot "$leafName.key"
        })
        $mutations.Add([pscustomobject]@{
            Source = Join-Path $StageCertificateRoot "$leafName.crt"
            Destination = Join-Path $LiveCertificateRoot "$leafName.crt"
        })
        $mutations.Add([pscustomobject]@{
            Source = Join-Path $StageSecretRoot "$leafName.key"
            Destination = Join-Path $LiveSecretRoot "$leafName.key"
        })
    }
    try {
        foreach ($mutation in $mutations) {
            if (-not (Test-Path -LiteralPath $mutation.Source -PathType Leaf)) {
                throw "Certificate mutation source is missing: $($mutation.Source)"
            }
            if (Test-Path -LiteralPath $mutation.Destination) {
                throw "Certificate mutation destination already exists: $($mutation.Destination)"
            }
            Move-Item -LiteralPath $mutation.Source -Destination $mutation.Destination
            $journal.Add($mutation)
            if ($FaultAfterMutation -gt 0 -and $journal.Count -eq $FaultAfterMutation) {
                throw "Injected certificate mutation fault after mutation $FaultAfterMutation"
            }
        }
    } catch {
        $switchFailure = $_
        if ($journal.Count -gt 0) {
            try {
                Undo-RagCertificateLeafSetSwitch -Journal $journal.ToArray()
            } catch {
                throw "$($switchFailure.Exception.Message); $($_.Exception.Message)"
            }
        }
        throw $switchFailure
    }
    return $journal.ToArray()
}
