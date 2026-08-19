[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ComposeFile,
    [Parameter(Mandatory)][ValidatePattern('^localrag-team-[0-9a-f]{12}$')][string]$ComposeProject,
    [Parameter(Mandatory)][string]$ComposeEnvironment,
    [Parameter(Mandatory)][string]$SecretDocument
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function ConvertTo-SqlLiteral([string]$Value) { "'" + $Value.Replace("'", "''") + "'" }
$secret = Get-Content -Raw -LiteralPath $SecretDocument | ConvertFrom-Json
$passwords = [ordered]@{
    rag_migrator=[string]$secret.values.postgres_migrator
    rag_api=[string]$secret.values.postgres_api
    rag_worker=[string]$secret.values.postgres_worker
    rag_maintenance=[string]$secret.values.postgres_maintenance
}
if (@($passwords.Values | Sort-Object -Unique).Count -ne 4) {
    throw 'Team preview PostgreSQL role secrets must be pairwise distinct.'
}
$alter = @($passwords.Keys | ForEach-Object {
    "ALTER ROLE $_ PASSWORD $(ConvertTo-SqlLiteral $passwords[$_]);"
}) -join "`n"
$sql = @"
\set ON_ERROR_STOP on
\set ECHO none
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
BEGIN;
DO `$roles`$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rag_owner') THEN CREATE ROLE rag_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rag_migrator') THEN CREATE ROLE rag_migrator LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rag_api') THEN CREATE ROLE rag_api LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rag_worker') THEN CREATE ROLE rag_worker LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rag_maintenance') THEN CREATE ROLE rag_maintenance LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rag_backup') THEN CREATE ROLE rag_backup NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS; END IF;
END `$roles`$;
ALTER ROLE rag_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS;
ALTER ROLE rag_migrator LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE rag_api LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE rag_worker LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE rag_maintenance LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE rag_backup NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS PASSWORD NULL;
ALTER ROLE rag_backup SET default_transaction_read_only=on;
GRANT rag_owner TO rag_migrator;
$alter
GRANT CONNECT ON DATABASE rag TO rag_migrator,rag_api,rag_worker,rag_maintenance,rag_backup;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE,CREATE ON SCHEMA public TO rag_owner;
GRANT USAGE ON SCHEMA public TO rag_migrator,rag_api,rag_worker,rag_maintenance,rag_backup;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC,rag_owner,rag_migrator,rag_api,rag_worker,rag_maintenance,rag_backup;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO rag_owner;
GRANT EXECUTE ON FUNCTION public.cosine_distance(vector,vector) TO rag_api;
COMMIT;
"@
$docker = (Get-Command docker.exe -ErrorAction Stop).Source
$prior = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $ignored = $sql | & $docker compose -p $ComposeProject --env-file $ComposeEnvironment `
        -f $ComposeFile exec -T postgres psql -X -U rag_cluster_admin -d rag 2>&1
    $code = $LASTEXITCODE
} finally { $ErrorActionPreference = $prior }
$ignored = $null
$sql = $null
foreach ($key in @($passwords.Keys)) { $passwords[$key] = $null }
if ($code -ne 0) { throw 'Automatic Team preview PostgreSQL role provisioning failed.' }
[pscustomobject]@{result='pass';passwords_logged=$false;transport='docker_exec_stdin'} |
    ConvertTo-Json -Compress
