[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param(
    [Parameter(Mandatory)][string]$LanIpv4,
    [Parameter(Mandatory)][string]$LanIpv6
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
. (Join-Path $PSScriptRoot 'RagManagedRootSafety.ps1')

$productionEnvironment = Join-Path $repository '.env.production.local'
$preparationRoot = 'C:\ProgramData\LocalRAG-Preparation'
$roleSecretRoot = Join-Path $preparationRoot 'postgres-role-secrets'
$rustfsRootCredential = Join-Path $preparationRoot 'rustfs-root.env'
$rustfsCredentialRoot = Join-Path $preparationRoot 'object-storage'
$serviceSecretFile = Join-Path $preparationRoot 'service-secrets.env'
$environmentRoot = Join-Path $preparationRoot 'environments'
$templateRoot = Join-Path $repository 'ops\windows\environments'
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$psqlSha256 = '1116c77f820606f52cd3d0f676012470d494092cba321a6cbd898f4701eb944e'
$mc = 'C:\Program Files\LocalRAG-Tools\mc\mc.exe'
$mcSha256 = 'c8db13ebeda31497f354c0e950809db0ae9b2a2a69b8afee68c128c37300c157'
$databaseEndpoint = '127.0.0.1:45432'
$rustfsEndpoint = 'http://127.0.0.1:59000'
$bucket = 'rag-originals'
$systemSid = 'S-1-5-18'
$administratorsSid = 'S-1-5-32-544'
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value

function ConvertTo-RagLanAddress {
    param(
        [Parameter(Mandatory)][string]$Value,
        [Parameter(Mandatory)][ValidateSet('IPv4','IPv6')][string]$Family
    )
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Value,[ref]$parsed)) {
        throw "Lan$Family is not a valid IP address."
    }
    $bytes = $parsed.GetAddressBytes()
    if ($Family -ceq 'IPv4') {
        $isPrivate = $parsed.AddressFamily -eq 'InterNetwork' -and (
            $bytes[0] -eq 10 -or
            ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
            ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
        )
        if (-not $isPrivate) { throw 'LanIpv4 must be an RFC1918 address.' }
    }
    elseif ($parsed.AddressFamily -ne 'InterNetworkV6' -or
        (($bytes[0] -band 0xfe) -ne 0xfc)) {
        throw 'LanIpv6 must be a unique-local IPv6 address.'
    }
    return $parsed.ToString()
}

$normalizedLanIpv4 = ConvertTo-RagLanAddress -Value $LanIpv4 -Family IPv4
$normalizedLanIpv6 = ConvertTo-RagLanAddress -Value $LanIpv6 -Family IPv6

function Assert-RagAdministrator {
    $principal = [Security.Principal.WindowsPrincipal]::new(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    if (-not $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )) {
        throw 'Production application initialization requires an elevated PowerShell session.'
    }
}

function Set-RagProtectedAcl {
    param(
        [Parameter(Mandatory)][string]$Path,
        [switch]$Recurse
    )
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    $ownerArguments = @($Path, '/setowner', "*$administratorsSid")
    & icacls.exe @ownerArguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not set protected owner: $Path" }
    $systemGrant = "*$systemSid`:(F)"
    $administratorGrant = "*$administratorsSid`:(F)"
    if ($item.PSIsContainer) {
        $systemGrant = "*$systemSid`:(OI)(CI)(F)"
        $administratorGrant = "*$administratorsSid`:(OI)(CI)(F)"
    }
    $aclArguments = @(
        $Path, '/inheritance:r', '/grant:r', $systemGrant, $administratorGrant
    )
    & icacls.exe @aclArguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not set protected ACL: $Path" }
    $acl = Get-Acl -LiteralPath $Path
    $unapproved = @($acl.Access | Where-Object {
        $_.IdentityReference.Value -cnotin @(
            'NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators'
        )
    } | Select-Object -ExpandProperty IdentityReference -Unique)
    foreach ($identity in $unapproved) {
        & icacls.exe $Path /remove:g ([string]$identity.Value) | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not remove an unapproved protected-path allow ACE: $Path"
        }
        & icacls.exe $Path /remove:d ([string]$identity.Value) | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not remove an unapproved protected-path deny ACE: $Path"
        }
    }
    if ($Recurse -and $item.PSIsContainer) {
        foreach ($child in @(Get-ChildItem -LiteralPath $Path -Force -Recurse)) {
            Set-RagProtectedAcl -Path $child.FullName
        }
    }
}

function Assert-RagProtectedPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][bool]$Directory,
        [switch]$AllowCurrentUser
    )
    Assert-RagPathComponentsNotReparse -Path $Path
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -ne $Directory -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Protected preparation path has the wrong type: $Path"
    }
    $trusted = @($systemSid, $administratorsSid)
    if ($AllowCurrentUser) { $trusted += $currentSid }
    $acl = Get-Acl -LiteralPath $Path
    $ownerSid = (New-Object Security.Principal.NTAccount($acl.Owner)).Translate(
        [Security.Principal.SecurityIdentifier]
    ).Value
    if ($ownerSid -cnotin $trusted) {
        throw "Protected preparation path has an unapproved owner: $Path"
    }
    $rules = $acl.GetAccessRules(
        $true, $true, [Security.Principal.SecurityIdentifier]
    )
    foreach ($rule in @($rules)) {
        if ($rule.AccessControlType -ceq 'Allow' -and
            $rule.IdentityReference.Value -cnotin $trusted) {
            throw "Protected preparation path grants an unapproved identity: $Path"
        }
    }
}

function New-RagSecret {
    $bytes = [byte[]]::new(48)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
        return [Convert]::ToBase64String($bytes)
    }
    finally {
        $generator.Dispose()
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
}

function Write-RagProtectedText {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Content
    )
    if (Test-Path -LiteralPath $Path) {
        throw "Refusing to overwrite protected preparation state: $Path"
    }
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText(
            $temporary, $Content, [Text.UTF8Encoding]::new($false)
        )
        Set-RagProtectedAcl -Path $temporary
        [IO.File]::Move($temporary, $Path)
        Assert-RagProtectedPath -Path $Path -Directory $false
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Read-RagExactEnvironment {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string[]]$ExpectedKeys,
        [switch]$AllowCurrentUser
    )
    Assert-RagProtectedPath -Path $Path -Directory $false `
        -AllowCurrentUser:$AllowCurrentUser
    $values = [ordered]@{}
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line -cnotmatch '^([A-Z0-9_]+)=(.*)$' -or
            $values.Contains($Matches[1])) {
            throw "Protected environment file is invalid: $Path"
        }
        $values[$Matches[1]] = $Matches[2]
    }
    $actualKeySet = @($values.Keys | Sort-Object) -join ','
    $expectedKeySet = @($ExpectedKeys | Sort-Object) -join ','
    if ($actualKeySet -cne $expectedKeySet) {
        throw (
            "Protected environment key set is invalid: $Path; " +
            "actual=$actualKeySet; expected=$expectedKeySet"
        )
    }
    foreach ($key in $ExpectedKeys) {
        if ([string]::IsNullOrWhiteSpace($values[$key])) {
            throw "Protected environment contains an empty value: $Path"
        }
    }
    return $values
}

function New-RagProtectedDirectory {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        [IO.Directory]::CreateDirectory($Path) | Out-Null
    }
    Set-RagProtectedAcl -Path $Path
    Assert-RagProtectedPath -Path $Path -Directory $true
}

function Get-RagRolePassword {
    param([Parameter(Mandatory)][string]$Role)
    $path = Join-Path $roleSecretRoot "$Role.password"
    if (-not (Test-Path -LiteralPath $path)) {
        Write-RagProtectedText -Path $path -Content (New-RagSecret)
    }
    Assert-RagProtectedPath -Path $path -Directory $false
    $value = [IO.File]::ReadAllText($path)
    if ([string]::IsNullOrWhiteSpace($value) -or $value -match '[\r\n]') {
        throw "Protected PostgreSQL role password is invalid: $Role"
    }
    return $value
}

function ConvertTo-RagDatabaseUrl {
    param(
        [Parameter(Mandatory)][string]$Role,
        [Parameter(Mandatory)][string]$Password
    )
    $encoded = [uri]::EscapeDataString($Password)
    return "postgresql+psycopg://$Role`:$encoded@$databaseEndpoint/rag"
}

function Write-RagRenderedEnvironment {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][hashtable]$Overrides,
        [hashtable]$Additional = @{}
    )
    $template = Join-Path $templateRoot "$Name.env.example"
    $destination = Join-Path $environmentRoot "$Name.env"
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $output = @(foreach ($line in [IO.File]::ReadAllLines($template)) {
        if ($line -cnotmatch '^([A-Z0-9_]+)=(.*)$') {
            throw "Environment template is invalid: $template"
        }
        $key = $Matches[1]
        [void]$seen.Add($key)
        if ($Overrides.ContainsKey($key)) { "$key=$($Overrides[$key])" } else { $line }
    })
    foreach ($key in $Overrides.Keys) {
        if (-not $seen.Contains($key)) {
            throw "Environment override does not exist in the template: $Name/$key"
        }
    }
    foreach ($key in $Additional.Keys) {
        if ($seen.Contains($key)) {
            throw "Additional environment key duplicates the template: $Name/$key"
        }
        $output += "$key=$($Additional[$key])"
    }
    $content = ($output -join "`n") + "`n"
    if ($content -match 'REPLACE') {
        throw "Prepared environment still contains a placeholder: $Name"
    }
    if (Test-Path -LiteralPath $destination) {
        Assert-RagProtectedPath -Path $destination -Directory $false
        if ([IO.File]::ReadAllText($destination) -cne $content) {
            throw "Existing prepared environment differs from the current contract: $Name"
        }
        return
    }
    Write-RagProtectedText -Path $destination -Content $content
}

function Set-RagProcessEnvironment {
    param([Parameter(Mandatory)][Collections.IDictionary]$Values)
    foreach ($entry in $Values.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable(
            [string]$entry.Key, [string]$entry.Value, 'Process'
        )
    }
}

function Invoke-RagNativeCommand {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$ArgumentList,
        [Parameter(Mandatory)][string]$FailureMessage
    )
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $FilePath @ArgumentList 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    $output | Write-Output
    if ($exitCode -ne 0) { throw $FailureMessage }
}

Assert-RagAdministrator
if (-not $PSCmdlet.ShouldProcess(
    'rag-prod application stores and protected service environments',
    'Provision least-privilege identities, apply schema, and verify storage'
)) { return }

$productionKeys = @(
    'POSTGRES_DB', 'POSTGRES_CLUSTER_ADMIN_PASSWORD', 'POSTGRES_PORT',
    'RUSTFS_API_PORT', 'RUSTFS_CONSOLE_PORT', 'RUSTFS_ROOT_ACCESS_KEY',
    'RUSTFS_ROOT_SECRET_KEY'
)
$production = Read-RagExactEnvironment -Path $productionEnvironment `
    -ExpectedKeys $productionKeys -AllowCurrentUser
if ($production.POSTGRES_DB -cne 'rag' -or
    $production.POSTGRES_PORT -cne '45432' -or
    $production.RUSTFS_API_PORT -cne '59000' -or
    $production.RUSTFS_CONSOLE_PORT -cne '59001') {
    throw 'Protected production store environment does not match the fixed port contract.'
}

$docker = (Get-Command docker.exe -ErrorAction Stop).Source
$containerIds = @(& $docker compose --project-name rag-prod `
    --env-file $productionEnvironment -f (Join-Path $repository 'compose.yaml') `
    ps -q postgres rustfs)
if ($LASTEXITCODE -ne 0 -or $containerIds.Count -ne 2) {
    throw 'The isolated production store inventory is unavailable.'
}
foreach ($containerId in $containerIds) {
    $summary = (& $docker inspect --format `
        '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}' `
        $containerId) -split '\|', 2
    if ($LASTEXITCODE -ne 0 -or $summary[0] -cne 'running' -or
        $summary[1] -cne 'healthy') {
        throw "Production store is not healthy: $containerId"
    }
}

New-RagProtectedDirectory -Path $preparationRoot
New-RagProtectedDirectory -Path $roleSecretRoot
New-RagProtectedDirectory -Path $environmentRoot

$roles = @('rag_migrator', 'rag_api', 'rag_worker', 'rag_maintenance')
$passwords = [ordered]@{}
foreach ($role in $roles) { $passwords[$role] = Get-RagRolePassword -Role $role }
if (@($passwords.Values | Sort-Object -Unique).Count -ne $roles.Count) {
    throw 'PostgreSQL application role passwords are not pairwise distinct.'
}

if (-not (Test-Path -LiteralPath $rustfsRootCredential)) {
    Write-RagProtectedText -Path $rustfsRootCredential -Content (
        "RUSTFS_ROOT_ACCESS_KEY=$($production.RUSTFS_ROOT_ACCESS_KEY)`n" +
        "RUSTFS_ROOT_SECRET_KEY=$($production.RUSTFS_ROOT_SECRET_KEY)`n"
    )
}
$rootCredential = Read-RagExactEnvironment -Path $rustfsRootCredential `
    -ExpectedKeys @('RUSTFS_ROOT_ACCESS_KEY', 'RUSTFS_ROOT_SECRET_KEY')
if ($rootCredential.RUSTFS_ROOT_ACCESS_KEY -cne $production.RUSTFS_ROOT_ACCESS_KEY -or
    $rootCredential.RUSTFS_ROOT_SECRET_KEY -cne $production.RUSTFS_ROOT_SECRET_KEY) {
    throw 'Protected RustFS root-credential snapshot does not match the production store.'
}

$previousPgPassword = $env:PGPASSWORD
try {
    $env:PGPASSWORD = $production.POSTGRES_CLUSTER_ADMIN_PASSWORD
    & (Join-Path $repository 'ops\security\provision-postgres-roles.ps1') `
        -DatabaseHost 127.0.0.1 -DatabasePort 45432 -DatabaseName rag `
        -ClusterAdministrator rag_cluster_admin -PsqlPath $psql `
        -PsqlSha256 $psqlSha256 -RoleSecretDirectory $roleSecretRoot
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL role provisioning failed.' }
}
finally {
    $env:PGPASSWORD = $previousPgPassword
}

if (Test-Path -LiteralPath $rustfsCredentialRoot) {
    Assert-RagProtectedPath -Path $rustfsCredentialRoot -Directory $true `
        -AllowCurrentUser
    if (@(Get-ChildItem -LiteralPath $rustfsCredentialRoot -Force).Count -eq 0) {
        Remove-Item -LiteralPath $rustfsCredentialRoot
    } else {
        Assert-RagProtectedPath -Path $rustfsCredentialRoot -Directory $true
    }
}
if (-not (Test-Path -LiteralPath $rustfsCredentialRoot)) {
    & (Join-Path $repository 'ops\security\provision-rustfs-iam.ps1') `
        -Endpoint "$rustfsEndpoint/" -Bucket $bucket `
        -SecretOutputDirectory $rustfsCredentialRoot -McPath $mc `
        -McSha256 $mcSha256 -RootCredentialFile $rustfsRootCredential
    if ($LASTEXITCODE -ne 0) { throw 'RustFS IAM provisioning failed.' }
    Set-RagProtectedAcl -Path $rustfsCredentialRoot -Recurse
}
Assert-RagProtectedPath -Path $rustfsCredentialRoot -Directory $true
$storage = @{}
foreach ($identity in @('api', 'ingestion', 'deletion', 'maintenance')) {
    $path = Join-Path $rustfsCredentialRoot "$identity-object-storage.env"
    $storage[$identity] = Read-RagExactEnvironment -Path $path -ExpectedKeys @(
        'OBJECT_STORAGE_ACCESS_KEY_ID', 'OBJECT_STORAGE_SECRET_ACCESS_KEY'
    )
}

if (-not (Test-Path -LiteralPath $serviceSecretFile)) {
    Write-RagProtectedText -Path $serviceSecretFile -Content (
        "COORDINATOR_SERVICE_TOKEN=$(New-RagSecret)`n" +
        "OCR_SERVICE_TOKEN=$(New-RagSecret)`n" +
        "CSRF_SIGNING_SECRET=$(New-RagSecret)`n" +
        'DEPLOYMENT_ID=rag-prod-20260802' + "`n"
    )
}
$serviceSecrets = Read-RagExactEnvironment -Path $serviceSecretFile -ExpectedKeys @(
    'COORDINATOR_SERVICE_TOKEN', 'OCR_SERVICE_TOKEN', 'CSRF_SIGNING_SECRET',
    'DEPLOYMENT_ID'
)

$databaseUrls = @{}
foreach ($role in $roles) {
    $databaseUrls[$role] = ConvertTo-RagDatabaseUrl `
        -Role $role -Password $passwords[$role]
}
$objectCommon = @{
    OBJECT_STORAGE_BUCKET = $bucket
    OBJECT_STORAGE_ENDPOINT_URL = $rustfsEndpoint
    OBJECT_STORAGE_USE_TLS = 'false'
}
Write-RagRenderedEnvironment -Name api -Overrides (@{
    DATABASE_URL = $databaseUrls.rag_api
    COORDINATOR_SERVICE_TOKEN = $serviceSecrets.COORDINATOR_SERVICE_TOKEN
    CSRF_SIGNING_SECRET = $serviceSecrets.CSRF_SIGNING_SECRET
    DEPLOYMENT_ID = $serviceSecrets.DEPLOYMENT_ID
    OBJECT_STORAGE_ACCESS_KEY_ID = $storage.api.OBJECT_STORAGE_ACCESS_KEY_ID
    OBJECT_STORAGE_SECRET_ACCESS_KEY = $storage.api.OBJECT_STORAGE_SECRET_ACCESS_KEY
} + $objectCommon)
Write-RagRenderedEnvironment -Name ingestion -Overrides (@{
    WORKER_DATABASE_URL = $databaseUrls.rag_worker
    COORDINATOR_SERVICE_TOKEN = $serviceSecrets.COORDINATOR_SERVICE_TOKEN
    OCR_SERVICE_TOKEN = $serviceSecrets.OCR_SERVICE_TOKEN
    OBJECT_STORAGE_ACCESS_KEY_ID = $storage.ingestion.OBJECT_STORAGE_ACCESS_KEY_ID
    OBJECT_STORAGE_SECRET_ACCESS_KEY = $storage.ingestion.OBJECT_STORAGE_SECRET_ACCESS_KEY
} + $objectCommon)
Write-RagRenderedEnvironment -Name deletion -Overrides (@{
    WORKER_DATABASE_URL = $databaseUrls.rag_worker
    OBJECT_STORAGE_ACCESS_KEY_ID = $storage.deletion.OBJECT_STORAGE_ACCESS_KEY_ID
    OBJECT_STORAGE_SECRET_ACCESS_KEY = $storage.deletion.OBJECT_STORAGE_SECRET_ACCESS_KEY
} + $objectCommon)
Write-RagRenderedEnvironment -Name inference -Overrides @{
    COORDINATOR_SERVICE_TOKEN = $serviceSecrets.COORDINATOR_SERVICE_TOKEN
}
Write-RagRenderedEnvironment -Name ocr -Overrides @{
    OCR_SERVICE_TOKEN = $serviceSecrets.OCR_SERVICE_TOKEN
}
Write-RagRenderedEnvironment -Name web -Overrides @{}
Write-RagRenderedEnvironment -Name caddy -Overrides @{
    RAG_LAN_IPV4 = $normalizedLanIpv4
    RAG_LAN_IPV6 = $normalizedLanIpv6
}
Write-RagRenderedEnvironment -Name migration -Overrides @{
    MIGRATION_DATABASE_URL = $databaseUrls.rag_migrator
}
Write-RagRenderedEnvironment -Name maintenance -Overrides @{
    MAINTENANCE_DATABASE_URL = $databaseUrls.rag_maintenance
} -Additional @{
    ENVIRONMENT = 'production'
    CORS_ORIGINS = '[]'
    CSRF_SIGNING_SECRET = $serviceSecrets.CSRF_SIGNING_SECRET
    COORDINATOR_SERVICE_TOKEN = $serviceSecrets.COORDINATOR_SERVICE_TOKEN
    OBJECT_STORAGE_ENDPOINT_URL = $rustfsEndpoint
    OBJECT_STORAGE_REGION = 'us-east-1'
    OBJECT_STORAGE_BUCKET = $bucket
    OBJECT_STORAGE_ACCESS_KEY_ID = $storage.maintenance.OBJECT_STORAGE_ACCESS_KEY_ID
    OBJECT_STORAGE_SECRET_ACCESS_KEY = $storage.maintenance.OBJECT_STORAGE_SECRET_ACCESS_KEY
    OBJECT_STORAGE_FORCE_PATH_STYLE = 'true'
    OBJECT_STORAGE_USE_TLS = 'false'
}

$python = Join-Path $repository 'apps\api\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'The locked API Python environment is unavailable.'
}
$env:MIGRATION_DATABASE_URL = $databaseUrls.rag_migrator
$previousLocation = Get-Location
try {
    Set-Location (Join-Path $repository 'apps\api')
    Invoke-RagNativeCommand -FilePath $python `
        -ArgumentList @('-m', 'alembic', 'upgrade', 'head') `
        -FailureMessage 'Production Alembic upgrade failed.'
    Invoke-RagNativeCommand -FilePath $python `
        -ArgumentList @('-m', 'alembic', 'current') `
        -FailureMessage 'Production Alembic revision verification failed.'

    $apiExpectedKeys = @(
        [IO.File]::ReadAllLines((Join-Path $templateRoot 'api.env.example')) |
            ForEach-Object { ($_ -split '=', 2)[0] }
    )
    $apiEnvironment = Read-RagExactEnvironment `
        -Path (Join-Path $environmentRoot 'api.env') `
        -ExpectedKeys $apiExpectedKeys
    $maintenanceEnvironment = Read-RagExactEnvironment `
        -Path (Join-Path $environmentRoot 'maintenance.env') `
        -ExpectedKeys @(
            'MAINTENANCE_DATABASE_URL', 'ENVIRONMENT', 'CORS_ORIGINS',
            'CSRF_SIGNING_SECRET', 'COORDINATOR_SERVICE_TOKEN',
            'OBJECT_STORAGE_ENDPOINT_URL', 'OBJECT_STORAGE_REGION',
            'OBJECT_STORAGE_BUCKET', 'OBJECT_STORAGE_ACCESS_KEY_ID',
            'OBJECT_STORAGE_SECRET_ACCESS_KEY', 'OBJECT_STORAGE_FORCE_PATH_STYLE',
            'OBJECT_STORAGE_USE_TLS'
        )
    Set-RagProcessEnvironment -Values $apiEnvironment
    Set-RagProcessEnvironment -Values $maintenanceEnvironment
    Invoke-RagNativeCommand -FilePath $python `
        -ArgumentList @('-m', 'app.maintenance_cli', 'storage-bootstrap') `
        -FailureMessage 'Production private-bucket verification failed.'
}
finally {
    Set-Location $previousLocation
}

$passwords.Clear()
$production.Clear()
$rootCredential.Clear()
[ordered]@{
    result = 'pass'
    postgres_roles = $roles
    alembic_revision = '0006_versioned_claim'
    rustfs_bucket = $bucket
    rustfs_identities = @('api', 'ingestion', 'deletion', 'maintenance')
    environment_root = $environmentRoot
    administrator_bootstrap_required = $true
} | ConvertTo-Json -Depth 4
