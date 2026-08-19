[CmdletBinding()]
param(
    [Parameter(Mandatory)][uri]$Endpoint,
    [Parameter(Mandatory)][string]$SecretDocument,
    [Parameter(Mandatory)][string]$McPath,
    [Parameter(Mandatory)][string]$WorkingRoot
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$secret = Get-Content -Raw -LiteralPath $SecretDocument | ConvertFrom-Json
$values = $secret.values
$mc = (Resolve-Path -LiteralPath $McPath).Path
if ($mc -notmatch '[\\/]tools[\\/]mc[\\/]mc\.exe$') {
    throw 'Team preview RustFS provisioning requires the packaged mc executable.'
}
$config = Join-Path $WorkingRoot 'mc-config'
[IO.Directory]::CreateDirectory($config) | Out-Null
$env:MC_CONFIG_DIR = $config
$alias = 'ragteampreview'
$rootVariable = 'MC_HOST_' + $alias
$origin = $Endpoint.GetLeftPart([UriPartial]::Authority)
$rootAccess = [uri]::EscapeDataString([string]$values.rustfs_root_access)
$rootSecret = [uri]::EscapeDataString([string]$values.rustfs_root_secret)
Set-Item -LiteralPath "Env:$rootVariable" -Value (
    $origin.Replace('://', "://$rootAccess`:$rootSecret@") + '/'
)
try {
    & $mc mb --ignore-existing "$alias/rag-originals" *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Team preview RustFS bucket creation failed.' }
    $roles = @(
        @{Name='api';Actions=@('s3:GetBucketLocation','s3:ListBucket','s3:GetObject','s3:PutObject','s3:GetObjectAttributes')},
        @{Name='ingestion';Actions=@('s3:GetObject','s3:GetObjectAttributes')},
        @{Name='deletion';Actions=@('s3:DeleteObject')},
        @{Name='maintenance';Actions=@('s3:GetBucketLocation','s3:ListBucket','s3:GetObject','s3:PutObject','s3:DeleteObject','s3:GetObjectAttributes')}
    )
    foreach ($role in $roles) {
        $access = [string]$values.("rustfs_$($role.Name)_access")
        $password = [string]$values.("rustfs_$($role.Name)_secret")
        $policyName = "rag-team-$($role.Name)"
        $objects = @($role.Actions | Where-Object { $_ -notin @('s3:ListBucket','s3:GetBucketLocation') })
        $statements = @([ordered]@{Effect='Allow';Action=$objects;Resource=@('arn:aws:s3:::rag-originals/*')})
        $bucketActions = @($role.Actions | Where-Object { $_ -in @('s3:ListBucket','s3:GetBucketLocation') })
        if ($bucketActions.Count) { $statements += [ordered]@{Effect='Allow';Action=$bucketActions;Resource=@('arn:aws:s3:::rag-originals')} }
        $policy = Join-Path $WorkingRoot "$policyName.json"
        [IO.File]::WriteAllText($policy,([ordered]@{Version='2012-10-17';Statement=$statements}|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
        & $mc admin policy create $alias $policyName $policy *> $null
        if ($LASTEXITCODE -ne 0) { throw "Team preview RustFS policy failed: $($role.Name)" }
        & $mc admin user info $alias $access *> $null
        if ($LASTEXITCODE -ne 0) {
            # mc supports user creation only as: alias, access key, secret key.
            # These operands are transiently visible to local Administrators/SYSTEM,
            # which is the same trust boundary as the ACL-protected store secret file.
            # All native output streams are discarded so credentials cannot be echoed
            # into installer output or logs.
            try {
                & $mc admin user add $alias $access $password *> $null
                if ($LASTEXITCODE -ne 0) {
                    throw "Team preview RustFS identity failed: $($role.Name)"
                }
            }
            finally { $password = $null }
        }
        else { $password = $null }
        & $mc admin policy attach $alias $policyName --user $access *> $null
        if ($LASTEXITCODE -ne 0) { throw "Team preview RustFS policy attachment failed: $($role.Name)" }
        $access = $null
    }
    & $mc anonymous set none "$alias/rag-originals" *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Team preview RustFS anonymous access denial failed.' }
} finally {
    Remove-Item -LiteralPath "Env:$rootVariable" -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath Env:MC_CONFIG_DIR -ErrorAction SilentlyContinue
    $rootAccess = $null
    $rootSecret = $null
    $values = $null
    $secret = $null
}
[pscustomobject]@{
    result='pass';bucket='rag-originals';credentials_logged=$false
    argument_visibility='local_administrators_and_system_only'
} |
    ConvertTo-Json -Compress
