[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Medium')]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'LocalRAG\Personal'),
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'LocalRAGData'),
    [string]$ReleaseRoot,
    [switch]$DevelopmentSource,
    [switch]$Plan
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagPersonal.psm1') -Force
if ([string]::IsNullOrWhiteSpace($ReleaseRoot)) {
    $ReleaseRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
}

function Invoke-RagPersonalContractValidation {
    param(
        [Parameter(Mandatory)][string]$Root,
        [switch]$SourceMode
    )
    $validator = Join-Path $Root 'ops\windows\v8a\validate_contracts.py'
    if (-not (Test-Path -LiteralPath $validator -PathType Leaf)) {
        throw 'The V8A contract validator is missing from the release.'
    }
    $python = Join-Path $Root 'runtimes\api-python\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        if (-not $SourceMode) {
            throw 'The packaged API Python runtime is missing.'
        }
        $python = Join-Path $Root 'apps\api\.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
            if ($null -eq $pythonCommand) {
                throw 'Source-tree validation requires the uv-prepared API Python environment.'
            }
            $python = $pythonCommand.Source
        }
    }
    $output = & $python $validator 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "V8A contract validation failed: $($output -join ' ')"
    }
    $result = ($output -join "`n") | ConvertFrom-Json
    if ($result.result -cne 'pass' -or $result.mutations_performed -ne $false) {
        throw 'V8A contract validation did not return a read-only passing result.'
    }
    return $result
}

function Invoke-RagPersonalWithEnvironment {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Values,
        [Parameter(Mandatory)][scriptblock]$Action
    )
    $prior = @{}
    try {
        foreach ($key in $Values.Keys) {
            $prior[$key] = [Environment]::GetEnvironmentVariable(
                [string]$key, [EnvironmentVariableTarget]::Process
            )
            [Environment]::SetEnvironmentVariable(
                [string]$key, [string]$Values[$key],
                [EnvironmentVariableTarget]::Process
            )
        }
        & $Action
    }
    finally {
        foreach ($key in $Values.Keys) {
            [Environment]::SetEnvironmentVariable(
                [string]$key, $prior[$key], [EnvironmentVariableTarget]::Process
            )
        }
    }
}

function Invoke-RagPersonalApiPython {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$Root,
        [switch]$SourceMode
    )
    $apiRoot = Join-Path $Root 'apps\api'
    if ($SourceMode) {
        $uv = Get-Command uv.exe -ErrorAction SilentlyContinue
        if ($null -eq $uv) {
            throw 'Source-tree installation requires uv; packaged users do not.'
        }
        & $uv.Source --directory $apiRoot run python @Arguments
    }
    else {
        $python = Join-Path $Root 'runtimes\api-python\python.exe'
        Push-Location $apiRoot
        try { & $python @Arguments }
        finally { Pop-Location }
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'Packaged API maintenance command failed.'
    }
}

function Get-RagPersonalEnvValues {
    param([Parameter(Mandatory)][string]$Path)
    $values = [ordered]@{}
    $text = [Text.UTF8Encoding]::new($false, $true).GetString(
        [IO.File]::ReadAllBytes($Path)
    )
    foreach ($line in ($text -split "`r?`n")) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $separator = $line.IndexOf('=')
        if ($separator -le 0) { throw "Invalid generated environment file: $Path" }
        $key = $line.Substring(0, $separator)
        if ($values.Contains($key)) { throw "Duplicate generated environment key: $key" }
        $values[$key] = $line.Substring($separator + 1)
    }
    return $values
}

function Copy-RagPersonalContractFile {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination
    )
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        $sourceHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
        $destinationHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
        if ($sourceHash -cne $destinationHash) {
            throw "Existing installed contract differs from the release: $Destination"
        }
        return
    }
    [IO.File]::Copy($Source, $Destination, $false)
    Protect-RagPersonalPath -Path $Destination
}

function Install-RagPersonalStartMenu {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Release
    )
    $programs = [Environment]::GetFolderPath('Programs')
    if ([string]::IsNullOrWhiteSpace($programs)) {
        throw 'The current-user Start menu is unavailable.'
    }
    $menu = Join-Path $programs 'Local RAG'
    if (Test-Path -LiteralPath $menu) {
        $item = Get-Item -LiteralPath $menu -Force
        if (-not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'The Local RAG Start-menu location is unsafe.'
        }
        $known = @(
            'Start Local RAG.lnk', 'Check for updates.lnk',
            'Recovery - issue setup code.lnk', 'Uninstall Local RAG.lnk'
        )
        $unknown = @(Get-ChildItem -LiteralPath $menu -Force | Where-Object {
            $_.Name -cnotin $known
        })
        if ($unknown.Count -gt 0) {
            throw 'The Local RAG Start-menu folder contains unknown entries.'
        }
    }
    else {
        [IO.Directory]::CreateDirectory($menu) | Out-Null
    }
    $shell = New-Object -ComObject WScript.Shell
    $cmd = Join-Path ([Environment]::SystemDirectory) 'cmd.exe'
    $launchers = [ordered]@{
        'Start Local RAG.lnk' = 'Start-Local-RAG.cmd'
        'Check for updates.lnk' = 'Check-for-Updates.cmd'
        'Recovery - issue setup code.lnk' = 'Issue-New-Setup-Code.cmd'
        'Uninstall Local RAG.lnk' = 'Uninstall-Local-RAG.cmd'
    }
    foreach ($entry in $launchers.GetEnumerator()) {
        $target = Join-Path $Release "ops\windows\v8a\$($entry.Value)"
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "A Personal launcher is missing: $($entry.Value)"
        }
        $shortcutPath = Join-Path $menu $entry.Key
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $cmd
        $shortcut.Arguments = "/d /c `"`"$target`"`""
        $shortcut.WorkingDirectory = $Release
        $shortcut.Description = $entry.Key.Replace('.lnk', '')
        $shortcut.Save()
    }
    $marker = Join-Path $Root 'state\start-menu.json'
    Write-RagPersonalUtf8File -Path $marker -Protect -Value (([ordered]@{
        schema_version = 1
        menu_path = $menu
        entries = @($launchers.Keys)
    } | ConvertTo-Json -Depth 3))
}

function Write-RagPersonalBootstrapEnvironments {
    param(
        [Parameter(Mandatory)][pscustomobject]$Secrets,
        [Parameter(Mandatory)][string]$ConfigurationRoot,
        [Parameter(Mandatory)][string]$PersonalDataRoot
    )
    $values = $Secrets.values
    $dbBase = '127.0.0.1:5432/rag'
    Write-RagPersonalEnvironmentFile -Path (Join-Path $ConfigurationRoot 'migration.env') `
        -Values ([ordered]@{
            MIGRATION_DATABASE_URL="postgresql+psycopg://rag_migrator:$($values.postgres_migrator)@$dbBase"
        })
    Write-RagPersonalEnvironmentFile -Path (Join-Path $ConfigurationRoot 'maintenance.env') `
        -Values ([ordered]@{
            MAINTENANCE_DATABASE_URL="postgresql+psycopg://rag_maintenance:$($values.postgres_maintenance)@$dbBase"
            DATA_ROOT=(Join-Path $PersonalDataRoot 'application')
            OBJECT_STORAGE_ENDPOINT_URL='http://127.0.0.1:9000'
            OBJECT_STORAGE_REGION='us-east-1'
            OBJECT_STORAGE_BUCKET='rag-originals'
            OBJECT_STORAGE_FORCE_PATH_STYLE='true'
            OBJECT_STORAGE_USE_TLS='false'
            OBJECT_STORAGE_ACCESS_KEY_ID=$values.rustfs_maintenance_access
            OBJECT_STORAGE_SECRET_ACCESS_KEY=$values.rustfs_maintenance_secret
        })
}

function Write-RagPersonalRuntimeEnvironments {
    param(
        [Parameter(Mandatory)][pscustomobject]$Secrets,
        [Parameter(Mandatory)][string]$ConfigurationRoot,
        [Parameter(Mandatory)][string]$PersonalDataRoot,
        [Parameter(Mandatory)][string]$Root
    )
    $values = $Secrets.values
    $deploymentId = 'rag-personal-' + $Secrets.installation_id.Substring(0, 12)
    $dbBase = '127.0.0.1:5432/rag'
    $ocrPython = if ($DevelopmentSource) {
        Join-Path $Root '.venv-ocr\Scripts\python.exe'
    } else { Join-Path $Root 'runtimes\ocr-python\python.exe' }
    $common = [ordered]@{
        ENVIRONMENT='production'
        DEPLOYMENT_ID=$deploymentId
        DATA_ROOT=(Join-Path $PersonalDataRoot 'application')
        OLLAMA_BASE_URL='http://127.0.0.1:11434'
        GENERATION_MODEL='qwen3:8b'
        EMBEDDING_MODEL='qwen3-embedding:0.6b'
        RERANKER_MODEL='BAAI/bge-reranker-v2-m3'
        OCR_PYTHON_EXECUTABLE=$ocrPython
        OCR_PIPELINE_VERSION='v1.6'
        OCR_DEVICE='cpu'
        OBJECT_STORAGE_ENDPOINT_URL='http://127.0.0.1:9000'
        OBJECT_STORAGE_REGION='us-east-1'
        OBJECT_STORAGE_BUCKET='rag-originals'
        OBJECT_STORAGE_FORCE_PATH_STYLE='true'
        OBJECT_STORAGE_USE_TLS='false'
    }
    $roles = [ordered]@{
        api = [ordered]@{
            PRODUCT_PROFILE='personal'
            CANONICAL_ORIGIN='http://127.0.0.1:3000'
            CANONICAL_HOST='127.0.0.1'
            CORS_ORIGINS='[]'
            DATABASE_URL="postgresql+psycopg://rag_api:$($values.postgres_api)@$dbBase"
            CSRF_SIGNING_SECRET=$values.csrf_signing_secret
            COORDINATOR_BASE_URL='http://127.0.0.1:8100'
            COORDINATOR_SERVICE_TOKEN=$values.coordinator_service_token
            CONTROLLER_BASE_URL='http://127.0.0.1:8102'
            CONTROLLER_SERVICE_TOKEN=$values.controller_service_token
            OCR_SERVICE_BASE_URL='http://127.0.0.1:8101'
            OCR_SERVICE_TOKEN=$values.ocr_service_token
            OBJECT_STORAGE_ACCESS_KEY_ID=$values.rustfs_api_access
            OBJECT_STORAGE_SECRET_ACCESS_KEY=$values.rustfs_api_secret
        }
        ingestion = [ordered]@{
            WORKER_DATABASE_URL="postgresql+psycopg://rag_worker:$($values.postgres_worker)@$dbBase"
            COORDINATOR_BASE_URL='http://127.0.0.1:8100'
            COORDINATOR_SERVICE_TOKEN=$values.coordinator_service_token
            OCR_SERVICE_BASE_URL='http://127.0.0.1:8101'
            OCR_SERVICE_TOKEN=$values.ocr_service_token
            OBJECT_STORAGE_ACCESS_KEY_ID=$values.rustfs_ingestion_access
            OBJECT_STORAGE_SECRET_ACCESS_KEY=$values.rustfs_ingestion_secret
        }
        deletion = [ordered]@{
            WORKER_DATABASE_URL="postgresql+psycopg://rag_worker:$($values.postgres_worker)@$dbBase"
            OBJECT_STORAGE_ACCESS_KEY_ID=$values.rustfs_deletion_access
            OBJECT_STORAGE_SECRET_ACCESS_KEY=$values.rustfs_deletion_secret
        }
    }
    foreach ($role in $roles.Keys) {
        $document = [ordered]@{}
        foreach ($entry in $common.GetEnumerator()) { $document[$entry.Key] = $entry.Value }
        foreach ($entry in $roles[$role].GetEnumerator()) { $document[$entry.Key] = $entry.Value }
        Write-RagPersonalEnvironmentFile -Path (Join-Path $ConfigurationRoot "$role.env") `
            -Values $document
    }
    Write-RagPersonalEnvironmentFile -Path (Join-Path $ConfigurationRoot 'inference.env') `
        -Values ([ordered]@{
            ENVIRONMENT='production'; DEPLOYMENT_ID=$deploymentId;
            OLLAMA_BASE_URL='http://127.0.0.1:11434'; GENERATION_MODEL='qwen3:8b';
            EMBEDDING_MODEL='qwen3-embedding:0.6b';
            RERANKER_MODEL='BAAI/bge-reranker-v2-m3';
            COORDINATOR_SERVICE_TOKEN=$values.coordinator_service_token
        })
    Write-RagPersonalEnvironmentFile -Path (Join-Path $ConfigurationRoot 'ocr.env') `
        -Values ([ordered]@{
            ENVIRONMENT='production'; DEPLOYMENT_ID=$deploymentId;
            OCR_PYTHON_EXECUTABLE=$ocrPython; OCR_PIPELINE_VERSION='v1.6';
            OCR_DEVICE='cpu'; OCR_CPU_THREADS='10'; OCR_PAGE_BATCH_SIZE='8'; OCR_PROCESS_COUNT='1';
            OCR_SERVICE_TOKEN=$values.ocr_service_token;
            OCR_WORKSPACE_ROOT=(Join-Path $PersonalDataRoot 'ocr-work')
        })
    Write-RagPersonalEnvironmentFile -Path (Join-Path $ConfigurationRoot 'web.env') `
        -Values ([ordered]@{
            NODE_ENV='production'; HOSTNAME='127.0.0.1'; PORT='3000';
            INTERNAL_API_URL='http://127.0.0.1:8000'
        })
}

$resolvedInstallRoot = Assert-RagPersonalPathSafe -Path $InstallRoot -AllowMissing
$resolvedDataRoot = Assert-RagPersonalPathSafe -Path $DataRoot -AllowMissing
$resolvedReleaseRoot = Assert-RagPersonalPathSafe -Path $ReleaseRoot
if ($resolvedDataRoot.StartsWith($resolvedInstallRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The preserved Personal data root must not be inside the removable install root.'
}

$releaseManifestPath = Join-Path $resolvedReleaseRoot 'ops\windows\v8a\personal-release.json'
$releaseManifest = Read-RagPersonalJson -Path $releaseManifestPath
if ($releaseManifest.profile_id -cne 'personal') {
    throw 'The selected release is not a Personal profile payload.'
}
if (-not $DevelopmentSource -and $releaseManifest.payload_state -cne 'packaged') {
    throw 'Public Personal installation requires a V8F verified packaged payload.'
}

$contractResult = Invoke-RagPersonalContractValidation -Root $resolvedReleaseRoot `
    -SourceMode:$DevelopmentSource
$preflight = Get-RagPersonalPreflight -DataRoot $resolvedDataRoot
if ($Plan) {
    [pscustomobject]@{
        result='pass'
        mode='read_only_plan'
        profile='personal'
        install_root=$resolvedInstallRoot
        data_root=$resolvedDataRoot
        contract_sha256=$contractResult.contract_sha256
        preflight=$preflight
        steps=@($releaseManifest.install_steps)
        final_state='setup_required'
        owner_onboarding_included=$true
        mutations_performed=$false
    } | ConvertTo-Json -Depth 8
    return
}
if (-not $PSCmdlet.ShouldProcess($resolvedInstallRoot, 'Prepare Local RAG Personal V8A')) {
    return
}

$stateRoot = Join-Path $resolvedInstallRoot 'state'
$journalPath = Join-Path $stateRoot 'installation-journal.json'
$journal = $null
try {
    if (-not (Test-Path -LiteralPath $journalPath -PathType Leaf)) {
        if (Test-Path -LiteralPath $resolvedInstallRoot) {
            $existing = @(Get-ChildItem -LiteralPath $resolvedInstallRoot -Force)
            if ($existing.Count -gt 0) {
                throw 'Unknown pre-existing Personal install root will not be adopted.'
            }
        }
        else { [IO.Directory]::CreateDirectory($resolvedInstallRoot) | Out-Null }
        if (Test-Path -LiteralPath $resolvedDataRoot) {
            throw 'Unknown pre-existing Personal data root will not be adopted.'
        }
        [IO.Directory]::CreateDirectory($stateRoot) | Out-Null
        Protect-RagPersonalPath -Path $resolvedInstallRoot -Directory
        Protect-RagPersonalPath -Path $stateRoot -Directory
    }
    $journal = New-RagPersonalJournal -Path $journalPath `
        -InstallRoot $resolvedInstallRoot -DataRoot $resolvedDataRoot `
        -ReleaseRoot $resolvedReleaseRoot

    foreach ($completedReadOnly in @('contracts_validated','prerequisites_validated')) {
        if (-not (Test-RagPersonalStepComplete -Journal $journal -Step $completedReadOnly)) {
            Start-RagPersonalStep -Journal $journal -Step $completedReadOnly -JournalPath $journalPath
            Complete-RagPersonalStep -Journal $journal -Step $completedReadOnly -JournalPath $journalPath
        }
    }

    if (-not (Test-RagPersonalStepComplete -Journal $journal -Step 'roots_created')) {
        Start-RagPersonalStep -Journal $journal -Step 'roots_created' -JournalPath $journalPath
        $owned = @('cache','config','logs','secrets','state')
        foreach ($relative in $owned) {
            $path = Join-Path $resolvedInstallRoot $relative
            if (-not (Test-Path -LiteralPath $path)) { [IO.Directory]::CreateDirectory($path) | Out-Null }
            Protect-RagPersonalPath -Path $path -Directory
        }
        [IO.Directory]::CreateDirectory($resolvedDataRoot) | Out-Null
        Protect-RagPersonalPath -Path $resolvedDataRoot -Directory
        foreach ($relative in @('application','ocr-work','postgres','rustfs')) {
            $path = Join-Path $resolvedDataRoot $relative
            [IO.Directory]::CreateDirectory($path) | Out-Null
            Protect-RagPersonalPath -Path $path -Directory
        }
        $journal.owned_paths = @($owned | ForEach-Object { Join-Path $resolvedInstallRoot $_ })
        Save-RagPersonalJournal -Journal $journal -Path $journalPath
        Complete-RagPersonalStep -Journal $journal -Step 'roots_created' -JournalPath $journalPath
    }

    $secretRoot = Join-Path $resolvedInstallRoot 'secrets'
    $configRoot = Join-Path $resolvedInstallRoot 'config'
    $secretPath = Join-Path $secretRoot 'installation-secrets.json'
    $composeEnvironment = Join-Path $configRoot 'compose.env'
    if (-not (Test-RagPersonalStepComplete -Journal $journal -Step 'secrets_created')) {
        Start-RagPersonalStep -Journal $journal -Step 'secrets_created' -JournalPath $journalPath
        if (Test-Path -LiteralPath $secretPath -PathType Leaf) {
            $secrets = Read-RagPersonalJson -Path $secretPath
            if ($secrets.installation_id -cne $journal.installation_id) {
                throw 'Existing Personal secret document belongs to another installation.'
            }
        }
        else {
            $secrets = New-RagPersonalSecretDocument -InstallationId $journal.installation_id
            Write-RagPersonalUtf8File -Path $secretPath `
                -Value ($secrets | ConvertTo-Json -Depth 6) -Protect
        }
        $dockerData = $resolvedDataRoot.Replace('\','/')
        Write-RagPersonalEnvironmentFile -Path $composeEnvironment -Values ([ordered]@{
            RAG_PERSONAL_INSTALLATION_ID=$journal.installation_id
            RAG_PERSONAL_POSTGRES_DATA="$dockerData/postgres"
            RAG_PERSONAL_RUSTFS_DATA="$dockerData/rustfs"
            POSTGRES_CLUSTER_ADMIN_PASSWORD=$secrets.values.postgres_cluster_admin
            RUSTFS_ROOT_ACCESS_KEY=$secrets.values.rustfs_root_access
            RUSTFS_ROOT_SECRET_KEY=$secrets.values.rustfs_root_secret
        })
        $installedCompose = Join-Path $configRoot 'compose.personal.yaml'
        $sourceCompose = Join-Path $resolvedReleaseRoot $releaseManifest.stores.compose_file
        Copy-RagPersonalContractFile -Source $sourceCompose -Destination $installedCompose
        $installedRelease = Join-Path $configRoot 'personal-release.json'
        Copy-RagPersonalContractFile -Source $releaseManifestPath -Destination $installedRelease
        Complete-RagPersonalStep -Journal $journal -Step 'secrets_created' -JournalPath $journalPath
    }
    $secrets = Read-RagPersonalJson -Path $secretPath

    $composeFile = Join-Path $configRoot 'compose.personal.yaml'
    $docker = (Get-Command docker.exe -ErrorAction Stop).Source
    if (-not (Test-RagPersonalStepComplete -Journal $journal -Step 'stores_started')) {
        Start-RagPersonalStep -Journal $journal -Step 'stores_started' -JournalPath $journalPath
        $existingContainers = @(& $docker ps -a --filter `
            "label=com.docker.compose.project=$($journal.compose_project)" --format '{{.ID}}')
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect existing Personal containers.' }
        if ($existingContainers.Count -eq 0) { Assert-RagPersonalPortsFree }
        foreach ($container in $existingContainers) {
            if ([string]::IsNullOrWhiteSpace($container)) { continue }
            $label = & $docker inspect --format `
                '{{ index .Config.Labels "com.localrag.installation-id" }}' $container
            if ($LASTEXITCODE -ne 0 -or $label.Trim() -cne $journal.installation_id) {
                throw 'Unknown existing container will not be adopted.'
            }
        }
        & $docker compose -p $journal.compose_project --env-file $composeEnvironment `
            -f $composeFile up -d --wait
        if ($LASTEXITCODE -ne 0) { throw 'Personal PostgreSQL/RustFS startup failed.' }
        foreach ($check in @(@('postgres','5432'),@('rustfs','9000'))) {
            $binding = (& $docker compose -p $journal.compose_project `
                --env-file $composeEnvironment -f $composeFile port $check[0] $check[1]).Trim()
            if ($LASTEXITCODE -ne 0 -or $binding -cnotmatch '^127\.0\.0\.1:[0-9]+$') {
                throw "Personal $($check[0]) is not bound to IPv4 loopback only."
            }
        }
        Complete-RagPersonalStep -Journal $journal -Step 'stores_started' -JournalPath $journalPath
    }

    if (-not (Test-RagPersonalStepComplete -Journal $journal -Step 'postgres_provisioned')) {
        Start-RagPersonalStep -Journal $journal -Step 'postgres_provisioned' -JournalPath $journalPath
        & (Join-Path $PSScriptRoot 'Initialize-RagPersonalPostgres.ps1') `
            -ComposeFile $composeFile -ComposeProject $journal.compose_project `
            -ComposeEnvironment $composeEnvironment -SecretDocument $secretPath | Out-Null
        Complete-RagPersonalStep -Journal $journal -Step 'postgres_provisioned' -JournalPath $journalPath
    }

    if (-not (Test-RagPersonalStepComplete -Journal $journal -Step 'rustfs_provisioned')) {
        Start-RagPersonalStep -Journal $journal -Step 'rustfs_provisioned' -JournalPath $journalPath
        $mcPath = if ($DevelopmentSource) {
            $sourceMc = Join-Path $resolvedReleaseRoot 'runtime\tools\mc\mc.exe'
            if (Test-Path -LiteralPath $sourceMc -PathType Leaf) {
                $sourceMc
            }
            else {
                $command = Get-Command mc.exe -ErrorAction SilentlyContinue
                if ($null -eq $command) {
                    throw 'Source setup could not find its verified RustFS setup tool.'
                }
                $command.Source
            }
        }
        else { Join-Path $resolvedReleaseRoot 'tools\mc\mc.exe' }
        & (Join-Path $PSScriptRoot 'Initialize-RagPersonalRustfs.ps1') `
            -Endpoint 'http://127.0.0.1:9000/' -Bucket 'rag-originals' `
            -SecretDocument $secretPath -CredentialOutputDirectory $secretRoot `
            -McPath $mcPath -DevelopmentSource:$DevelopmentSource | Out-Null
        Write-RagPersonalBootstrapEnvironments -Secrets $secrets `
            -ConfigurationRoot $configRoot -PersonalDataRoot $resolvedDataRoot
        Complete-RagPersonalStep -Journal $journal -Step 'rustfs_provisioned' -JournalPath $journalPath
    }

    if (-not (Test-RagPersonalStepComplete -Journal $journal -Step 'schema_migrated')) {
        Start-RagPersonalStep -Journal $journal -Step 'schema_migrated' -JournalPath $journalPath
        $migration = Get-RagPersonalEnvValues -Path (Join-Path $configRoot 'migration.env')
        Invoke-RagPersonalWithEnvironment -Values $migration -Action {
            Invoke-RagPersonalApiPython -Arguments @('-m','alembic','upgrade','head') `
                -Root $resolvedReleaseRoot -SourceMode:$DevelopmentSource
            $current = Invoke-RagPersonalApiPython -Arguments @('-m','alembic','current') `
                -Root $resolvedReleaseRoot -SourceMode:$DevelopmentSource 6>&1
            if (($current -join ' ') -notmatch [regex]::Escape($releaseManifest.expected_alembic_revision)) {
                throw 'Personal database did not reach the packaged Alembic revision.'
            }
        }
        Complete-RagPersonalStep -Journal $journal -Step 'schema_migrated' -JournalPath $journalPath
    }

    if (-not (Test-RagPersonalStepComplete -Journal $journal -Step 'storage_bootstrapped')) {
        Start-RagPersonalStep -Journal $journal -Step 'storage_bootstrapped' -JournalPath $journalPath
        $maintenance = Get-RagPersonalEnvValues -Path (Join-Path $configRoot 'maintenance.env')
        Invoke-RagPersonalWithEnvironment -Values $maintenance -Action {
            Invoke-RagPersonalApiPython `
                -Arguments @('-m','app.maintenance_cli','storage-bootstrap') `
                -Root $resolvedReleaseRoot -SourceMode:$DevelopmentSource
        }
        Complete-RagPersonalStep -Journal $journal -Step 'storage_bootstrapped' -JournalPath $journalPath
    }

    if (-not (Test-RagPersonalStepComplete -Journal $journal -Step 'models_acquired')) {
        Start-RagPersonalStep -Journal $journal -Step 'models_acquired' -JournalPath $journalPath
        $ollama = (Get-Command ollama.exe -ErrorAction Stop).Source
        foreach ($model in @($releaseManifest.ollama_models)) {
            if ($model.download_policy -cne 'pinned_resumable' -or
                [string]$model.expected_digest -cnotmatch '^[0-9a-f]{64}$') {
                throw 'Personal release contains an unpinned model download policy.'
            }
            & $ollama pull ([string]$model.identity)
            if ($LASTEXITCODE -ne 0) {
                throw "Ollama could not acquire the allowlisted model: $($model.identity)"
            }
            $inventory = Invoke-RestMethod -Method Get `
                -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 10
            $matches = @($inventory.models | Where-Object {
                [string]$_.model -ceq [string]$model.identity
            })
            if ($matches.Count -ne 1 -or
                [string]$matches[0].digest -cne [string]$model.expected_digest) {
                throw "Ollama returned unexpected content for the pinned model: $($model.identity)"
            }
        }
        Complete-RagPersonalStep -Journal $journal -Step 'models_acquired' -JournalPath $journalPath
    }

    if (-not (Test-RagPersonalStepComplete -Journal $journal -Step 'setup_code_issued')) {
        Start-RagPersonalStep -Journal $journal -Step 'setup_code_issued' `
            -JournalPath $journalPath
        Write-RagPersonalRuntimeEnvironments -Secrets $secrets `
            -ConfigurationRoot $configRoot -PersonalDataRoot $resolvedDataRoot `
            -Root $resolvedReleaseRoot
        $setupCode = & (Join-Path $PSScriptRoot 'Issue-RagPersonalSetupCode.ps1') `
            -InstallRoot $resolvedInstallRoot -ReleaseRoot $resolvedReleaseRoot `
            -DevelopmentSource:$DevelopmentSource -PassThru
        if ([string]$setupCode -cnotmatch '^[A-Za-z0-9_-]{32,128}$') {
            throw 'Personal setup-code issuance returned an invalid result.'
        }
        & (Join-Path $PSScriptRoot 'Show-RagPersonalSetupCode.ps1') `
            -Code ([string]$setupCode)
        $setupCode = $null
        Install-RagPersonalStartMenu -Root $resolvedInstallRoot `
            -Release $resolvedReleaseRoot
        if (-not $DevelopmentSource) {
            $trustMetadataPath = Join-Path $resolvedReleaseRoot `
                'release-trust-metadata.json'
            $trustMetadata = Read-RagPersonalJson -Path $trustMetadataPath
            if (
                $trustMetadata.schema_version -ne 1 -or
                [string]$trustMetadata.policy_id -cne 'local-rag-v8-release-trust' -or
                [string]$trustMetadata.root_id -cne 'rag-root-v8' -or
                [string]$trustMetadata.release_id -cnotmatch `
                    '^[a-z0-9][a-z0-9._-]{5,127}$' -or
                $trustMetadata.release_sequence -isnot [int] -or
                [int]$trustMetadata.release_sequence -lt 1
            ) {
                throw 'Packaged Personal release trust metadata is invalid.'
            }
            Write-RagPersonalUtf8File `
                -Path (Join-Path $stateRoot 'release-state.json') -Protect `
                -Value (([ordered]@{
                    schema_version = 1
                    release_id = [string]$trustMetadata.release_id
                    release_sequence = [int]$trustMetadata.release_sequence
                    trust_metadata_sha256 = (
                        Get-FileHash -LiteralPath $trustMetadataPath `
                            -Algorithm SHA256
                    ).Hash.ToLowerInvariant()
                } | ConvertTo-Json -Compress))
        }
        Complete-RagPersonalStep -Journal $journal -Step 'setup_code_issued' `
            -JournalPath $journalPath
    }

    Assert-RagPersonalJournal -Journal $journal
    if ($journal.state -cne 'setup_required') {
        throw 'Personal V8A preparation did not reach setup_required.'
    }
    [pscustomobject]@{
        result='pass'
        profile='personal'
        state='setup_required'
        installation_id=$journal.installation_id
        browser_origin='http://127.0.0.1:3000'
        owner_creation_available=$true
        owner_setup_implemented=$true
        setup_code_recovery_launcher=(
            Join-Path $PSScriptRoot 'Issue-New-Setup-Code.cmd'
        )
        application_launcher=(Join-Path $PSScriptRoot 'Start-Local-RAG.cmd')
        data_preserved=$true
    } | ConvertTo-Json -Depth 4
}
catch {
    if ($null -ne $journal) {
        $code = if ([string]::IsNullOrWhiteSpace([string]$journal.current_step)) {
            'installation_step_failed'
        } else { ([string]$journal.current_step) + '_failed' }
        Set-RagPersonalFailure -Journal $journal -ErrorCode $code -JournalPath $journalPath
    }
    throw
}
