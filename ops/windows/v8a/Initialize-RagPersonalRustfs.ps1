[CmdletBinding()]
param(
    [Parameter(Mandatory)][uri]$Endpoint,
    [Parameter(Mandatory)][ValidateSet('rag-originals')][string]$Bucket,
    [Parameter(Mandatory)][string]$SecretDocument,
    [Parameter(Mandatory)][string]$CredentialOutputDirectory,
    [Parameter(Mandatory)][string]$McPath,
    [switch]$DevelopmentSource
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagPersonal.psm1') -Force

if (
    $Endpoint.Scheme -cne 'http' -or
    -not [Net.IPAddress]::IsLoopback([Net.IPAddress]::Parse($Endpoint.DnsSafeHost)) -or
    $Endpoint.UserInfo -or $Endpoint.Query -or $Endpoint.Fragment -or
    $Endpoint.AbsolutePath -cne '/'
) {
    throw 'Personal RustFS bootstrap requires a credential-free loopback HTTP origin.'
}
$resolvedMc = [IO.Path]::GetFullPath($McPath)
$mcItem = Get-Item -LiteralPath $resolvedMc -Force
if ($mcItem.PSIsContainer -or ($mcItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'The packaged mc tool must be a regular non-reparse executable.'
}
if (-not $DevelopmentSource -and $resolvedMc -notmatch '[\\/]tools[\\/]mc[\\/]mc\.exe$') {
    throw 'Packaged Personal installation requires the release-owned mc tool.'
}
$secrets = Read-RagPersonalJson -Path $SecretDocument
$values = $secrets.values
$installationId = [string]$secrets.installation_id
if ($installationId -cnotmatch '^[0-9a-f]{32}$') {
    throw 'Personal RustFS secret identity is invalid.'
}
if (-not (Test-Path -LiteralPath $CredentialOutputDirectory -PathType Container)) {
    [IO.Directory]::CreateDirectory($CredentialOutputDirectory) | Out-Null
    Protect-RagPersonalPath -Path $CredentialOutputDirectory -Directory
}

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'rag-personal-rustfs-' + [guid]::NewGuid().ToString('N')
)
[IO.Directory]::CreateDirectory($temporaryRoot) | Out-Null
Protect-RagPersonalPath -Path $temporaryRoot -Directory
$previousConfig = $env:MC_CONFIG_DIR
$aliasVariable = $null
try {
    $env:MC_CONFIG_DIR = Join-Path $temporaryRoot 'mc-config'
    $alias = 'ragpersonal' + $installationId.Substring(0, 12)
    $aliasVariable = 'MC_HOST_' + $alias
    $encodedAccess = [uri]::EscapeDataString([string]$values.rustfs_root_access)
    $encodedSecret = [uri]::EscapeDataString([string]$values.rustfs_root_secret)
    $origin = $Endpoint.GetLeftPart([UriPartial]::Authority)
    Set-Item -LiteralPath "Env:$aliasVariable" -Value (
        $origin.Replace('://', "://$encodedAccess`:$encodedSecret@") + '/'
    )
    & $resolvedMc mb --ignore-existing "$alias/$Bucket" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Personal RustFS bucket bootstrap failed.' }

    $roles = @(
        [pscustomobject]@{
            Name='api'; Access=[string]$values.rustfs_api_access;
            Secret=[string]$values.rustfs_api_secret;
            Actions=@('s3:GetBucketLocation','s3:ListBucket','s3:GetObject','s3:PutObject','s3:GetObjectAttributes')
        },
        [pscustomobject]@{
            Name='ingestion'; Access=[string]$values.rustfs_ingestion_access;
            Secret=[string]$values.rustfs_ingestion_secret;
            Actions=@('s3:GetObject','s3:GetObjectAttributes')
        },
        [pscustomobject]@{
            Name='deletion'; Access=[string]$values.rustfs_deletion_access;
            Secret=[string]$values.rustfs_deletion_secret;
            Actions=@('s3:DeleteObject')
        },
        [pscustomobject]@{
            Name='maintenance'; Access=[string]$values.rustfs_maintenance_access;
            Secret=[string]$values.rustfs_maintenance_secret;
            Actions=@('s3:GetBucketLocation','s3:ListBucket','s3:GetObject','s3:PutObject','s3:DeleteObject','s3:GetObjectAttributes')
        }
    )
    foreach ($role in $roles) {
        $policyName = "rag-personal-$($role.Name)-$($installationId.Substring(0, 12))"
        $objectActions = @($role.Actions | Where-Object {
            $_ -notin @('s3:ListBucket','s3:GetBucketLocation')
        })
        $statements = @([ordered]@{
            Effect='Allow'; Action=$objectActions; Resource=@("arn:aws:s3:::$Bucket/*")
        })
        $bucketActions = @($role.Actions | Where-Object {
            $_ -in @('s3:ListBucket','s3:GetBucketLocation')
        })
        if ($bucketActions.Count -gt 0) {
            $statements += [ordered]@{
                Effect='Allow'; Action=$bucketActions; Resource=@("arn:aws:s3:::$Bucket")
            }
        }
        $policyPath = Join-Path $temporaryRoot "$policyName.json"
        Write-RagPersonalUtf8File -Path $policyPath -Value (
            [ordered]@{Version='2012-10-17';Statement=$statements} | ConvertTo-Json -Depth 8
        ) -Protect
        & $resolvedMc admin policy create $alias $policyName $policyPath | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Personal RustFS $($role.Name) policy failed." }
        & $resolvedMc admin user add $alias $role.Access $role.Secret | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Personal RustFS $($role.Name) identity failed." }
        & $resolvedMc admin policy attach $alias $policyName --user $role.Access | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Personal RustFS $($role.Name) policy attachment failed." }
        Write-RagPersonalEnvironmentFile `
            -Path (Join-Path $CredentialOutputDirectory "$($role.Name)-object-storage.env") `
            -Values ([ordered]@{
                OBJECT_STORAGE_ACCESS_KEY_ID=$role.Access
                OBJECT_STORAGE_SECRET_ACCESS_KEY=$role.Secret
            })
    }
    & $resolvedMc anonymous set none "$alias/$Bucket" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Personal RustFS anonymous denial failed.' }
}
finally {
    if ($null -ne $aliasVariable) {
        Remove-Item -LiteralPath "Env:$aliasVariable" -ErrorAction SilentlyContinue
    }
    if ($null -eq $previousConfig) {
        Remove-Item -LiteralPath 'Env:MC_CONFIG_DIR' -ErrorAction SilentlyContinue
    }
    else { $env:MC_CONFIG_DIR = $previousConfig }
    if (Test-Path -LiteralPath $temporaryRoot) {
        [IO.Directory]::Delete($temporaryRoot, $true)
    }
    $encodedAccess = $null
    $encodedSecret = $null
}
[pscustomobject]@{
    result='pass'
    bucket=$Bucket
    identities=@('api','ingestion','deletion','maintenance')
    root_credentials_persisted_to_application=$false
    mutations_performed=$true
} | ConvertTo-Json -Compress
