[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][string]$ProgramDataRoot,
    [Parameter(Mandatory)][string]$LocalAddress,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$expectedRevision = '0014_restart_without_backup'
$services = @('caddy','web','api','ingestion','deletion','inference','ocr')

if ($Plan) {
    [ordered]@{
        result='plan';mutations_performed=$false
        profile='team_lan_preview_unsigned'
        host_prerequisites=@('windows_10_or_11_workstation','elevated','docker_desktop_running','ollama_running')
        host_tools_not_required=@('psql','mc','node','python','uv')
        packaged_tools=@('runtimes/api-python/python.exe','tools/mc/mc.exe')
        canonical_host='rag.home.arpa';canonical_origin='https://rag.home.arpa'
        local_address=$LocalAddress;service_environment_files=$services
        expected_alembic_revision=$expectedRevision
        store_data_root=(Join-Path $ProgramDataRoot 'data')
        failure_policy='preserve provisioned data, secrets, and journal after store creation'
    } | ConvertTo-Json -Depth 4
    return
}

Import-Module (Join-Path $PSScriptRoot 'RagTeamLanPreview.psm1') -Force
$address = Assert-RagTeamPreviewRfc1918Address -Address $LocalAddress
$release = (Resolve-Path -LiteralPath $ReleaseRoot).Path
$root = [IO.Path]::GetFullPath($ProgramDataRoot)
$composeFile = Join-Path $PSScriptRoot 'compose.team-preview.yaml'
$templates = Join-Path $release 'ops\windows\environments'
$python = Join-Path $release 'runtimes\api-python\python.exe'
$mc = Join-Path $release 'tools\mc\mc.exe'
foreach ($path in @($composeFile,$python,$mc)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Packaged Team preview provisioning asset is missing: $path"
    }
}

function Protect-PreviewPath([string]$Path,[switch]$Directory) {
    $grant = if ($Directory) { @('*S-1-5-18:(OI)(CI)(F)','*S-1-5-32-544:(OI)(CI)(F)') } else { @('*S-1-5-18:(F)','*S-1-5-32-544:(F)') }
    & icacls.exe $Path /inheritance:r /grant:r $grant | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not protect Team preview state: $Path" }
}
function Ensure-PreviewDirectory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { [IO.Directory]::CreateDirectory($Path) | Out-Null }
    Protect-PreviewPath -Path $Path -Directory
}
function Write-PreviewFile([string]$Path,[string]$Text) {
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($temporary,$Text,[Text.UTF8Encoding]::new($false))
        Protect-PreviewPath -Path $temporary
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary,$Path,$null) }
        else { [IO.File]::Move($temporary,$Path) }
    } finally { if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) } }
}
function New-PreviewSecret([int]$Bytes=48) {
    $buffer=[byte[]]::new($Bytes);$rng=[Security.Cryptography.RandomNumberGenerator]::Create()
    try {$rng.GetBytes($buffer);[Convert]::ToBase64String($buffer).TrimEnd('=').Replace('+','-').Replace('/','_')}
    finally {$rng.Dispose();[Array]::Clear($buffer,0,$buffer.Length)}
}
function New-PreviewAccess([string]$Prefix) { "$Prefix-$([guid]::NewGuid().ToString('N').Substring(0,16))" }
function Read-Env([string]$Path) {
    $result=[ordered]@{}
    foreach($line in [IO.File]::ReadAllLines($Path)) {
        if([string]::IsNullOrWhiteSpace($line)){continue}
        if($line -cnotmatch '^([A-Z0-9_]+)=(.*)$' -or $result.Contains($Matches[1])){throw "Invalid environment: $Path"}
        $result[$Matches[1]]=$Matches[2]
    }
    $result
}
function Write-StrictEnvironment(
    [string]$Name,
    [Collections.IDictionary]$Overrides,
    [string[]]$Remove=@(),
    [Collections.IDictionary]$Additional=@{}
) {
    $template=Join-Path $templates "$Name.env.example";$destination=Join-Path $environmentRoot "$Name.env"
    $output=[Collections.Generic.List[string]]::new();$seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach($line in [IO.File]::ReadAllLines($template)) {
        if($line -cnotmatch '^([A-Z0-9_]+)=(.*)$'){throw "Invalid environment template: $template"}
        $key=$Matches[1];if($key -cin $Remove){continue};[void]$seen.Add($key)
        $value=if($Overrides.Contains($key)){[string]$Overrides[$key]}else{$Matches[2]}
        if([string]::IsNullOrWhiteSpace($value) -or $value -match '(REPLACE|<[^>]+>)'){throw "Unresolved Team preview environment value: $Name/$key"}
        $output.Add("$key=$value")
    }
    foreach($key in $Overrides.Keys){if(-not $seen.Contains([string]$key)){throw "Unexpected Team preview environment override: $Name/$key"}}
    foreach($key in $Additional.Keys){
        if($seen.Contains([string]$key)){throw "Duplicate Team preview additional environment key: $Name/$key"}
        $value=[string]$Additional[$key]
        if([string]::IsNullOrWhiteSpace($value) -or $value -match '(REPLACE|<[^>]+>)'){throw "Unresolved Team preview additional environment value: $Name/$key"}
        $output.Add("$key=$value")
    }
    Write-PreviewFile $destination (($output -join "`r`n")+"`r`n")
}
function Save-Journal([string]$Step,[bool]$Provisioned) {
    $script:journal.current_step=$Step;$script:journal.provisioned=$Provisioned
    Write-PreviewFile $journalPath (($script:journal|ConvertTo-Json -Depth 5 -Compress)+"`n")
}
function Invoke-WithEnvironment([Collections.IDictionary]$Values,[scriptblock]$Action) {
    $prior=@{};try{foreach($key in $Values.Keys){$prior[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,[string]$Values[$key],'Process')};& $Action}
    finally{foreach($key in $Values.Keys){[Environment]::SetEnvironmentVariable($key,$prior[$key],'Process')}}
}

$dataRoot=Join-Path $root 'data';$secretRoot=Join-Path $root 'secrets'
$environmentRoot=Join-Path $root 'environments';$stateRoot=Join-Path $root 'state'
foreach($path in @($root,$dataRoot,(Join-Path $dataRoot 'postgres'),(Join-Path $dataRoot 'rustfs'),$secretRoot,$environmentRoot,$stateRoot)){Ensure-PreviewDirectory $path}
$journalPath=Join-Path $stateRoot 'team-preview-provisioning.json'
$secretPath=Join-Path $secretRoot 'team-preview-secrets.json'
if(Test-Path -LiteralPath $journalPath){$script:journal=Get-Content -Raw $journalPath|ConvertFrom-Json}
else{$script:journal=[ordered]@{schema_version=1;installation_id=[guid]::NewGuid().ToString('D');current_step='created';provisioned=$false}}
if([string]$script:journal.installation_id -cnotmatch '^[0-9a-f]{8}-[0-9a-f-]{27}$'){throw 'Team preview provisioning journal is invalid.'}

if(Test-Path -LiteralPath $secretPath){$secret=Get-Content -Raw $secretPath|ConvertFrom-Json}
else{
    $secret=[ordered]@{schema_version=1;installation_id=$script:journal.installation_id;values=[ordered]@{
        postgres_cluster_admin=New-PreviewSecret;postgres_migrator=New-PreviewSecret;postgres_api=New-PreviewSecret;postgres_worker=New-PreviewSecret;postgres_maintenance=New-PreviewSecret
        rustfs_root_access=New-PreviewAccess 'ragroot';rustfs_root_secret=New-PreviewSecret
        rustfs_api_access=New-PreviewAccess 'ragapi';rustfs_api_secret=New-PreviewSecret
        rustfs_ingestion_access=New-PreviewAccess 'ragingest';rustfs_ingestion_secret=New-PreviewSecret
        rustfs_deletion_access=New-PreviewAccess 'ragdelete';rustfs_deletion_secret=New-PreviewSecret
        rustfs_maintenance_access=New-PreviewAccess 'ragmaint';rustfs_maintenance_secret=New-PreviewSecret
        csrf_signing_secret=New-PreviewSecret;coordinator_service_token=New-PreviewSecret;controller_service_token=New-PreviewSecret;ocr_service_token=New-PreviewSecret
    }}
    Write-PreviewFile $secretPath (($secret|ConvertTo-Json -Depth 5 -Compress)+"`n")
}
$v=$secret.values;$project='localrag-team-'+([string]$script:journal.installation_id).Replace('-','').Substring(0,12)
$composeEnvironment=Join-Path $secretRoot 'stores.env'
$composeValues=[ordered]@{
    RAG_TEAM_INSTALLATION_ID=$script:journal.installation_id
    RAG_TEAM_POSTGRES_DATA=(Join-Path $dataRoot 'postgres')
    RAG_TEAM_RUSTFS_DATA=(Join-Path $dataRoot 'rustfs')
    POSTGRES_CLUSTER_ADMIN_PASSWORD=$v.postgres_cluster_admin
    RUSTFS_ROOT_ACCESS_KEY=$v.rustfs_root_access;RUSTFS_ROOT_SECRET_KEY=$v.rustfs_root_secret
}
Write-PreviewFile $composeEnvironment ((@($composeValues.Keys|ForEach-Object{"$_=$($composeValues[$_])"}) -join "`r`n")+"`r`n")
$docker=(Get-Command docker.exe -ErrorAction Stop).Source
& $docker compose -p $project --env-file $composeEnvironment -f $composeFile up -d --wait
if($LASTEXITCODE -ne 0){throw 'Team preview stores failed to start healthy.'};Save-Journal 'stores_started' $true
& (Join-Path $PSScriptRoot 'Initialize-RagTeamPreviewPostgres.ps1') -ComposeFile $composeFile -ComposeProject $project -ComposeEnvironment $composeEnvironment -SecretDocument $secretPath|Out-Null
Save-Journal 'postgres_provisioned' $true
$rustWorking=Join-Path $stateRoot 'rustfs-bootstrap';Ensure-PreviewDirectory $rustWorking
& (Join-Path $PSScriptRoot 'Initialize-RagTeamPreviewRustfs.ps1') -Endpoint 'http://127.0.0.1:9000/' -SecretDocument $secretPath -McPath $mc -WorkingRoot $rustWorking|Out-Null
Save-Journal 'rustfs_provisioned' $true

$dbBase='127.0.0.1:5432/rag';$objectCommon=@{OBJECT_STORAGE_BUCKET='rag-originals';OBJECT_STORAGE_ENDPOINT_URL='http://127.0.0.1:9000';OBJECT_STORAGE_USE_TLS='false'}
Write-StrictEnvironment api (@{DATABASE_URL="postgresql+psycopg://rag_api:$($v.postgres_api)@$dbBase";CANONICAL_HOST='rag.home.arpa';CANONICAL_ORIGIN='https://rag.home.arpa';COORDINATOR_SERVICE_TOKEN=$v.coordinator_service_token;CONTROLLER_SERVICE_TOKEN=$v.controller_service_token;CSRF_SIGNING_SECRET=$v.csrf_signing_secret;DEPLOYMENT_ID=('rag-team-'+$project.Substring(14));OBJECT_STORAGE_ACCESS_KEY_ID=$v.rustfs_api_access;OBJECT_STORAGE_SECRET_ACCESS_KEY=$v.rustfs_api_secret}+$objectCommon) @() @{PRODUCT_PROFILE='team_lan_preview_unsigned';RAG_LAN_IPV4=$address}
Write-StrictEnvironment ingestion (@{WORKER_DATABASE_URL="postgresql+psycopg://rag_worker:$($v.postgres_worker)@$dbBase";COORDINATOR_SERVICE_TOKEN=$v.coordinator_service_token;OCR_SERVICE_TOKEN=$v.ocr_service_token;OBJECT_STORAGE_ACCESS_KEY_ID=$v.rustfs_ingestion_access;OBJECT_STORAGE_SECRET_ACCESS_KEY=$v.rustfs_ingestion_secret}+$objectCommon)
Write-StrictEnvironment deletion (@{WORKER_DATABASE_URL="postgresql+psycopg://rag_worker:$($v.postgres_worker)@$dbBase";OBJECT_STORAGE_ACCESS_KEY_ID=$v.rustfs_deletion_access;OBJECT_STORAGE_SECRET_ACCESS_KEY=$v.rustfs_deletion_secret}+$objectCommon)
Write-StrictEnvironment inference @{COORDINATOR_SERVICE_TOKEN=$v.coordinator_service_token}
Write-StrictEnvironment ocr @{OCR_SERVICE_TOKEN=$v.ocr_service_token}
Write-StrictEnvironment web @{}
Write-StrictEnvironment caddy @{RAG_LAN_IPV4=$address} @('RAG_LAN_IPV6')
Save-Journal 'environments_rendered' $true

$migration=@{MIGRATION_DATABASE_URL="postgresql+psycopg://rag_migrator:$($v.postgres_migrator)@$dbBase"}
Invoke-WithEnvironment $migration {
    Push-Location (Join-Path $release 'apps\api');try{
        & $python -m alembic upgrade head|Out-Null;if($LASTEXITCODE -ne 0){throw 'Team preview Alembic upgrade failed.'}
        $current=@(& $python -m alembic current 2>&1);if($LASTEXITCODE -ne 0 -or ($current -join ' ') -notmatch [regex]::Escape($expectedRevision)){throw 'Team preview database did not reach exact packaged Alembic head.'}
    }finally{Pop-Location}
}
$rlsSql="SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relrowsecurity AND NOT c.relforcerowsecurity;"
$rls=@($rlsSql|& $docker compose -p $project --env-file $composeEnvironment -f $composeFile exec -T postgres psql -X -A -t -U rag_cluster_admin -d rag)
if($LASTEXITCODE -ne 0 -or ($rls|Select-Object -Last 1).Trim() -ne '0'){throw 'Team preview schema contains RLS tables that are not forced.'}
Save-Journal 'schema_migrated' $true

$maintenance=[ordered]@{MAINTENANCE_DATABASE_URL="postgresql+psycopg://rag_maintenance:$($v.postgres_maintenance)@$dbBase";ENVIRONMENT='production';CORS_ORIGINS='[]';CSRF_SIGNING_SECRET=$v.csrf_signing_secret;COORDINATOR_SERVICE_TOKEN=$v.coordinator_service_token;OBJECT_STORAGE_ENDPOINT_URL='http://127.0.0.1:9000';OBJECT_STORAGE_REGION='us-east-1';OBJECT_STORAGE_BUCKET='rag-originals';OBJECT_STORAGE_ACCESS_KEY_ID=$v.rustfs_maintenance_access;OBJECT_STORAGE_SECRET_ACCESS_KEY=$v.rustfs_maintenance_secret;OBJECT_STORAGE_FORCE_PATH_STYLE='true';OBJECT_STORAGE_USE_TLS='false'}
$setupCode=$null
Invoke-WithEnvironment $maintenance {
    Push-Location (Join-Path $release 'apps\api');try{
        & $python -m app.maintenance_cli storage-bootstrap|Out-Null;if($LASTEXITCODE -ne 0){throw 'Team preview storage bootstrap failed.'}
        $output=@(& $python -m app.maintenance_cli --confirm-stopped setup-code-issue 2>&1)
        if($LASTEXITCODE -ne 0){throw 'Team preview owner setup-code issuance failed.'}
        $script:setupCode=[string](@($output|Where-Object{-not [string]::IsNullOrWhiteSpace([string]$_)})[-1])
    }finally{Pop-Location}
}
if($script:setupCode -cnotmatch '^[A-Za-z0-9_-]{32,128}$'){throw 'Team preview setup-code result is invalid.'}
Save-Journal 'setup_code_issued' $true
[ordered]@{result='provisioned';installation_id=$script:journal.installation_id;connector_generation=1;alembic_revision=$expectedRevision;environment_root=$environmentRoot;setup_code=$script:setupCode;data_preserved=$true} | ConvertTo-Json -Depth 3
$script:setupCode=$null
