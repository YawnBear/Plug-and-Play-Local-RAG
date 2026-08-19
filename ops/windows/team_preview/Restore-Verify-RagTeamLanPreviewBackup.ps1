[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BackupBundle,
    [Parameter(Mandatory)][string]$ProgramDataRoot,
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][ValidatePattern('^[0-9]{4}_[a-z0-9_]+$')][string]$ExpectedRevision,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f-]{36}$')][string]$InstallationId,
    [switch]$Plan
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest

function Write-Json([string]$Path,$Value) {
    $temporary="$Path.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temporary,(($Value|ConvertTo-Json -Depth 20)+"`n"),[Text.UTF8Encoding]::new($false))
    if(Test-Path -LiteralPath $Path){[IO.File]::Replace($temporary,$Path,$null,$true)}else{[IO.File]::Move($temporary,$Path)}
}
function Protect-VerificationPath([string]$Path,[switch]$Recursive) {
    $extra=if($Recursive){@('/T','/C')}else{@()}
    & icacls.exe $Path /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' `
        '*S-1-5-18:(F)' '*S-1-5-32-544:(OI)(CI)(F)' `
        '*S-1-5-32-544:(F)' @extra | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Restore verification ACL protection failed.'}
}
function New-Secret([int]$Bytes=32){$b=[byte[]]::new($Bytes);$r=[Security.Cryptography.RandomNumberGenerator]::Create();try{$r.GetBytes($b);[Convert]::ToBase64String($b).TrimEnd('=').Replace('+','-').Replace('/','_')}finally{$r.Dispose();[Array]::Clear($b,0,$b.Length)}}
function Write-Environment([string]$Path,[Collections.IDictionary]$Values){
    foreach($key in $Values.Keys){if([string]$Values[$key] -match '[\r\n]'){throw 'Verifier environment value is invalid.'}}
    [IO.File]::WriteAllText($Path,((@($Values.Keys|ForEach-Object{"$_=$($Values[$_])"})-join "`r`n")+"`r`n"),[Text.UTF8Encoding]::new($false))
}
function Invoke-WithEnvironment([Collections.IDictionary]$Values,[scriptblock]$Action){$prior=@{};try{foreach($key in $Values.Keys){$prior[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,[string]$Values[$key],'Process')};& $Action}finally{foreach($key in $Values.Keys){[Environment]::SetEnvironmentVariable($key,$prior[$key],'Process')}}}
function Invoke-Compose([Parameter(ValueFromRemainingArguments)][string[]]$Arguments){& $script:docker compose -p $script:project --env-file $script:environment -f $script:compose @Arguments;if($LASTEXITCODE -ne 0){throw 'Isolated restore container operation failed.'}}
function Invoke-Psql([string]$Sql){$prior=$ErrorActionPreference;try{$ErrorActionPreference='Continue';$output=@($Sql|& $script:docker compose -p $script:project --env-file $script:environment -f $script:compose exec -T postgres psql -X -A -t -v ON_ERROR_STOP=1 -U rag_cluster_admin -d rag 2>&1);$code=$LASTEXITCODE}finally{$ErrorActionPreference=$prior};if($code -ne 0){throw 'Isolated restored database inspection failed.'};[string](@($output|Where-Object{-not[string]::IsNullOrWhiteSpace([string]$_)})[-1])}
function Get-PublishedPort([string]$Service,[string]$Target){$value=(& $script:docker compose -p $script:project --env-file $script:environment -f $script:compose port $Service $Target|Out-String).Trim();if($LASTEXITCODE -ne 0 -or $value -cnotmatch '^127\.0\.0\.1:([0-9]+)$'){throw 'Restore verifier is not bound to IPv4 loopback.'};[int]$Matches[1]}

$bundle=(Resolve-Path -LiteralPath $BackupBundle).Path.TrimEnd('\')
$bundleItem=Get-Item -LiteralPath $bundle -Force
if(-not $bundleItem.PSIsContainer -or ($bundleItem.Attributes-band[IO.FileAttributes]::ReparsePoint)){throw 'Backup bundle must be an existing real directory.'}
$metadataPath=Join-Path $bundle 'backup-metadata.json';$manifestPath=Join-Path $bundle 'manifest.json';$dumpPath=Join-Path $bundle 'database.dump'
foreach($path in @($metadataPath,$manifestPath,$dumpPath)){if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw 'Backup bundle is incomplete.'}}
$metadata=Get-Content -Raw $metadataPath|ConvertFrom-Json;$manifest=Get-Content -Raw $manifestPath|ConvertFrom-Json
if($metadata.schema_version -ne 1 -or [string]$metadata.profile -cne 'team_lan_preview_unsigned' -or
    [string]$metadata.installation_id -cne $InstallationId -or [string]$metadata.alembic_revision -cne $ExpectedRevision -or
    [string]$manifest.alembic_revision -cne $ExpectedRevision){throw 'Backup bundle is not bound to this Team/LAN preview installation and revision.'}
$manifestHash=(Get-FileHash $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant();$dumpHash=(Get-FileHash $dumpPath -Algorithm SHA256).Hash.ToLowerInvariant()
if($manifestHash -cne [string]$metadata.manifest_sha256 -or $dumpHash -cne [string]$metadata.database_sha256){throw 'Backup bundle checksum verification failed.'}
[int64]$objectBytes=0;foreach($object in @($manifest.objects)){$objectBytes += [int64]$object.byte_size}
if(@($manifest.objects).Count -ne [int]$metadata.object_count -or $objectBytes -ne [int64]$metadata.object_bytes){throw 'Backup object inventory summary is invalid.'}
if($Plan){[ordered]@{result='plan';mutations_performed=$false;profile='team_lan_preview_unsigned';backup_id=$metadata.backup_id;steps=@('start_disposable_loopback_stores','restore_custom_dump','verify_revision_roles_grants_forced_rls','compare_application_catalog','import_and_hash_exact_object_inventory','write_strict_evidence','destroy_disposable_stores')}|ConvertTo-Json -Depth 4;return}

$script:compose=Join-Path $PSScriptRoot 'Restore-RagTeamLanPreview.compose.yaml';$python=Join-Path $ReleaseRoot 'runtimes\api-python\python.exe'
$provision=Join-Path $PSScriptRoot 'Initialize-RagTeamPreviewPostgres.ps1'
foreach($path in @($script:compose,$python,$provision)){if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "Restore verifier asset is missing: $path"}}
$verifyId=[guid]::NewGuid().ToString('N');$script:project='localrag-team-'+$verifyId.Substring(0,12)
$work=Join-Path $ProgramDataRoot "restore-verification\$verifyId";$postgres=Join-Path $work 'postgres';$rustfs=Join-Path $work 'rustfs';$application=Join-Path $work 'application'
foreach($path in @($work,$postgres,$rustfs,$application)){[IO.Directory]::CreateDirectory($path)|Out-Null}
$secrets=[ordered]@{schema_version=1;installation_id=$verifyId;values=[ordered]@{postgres_cluster_admin=New-Secret;postgres_migrator=New-Secret;postgres_api=New-Secret;postgres_worker=New-Secret;postgres_maintenance=New-Secret;rustfs_root_access=('verify-'+$verifyId.Substring(0,16));rustfs_root_secret=New-Secret}}
$secretPath=Join-Path $work 'secrets.json';Write-Json $secretPath $secrets
$script:environment=Join-Path $work 'compose.env';Write-Environment $script:environment ([ordered]@{RAG_VERIFY_ID=$verifyId;RAG_VERIFY_POSTGRES_DATA=$postgres;RAG_VERIFY_RUSTFS_DATA=$rustfs;POSTGRES_CLUSTER_ADMIN_PASSWORD=$secrets.values.postgres_cluster_admin;RUSTFS_ROOT_ACCESS_KEY=$secrets.values.rustfs_root_access;RUSTFS_ROOT_SECRET_KEY=$secrets.values.rustfs_root_secret})
Protect-VerificationPath $work -Recursive
$script:docker=(Get-Command docker.exe -ErrorAction Stop).Source
try{
    Invoke-Compose up -d --wait
    & $provision -ComposeFile $script:compose -ComposeProject $script:project -ComposeEnvironment $script:environment -SecretDocument $secretPath|Out-Null
    if($LASTEXITCODE -ne 0){throw 'Isolated restore PostgreSQL provisioning failed.'}
    Invoke-Compose cp $dumpPath 'postgres:/tmp/localrag-restore.dump'
    Invoke-Compose exec -T postgres pg_restore -U rag_cluster_admin -d rag --exit-on-error --clean --if-exists /tmp/localrag-restore.dump
    $security=Invoke-Psql @"
SELECT (SELECT version_num='$ExpectedRevision' FROM alembic_version)
 AND v4_schema_revision()='$ExpectedRevision' AND v9_runtime_configuration_integrity()
 AND (SELECT count(*)=6 FROM pg_roles WHERE rolname IN ('rag_owner','rag_migrator','rag_api','rag_worker','rag_maintenance','rag_backup'))
 AND NOT (SELECT rolcanlogin FROM pg_roles WHERE rolname='rag_owner') AND (SELECT rolbypassrls FROM pg_roles WHERE rolname='rag_owner')
 AND (SELECT rolcanlogin AND NOT rolbypassrls FROM pg_roles WHERE rolname='rag_migrator') AND pg_has_role('rag_migrator','rag_owner','member')
 AND (SELECT bool_and(rolcanlogin AND NOT rolbypassrls) FROM pg_roles WHERE rolname IN ('rag_api','rag_worker','rag_maintenance'))
 AND (SELECT NOT rolcanlogin AND rolbypassrls FROM pg_roles WHERE rolname='rag_backup')
 AND NOT has_schema_privilege('public','public','CREATE')
 AND (SELECT bool_and(has_database_privilege(rolname,'rag','CONNECT')) FROM pg_roles WHERE rolname IN ('rag_owner','rag_migrator','rag_api','rag_worker','rag_maintenance','rag_backup'))
 AND (SELECT bool_and(has_schema_privilege(rolname,'public','USAGE')) FROM pg_roles WHERE rolname IN ('rag_owner','rag_migrator','rag_api','rag_worker','rag_maintenance','rag_backup'))
 AND has_function_privilege('rag_backup','v4_schema_revision()','EXECUTE')
 AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relrowsecurity AND NOT c.relforcerowsecurity);
"@
    if($security.Trim() -cne 't'){throw 'Restored database revision, roles, grants, or forced RLS validation failed.'}
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
    $expectedCatalog=$metadata.source_catalog|ConvertTo-Json -Compress;$actualCatalog=($catalog|ConvertFrom-Json)|ConvertTo-Json -Compress
    if($actualCatalog -cne $expectedCatalog){throw 'Restored application row/catalog invariants do not match the source.'}
    $postgresPort=Get-PublishedPort postgres 5432;$rustfsPort=Get-PublishedPort rustfs 9000;$v=$secrets.values
    $maintenance=[ordered]@{MAINTENANCE_DATABASE_URL="postgresql+psycopg://rag_maintenance:$($v.postgres_maintenance)@127.0.0.1:$postgresPort/rag";DATA_ROOT=$application;PYTHONPATH=(Join-Path $ReleaseRoot 'apps\api');ENVIRONMENT='production';CORS_ORIGINS='[]';CSRF_SIGNING_SECRET=(New-Secret);COORDINATOR_SERVICE_TOKEN=(New-Secret);OBJECT_STORAGE_ENDPOINT_URL="http://127.0.0.1:$rustfsPort";OBJECT_STORAGE_REGION='us-east-1';OBJECT_STORAGE_BUCKET='rag-originals';OBJECT_STORAGE_ACCESS_KEY_ID=$v.rustfs_root_access;OBJECT_STORAGE_SECRET_ACCESS_KEY=$v.rustfs_root_secret;OBJECT_STORAGE_FORCE_PATH_STYLE='true';OBJECT_STORAGE_USE_TLS='false'}
    Invoke-WithEnvironment $maintenance {Push-Location (Join-Path $ReleaseRoot 'apps\api');try{& $python -m app.maintenance_cli storage-bootstrap|Out-Null;if($LASTEXITCODE -ne 0){throw 'Verifier bucket bootstrap failed.'};& $python -m app.maintenance_cli --confirm-stopped storage-import $bundle|Out-Null;if($LASTEXITCODE -ne 0){throw 'Exact object restore failed.'};& $python -m app.maintenance_cli storage-audit|Out-Null;if($LASTEXITCODE -ne 0){throw 'Restored object inventory audit failed.'}}finally{Pop-Location}}
    $postgresContainer=(& $script:docker compose -p $script:project --env-file $script:environment -f $script:compose ps -q postgres|Out-String).Trim();$rustfsContainer=(& $script:docker compose -p $script:project --env-file $script:environment -f $script:compose ps -q rustfs|Out-String).Trim()
    $postgresImage=(& $script:docker inspect --format '{{.Image}}' $postgresContainer|Out-String).Trim();$rustfsImage=(& $script:docker inspect --format '{{.Image}}' $rustfsContainer|Out-String).Trim()
    if($postgresImage -cnotmatch '^sha256:[0-9a-f]{64}$' -or $rustfsImage -cnotmatch '^sha256:[0-9a-f]{64}$'){throw 'Verifier container image digest inspection failed.'}
    $evidencePath=Join-Path $bundle 'restore-verified-evidence.json'
    $evidence=[ordered]@{schema_version=1;result='pass';verifier='team-lan-preview.isolated-restore.v1';profile='team_lan_preview_unsigned';installation_id=$InstallationId;backup_id=[string]$metadata.backup_id;verified_at=[DateTimeOffset]::UtcNow.ToString('o');alembic_revision=$ExpectedRevision;database_sha256=$dumpHash;manifest_sha256=$manifestHash;database_fingerprint=[string]$metadata.database_fingerprint;object_count=[int]$metadata.object_count;object_bytes=[int64]$metadata.object_bytes;object_inventory='exact_size_sha256_pass';database_security='roles_grants_forced_rls_pass';application_catalog='row_counts_and_catalog_hashes_pass';postgres_image_digest=$postgresImage;rustfs_image_digest=$rustfsImage;postgres_image_reference='pgvector/pgvector:0.8.5-pg18-bookworm@sha256:766437bbab40c7d0b080d380e2976f9ca2e880ce8fe7544c60e832eceaf43c1c';rustfs_image_reference='rustfs/rustfs:1.0.0-beta.10@sha256:60f4f2f41ce95216f8cac676e69f9d90c0bfec458a3bc7fd7fb9b7c2452ac57a'}
    Write-Json $evidencePath $evidence;Protect-VerificationPath $bundle -Recursive
    $evidenceHash=(Get-FileHash $evidencePath -Algorithm SHA256).Hash.ToLowerInvariant()
    [ordered]@{result='pass';backup_id=$metadata.backup_id;bundle=$bundle;evidence_path=$evidencePath;evidence_sha256=$evidenceHash;manifest_sha256=$manifestHash;database_sha256=$dumpHash;object_count=$metadata.object_count;object_bytes=$metadata.object_bytes}|ConvertTo-Json -Depth 4
}finally{
    try{Invoke-Compose down --remove-orphans}catch{Write-Warning 'Disposable restore verifier cleanup requires attended follow-up.'}
    if(Test-Path -LiteralPath $work){[IO.Directory]::Delete($work,$true)}
    $secrets=$null;$v=$null;$maintenance=$null
}
