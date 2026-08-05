[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$PrivateKeyPath,
    [Parameter(Mandatory)][string]$AllowedSignersOutput
)
$ErrorActionPreference = 'Stop'
foreach ($path in @($PrivateKeyPath, "$PrivateKeyPath.pub", $AllowedSignersOutput)) {
    if (Test-Path -LiteralPath $path) { throw "Refusing to overwrite signing material: $path" }
}
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$privateFullPath = [IO.Path]::GetFullPath($PrivateKeyPath)
if ($privateFullPath.StartsWith($repositoryRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The offline release private key must not be created inside the repository'
}
if (-not $PSCmdlet.ShouldProcess($privateFullPath, 'Generate protected offline Ed25519 release identity')) {
    return
}
$sshKeygen = Join-Path ([Environment]::SystemDirectory) 'OpenSSH\ssh-keygen.exe'
& $sshKeygen -q -t ed25519 -N '""' -C local-rag-release-anchor -f $privateFullPath
if ($LASTEXITCODE -ne 0) { throw 'Offline Ed25519 release key generation failed' }
$publicParts = (Get-Content -Raw -LiteralPath "$privateFullPath.pub").Trim().Split(' ')
if ($publicParts.Count -lt 2 -or $publicParts[0] -cne 'ssh-ed25519') {
    throw 'Generated release public key is invalid'
}
[IO.File]::WriteAllText(
    $AllowedSignersOutput,
    "rag-release $($publicParts[0]) $($publicParts[1])`n",
    [Text.UTF8Encoding]::new($false)
)
icacls.exe $privateFullPath /setowner '*S-1-5-18' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Offline release private-key owner lockdown failed' }
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls.exe $privateFullPath /remove:g "*$currentSid" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Offline release private-key operator grant removal failed' }
icacls.exe $privateFullPath /inheritance:r /grant:r `
    '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Offline release private-key ACL lockdown failed' }
[pscustomobject]@{
    result='generated'
    private_key=$privateFullPath
    allowed_signers=$AllowedSignersOutput
    allowed_signers_sha256=(
        Get-FileHash -LiteralPath $AllowedSignersOutput -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    next_step='Replace release-allowed-signers and embedded digest, then sign the initial release manifest.'
} | ConvertTo-Json
