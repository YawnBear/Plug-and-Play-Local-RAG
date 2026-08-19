[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BackupRoot,
    [Parameter(Mandatory)][string]$ProgramDataRoot,
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][string]$CandidateRoot,
    [Parameter(Mandatory)][ValidatePattern('^[0-9]{4}_[a-z0-9_]+$')][string]$ExpectedRevision,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f-]{36}$')][string]$InstallationId,
    [switch]$ConfirmServiceStopped,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Test-PathOverlap([string]$Left,[string]$Right) {
    $a=[IO.Path]::GetFullPath($Left).TrimEnd('\');$b=[IO.Path]::GetFullPath($Right).TrimEnd('\')
    return $a.Equals($b,[StringComparison]::OrdinalIgnoreCase) -or
        $a.StartsWith($b+'\',[StringComparison]::OrdinalIgnoreCase) -or
        $b.StartsWith($a+'\',[StringComparison]::OrdinalIgnoreCase)
}
function Protect-BackupPath([string]$Path,[switch]$Recursive) {
    $extra=if($Recursive){@('/T','/C')}else{@()}
    & icacls.exe $Path /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' `
        '*S-1-5-18:(F)' '*S-1-5-32-544:(OI)(CI)(F)' `
        '*S-1-5-32-544:(F)' @extra | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Restore-verified backup ACL protection failed.'}
}
function Write-Json([string]$Path,$Value) {
    $temporary="$Path.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temporary,(($Value|ConvertTo-Json -Depth 20)+"`n"),[Text.UTF8Encoding]::new($false))
    if(Test-Path -LiteralPath $Path){[IO.File]::Replace($temporary,$Path,$null,$true)}else{[IO.File]::Move($temporary,$Path)}
}
function Invoke-WithEnvironment([Collections.IDictionary]$Values,[scriptblock]$Action) {
    $prior=@{}
    try{foreach($key in $Values.Keys){$prior[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,[string]$Values[$key],'Process')};& $Action}
    finally{foreach($key in $Values.Keys){[Environment]::SetEnvironmentVariable($key,$prior[$key],'Process')}}
}
function Invoke-Psql([string]$Sql) {
    $prior=$ErrorActionPreference
    try{
        $ErrorActionPreference='Continue'
        $output=@($Sql | & $script:docker compose -p $script:project --env-file $script:storeEnvironment `
            -f $script:composeFile exec -T postgres psql -X -A -t -v ON_ERROR_STOP=1 `
            -U rag_cluster_admin -d rag 2>&1)
        $code=$LASTEXITCODE
    }finally{$ErrorActionPreference=$prior}
    if($code -ne 0){throw 'Source PostgreSQL backup preflight failed.'}
    return [string](@($output|Where-Object{-not [string]::IsNullOrWhiteSpace([string]$_)})[-1])
}

$root=(Resolve-Path -LiteralPath $BackupRoot).Path.TrimEnd('\')
$rootItem=Get-Item -LiteralPath $root -Force
if(-not $rootItem.PSIsContainer -or ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    $root -ceq [IO.Path]::GetPathRoot($root)) { throw 'Backup root must be an existing real non-volume-root directory.' }
foreach($protected in @($ProgramDataRoot,(Split-Path $ReleaseRoot -Parent),$ReleaseRoot,$CandidateRoot)){
    if(Test-PathOverlap $root $protected){throw 'Backup root must be outside ProgramData, Program Files, current, and candidate paths.'}
}
if($Plan){
    [ordered]@{result='plan';mutations_performed=$false;profile='team_lan_preview_unsigned';backup_root=$root;
        steps=@('require_service_stopped','custom_pg_dump','exact_rustfs_export','isolated_restore','database_security_and_catalog_verification','exact_object_inventory_verification','protect_and_retain_evidence')}|ConvertTo-Json -Depth 4
    return
}
if(-not $ConfirmServiceStopped -or (Get-Service RagSupervisor -ErrorAction Stop).Status -ne 'Stopped'){
    throw 'Backup capture requires the application service to be stopped and explicitly confirmed.'
}
$secretPath=Join-Path $ProgramDataRoot 'secrets\team-preview-secrets.json'
$script:storeEnvironment=Join-Path $ProgramDataRoot 'secrets\stores.env'
$script:composeFile=Join-Path $ReleaseRoot 'ops\windows\team_preview\compose.team-preview.yaml'
$python=Join-Path $ReleaseRoot 'runtimes\api-python\python.exe'
foreach($path in @($secretPath,$script:storeEnvironment,$script:composeFile,$python)){
    if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "Installed backup contract is missing: $path"}
}
$secret=Get-Content -Raw -LiteralPath $secretPath|ConvertFrom-Json
if([string]$secret.installation_id -cne $InstallationId){throw 'Installed backup secrets are not bound to this installation.'}
$script:project='localrag-team-'+$InstallationId.Replace('-','').Substring(0,12)
$script:docker=(Get-Command docker.exe -ErrorAction Stop).Source
$stamp=[DateTimeOffset]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$backupId=[guid]::NewGuid().ToString('D')
$protectedRoot=Join-Path $root 'LocalRAG-Team-Verified-Backups'
if(Test-Path -LiteralPath $protectedRoot){
    $protectedItem=Get-Item -LiteralPath $protectedRoot -Force
    if(-not $protectedItem.PSIsContainer -or ($protectedItem.Attributes -band [IO.FileAttributes]::ReparsePoint)){
        throw 'Protected Team backup container must be a real directory.'
    }
}else{[IO.Directory]::CreateDirectory($protectedRoot)|Out-Null}
Protect-BackupPath -Path $protectedRoot -Recursive
$bundle=Join-Path $protectedRoot "LocalRAG-Team-backup-$stamp-$($backupId.Substring(0,8))"
$workspace=Join-Path $ProgramDataRoot "backup-work\$($backupId.Replace('-',''))"
[IO.Directory]::CreateDirectory($workspace)|Out-Null
$containerDump="/tmp/localrag-$($backupId.Replace('-','')).dump"
$dump=Join-Path $workspace 'database.dump'
try{
    $revision=Invoke-Psql 'SELECT version_num FROM alembic_version;'
    if($revision -cne $ExpectedRevision){throw 'Source database revision does not match the update contract.'}
    $catalog=Invoke-Psql @"
SELECT jsonb_build_object(
 'users',(SELECT count(*) FROM users),'teams',(SELECT count(*) FROM teams),
 'documents',(SELECT count(*) FROM documents),'chunks',(SELECT count(*) FROM chunks),
 'library_nodes',(SELECT count(*) FROM library_nodes),'ingestion_jobs',(SELECT count(*) FROM ingestion_jobs),
 'chats',(SELECT count(*) FROM chats),'chat_turns',(SELECT count(*) FROM chat_turns),
 'object_deletions',(SELECT count(*) FROM object_deletions),
 'document_catalog_sha256',encode(digest(COALESCE((SELECT string_agg(id::text||':'||object_key||':'||sha256||':'||byte_size::text,'|' ORDER BY id) FROM documents),''),'sha256'),'hex'),
 'user_catalog_sha256',encode(digest(COALESCE((SELECT string_agg(id::text||':'||username||':'||role||':'||status,'|' ORDER BY id) FROM users),''),'sha256'),'hex'));
"@
    $null=$catalog|ConvertFrom-Json
    & $script:docker compose -p $script:project --env-file $script:storeEnvironment -f $script:composeFile `
        exec -T postgres pg_dump -U rag_cluster_admin -d rag --format=custom --file=$containerDump
    if($LASTEXITCODE -ne 0){throw 'PostgreSQL custom dump failed.'}
    & $script:docker compose -p $script:project --env-file $script:storeEnvironment -f $script:composeFile `
        cp "postgres:$containerDump" $dump
    if($LASTEXITCODE -ne 0){throw 'PostgreSQL custom dump export failed.'}
    $v=$secret.values
    $maintenance=[ordered]@{
        MAINTENANCE_DATABASE_URL="postgresql+psycopg://rag_maintenance:$($v.postgres_maintenance)@127.0.0.1:5432/rag"
        ENVIRONMENT='production';CORS_ORIGINS='[]';CSRF_SIGNING_SECRET=$v.csrf_signing_secret
        COORDINATOR_SERVICE_TOKEN=$v.coordinator_service_token;PYTHONPATH=(Join-Path $ReleaseRoot 'apps\api')
        OBJECT_STORAGE_ENDPOINT_URL='http://127.0.0.1:9000';OBJECT_STORAGE_REGION='us-east-1'
        OBJECT_STORAGE_BUCKET='rag-originals';OBJECT_STORAGE_ACCESS_KEY_ID=$v.rustfs_maintenance_access
        OBJECT_STORAGE_SECRET_ACCESS_KEY=$v.rustfs_maintenance_secret;OBJECT_STORAGE_FORCE_PATH_STYLE='true';OBJECT_STORAGE_USE_TLS='false'
    }
    Invoke-WithEnvironment $maintenance {
        Push-Location (Join-Path $ReleaseRoot 'apps\api')
        try{& $python -m app.maintenance_cli --confirm-stopped storage-export $bundle --database-dump $dump|Out-Null;if($LASTEXITCODE -ne 0){throw 'Exact RustFS export failed.'}}
        finally{Pop-Location}
    }
    $manifestPath=Join-Path $bundle 'manifest.json';$manifest=Get-Content -Raw $manifestPath|ConvertFrom-Json
    if([string]$manifest.alembic_revision -cne $ExpectedRevision){throw 'Backup manifest revision is invalid.'}
    [int64]$objectBytes=0;foreach($object in @($manifest.objects)){$objectBytes += [int64]$object.byte_size}
    $metadata=[ordered]@{schema_version=1;backup_id=$backupId;profile='team_lan_preview_unsigned';installation_id=$InstallationId;
        captured_at=[DateTimeOffset]::UtcNow.ToString('o');alembic_revision=$ExpectedRevision;
        database_sha256=(Get-FileHash -LiteralPath (Join-Path $bundle 'database.dump') -Algorithm SHA256).Hash.ToLowerInvariant();
        database_bytes=(Get-Item -LiteralPath (Join-Path $bundle 'database.dump')).Length;
        manifest_sha256=(Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant();
        object_count=@($manifest.objects).Count;object_bytes=$objectBytes;database_fingerprint=[string]$manifest.database_fingerprint;
        source_catalog=($catalog|ConvertFrom-Json);restore_verification='pending'}
    Write-Json (Join-Path $bundle 'backup-metadata.json') $metadata
    Protect-BackupPath -Path $bundle -Recursive
    [ordered]@{result='captured';backup_id=$backupId;bundle=$bundle;manifest_sha256=$metadata.manifest_sha256;
        database_sha256=$metadata.database_sha256;object_count=$metadata.object_count;object_bytes=$objectBytes}|ConvertTo-Json -Depth 4
}catch{
    if(Test-Path -LiteralPath $bundle){[IO.Directory]::Delete($bundle,$true)}
    throw
}finally{
    & $script:docker compose -p $script:project --env-file $script:storeEnvironment -f $script:composeFile `
        exec -T postgres rm -f $containerDump 2>$null | Out-Null
    if(Test-Path -LiteralPath $workspace){[IO.Directory]::Delete($workspace,$true)}
    $secret=$null;$v=$null
}
