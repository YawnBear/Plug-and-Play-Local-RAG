[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BackupBundle,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$EvidenceSha256,
    [Parameter(Mandatory)][string]$ProgramDataRoot,
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][ValidatePattern('^[0-9]{4}_[a-z0-9_]+$')][string]$ExpectedRevision,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f-]{36}$')][string]$InstallationId,
    [switch]$ConfirmServiceStopped,
    [switch]$Plan
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest

function Invoke-WithEnvironment([Collections.IDictionary]$Values,[scriptblock]$Action){$prior=@{};try{foreach($key in $Values.Keys){$prior[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,[string]$Values[$key],'Process')};& $Action}finally{foreach($key in $Values.Keys){[Environment]::SetEnvironmentVariable($key,$prior[$key],'Process')}}}
function Invoke-Psql([string]$Sql){$prior=$ErrorActionPreference;try{$ErrorActionPreference='Continue';$output=@($Sql|& $script:docker compose -p $script:project --env-file $script:storeEnvironment -f $script:compose exec -T postgres psql -X -A -t -v ON_ERROR_STOP=1 -U rag_cluster_admin -d rag 2>&1);$code=$LASTEXITCODE}finally{$ErrorActionPreference=$prior};if($code -ne 0){throw 'Restored live PostgreSQL inspection failed.'};[string](@($output|Where-Object{-not[string]::IsNullOrWhiteSpace([string]$_)})[-1])}

$bundle=(Resolve-Path -LiteralPath $BackupBundle).Path.TrimEnd('\');$evidencePath=Join-Path $bundle 'restore-verified-evidence.json';$metadataPath=Join-Path $bundle 'backup-metadata.json';$manifestPath=Join-Path $bundle 'manifest.json';$dumpPath=Join-Path $bundle 'database.dump'
foreach($path in @($evidencePath,$metadataPath,$manifestPath,$dumpPath)){if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw 'Verified rollback backup is incomplete.'}}
if((Get-FileHash $evidencePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $EvidenceSha256){throw 'Rollback backup evidence hash does not match the update journal.'}
$evidence=Get-Content -Raw $evidencePath|ConvertFrom-Json;$metadata=Get-Content -Raw $metadataPath|ConvertFrom-Json;$manifest=Get-Content -Raw $manifestPath|ConvertFrom-Json
if([string]$evidence.result -cne 'pass' -or [string]$evidence.profile -cne 'team_lan_preview_unsigned' -or [string]$evidence.installation_id -cne $InstallationId -or [string]$evidence.backup_id -cne [string]$metadata.backup_id -or [string]$metadata.installation_id -cne $InstallationId -or [string]$manifest.alembic_revision -cne $ExpectedRevision){throw 'Rollback backup evidence binding is invalid.'}
if((Get-FileHash $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne [string]$evidence.manifest_sha256 -or (Get-FileHash $dumpPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne [string]$evidence.database_sha256){throw 'Rollback backup pair is tampered.'}
if($Plan){[ordered]@{result='plan';mutations_performed=$false;steps=@('require_service_stopped','replace_live_database_from_verified_custom_dump','empty_live_object_bucket','import_exact_verified_objects','verify_database_security_catalog_and_object_inventory','restart_prior_service_only_by_caller')}|ConvertTo-Json -Depth 4;return}
if(-not $ConfirmServiceStopped -or (Get-Service RagSupervisor -ErrorAction Stop).Status -ne 'Stopped'){throw 'Live backup restore requires the application service to be stopped and explicitly confirmed.'}
$secretPath=Join-Path $ProgramDataRoot 'secrets\team-preview-secrets.json';$script:storeEnvironment=Join-Path $ProgramDataRoot 'secrets\stores.env';$script:compose=Join-Path $ReleaseRoot 'ops\windows\team_preview\compose.team-preview.yaml';$python=Join-Path $ReleaseRoot 'runtimes\api-python\python.exe';$mc=Join-Path $ReleaseRoot 'tools\mc\mc.exe'
foreach($path in @($secretPath,$script:storeEnvironment,$script:compose,$python,$mc)){if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "Installed rollback contract is missing: $path"}}
$secret=Get-Content -Raw $secretPath|ConvertFrom-Json
if([string]$secret.installation_id -cne $InstallationId){throw 'Installed rollback secrets are not bound to this installation.'}
$script:project='localrag-team-'+$InstallationId.Replace('-','').Substring(0,12);$script:docker=(Get-Command docker.exe -ErrorAction Stop).Source;$containerDump="/tmp/localrag-rollback-$([guid]::NewGuid().ToString('N')).dump"
try{
    & $script:docker compose -p $script:project --env-file $script:storeEnvironment -f $script:compose cp $dumpPath "postgres:$containerDump";if($LASTEXITCODE -ne 0){throw 'Verified rollback database dump transfer failed.'}
    & $script:docker compose -p $script:project --env-file $script:storeEnvironment -f $script:compose exec -T postgres dropdb -U rag_cluster_admin --maintenance-db=postgres --if-exists --force rag;if($LASTEXITCODE -ne 0){throw 'Live rollback database removal failed.'}
    & $script:docker compose -p $script:project --env-file $script:storeEnvironment -f $script:compose exec -T postgres createdb -U rag_cluster_admin --maintenance-db=postgres --template=template0 --owner=rag_cluster_admin rag;if($LASTEXITCODE -ne 0){throw 'Live rollback database recreation failed.'}
    & $script:docker compose -p $script:project --env-file $script:storeEnvironment -f $script:compose exec -T postgres pg_restore -U rag_cluster_admin -d rag --exit-on-error $containerDump;if($LASTEXITCODE -ne 0){throw 'Verified live PostgreSQL restore failed.'}
    $v=$secret.values;$mcRoot='MC_HOST_ragteamrollback';$escapedAccess=[uri]::EscapeDataString([string]$v.rustfs_root_access);$escapedSecret=[uri]::EscapeDataString([string]$v.rustfs_root_secret)
    $maintenance=[ordered]@{MAINTENANCE_DATABASE_URL="postgresql+psycopg://rag_maintenance:$($v.postgres_maintenance)@127.0.0.1:5432/rag";PYTHONPATH=(Join-Path $ReleaseRoot 'apps\api');ENVIRONMENT='production';CORS_ORIGINS='[]';CSRF_SIGNING_SECRET=$v.csrf_signing_secret;COORDINATOR_SERVICE_TOKEN=$v.coordinator_service_token;OBJECT_STORAGE_ENDPOINT_URL='http://127.0.0.1:9000';OBJECT_STORAGE_REGION='us-east-1';OBJECT_STORAGE_BUCKET='rag-originals';OBJECT_STORAGE_ACCESS_KEY_ID=$v.rustfs_maintenance_access;OBJECT_STORAGE_SECRET_ACCESS_KEY=$v.rustfs_maintenance_secret;OBJECT_STORAGE_FORCE_PATH_STYLE='true';OBJECT_STORAGE_USE_TLS='false';$mcRoot="http://$escapedAccess`:$escapedSecret@127.0.0.1:9000/"}
    Invoke-WithEnvironment $maintenance {
        & $mc rb --force ragteamrollback/rag-originals|Out-Null;if($LASTEXITCODE -ne 0){throw 'Live rollback object bucket reset failed.'}
        Push-Location (Join-Path $ReleaseRoot 'apps\api')
        try{& $python -m app.maintenance_cli storage-bootstrap|Out-Null;if($LASTEXITCODE -ne 0){throw 'Live rollback object bucket bootstrap failed.'};& $python -m app.maintenance_cli --confirm-stopped storage-import $bundle|Out-Null;if($LASTEXITCODE -ne 0){throw 'Verified live object restore failed.'};& $python -m app.maintenance_cli storage-audit|Out-Null;if($LASTEXITCODE -ne 0){throw 'Restored live object inventory audit failed.'}}
        finally{Pop-Location}
    }
    $security=Invoke-Psql @"
SELECT v4_schema_revision()='$ExpectedRevision' AND v9_runtime_configuration_integrity()
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
    if($security.Trim() -cne 't'){throw 'Restored live database security verification failed.'}
    $catalog=Invoke-Psql @"
SELECT jsonb_build_object(
 'users',(SELECT count(*) FROM users),'teams',(SELECT count(*) FROM teams),'documents',(SELECT count(*) FROM documents),'chunks',(SELECT count(*) FROM chunks),
 'library_nodes',(SELECT count(*) FROM library_nodes),'ingestion_jobs',(SELECT count(*) FROM ingestion_jobs),'chats',(SELECT count(*) FROM chats),'chat_turns',(SELECT count(*) FROM chat_turns),'object_deletions',(SELECT count(*) FROM object_deletions),
 'document_catalog_sha256',encode(digest(COALESCE((SELECT string_agg(id::text||':'||object_key||':'||sha256||':'||byte_size::text,'|' ORDER BY id) FROM documents),''),'sha256'),'hex'),
 'user_catalog_sha256',encode(digest(COALESCE((SELECT string_agg(id::text||':'||username||':'||role||':'||status,'|' ORDER BY id) FROM users),''),'sha256'),'hex'));
"@
    if((($catalog|ConvertFrom-Json)|ConvertTo-Json -Compress) -cne (($metadata.source_catalog)|ConvertTo-Json -Compress)){throw 'Restored live application catalog does not match the verified backup.'}
    [ordered]@{result='restored_and_verified';backup_id=$metadata.backup_id;evidence_sha256=$EvidenceSha256;database_catalog='pass';object_inventory='exact_size_sha256_pass'}|ConvertTo-Json -Depth 3
}finally{
    & $script:docker compose -p $script:project --env-file $script:storeEnvironment -f $script:compose exec -T postgres rm -f $containerDump 2>$null|Out-Null
    $secret=$null;$v=$null;$maintenance=$null;$escapedSecret=$null
}
