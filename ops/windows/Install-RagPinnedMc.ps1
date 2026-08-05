[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param(
    [Parameter(Mandatory)][string]$SourcePath,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedSha256
)

$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Install-RagPinnedMc.ps1 must run elevated'
}

$source = (Resolve-Path -LiteralPath $SourcePath).Path
$sourceItem = Get-Item -LiteralPath $source -Force
if ($sourceItem.PSIsContainer -or
    ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant() -cne
        $ExpectedSha256) {
    throw 'Pinned mc staging input is invalid'
}

$toolRoot = Join-Path $env:ProgramFiles 'LocalRAG-Tools\mc'
$destination = Join-Path $toolRoot 'mc.exe'
if (-not $PSCmdlet.ShouldProcess($destination, 'Install protected pinned mc client')) { return }

if (Test-Path -LiteralPath $toolRoot) {
    $unexpected = @(Get-ChildItem -LiteralPath $toolRoot -Force | Where-Object Name -cne 'mc.exe')
    if ($unexpected.Count -gt 0) { throw 'Pinned mc tool root contains unexpected content' }
} else {
    New-Item -ItemType Directory -Path $toolRoot | Out-Null
}

if (Test-Path -LiteralPath $destination) {
    & icacls.exe $destination /inheritance:r /grant:r `
        '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Existing pinned mc destination ACL could not be made verifiable'
    }
    $installed = Get-Item -LiteralPath $destination -Force
    if ($installed.PSIsContainer -or
        ($installed.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash.ToLowerInvariant() -cne
            $ExpectedSha256) {
        throw 'Existing pinned mc destination does not match the approved binary'
    }
} else {
    $pending = Join-Path $toolRoot ('mc-' + [guid]::NewGuid().ToString('N') + '.pending')
    try {
        Copy-Item -LiteralPath $source -Destination $pending
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $pending).Hash.ToLowerInvariant() -cne
            $ExpectedSha256) { throw 'Pinned mc copy verification failed' }
        [IO.File]::Move($pending, $destination)
    } finally {
        if (Test-Path -LiteralPath $pending) { Remove-Item -LiteralPath $pending -Force }
    }
}

& icacls.exe $toolRoot /setowner '*S-1-5-32-544' /T /C | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Pinned mc owner lockdown failed' }
& icacls.exe $toolRoot /inheritance:r /grant:r `
    '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' /T /C | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Pinned mc DACL lockdown failed' }
& icacls.exe $destination /inheritance:r /grant:r `
    '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Pinned mc file DACL lockdown failed' }
foreach ($path in @($toolRoot, $destination)) {
    $acl = Get-Acl -LiteralPath $path
    $unapproved = @($acl.Access | Where-Object {
        $_.IdentityReference.Value -cnotin @(
            'NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators'
        )
    } | Select-Object -ExpandProperty IdentityReference -Unique)
    foreach ($identity in $unapproved) {
        & icacls.exe $path /remove:g ([string]$identity.Value) | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Pinned mc unapproved allow-ACE removal failed: $path"
        }
        & icacls.exe $path /remove:d ([string]$identity.Value) | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Pinned mc unapproved deny-ACE removal failed: $path"
        }
    }
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash.ToLowerInvariant() -cne
    $ExpectedSha256) {
    throw 'Pinned mc post-lockdown verification failed'
}

$writeMask = [long](
    0x2 -bor 0x4 -bor 0x10 -bor 0x40 -bor 0x100 -bor
    0x10000 -bor 0x40000 -bor 0x80000
)
foreach ($path in @($toolRoot, $destination)) {
    $item = Get-Item -LiteralPath $path -Force
    $acl = Get-Acl -LiteralPath $path
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint -or
        $acl.Owner -cne 'BUILTIN\Administrators') {
        throw 'Pinned mc protected path verification failed'
    }
    foreach ($rule in @($acl.Access)) {
        if ($rule.AccessControlType -ceq 'Allow' -and
            $rule.IdentityReference.Value -cnotin @(
                'NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators'
            ) -and ([long]$rule.FileSystemRights -band $writeMask) -ne 0) {
            throw 'Pinned mc path remains writable by an unapproved identity'
        }
    }
    if ($path -ceq $destination) {
        foreach ($identity in @('NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators')) {
            if (@($acl.Access | Where-Object {
                $_.AccessControlType -ceq 'Allow' -and
                $_.IdentityReference.Value -ceq $identity -and
                ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl)
            }).Count -ne 1) {
                throw 'Pinned mc file lacks an exact trusted full-control grant'
            }
        }
    }
}

[ordered]@{
    result = 'installed'
    path = $destination
    sha256 = $ExpectedSha256
    owner = 'BUILTIN\Administrators'
    writable_by = @('NT AUTHORITY\SYSTEM','BUILTIN\Administrators')
} | ConvertTo-Json -Depth 3
