$ErrorActionPreference = 'Stop'

function Get-RagMachineFingerprint {
    $identity = Get-CimInstance Win32_ComputerSystemProduct -ErrorAction Stop
    $uuid = ([string]$identity.UUID).Trim().ToLowerInvariant()
    $name = ([string]$env:COMPUTERNAME).Trim().ToLowerInvariant()
    if ($uuid -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' -or
        [string]::IsNullOrWhiteSpace($name)) {
        throw 'Stable machine identity is unavailable'
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes("local-rag-host-v1`n$name`n$uuid")
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Assert-RagFreshHostEvidence {
    param(
        [Parameter(Mandatory)]$Evidence,
        [Parameter(Mandatory)][int]$MaxAgeSeconds
    )
    if ($Evidence.machine_fingerprint -cne (Get-RagMachineFingerprint)) {
        throw 'Dependency evidence was captured on a different machine'
    }
    $captured = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        [string]$Evidence.captured_at,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal,
        [ref]$captured
    )) {
        throw 'Dependency evidence capture time is invalid'
    }
    $age = ([DateTimeOffset]::UtcNow - $captured).TotalSeconds
    if ($age -lt -5 -or $age -gt $MaxAgeSeconds) {
        throw 'Dependency evidence is stale or from the future'
    }
}
