Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:InstallSteps = @(
    'contracts_validated',
    'prerequisites_validated',
    'roots_created',
    'secrets_created',
    'stores_started',
    'postgres_provisioned',
    'rustfs_provisioned',
    'schema_migrated',
    'storage_bootstrapped',
    'models_acquired',
    'setup_code_issued'
)

function ConvertTo-RagPersonalFullPath {
    param([Parameter(Mandatory)][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw 'Personal installation paths must not be empty.'
    }
    return [IO.Path]::GetFullPath($Path)
}

function Assert-RagPersonalPathSafe {
    param(
        [Parameter(Mandatory)][string]$Path,
        [switch]$AllowMissing
    )
    $resolved = ConvertTo-RagPersonalFullPath -Path $Path
    $root = [IO.Path]::GetPathRoot($resolved)
    if ($resolved.TrimEnd('\') -ceq $root.TrimEnd('\')) {
        throw "A drive root cannot be a Personal installation target: $resolved"
    }
    $forbidden = @(
        [Environment]::GetFolderPath('Windows'),
        [Environment]::GetFolderPath('ProgramFiles'),
        [Environment]::GetFolderPath('ProgramFilesX86'),
        [Environment]::GetFolderPath('CommonApplicationData'),
        [Environment]::GetFolderPath('UserProfile')
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    foreach ($item in $forbidden) {
        if ($resolved.TrimEnd('\') -ieq ([IO.Path]::GetFullPath($item)).TrimEnd('\')) {
            throw "A broad system/profile root cannot be a Personal target: $resolved"
        }
    }
    $cursor = $resolved
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $entry = Get-Item -LiteralPath $cursor -Force
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Personal installation paths cannot contain reparse points: $cursor"
            }
        }
        elseif (-not $AllowMissing -and $cursor -ceq $resolved) {
            throw "Required Personal path does not exist: $resolved"
        }
        $parent = [IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }
        $next = $parent.FullName
        if ($next -ceq $cursor) { break }
        $cursor = $next
    }
    return $resolved
}

function Protect-RagPersonalPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [switch]$Directory
    )
    $resolved = Assert-RagPersonalPathSafe -Path $Path
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity.User) { throw 'Current Windows SID is unavailable.' }
    $sid = $identity.User.Value
    $grants = if ($Directory) {
        @("*$sid`:(OI)(CI)(F)", '*S-1-5-18:(OI)(CI)(F)')
    }
    else {
        @("*$sid`:(F)", '*S-1-5-18:(F)')
    }
    & (Join-Path ([Environment]::SystemDirectory) 'icacls.exe') $resolved `
        /inheritance:r /grant:r $grants | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not protect Personal path ACL: $resolved"
    }
    & (Join-Path ([Environment]::SystemDirectory) 'icacls.exe') $resolved /verify | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Personal path ACL verification failed: $resolved"
    }
}

function Write-RagPersonalUtf8File {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Value,
        [switch]$Protect
    )
    [IO.File]::WriteAllText($Path, $Value, [Text.UTF8Encoding]::new($false))
    if ($Protect) { Protect-RagPersonalPath -Path $Path }
}

function New-RagPersonalSecret {
    param([ValidateRange(24, 128)][int]$Bytes = 48)
    $buffer = [byte[]]::new($Bytes)
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($buffer) }
    finally { $random.Dispose() }
    try {
        return [Convert]::ToBase64String($buffer).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    }
    finally { [Array]::Clear($buffer, 0, $buffer.Length) }
}

function New-RagPersonalAccessKey {
    param([Parameter(Mandatory)][ValidatePattern('^[a-z]{3,9}$')][string]$Prefix)
    $buffer = [byte[]]::new(5)
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($buffer)
        $suffix = [BitConverter]::ToString($buffer).Replace('-', '').ToLowerInvariant()
        return $Prefix + $suffix
    }
    finally {
        $random.Dispose()
        [Array]::Clear($buffer, 0, $buffer.Length)
    }
}

function New-RagPersonalSecretDocument {
    param([Parameter(Mandatory)][string]$InstallationId)
    $values = [ordered]@{
        postgres_cluster_admin = New-RagPersonalSecret
        postgres_migrator = New-RagPersonalSecret
        postgres_api = New-RagPersonalSecret
        postgres_worker = New-RagPersonalSecret
        postgres_maintenance = New-RagPersonalSecret
        rustfs_root_access = New-RagPersonalAccessKey -Prefix 'ragroot'
        rustfs_root_secret = New-RagPersonalSecret
        rustfs_api_access = New-RagPersonalAccessKey -Prefix 'ragapi'
        rustfs_api_secret = New-RagPersonalSecret
        rustfs_ingestion_access = New-RagPersonalAccessKey -Prefix 'ragingest'
        rustfs_ingestion_secret = New-RagPersonalSecret
        rustfs_deletion_access = New-RagPersonalAccessKey -Prefix 'ragdelete'
        rustfs_deletion_secret = New-RagPersonalSecret
        rustfs_maintenance_access = New-RagPersonalAccessKey -Prefix 'ragmaint'
        rustfs_maintenance_secret = New-RagPersonalSecret
        csrf_signing_secret = New-RagPersonalSecret
        coordinator_service_token = New-RagPersonalSecret
        controller_service_token = New-RagPersonalSecret
        ocr_service_token = New-RagPersonalSecret
    }
    $distinct = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($value in $values.Values) {
        if (-not $distinct.Add([string]$value)) {
            throw 'Generated Personal secrets were unexpectedly duplicated.'
        }
    }
    return [pscustomobject]@{
        schema_version = 1
        installation_id = $InstallationId
        values = [pscustomobject]$values
    }
}

function Read-RagPersonalJson {
    param([Parameter(Mandatory)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.Length -gt 1MB) {
        throw "JSON input must be a bounded regular file: $Path"
    }
    return [Text.UTF8Encoding]::new($false, $true).GetString(
        [IO.File]::ReadAllBytes($item.FullName)
    ) | ConvertFrom-Json
}

function Save-RagPersonalJournal {
    param(
        [Parameter(Mandatory)][pscustomobject]$Journal,
        [Parameter(Mandatory)][string]$Path
    )
    $Journal.updated_at = [DateTimeOffset]::UtcNow.ToString('o')
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    $backup = "$Path.bak"
    try {
        Write-RagPersonalUtf8File -Path $temporary `
            -Value ($Journal | ConvertTo-Json -Depth 8) -Protect
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [IO.File]::Replace($temporary, $Path, $backup, $true)
            if (Test-Path -LiteralPath $backup) { [IO.File]::Delete($backup) }
        }
        else {
            [IO.File]::Move($temporary, $Path)
        }
        Protect-RagPersonalPath -Path $Path
    }
    finally {
        if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) }
    }
}

function Assert-RagPersonalJournal {
    param([Parameter(Mandatory)][pscustomobject]$Journal)
    $properties = @($Journal.PSObject.Properties.Name | Sort-Object)
    $expected = @(
        'completed_steps', 'compose_project', 'created_at', 'current_step',
        'data_root', 'install_root', 'installation_id', 'last_error_code',
        'owned_paths', 'profile_id', 'release_root', 'schema_version', 'state',
        'updated_at'
    ) | Sort-Object
    if (($properties -join ',') -cne ($expected -join ',')) {
        throw 'Personal installation journal fields are invalid.'
    }
    if ($Journal.schema_version -ne 1 -or $Journal.profile_id -cne 'personal' -or
        $Journal.installation_id -cnotmatch '^[0-9a-f]{32}$' -or
        $Journal.compose_project -cnotmatch '^localrag-personal-[0-9a-f]{12}$' -or
        $Journal.state -cnotin @('in_progress', 'setup_required', 'failed')) {
        throw 'Personal installation journal identity/state is invalid.'
    }
    $completed = @($Journal.completed_steps)
    if ($completed.Count -ne (@($completed | Select-Object -Unique)).Count) {
        throw 'Personal installation journal has duplicate completed steps.'
    }
    $prefix = @($script:InstallSteps | Select-Object -First $completed.Count)
    if (($completed -join ',') -cne ($prefix -join ',')) {
        throw 'Personal installation journal completed steps are out of order.'
    }
}

function New-RagPersonalJournal {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$InstallRoot,
        [Parameter(Mandatory)][string]$DataRoot,
        [Parameter(Mandatory)][string]$ReleaseRoot
    )
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $existing = Read-RagPersonalJson -Path $Path
        Assert-RagPersonalJournal -Journal $existing
        if (
            $existing.install_root -cne $InstallRoot -or
            $existing.data_root -cne $DataRoot -or
            $existing.release_root -cne $ReleaseRoot
        ) {
            throw 'Personal installer roots do not match the existing journal.'
        }
        return $existing
    }
    $installationId = [guid]::NewGuid().ToString('N')
    $journal = [pscustomobject][ordered]@{
        schema_version = 1
        installation_id = $installationId
        profile_id = 'personal'
        compose_project = 'localrag-personal-' + $installationId.Substring(0, 12)
        install_root = $InstallRoot
        data_root = $DataRoot
        release_root = $ReleaseRoot
        state = 'in_progress'
        completed_steps = @()
        current_step = $null
        last_error_code = $null
        owned_paths = @()
        created_at = [DateTimeOffset]::UtcNow.ToString('o')
        updated_at = [DateTimeOffset]::UtcNow.ToString('o')
    }
    Save-RagPersonalJournal -Journal $journal -Path $Path
    return $journal
}

function Test-RagPersonalStepComplete {
    param(
        [Parameter(Mandatory)][pscustomobject]$Journal,
        [Parameter(Mandatory)][ValidateSet(
            'contracts_validated', 'prerequisites_validated', 'roots_created',
            'secrets_created', 'stores_started', 'postgres_provisioned',
            'rustfs_provisioned', 'schema_migrated', 'storage_bootstrapped',
            'models_acquired', 'setup_code_issued'
        )][string]$Step
    )
    return @($Journal.completed_steps) -ccontains $Step
}

function Start-RagPersonalStep {
    param(
        [Parameter(Mandatory)][pscustomobject]$Journal,
        [Parameter(Mandatory)][string]$Step,
        [Parameter(Mandatory)][string]$JournalPath
    )
    $Journal.state = 'in_progress'
    $Journal.current_step = $Step
    $Journal.last_error_code = $null
    Save-RagPersonalJournal -Journal $Journal -Path $JournalPath
}

function Complete-RagPersonalStep {
    param(
        [Parameter(Mandatory)][pscustomobject]$Journal,
        [Parameter(Mandatory)][string]$Step,
        [Parameter(Mandatory)][string]$JournalPath
    )
    if (Test-RagPersonalStepComplete -Journal $Journal -Step $Step) { return }
    $next = $script:InstallSteps[@($Journal.completed_steps).Count]
    if ($Step -cne $next) { throw "Personal install step is out of order: $Step" }
    $Journal.completed_steps = @($Journal.completed_steps) + $Step
    $Journal.current_step = $null
    if (@($Journal.completed_steps).Count -eq $script:InstallSteps.Count) {
        $Journal.state = 'setup_required'
    }
    Save-RagPersonalJournal -Journal $Journal -Path $JournalPath
}

function Set-RagPersonalFailure {
    param(
        [Parameter(Mandatory)][pscustomobject]$Journal,
        [Parameter(Mandatory)][string]$ErrorCode,
        [Parameter(Mandatory)][string]$JournalPath
    )
    if ($ErrorCode -cnotmatch '^[a-z0-9_]{3,64}$') {
        $ErrorCode = 'installation_step_failed'
    }
    $Journal.state = 'failed'
    $Journal.last_error_code = $ErrorCode
    Save-RagPersonalJournal -Journal $Journal -Path $JournalPath
}

function Get-RagPersonalPreflight {
    param(
        [Parameter(Mandatory)][string]$DataRoot,
        [ValidateRange(8, 256)][int]$MinimumRamGiB = 32,
        [ValidateRange(10, 2048)][int]$MinimumFreeDiskGiB = 30
    )
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'Local RAG Personal V8A supports Windows only.'
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'Local RAG Personal V8A requires 64-bit Windows.'
    }
    Add-Type -AssemblyName Microsoft.VisualBasic
    $computer = [Microsoft.VisualBasic.Devices.ComputerInfo]::new()
    $ramGiB = [math]::Floor([double]$computer.TotalPhysicalMemory / 1GB)
    if ($ramGiB -lt $MinimumRamGiB) {
        throw "At least $MinimumRamGiB GiB RAM is required; detected $ramGiB GiB."
    }
    $dataPath = ConvertTo-RagPersonalFullPath -Path $DataRoot
    $driveName = [IO.Path]::GetPathRoot($dataPath)
    $drive = [IO.DriveInfo]::new($driveName)
    if (-not $drive.IsReady) { throw 'The selected data-location drive is not ready.' }
    $freeGiB = [math]::Floor([double]$drive.AvailableFreeSpace / 1GB)
    if ($freeGiB -lt $MinimumFreeDiskGiB) {
        throw "At least $MinimumFreeDiskGiB GiB free disk is required; detected $freeGiB GiB."
    }
    $docker = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($null -eq $docker) { throw 'Docker Desktop is not installed or not available.' }
    & $docker.Source version --format '{{.Server.Version}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is installed but its engine is not ready.' }
    & $docker.Source compose version --short | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose is unavailable.' }
    $ollama = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($null -eq $ollama) { throw 'Ollama is not installed or not available.' }
    try {
        $ollamaVersion = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:11434/api/version' `
            -TimeoutSec 5
    }
    catch { throw 'Ollama is installed but its local service is not ready.' }
    return [pscustomobject]@{
        windows_x64 = $true
        ram_gib = $ramGiB
        free_disk_gib = $freeGiB
        docker_ready = $true
        ollama_ready = $true
        ollama_version = [string]$ollamaVersion.version
        mutations_performed = $false
    }
}

function Assert-RagPersonalPortsFree {
    foreach ($port in @(3000, 5432, 8000, 8100, 8101, 8102, 9000)) {
        $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
        if ($null -ne $listener) {
            throw "Required Personal loopback port is already in use: $port"
        }
    }
}

function Write-RagPersonalEnvironmentFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][Collections.IDictionary]$Values
    )
    foreach ($key in $Values.Keys) {
        if ([string]$key -cnotmatch '^[A-Z][A-Z0-9_]{1,95}$') {
            throw "Invalid Personal environment key: $key"
        }
        $value = [string]$Values[$key]
        if ($value.Contains("`n") -or $value.Contains("`r")) {
            throw "Personal environment value contains a newline: $key"
        }
    }
    $text = (($Values.Keys | ForEach-Object { "$_=$($Values[$_])" }) -join "`r`n") + "`r`n"
    Write-RagPersonalUtf8File -Path $Path -Value $text -Protect
}

Export-ModuleMember -Function @(
    'Assert-RagPersonalPathSafe',
    'Protect-RagPersonalPath',
    'Write-RagPersonalUtf8File',
    'New-RagPersonalSecret',
    'New-RagPersonalSecretDocument',
    'Read-RagPersonalJson',
    'Save-RagPersonalJournal',
    'Assert-RagPersonalJournal',
    'New-RagPersonalJournal',
    'Test-RagPersonalStepComplete',
    'Start-RagPersonalStep',
    'Complete-RagPersonalStep',
    'Set-RagPersonalFailure',
    'Get-RagPersonalPreflight',
    'Assert-RagPersonalPortsFree',
    'Write-RagPersonalEnvironmentFile'
)
