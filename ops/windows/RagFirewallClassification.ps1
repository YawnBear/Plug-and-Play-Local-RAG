$ErrorActionPreference = 'Stop'

function Test-RagPortIsAmbientAny {
    param([Parameter(Mandatory)][string]$PortSpec)
    return @($PortSpec -split ',' | ForEach-Object { $_.Trim() } |
        Where-Object { $_ -ieq 'Any' }).Count -gt 0
}

function Test-RagPortExplicitlyScopes443 {
    param([Parameter(Mandatory)][string]$PortSpec)
    if (Test-RagPortIsAmbientAny $PortSpec) { return $false }
    foreach ($part in @($PortSpec -split ',')) {
        $token = $part.Trim()
        if ($token -ceq '443') { return $true }
        if ($token -match '^([0-9]+)-([0-9]+)$') {
            $lower = [int]$Matches[1]
            $upper = [int]$Matches[2]
            if ($lower -gt $upper -or ($lower -le 443 -and 443 -le $upper)) {
                return $true
            }
        } elseif ($token -notmatch '^[0-9]+$') {
            return $true
        }
    }
    return $false
}

function Test-RagRuleCanAdmitPinnedCaddy443 {
    param(
        [Parameter(Mandatory)][pscustomobject]$Rule,
        [Parameter(Mandatory)][string]$PinnedProgram
    )
    if (-not [string]::IsNullOrWhiteSpace(
            [string]$Rule.package_family_name
        ) -or
        $Rule.protocol -notin @('TCP','Any','256') -or
        (-not (Test-RagPortExplicitlyScopes443 $Rule.local_port) -and
         -not (Test-RagPortIsAmbientAny $Rule.local_port))) { return $false }
    $program = [string]$Rule.program
    if (-not [string]::IsNullOrWhiteSpace($program) -and $program -notin @('Any','*')) {
        try { if ([IO.Path]::GetFullPath($program) -cne [IO.Path]::GetFullPath($PinnedProgram)) { return $false } }
        catch { return $false }
    }
    $local = @($Rule.local_address -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($local.Count -gt 0 -and $local -notcontains 'Any' -and
        @($local | Where-Object { $_ -notin @('127.0.0.1','::1') }).Count -eq 0) { return $false }
    return $true
}
