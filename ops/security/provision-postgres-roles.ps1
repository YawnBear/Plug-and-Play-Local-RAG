[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('127.0.0.1', '::1')]
    [string]$DatabaseHost,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 65535)]
    [int]$DatabasePort,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z_][A-Za-z0-9_]*$')]
    [string]$DatabaseName,

    [Parameter(Mandatory = $true)]
    [ValidateSet('rag_cluster_admin')]
    [string]$ClusterAdministrator,

    [Parameter(Mandatory = $true)]
    [string]$PsqlPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$PsqlSha256,

    [string]$RoleSecretDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$WriteCapableRightsMask = [long](
    0x2 -bor 0x4 -bor 0x10 -bor 0x40 -bor 0x100 -bor
    0x10000 -bor 0x40000 -bor 0x80000
)

function ConvertTo-SqlLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

function Read-RoleSecret {
    param([Parameter(Mandatory = $true)][string]$RoleName)
    if (-not [string]::IsNullOrWhiteSpace($RoleSecretDirectory)) {
        $directory = [IO.Path]::GetFullPath($RoleSecretDirectory)
        $path = Join-Path $directory "$RoleName.password"
        foreach ($target in @($directory, $path)) {
            $item = Get-Item -LiteralPath $target -Force -ErrorAction Stop
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Protected PostgreSQL role-secret path contains a reparse point.'
            }
            $acl = Get-Acl -LiteralPath $target
            if ($acl.Owner -cnotin @('NT AUTHORITY\SYSTEM','BUILTIN\Administrators')) {
                throw 'Protected PostgreSQL role-secret path has an unapproved owner.'
            }
            foreach ($rule in @($acl.Access)) {
                if ($rule.AccessControlType -ceq 'Allow' -and
                    $rule.IdentityReference.Value -cnotin @(
                        'NT AUTHORITY\SYSTEM','BUILTIN\Administrators'
                    )) {
                    throw 'Protected PostgreSQL role-secret path grants an unapproved identity.'
                }
            }
        }
        $plain = [IO.File]::ReadAllText($path)
        if ([string]::IsNullOrWhiteSpace($plain) -or $plain -match '[\r\n]') {
            throw "Protected password for $RoleName is invalid."
        }
        return $plain
    }
    $secure = Read-Host "Password for $RoleName" -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        if ([string]::IsNullOrWhiteSpace($plain)) {
            throw "Password for $RoleName must not be empty."
        }
        return $plain
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

$roleNames = @(
    'rag_migrator',
    'rag_api',
    'rag_worker',
    'rag_maintenance'
)
$passwords = @{}
$temporarySql = $null
$temporaryDirectory = $null
try {
    $resolvedPsql = [IO.Path]::GetFullPath($PsqlPath)
    $psqlItem = Get-Item -LiteralPath $resolvedPsql
    if (
        $psqlItem.PSIsContainer -or
        ($psqlItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        (Get-FileHash -LiteralPath $resolvedPsql -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            $PsqlSha256
    ) {
        throw 'psql must be an exact pinned regular non-reparse executable.'
    }
    foreach ($target in @($resolvedPsql, $psqlItem.Directory.FullName)) {
        $psqlAcl = Get-Acl -LiteralPath $target
        if ($psqlAcl.Owner -cnotin @(
            'NT AUTHORITY\SYSTEM',
            'BUILTIN\Administrators',
            'NT SERVICE\TrustedInstaller'
        )) {
            throw 'Pinned psql executable path has an unapproved owner.'
        }
        foreach ($rule in @($psqlAcl.Access)) {
            $rights = [long]$rule.FileSystemRights
            if (
                $rule.AccessControlType -ceq 'Allow' -and
                $rule.IdentityReference.Value -cnotin @(
                    'NT AUTHORITY\SYSTEM',
                    'BUILTIN\Administrators',
                    'NT SERVICE\TrustedInstaller'
                ) -and
                ($rights -band $WriteCapableRightsMask) -ne 0
            ) {
                throw 'Pinned psql executable path is writable by an unapproved identity.'
            }
        }
    }
    $temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) (
        'rag-v4-roles-' + [guid]::NewGuid().ToString('N')
    )
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
    & icacls.exe $temporaryDirectory /inheritance:r `
        /grant:r "${currentIdentity}:(OI)(CI)(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to harden the temporary role directory ACL."
    }
    & icacls.exe $temporaryDirectory /verify | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Temporary role directory ACL verification failed."
    }
    $temporarySql = Join-Path $temporaryDirectory 'roles.sql'

    foreach ($roleName in $roleNames) {
        $passwords[$roleName] = Read-RoleSecret -RoleName $roleName
    }
    $distinctSecrets = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($roleName in $roleNames) {
        if (-not $distinctSecrets.Add($passwords[$roleName])) {
            throw 'PostgreSQL role passwords must be pairwise distinct.'
        }
    }

    $roleStatements = foreach ($roleName in $roleNames) {
        $literal = ConvertTo-SqlLiteral -Value $passwords[$roleName]
        "ALTER ROLE $roleName PASSWORD $literal;"
    }
$sql = @"
\set ON_ERROR_STOP on
SET client_min_messages TO warning;
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
        CREATE ROLE rag_migrator LOGIN NOINHERIT NOSUPERUSER NOCREATEDB
            NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_api') THEN
        CREATE ROLE rag_api LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_worker') THEN
        CREATE ROLE rag_worker LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'rag_maintenance'
    ) THEN
        CREATE ROLE rag_maintenance LOGIN NOINHERIT NOSUPERUSER NOCREATEDB
            NOCREATEROLE NOREPLICATION NOBYPASSRLS;
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
    NOREPLICATION BYPASSRLS;
ALTER ROLE rag_backup PASSWORD NULL;
ALTER ROLE rag_backup SET default_transaction_read_only = on;
DO `$memberships`$
DECLARE
    membership record;
BEGIN
    FOR membership IN
        SELECT member.rolname AS member_name,
               granted.rolname AS granted_name
        FROM pg_auth_members AS edge
        JOIN pg_roles AS member ON member.oid = edge.member
        JOIN pg_roles AS granted ON granted.oid = edge.roleid
        WHERE (
            member.rolname = ANY (
                ARRAY[
                    'rag_owner', 'rag_migrator', 'rag_api', 'rag_worker',
                    'rag_maintenance', 'rag_backup'
                ]
            )
            OR granted.rolname = ANY (
                ARRAY[
                    'rag_owner', 'rag_migrator', 'rag_api', 'rag_worker',
                    'rag_maintenance', 'rag_backup'
                ]
            )
        )
          AND NOT (
              member.rolname = 'rag_migrator'
              AND granted.rolname = 'rag_owner'
          )
    LOOP
        EXECUTE format(
            'REVOKE %I FROM %I',
            membership.granted_name,
            membership.member_name
        );
    END LOOP;
END
`$memberships`$;
GRANT rag_owner TO rag_migrator;
SELECT CASE
    WHEN to_regclass('public.alembic_version') IS NULL THEN 'true'
    ELSE 'false'
END AS bootstrap_function_acl
\gset
\if :bootstrap_function_acl
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, rag_owner, rag_migrator,
    rag_api, rag_worker, rag_maintenance, rag_backup;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO rag_owner;
GRANT EXECUTE ON FUNCTION public.cosine_distance(vector, vector) TO rag_api;
DO `$extension_acl`$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_proc AS routine
        JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname = 'public'
          AND has_function_privilege('public', routine.oid, 'EXECUTE')
    ) OR EXISTS (
        SELECT 1
        FROM pg_proc AS routine
        JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname = 'public'
          AND NOT has_function_privilege(
              'rag_owner', routine.oid, 'EXECUTE'
          )
    ) OR NOT has_function_privilege(
        'rag_api', 'public.cosine_distance(vector,vector)', 'EXECUTE'
    ) OR EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'rag_migrator', 'rag_worker',
            'rag_maintenance', 'rag_backup'
        ]) AS runtime(role_name)
        CROSS JOIN pg_proc AS routine
        JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname = 'public'
          AND has_function_privilege(runtime.role_name, routine.oid, 'EXECUTE')
    ) OR EXISTS (
        SELECT 1
        FROM pg_proc AS routine
        JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname = 'public'
          AND has_function_privilege('rag_api', routine.oid, 'EXECUTE')
          AND routine.oid <>
              'public.cosine_distance(vector,vector)'::regprocedure
    ) THEN
        RAISE EXCEPTION 'extension function ACL hardening failed';
    END IF;
END
`$extension_acl`$;
\endif
$($roleStatements -join "`r`n")
GRANT CONNECT ON DATABASE $DatabaseName TO rag_migrator, rag_api, rag_worker,
    rag_maintenance, rag_backup;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO rag_owner;
GRANT USAGE ON SCHEMA public TO rag_migrator, rag_api, rag_worker,
    rag_maintenance, rag_backup;
COMMIT;
"@
    [IO.File]::WriteAllText(
        $temporarySql,
        $sql,
        [Text.UTF8Encoding]::new($false)
    )

    & $resolvedPsql -X -h $DatabaseHost -p $DatabasePort -U $ClusterAdministrator `
        -d $DatabaseName -f $temporarySql
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL role provisioning failed with exit code $LASTEXITCODE."
    }
}
finally {
    foreach ($roleName in @($passwords.Keys)) {
        $passwords[$roleName] = $null
    }
    if ($null -ne $temporarySql -and (Test-Path -LiteralPath $temporarySql)) {
        Remove-Item -LiteralPath $temporarySql -Force
    }
    if (
        $null -ne $temporaryDirectory -and
        (Test-Path -LiteralPath $temporaryDirectory)
    ) {
        Remove-Item -LiteralPath $temporaryDirectory -Force
    }
}
