[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ComposeFile,
    [Parameter(Mandatory)][ValidatePattern('^localrag-(personal|verify)-[0-9a-f]{12}$')]
    [string]$ComposeProject,
    [Parameter(Mandatory)][string]$ComposeEnvironment,
    [Parameter(Mandatory)][string]$SecretDocument
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagPersonal.psm1') -Force

function ConvertTo-SqlLiteral {
    param([Parameter(Mandatory)][string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

$secrets = Read-RagPersonalJson -Path $SecretDocument
if ($secrets.schema_version -ne 1 -or $secrets.installation_id -cnotmatch '^[0-9a-f]{32}$') {
    throw 'Personal secret document identity is invalid.'
}
$values = $secrets.values
$rolePasswords = [ordered]@{
    rag_migrator = [string]$values.postgres_migrator
    rag_api = [string]$values.postgres_api
    rag_worker = [string]$values.postgres_worker
    rag_maintenance = [string]$values.postgres_maintenance
}
$distinct = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($password in $rolePasswords.Values) {
    if ([string]::IsNullOrWhiteSpace($password) -or -not $distinct.Add($password)) {
        throw 'Personal PostgreSQL role secrets must be nonempty and pairwise distinct.'
    }
}
$passwordStatements = foreach ($role in $rolePasswords.Keys) {
    "ALTER ROLE $role PASSWORD $(ConvertTo-SqlLiteral -Value $rolePasswords[$role]);"
}
$sql = @"
\set ON_ERROR_STOP on
\set ECHO none
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
BEGIN;
DO `$roles`$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_owner') THEN
        CREATE ROLE rag_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION BYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_migrator') THEN
        CREATE ROLE rag_migrator LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_api') THEN
        CREATE ROLE rag_api LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_worker') THEN
        CREATE ROLE rag_worker LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_maintenance') THEN
        CREATE ROLE rag_maintenance LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_backup') THEN
        CREATE ROLE rag_backup NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION BYPASSRLS;
    END IF;
END
`$roles`$;
ALTER ROLE rag_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION BYPASSRLS;
ALTER ROLE rag_migrator LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS;
ALTER ROLE rag_api LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS;
ALTER ROLE rag_worker LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS;
ALTER ROLE rag_maintenance LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS;
ALTER ROLE rag_backup NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION BYPASSRLS PASSWORD NULL;
ALTER ROLE rag_backup SET default_transaction_read_only = on;
GRANT rag_owner TO rag_migrator;
$($passwordStatements -join "`r`n")
GRANT CONNECT ON DATABASE rag TO rag_migrator, rag_api, rag_worker,
    rag_maintenance, rag_backup;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO rag_owner;
GRANT USAGE ON SCHEMA public TO rag_migrator, rag_api, rag_worker,
    rag_maintenance, rag_backup;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, rag_owner, rag_migrator,
    rag_api, rag_worker, rag_maintenance, rag_backup;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO rag_owner;
GRANT EXECUTE ON FUNCTION public.cosine_distance(vector, vector) TO rag_api;
COMMIT;
"@

$docker = (Get-Command docker.exe -ErrorAction Stop).Source
$result = $null
$provisioned = $false
for ($attempt = 1; $attempt -le 10; $attempt++) {
    $priorErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $result = $sql | & $docker compose -p $ComposeProject `
            --env-file $ComposeEnvironment -f $ComposeFile exec -T postgres `
            psql -X -U rag_cluster_admin -d rag 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $priorErrorPreference }
    if ($exitCode -eq 0) {
        $provisioned = $true
        break
    }
    if ($attempt -lt 10) { Start-Sleep -Seconds 2 }
}
if (-not $provisioned) {
    throw 'Automatic Personal PostgreSQL role provisioning failed. See Docker logs.'
}
[pscustomobject]@{
    result = 'pass'
    roles = @('rag_owner','rag_migrator','rag_api','rag_worker','rag_maintenance','rag_backup')
    passwords_logged = $false
    mutations_performed = $true
} | ConvertTo-Json -Compress

foreach ($key in @($rolePasswords.Keys)) { $rolePasswords[$key] = $null }
$sql = $null
$result = $null
