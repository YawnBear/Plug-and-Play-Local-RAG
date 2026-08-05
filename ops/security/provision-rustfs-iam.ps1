[CmdletBinding()]
param(
    [Parameter(Mandatory)][uri]$Endpoint,
    [Parameter(Mandatory)][ValidatePattern('^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$')]
    [string]$Bucket,
    [Parameter(Mandatory)][string]$SecretOutputDirectory,
    [Parameter(Mandatory)][string]$McPath,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$McSha256,
    [string]$RootCredentialFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$WriteCapableRightsMask = [long](
    0x2 -bor 0x4 -bor 0x10 -bor 0x40 -bor 0x100 -bor
    0x10000 -bor 0x40000 -bor 0x80000
)

if (
    $Endpoint.Scheme -cne 'http' -or
    -not [Net.IPAddress]::IsLoopback([Net.IPAddress]::Parse($Endpoint.DnsSafeHost)) -or
    $Endpoint.UserInfo -or $Endpoint.Query -or $Endpoint.Fragment -or
    $Endpoint.AbsolutePath -cne '/'
) {
    throw 'RustFS bootstrap endpoint must be a credential-free loopback HTTP origin.'
}

$accessPointer = $null
$secretPointer = $null
$rootAccessPlain = $null
$rootSecretPlain = $null
if ([string]::IsNullOrWhiteSpace($RootCredentialFile)) {
    $rootAccess = Read-Host 'RustFS root access key' -AsSecureString
    $rootSecret = Read-Host 'RustFS root secret key' -AsSecureString
    $accessPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($rootAccess)
    $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($rootSecret)
} else {
    $credentialPath = [IO.Path]::GetFullPath($RootCredentialFile)
    $credentialItem = Get-Item -LiteralPath $credentialPath -Force -ErrorAction Stop
    foreach ($target in @($credentialItem.Directory.FullName, $credentialPath)) {
        $targetItem = Get-Item -LiteralPath $target -Force -ErrorAction Stop
        $credentialAcl = Get-Acl -LiteralPath $target
        if (($target -ceq $credentialPath -and $targetItem.PSIsContainer) -or
            ($targetItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $credentialAcl.Owner -cnotin @('NT AUTHORITY\SYSTEM','BUILTIN\Administrators')) {
            throw 'Protected RustFS root-credential file is invalid.'
        }
        foreach ($rule in @($credentialAcl.Access)) {
            if ($rule.AccessControlType -ceq 'Allow' -and
                $rule.IdentityReference.Value -cnotin @(
                    'NT AUTHORITY\SYSTEM','BUILTIN\Administrators'
                )) {
                throw 'Protected RustFS root-credential file grants an unapproved identity.'
            }
        }
    }
    $rootValues = @{}
    foreach ($line in [IO.File]::ReadAllLines($credentialPath)) {
        if ($line -cnotmatch '^(RUSTFS_ROOT_ACCESS_KEY|RUSTFS_ROOT_SECRET_KEY)=(.+)$' -or
            $rootValues.ContainsKey($Matches[1])) {
            throw 'Protected RustFS root-credential file has an invalid key set.'
        }
        $rootValues[$Matches[1]] = $Matches[2]
    }
    $actualRootKeys = @($rootValues.Keys | Sort-Object) -join ','
    if ($actualRootKeys -cne 'RUSTFS_ROOT_ACCESS_KEY,RUSTFS_ROOT_SECRET_KEY') {
        throw 'Protected RustFS root-credential file has an invalid key set.'
    }
    $rootAccessPlain = $rootValues.RUSTFS_ROOT_ACCESS_KEY
    $rootSecretPlain = $rootValues.RUSTFS_ROOT_SECRET_KEY
}
$temporaryRoot = $null
$aliasVariable = $null
$previousMcConfigDir = $env:MC_CONFIG_DIR
try {
    $resolvedMc = [IO.Path]::GetFullPath($McPath)
    $mcItem = Get-Item -LiteralPath $resolvedMc
    if (
        $mcItem.PSIsContainer -or
        ($mcItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        (Get-FileHash -LiteralPath $resolvedMc -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            $McSha256
    ) {
        throw 'mc must be an exact pinned regular non-reparse executable.'
    }
    foreach ($target in @($resolvedMc, $mcItem.Directory.FullName)) {
        $mcAcl = Get-Acl -LiteralPath $target
        if ($mcAcl.Owner -cnotin @(
            'NT AUTHORITY\SYSTEM',
            'BUILTIN\Administrators',
            'NT SERVICE\TrustedInstaller'
        )) {
            throw 'Pinned mc executable path has an unapproved owner.'
        }
        foreach ($rule in @($mcAcl.Access)) {
            $rights = [long]$rule.FileSystemRights
            if (
                $rule.AccessControlType -ceq 'Allow' -and
                $rule.IdentityReference.Value -cnotin @(
                    'NT AUTHORITY\SYSTEM',
                    'BUILTIN\Administrators',
                    'NT SERVICE\TrustedInstaller'
                ) -and
                ($rights -band $WriteCapableRightsMask) -ne 0
            ) {
                throw 'Pinned mc executable path is writable by an unapproved identity.'
            }
        }
    }
    if ($null -ne $accessPointer) {
        $rootAccessPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($accessPointer)
        $rootSecretPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
    }
    if (
        [string]::IsNullOrWhiteSpace($rootAccessPlain) -or
        [string]::IsNullOrWhiteSpace($rootSecretPlain)
    ) {
        throw 'RustFS root bootstrap credentials must not be empty.'
    }
    if ($rootAccessPlain -match '[:@\s]' -or $rootSecretPlain -match '[:@\s]') {
        throw 'RustFS root bootstrap credentials are incompatible with MC_HOST syntax.'
    }

    $output = [IO.Path]::GetFullPath($SecretOutputDirectory)
    if (Test-Path -LiteralPath $output) {
        throw 'Secret output directory must not already exist.'
    }
    [IO.Directory]::CreateDirectory($output) | Out-Null
    $current = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $output /inheritance:r /grant:r `
        "${current}:(OI)(CI)(F)" `
        "*S-1-5-18:(OI)(CI)(F)" `
        "*S-1-5-32-544:(OI)(CI)(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not protect the RustFS IAM secret directory.'
    }

    $temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) (
        'rag-v4-rustfs-iam-' + [guid]::NewGuid().ToString('N')
    )
    [IO.Directory]::CreateDirectory($temporaryRoot) | Out-Null
    & icacls.exe $temporaryRoot /inheritance:r /grant:r "${current}:(OI)(CI)(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not protect the RustFS IAM temporary directory.'
    }
    $env:MC_CONFIG_DIR = Join-Path $temporaryRoot 'mc-config'

    $alias = 'ragv3bootstrap' + [guid]::NewGuid().ToString('N')
    $aliasVariable = 'MC_HOST_' + $alias
    $endpointAuthority = $Endpoint.GetLeftPart([UriPartial]::Authority)
    Set-Item -LiteralPath "Env:$aliasVariable" -Value (
        $endpointAuthority.Replace(
            '://', "://$rootAccessPlain`:$rootSecretPlain@"
        ) + '/'
    )
    & $resolvedMc mb --ignore-existing "$alias/$Bucket" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'RustFS bucket bootstrap failed.' }

    $roles = @(
        [pscustomobject]@{
            Name = 'api'
            Actions = @(
                's3:GetBucketLocation', 's3:ListBucket', 's3:GetObject',
                's3:PutObject', 's3:GetObjectAttributes'
            )
        },
        [pscustomobject]@{
            Name = 'ingestion'
            Actions = @('s3:GetObject', 's3:GetObjectAttributes')
        },
        [pscustomobject]@{
            Name = 'deletion'
            Actions = @('s3:DeleteObject')
        },
        [pscustomobject]@{
            Name = 'maintenance'
            Actions = @(
                's3:GetBucketLocation', 's3:ListBucket', 's3:GetObject',
                's3:PutObject', 's3:DeleteObject', 's3:GetObjectAttributes'
            )
        }
    )
    foreach ($role in $roles) {
        $accessKey = 'rag-' + $role.Name + '-' + [guid]::NewGuid().ToString('N')
        $secretBytes = [byte[]]::new(48)
        $random = [Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $random.GetBytes($secretBytes)
        }
        finally {
            $random.Dispose()
        }
        $secretKey = [Convert]::ToBase64String($secretBytes)
        $policyName = 'rag-v4-' + $role.Name
        $statements = @(
            [ordered]@{
                Effect = 'Allow'
                Action = @($role.Actions | Where-Object { $_ -ne 's3:ListBucket' -and $_ -ne 's3:GetBucketLocation' })
                Resource = @("arn:aws:s3:::$Bucket/*")
            }
        )
        $bucketActions = @($role.Actions | Where-Object { $_ -in @('s3:ListBucket', 's3:GetBucketLocation') })
        if ($bucketActions.Count -gt 0) {
            $statements += [ordered]@{
                Effect = 'Allow'
                Action = $bucketActions
                Resource = @("arn:aws:s3:::$Bucket")
            }
        }
        $policyPath = Join-Path $temporaryRoot "$policyName.json"
        [IO.File]::WriteAllText(
            $policyPath,
            ([ordered]@{ Version = '2012-10-17'; Statement = $statements } |
                ConvertTo-Json -Depth 8),
            [Text.UTF8Encoding]::new($false)
        )
        & $resolvedMc admin policy create $alias $policyName $policyPath | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "RustFS $($role.Name) policy creation failed." }
        & $resolvedMc admin user add $alias $accessKey $secretKey | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "RustFS $($role.Name) user creation failed." }
        & $resolvedMc admin policy attach $alias $policyName --user $accessKey | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "RustFS $($role.Name) policy attachment failed." }
        $environmentPath = Join-Path $output "$($role.Name)-object-storage.env"
        [IO.File]::WriteAllLines(
            $environmentPath,
            @(
                "OBJECT_STORAGE_ACCESS_KEY_ID=$accessKey",
                "OBJECT_STORAGE_SECRET_ACCESS_KEY=$secretKey"
            ),
            [Text.UTF8Encoding]::new($false)
        )
        $accessKey = $null
        $secretKey = $null
        [Array]::Clear($secretBytes, 0, $secretBytes.Length)
    }
    & $resolvedMc anonymous set none "$alias/$Bucket" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'RustFS anonymous-access denial failed.' }
    [pscustomobject]@{
        result = 'pass'
        bucket = $Bucket
        identities = @('api', 'ingestion', 'deletion', 'maintenance')
        root_credentials_persisted = $false
        secrets_logged = $false
    } | ConvertTo-Json -Compress
}
finally {
    if ($null -ne $aliasVariable) {
        Remove-Item -LiteralPath "Env:$aliasVariable" -ErrorAction SilentlyContinue
    }
    if ($null -eq $previousMcConfigDir) {
        Remove-Item -LiteralPath 'Env:MC_CONFIG_DIR' -ErrorAction SilentlyContinue
    }
    else {
        $env:MC_CONFIG_DIR = $previousMcConfigDir
    }
    if ($null -ne $temporaryRoot -and (Test-Path -LiteralPath $temporaryRoot)) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
    if ($null -ne $accessPointer) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($accessPointer)
    }
    if ($null -ne $secretPointer) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
    }
    $rootAccessPlain = $null
    $rootSecretPlain = $null
}
