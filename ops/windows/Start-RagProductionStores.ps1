[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'RagManagedRootSafety.ps1')

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$composePath = Join-Path $repository 'compose.yaml'
$environmentPath = Join-Path $repository '.env.production.local'
$project = 'rag-prod'
$ports = [ordered]@{
    POSTGRES_PORT = '45432'
    RUSTFS_API_PORT = '59000'
    RUSTFS_CONSOLE_PORT = '59001'
}
$requiredKeys = @(
    'POSTGRES_DB', 'POSTGRES_CLUSTER_ADMIN_PASSWORD', 'POSTGRES_PORT',
    'RUSTFS_API_PORT', 'RUSTFS_CONSOLE_PORT', 'RUSTFS_ROOT_ACCESS_KEY',
    'RUSTFS_ROOT_SECRET_KEY'
)

function New-RagProductionSecret {
    $bytes = [byte[]]::new(48)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    [Convert]::ToBase64String($bytes)
}

function Assert-RagProductionEnvironmentAcl {
    param([Parameter(Mandatory)][string]$Path)
    Assert-RagPathComponentsNotReparse -Path $Path
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Production environment path is not a regular file'
    }
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $trusted = @($currentSid, 'S-1-5-18', 'S-1-5-32-544')
    $acl = Get-Acl -LiteralPath $Path
    $ownerSid = (New-Object Security.Principal.NTAccount($acl.Owner)).Translate(
        [Security.Principal.SecurityIdentifier]
    ).Value
    if (-not $acl.AreAccessRulesProtected -or $ownerSid -cnotin $trusted) {
        throw 'Production environment file is not ACL protected'
    }
    $rules = $acl.GetAccessRules(
        $true, $true, [Security.Principal.SecurityIdentifier]
    )
    foreach ($rule in @($rules)) {
        if ($rule.IsInherited -or $rule.IdentityReference.Value -cnotin $trusted) {
            throw 'Production environment file grants access to an untrusted identity'
        }
    }
}

function Set-RagProductionEnvironmentAcl {
    param([Parameter(Mandatory)][string]$Path)
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $Path /inheritance:r /grant:r `
        "*$($currentSid):(F)" '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Production environment ACL application failed' }
    Assert-RagProductionEnvironmentAcl -Path $Path
}

function Read-RagProductionEnvironment {
    param([Parameter(Mandatory)][string]$Path)
    Assert-RagProductionEnvironmentAcl -Path $Path
    $values = [ordered]@{}
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line -cnotmatch '^([A-Z0-9_]+)=(.*)$') {
            throw 'Production environment file contains an invalid line'
        }
        $name = $Matches[1]
        if ($name -cnotin $requiredKeys -or $values.Contains($name)) {
            throw 'Production environment file contains an unexpected or duplicate key'
        }
        $values[$name] = $Matches[2]
    }
    $actualKeys = @($values.Keys | Sort-Object) -join ','
    $expectedKeys = @($requiredKeys | Sort-Object) -join ','
    if ($actualKeys -cne $expectedKeys -or $values.POSTGRES_DB -cne 'rag' -or
        $values.POSTGRES_PORT -cne $ports.POSTGRES_PORT -or
        $values.RUSTFS_API_PORT -cne $ports.RUSTFS_API_PORT -or
        $values.RUSTFS_CONSOLE_PORT -cne $ports.RUSTFS_CONSOLE_PORT -or
        [string]::IsNullOrWhiteSpace($values.POSTGRES_CLUSTER_ADMIN_PASSWORD) -or
        [string]::IsNullOrWhiteSpace($values.RUSTFS_ROOT_ACCESS_KEY) -or
        [string]::IsNullOrWhiteSpace($values.RUSTFS_ROOT_SECRET_KEY)) {
        throw 'Production environment file does not match the isolated store contract'
    }
    $values
}

function New-RagProductionEnvironment {
    param([Parameter(Mandatory)][string]$Path)
    Assert-RagPathComponentsNotReparse -Path $Path
    if (Test-Path -LiteralPath $Path) {
        throw 'Refusing to overwrite an existing production environment file'
    }
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    Assert-RagPathComponentsNotReparse -Path $temporary
    $content = @(
        'POSTGRES_DB=rag'
        "POSTGRES_CLUSTER_ADMIN_PASSWORD=$(New-RagProductionSecret)"
        "POSTGRES_PORT=$($ports.POSTGRES_PORT)"
        "RUSTFS_API_PORT=$($ports.RUSTFS_API_PORT)"
        "RUSTFS_CONSOLE_PORT=$($ports.RUSTFS_CONSOLE_PORT)"
        "RUSTFS_ROOT_ACCESS_KEY=rag-prod-root-$([guid]::NewGuid().ToString('N'))"
        "RUSTFS_ROOT_SECRET_KEY=$(New-RagProductionSecret)"
    ) -join "`n"
    try {
        [IO.File]::WriteAllText($temporary, "$content`n", [Text.UTF8Encoding]::new($false))
        Set-RagProductionEnvironmentAcl -Path $temporary
        [IO.File]::Move($temporary, $Path)
        Assert-RagProductionEnvironmentAcl -Path $Path
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
        $content = $null
    }
}

Assert-RagPathComponentsNotReparse -Path $repository
Assert-RagPathComponentsNotReparse -Path $composePath
Assert-RagPathComponentsNotReparse -Path $environmentPath
if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
    throw 'Repository compose.yaml is missing'
}
if (-not $PSCmdlet.ShouldProcess(
    'rag-prod PostgreSQL and RustFS',
    'Create protected credentials and start fresh isolated stores'
)) { return }

$docker = (Get-Command docker.exe -ErrorAction Stop).Source
$existingContainers = @(& $docker ps -a `
    --filter "label=com.docker.compose.project=$project" -q)
if ($LASTEXITCODE -ne 0) { throw 'Docker project inventory failed' }
$existingVolumes = @(& $docker volume ls `
    --filter "label=com.docker.compose.project=$project" -q)
if ($LASTEXITCODE -ne 0) { throw 'Docker volume inventory failed' }
$existingNetworks = @(& $docker network ls `
    --filter "label=com.docker.compose.project=$project" -q)
if ($LASTEXITCODE -ne 0) { throw 'Docker network inventory failed' }

if (-not (Test-Path -LiteralPath $environmentPath -PathType Leaf)) {
    if ($existingContainers.Count -gt 0 -or $existingVolumes.Count -gt 0 -or
        $existingNetworks.Count -gt 0) {
        throw 'Refusing to attach new credentials to pre-existing rag-prod resources'
    }
    foreach ($port in @($ports.Values | ForEach-Object { [int]$_ })) {
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
            throw "Production port is already listening: $port"
        }
    }
    New-RagProductionEnvironment -Path $environmentPath
}
[void](Read-RagProductionEnvironment -Path $environmentPath)

& $docker compose --project-name $project --env-file $environmentPath `
    -f $composePath config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Production Compose configuration validation failed' }
& $docker compose --project-name $project --env-file $environmentPath `
    -f $composePath up -d --wait postgres rustfs
if ($LASTEXITCODE -ne 0) { throw 'Production stores failed to start healthy' }

$containerIds = @(& $docker compose --project-name $project `
    --env-file $environmentPath -f $composePath ps -q postgres rustfs)
if ($LASTEXITCODE -ne 0 -or $containerIds.Count -ne 2) {
    throw 'Production Compose project does not contain exactly two stores'
}
$containers = foreach ($containerId in $containerIds) {
    $summary = (& $docker inspect --format `
        '{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}' `
        $containerId) -split '\|', 3
    if ($LASTEXITCODE -ne 0 -or $summary[1] -cne 'running' -or
        $summary[2] -cne 'healthy') {
        throw 'A production store is not running and healthy'
    }
    [ordered]@{ name=$summary[0].TrimStart('/'); status=$summary[1]; health=$summary[2] }
}

[ordered]@{
    result = 'started'
    project = $project
    fresh_data = ($existingContainers.Count -eq 0)
    postgres_port = [int]$ports.POSTGRES_PORT
    rustfs_api_port = [int]$ports.RUSTFS_API_PORT
    rustfs_console_port = [int]$ports.RUSTFS_CONSOLE_PORT
    credentials_file = $environmentPath
    containers = @($containers)
} | ConvertTo-Json -Depth 4
