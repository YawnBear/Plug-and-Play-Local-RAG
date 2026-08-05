[CmdletBinding(DefaultParameterSetName = 'Create')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Create')][string]$SignedUpdateManifest,
    [Parameter(Mandatory, ParameterSetName = 'Create')][string]$UpdateSignature,
    [Parameter(Mandatory, ParameterSetName = 'Create')][string]$ArtifactRoot,
    [Parameter(Mandatory, ParameterSetName = 'Create')][string]$SignedArtifactStageRoot,
    [Parameter(Mandatory, ParameterSetName = 'Reuse')][string]$ExistingSignedStage,
    [Parameter(Mandatory, ParameterSetName = 'Reuse')][string]$ExistingReleaseRoot,
    [Parameter(Mandatory)][string]$OcrFixture,
    [Parameter(Mandatory)][string]$OcrTempRoot,
    [Parameter(Mandatory)][string]$OcrOutput,
    [Parameter(Mandatory)][string]$PinnedDockerProgram,
    [Parameter(Mandatory)][string]$PostgresContainer,
    [Parameter(Mandatory)][string]$PostgresUser,
    [Parameter(Mandatory)][string]$PostgresDatabase,
    [Parameter(Mandatory)][string]$RustfsContainer,
    [Parameter(Mandatory)][uri]$RustfsEndpoint,
    [Parameter(Mandatory)][string]$RustfsApiCredentials,
    [Parameter(Mandatory)][string]$RustfsIngestionCredentials,
    [Parameter(Mandatory)][string]$RustfsDeletionCredentials,
    [Parameter(Mandatory)][string]$RustfsMaintenanceCredentials
)

$ErrorActionPreference = 'Stop'
$dependencyStagePath = $null
$dependencyPayloadRoot = $null
$ownsDependencyStage = $false
$ownsDependencyPayload = $false
try {
. (Join-Path $PSScriptRoot 'RagHostBinding.ps1')
$ReadDataRight = [long]0x1
$WriteCapableRightsMask = [long](
    0x2 -bor 0x4 -bor 0x10 -bor 0x40 -bor 0x100 -bor
    0x10000 -bor 0x40000 -bor 0x80000
)
$checks = [Collections.Generic.List[object]]::new()
$fixedRlsTables = @(
    'access_grants', 'acl_previews', 'audit_events', 'backup_runs',
    'chat_scopes', 'chat_turns', 'chats', 'chunks', 'documents',
    'effective_document_access', 'folder_create_grants', 'ingestion_jobs', 'library_nodes',
    'login_throttles', 'object_deletions', 'pre_auth_challenges',
    'security_epochs', 'service_leases', 'sessions', 'team_members',
    'teams', 'turn_citations', 'turn_sources', 'upload_reservations', 'users'
) | Sort-Object
$fixedQualifiedRlsTables = @($fixedRlsTables | ForEach-Object { "public.$_" })

function Add-Check {
    param([string]$Dependency, [bool]$Passed, [string]$Detail)
    $script:checks.Add([pscustomobject]@{
        dependency = $Dependency
        result = if ($Passed) { 'pass' } else { 'fail' }
        detail = $Detail
    })
}

function Assert-SecretAcl {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$ExpectedIdentities,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$ParentIdentities
    )
    $item = Get-Item -LiteralPath $Path
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.Length -gt 16384
    ) {
        throw 'RustFS credential input must be a bounded regular non-reparse file'
    }
    $allowedFile = @(
        'NT AUTHORITY\SYSTEM',
        'BUILTIN\Administrators'
    ) + $ExpectedIdentities
    foreach ($target in @($item.FullName, $item.Directory.FullName)) {
        $isParent = $target -ceq $item.Directory.FullName
        $allowed = if ($isParent) {
            @('NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators') + $ParentIdentities
        } else {
            $allowedFile
        }
        $acl = Get-Acl -LiteralPath $target
        if (
            -not $acl.AreAccessRulesProtected -or
            $null -eq $acl.Access -or
            $acl.Owner -cnotin @('NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators')
        ) {
            throw 'RustFS credential file and parent require protected admin ACLs'
        }
        foreach ($rule in @($acl.Access)) {
            $rights = [long]$rule.FileSystemRights
            if (
                $rule.IsInherited -or
                $rule.AccessControlType -cne 'Allow' -or
                $allowed -cnotcontains $rule.IdentityReference.Value
            ) {
                throw 'RustFS credential path contains an unsafe ACL entry'
            }
            if (
                $rule.IdentityReference.Value -cin $ParentIdentities -and
                ($rights -band $WriteCapableRightsMask) -ne 0
            ) {
                throw 'RustFS service identity must have read-only credential access'
            }
        }
    }
    $acl = Get-Acl -LiteralPath $Path
    foreach ($identity in $ExpectedIdentities) {
        if (
            @($acl.Access | Where-Object {
                $_.IdentityReference.Value -ceq $identity -and
                (([long]$_.FileSystemRights -band $ReadDataRight) -ne 0)
            }).Count -eq 0
        ) {
            throw "RustFS credential file does not grant $identity read access"
        }
    }
}

function Assert-ReadOnlyAssetAcl {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$ExpectedIdentity
    )
    $item = Get-Item -LiteralPath (Resolve-Path -LiteralPath $Path).Path
    if (-not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Signed model asset root must be a regular directory'
    }
    $descendants = [Collections.Generic.List[IO.FileSystemInfo]]::new()
    $pending = [Collections.Generic.Stack[IO.DirectoryInfo]]::new()
    $pending.Push($item)
    while ($pending.Count -gt 0) {
        foreach ($child in $pending.Pop().EnumerateFileSystemInfos()) {
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Signed model asset tree must not contain reparse points'
            }
            $descendants.Add($child)
            if ($child -is [IO.DirectoryInfo]) {
                $pending.Push($child)
            }
        }
    }
    $targets = @($item, $item.Parent) + @($descendants)
    $modelServiceIdentities = @(
        "$env:COMPUTERNAME\RagInferenceSvc",
        "$env:COMPUTERNAME\RagOcrSvc"
    )
    foreach ($targetItem in $targets) {
        $target = $targetItem.FullName
        $isParent = $targetItem.FullName -ceq $item.Parent.FullName
        $allowedIdentities = if ($isParent) {
            @('NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators') +
                $modelServiceIdentities
        } else {
            @(
                'NT AUTHORITY\SYSTEM',
                'BUILTIN\Administrators',
                $ExpectedIdentity
            )
        }
        $acl = Get-Acl -LiteralPath $target
        if (
            (($targetItem.FullName -ceq $item.FullName -or
                $targetItem.FullName -ceq $item.Parent.FullName) -and
                -not $acl.AreAccessRulesProtected) -or
            $acl.Owner -cnotin @('NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators')
        ) {
            throw 'Signed model asset root and parent require protected admin ACLs'
        }
        $serviceCanRead = $false
        foreach ($rule in @($acl.Access)) {
            $rights = [long]$rule.FileSystemRights
            if (
                $rule.AccessControlType -cne 'Allow' -or
                $rule.IdentityReference.Value -cnotin $allowedIdentities
            ) {
                throw 'Signed model asset ACL contains an unsafe entry'
            }
            if (
                $rule.IdentityReference.Value -cin $modelServiceIdentities -and
                ($rights -band $WriteCapableRightsMask) -ne 0
            ) {
                throw 'Model service identity must have read-only asset access'
            }
            if (
                $rule.IdentityReference.Value -ceq $ExpectedIdentity -and
                ($rights -band $ReadDataRight) -ne 0
            ) {
                $serviceCanRead = $true
            }
        }
        if ($targetItem.FullName -cne $item.Parent.FullName -and -not $serviceCanRead) {
            throw 'Model service identity lacks ReadData on an asset tree entry'
        }
    }
}

function Assert-ImmutablePayloadAcl {
    param([Parameter(Mandatory)][string]$Path)
    $resolvedRoot = (Resolve-Path -LiteralPath $Path).Path
    foreach ($target in @(
        Get-Item -LiteralPath $resolvedRoot
        Get-ChildItem -LiteralPath $resolvedRoot -Recurse -Force
    )) {
        if ($target.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Immutable staged payload contains a reparse point'
        }
        $acl = Get-Acl -LiteralPath $target.FullName
        $isRoot = $target.FullName -ceq $resolvedRoot
        if (($isRoot -and -not $acl.AreAccessRulesProtected) -or
            $acl.Owner -cnotin @('NT AUTHORITY\SYSTEM','BUILTIN\Administrators')) {
            throw 'Immutable staged payload owner or ACL protection is invalid'
        }
        foreach ($rule in @($acl.Access)) {
            $rights = [long]$rule.FileSystemRights
            if (($isRoot -and $rule.IsInherited) -or
                $rule.AccessControlType -cne 'Allow' -or
                ($rights -band $WriteCapableRightsMask) -ne 0) {
                throw 'Immutable staged payload contains an unexpected ACL entry'
            }
        }
    }
}

function Remove-OwnedDependencyRoot {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $item = Get-Item -LiteralPath $Path
    if (-not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Owned dependency cleanup target is unsafe: $Path"
    }
    $takeown = Join-Path ([Environment]::SystemDirectory) 'takeown.exe'
    & $takeown /F $Path /R /D Y | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not take ownership of dependency cleanup target: $Path"
    }
    $cleanupSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $cleanupIcacls = Join-Path ([Environment]::SystemDirectory) 'icacls.exe'
    & $cleanupIcacls $Path /grant:r `
        "*$cleanupSid`:(OI)(CI)(F)" "*$cleanupSid`:(F)" /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not grant cleanup access to dependency target: $Path"
    }
    [IO.Directory]::Delete($Path, $true)
    if (Test-Path -LiteralPath $Path) {
        throw "Dependency cleanup target remains: $Path"
    }
}

if ($PSCmdlet.ParameterSetName -ceq 'Reuse') {
    $updateResult = & (Join-Path $PSScriptRoot 'Test-RagUpdate.ps1') `
        -ExistingSignedStage $ExistingSignedStage | ConvertFrom-Json
} else {
    $updateResult = & (Join-Path $PSScriptRoot 'Test-RagUpdate.ps1') `
        -Manifest $SignedUpdateManifest `
        -Signature $UpdateSignature `
        -ArtifactRoot $ArtifactRoot `
        -SignedArtifactStageRoot $SignedArtifactStageRoot `
        -CleanupOnFailure | ConvertFrom-Json
}
if ($updateResult.result -cne 'pass') {
    throw 'Signed update artifact verification did not pass'
}
$stagePath = (Resolve-Path -LiteralPath $updateResult.stage_directory).Path
if ($PSCmdlet.ParameterSetName -ceq 'Create') {
    $dependencyStagePath = $stagePath
    $ownsDependencyStage = $true
}
$releasePath = Join-Path $stagePath 'release-evidence.json'
$verifierPath = Join-Path $stagePath 'verify_dependencies.py'
$signedNames = @($updateResult.artifacts.filename | Sort-Object)
$expectedPreliminaryNames = @(
    'Caddyfile','RagSupervisorService.exe','caddy.exe','csp-header.caddy',
    'deployment.json','local-rag-release.zip','release-evidence.json',
    'verify_dependencies.py'
) | Sort-Object
$expectedFinalNames = @($expectedPreliminaryNames + 'dependency-evidence.json' | Sort-Object)
if (($signedNames -join ',') -cne ($expectedPreliminaryNames -join ',') -and
    ($signedNames -join ',') -cne ($expectedFinalNames -join ',')) {
    throw 'Preliminary signed update does not contain the exact complete release set'
}
$release = Get-Content -Raw -LiteralPath $releasePath | ConvertFrom-Json
$releaseFields = @($release.PSObject.Properties.Name | Sort-Object)
$expectedReleaseFields = @(
    'alembic_revision', 'containers', 'force_rls_tables',
    'max_evidence_age_seconds', 'ocr', 'ollama_models', 'reranker',
    'runtimes', 'rustfs', 'schema_version', 'verifier_sha256'
) | Sort-Object
if (($releaseFields -join ',') -cne ($expectedReleaseFields -join ',')) {
    throw 'Signed release evidence fields are invalid'
}
if ($release.schema_version -ne 1 -or $release.alembic_revision -cne '0006_versioned_claim') {
    throw 'Signed release baseline revision is invalid'
}
if ((@($release.force_rls_tables | Sort-Object) -join ',') -cne ($fixedRlsTables -join ',')) {
    throw 'Signed release fixed RLS table set is incomplete'
}
$rerankerFields = @($release.reranker.PSObject.Properties.Name | Sort-Object)
if (
    ($rerankerFields -join ',') -cne 'device,identity,model_assets_sha256' -or
    $release.reranker.identity -cne 'BAAI/bge-reranker-v2-m3' -or
    $release.reranker.device -cne 'cpu' -or
    $release.reranker.model_assets_sha256 -cnotmatch '^[0-9a-f]{64}$'
) {
    throw 'Signed release reranker asset binding is invalid'
}
$runtimeFields = @($release.runtimes.PSObject.Properties.Name | Sort-Object)
$expectedRuntimeFields = @(
    'api_python_sha256', 'api_python_tree_sha256', 'docker_executable_sha256',
    'node_tree_sha256', 'ocr_python_sha256', 'ocr_python_tree_sha256',
    'openssl_tree_sha256'
) | Sort-Object
if (($runtimeFields -join ',') -cne ($expectedRuntimeFields -join ',') -or
    @(
        $expectedRuntimeFields |
            Where-Object { $release.runtimes.$_ -cnotmatch '^[0-9a-f]{64}$' }
    ).Count -ne 0) {
    throw 'Signed release runtime-tree binding is invalid'
}
$verifierHash = (Get-FileHash -LiteralPath $verifierPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($release.verifier_sha256 -cne $verifierHash) {
    throw 'Dependency verifier does not match the signed release pin'
}
# Dependency verification is an attended privileged preflight. Service accounts do
# not exist yet, so its live-probe credentials must be protected for SYSTEM and
# Administrators only. Runtime credential ACLs are established after exact account
# creation and are separately validated by the installed supervisor contract.
Assert-SecretAcl $RustfsApiCredentials @() @()
Assert-SecretAcl $RustfsIngestionCredentials @() @()
Assert-SecretAcl $RustfsDeletionCredentials @() @()
Assert-SecretAcl $RustfsMaintenanceCredentials @() @()
if ($PSCmdlet.ParameterSetName -ceq 'Reuse') {
    $payloadRoot = (Resolve-Path -LiteralPath $ExistingReleaseRoot).Path
} else {
    $payloadRoot = Join-Path (Split-Path -Parent $stagePath) (
        'payload-' + [Guid]::NewGuid().ToString('N')
    )
    $dependencyPayloadRoot = $payloadRoot
    $ownsDependencyPayload = $true
    . (Join-Path $PSScriptRoot 'Expand-RagVerifiedRelease.ps1')
    Expand-RagVerifiedRelease -Archive (Join-Path $stagePath 'local-rag-release.zip') `
        -Destination $payloadRoot
    icacls.exe $payloadRoot /setowner '*S-1-5-18' /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Immutable payload owner lockdown failed' }
    icacls.exe $payloadRoot /inheritance:r /grant:r `
        '*S-1-5-18:(OI)(CI)(RX)' '*S-1-5-32-544:(OI)(CI)(RX)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Immutable payload DACL lockdown failed' }
}
$ApiPython = Join-Path $payloadRoot 'runtimes\api-python\python.exe'
$OcrPython = Join-Path $payloadRoot 'runtimes\ocr-python\python.exe'
$RerankerModelRoot = Join-Path $payloadRoot 'signed-assets\bge-reranker-v2-m3'
$OcrModelRoot = Join-Path $payloadRoot 'signed-assets\paddleocr-vl-1.6'
Test-RagInstalledReleaseBinding -ReleaseRoot $payloadRoot -ReleaseEvidence $release
Assert-ImmutablePayloadAcl -Path $payloadRoot

$strictErrorActionPreference = $ErrorActionPreference
$proof = $null
$ErrorActionPreference = 'Continue'
$dockerPath = [IO.Path]::GetFullPath($PinnedDockerProgram)
$dockerItem = Get-Item -LiteralPath $dockerPath -ErrorAction SilentlyContinue
if (
    $null -eq $dockerItem -or
    $dockerItem.PSIsContainer -or
    ($dockerItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
) {
    Add-Check 'Docker' $false 'pinned Docker executable is absent or unsafe'
} elseif (
    (Get-FileHash -LiteralPath $dockerPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
    $release.runtimes.docker_executable_sha256
) {
    Add-Check 'Docker' $false 'Docker executable does not match the signed pin'
} else {
    foreach ($container in @(
        [pscustomobject]@{
            name = $PostgresContainer
            expected = $release.containers.postgres_image_digest
            label = 'PostgreSQL'
        },
        [pscustomobject]@{
            name = $RustfsContainer
            expected = $release.containers.rustfs_image_digest
            label = 'RustFS'
        }
    )) {
        if ($container.expected -isnot [string] -or $container.expected -cnotmatch '^sha256:[0-9a-f]{64}$') {
            throw "Signed $($container.label) image digest is invalid"
        }
        $image = @(& $dockerPath inspect --format '{{.Image}}' $container.name 2>$null)
        $imageExit = $LASTEXITCODE
        $health = @(& $dockerPath inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' $container.name 2>$null)
        $healthExit = $LASTEXITCODE
        Add-Check "$($container.label) image" (
            $imageExit -eq 0 -and $image.Count -eq 1 -and $image[0] -ceq $container.expected
        ) 'exact signed content digest'
        Add-Check "$($container.label) health" (
            $healthExit -eq 0 -and $health.Count -eq 1 -and $health[0] -ceq 'healthy'
        ) 'container health must be healthy'
    }

    $revision = @(& $dockerPath exec $PostgresContainer psql -X -A -t -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDatabase -c 'SELECT version_num FROM public.alembic_version' 2>$null)
    $revisionExit = $LASTEXITCODE
    Add-Check 'PostgreSQL Alembic revision' (
        $revisionExit -eq 0 -and
        $revision.Count -eq 1 -and
        $revision[0].Trim() -ceq '0006_versioned_claim'
    ) 'exact signed V5 revision'

    $readiness = @(& $dockerPath exec $PostgresContainer psql -X -A -t -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDatabase -c "SELECT schema_revision || '|' || CASE WHEN vector_extension THEN 'true' ELSE 'false' END || '|' || CASE WHEN bootstrap_required THEN 'true' ELSE 'false' END || '|' || CASE WHEN catalog_integrity THEN 'true' ELSE 'false' END FROM public.v5_readiness()" 2>$null)
    $readinessExit = $LASTEXITCODE
    $readinessFields = if ($readiness.Count -eq 1) {
        @($readiness[0].Trim().Split('|'))
    } else {
        @()
    }
    $bootstrapState = if (
        $readinessFields.Count -eq 4 -and
        $readinessFields[2] -cin @('true', 'false')
    ) {
        $readinessFields[2]
    } else {
        'unavailable'
    }
    Add-Check 'PostgreSQL hardened readiness' (
        $readinessExit -eq 0 -and
        $readinessFields.Count -eq 4 -and
        $readinessFields[0] -ceq '0006_versioned_claim' -and
        $readinessFields[1] -ceq 'true' -and
        $bootstrapState -cne 'unavailable' -and
        $readinessFields[3] -ceq 'true'
    ) "exact revision/vector/catalog integrity; bootstrap_required=$bootstrapState"

    $enabledRls = @(& $dockerPath exec $PostgresContainer psql -X -A -t -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDatabase -c "SELECT n.nspname || '.' || c.relname FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace WHERE c.relkind='r' AND c.relrowsecurity AND n.nspname='public' ORDER BY 1" 2>$null | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $enabledExit = $LASTEXITCODE
    $forcedRls = @(& $dockerPath exec $PostgresContainer psql -X -A -t -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDatabase -c "SELECT n.nspname || '.' || c.relname FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace WHERE c.relkind='r' AND c.relforcerowsecurity AND n.nspname='public' ORDER BY 1" 2>$null | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $forcedExit = $LASTEXITCODE
    Add-Check 'PostgreSQL enabled RLS' (
        $enabledExit -eq 0 -and
        (@($enabledRls | Sort-Object -Unique) -join ',') -ceq ($fixedQualifiedRlsTables -join ',')
    ) 'fixed complete relrowsecurity table set'
    Add-Check 'PostgreSQL forced RLS' (
        $forcedExit -eq 0 -and
        (@($forcedRls | Sort-Object -Unique) -join ',') -ceq ($fixedQualifiedRlsTables -join ',')
    ) 'fixed complete relforcerowsecurity table set'

    $roleProofSql = "WITH expected(name,can_login,bypass) AS (VALUES ('rag_owner',false,true),('rag_migrator',true,false),('rag_api',true,false),('rag_worker',true,false),('rag_maintenance',true,false),('rag_backup',false,true)), actual AS (SELECT rolname,rolcanlogin,rolbypassrls,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication FROM pg_catalog.pg_roles WHERE rolname IN (SELECT name FROM expected)), relevant_memberships AS (SELECT parent.rolname AS parent_name, member.rolname AS member_name FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles parent ON parent.oid=m.roleid JOIN pg_catalog.pg_roles member ON member.oid=m.member WHERE parent.rolname IN (SELECT name FROM expected) OR member.rolname IN (SELECT name FROM expected)) SELECT CASE WHEN (SELECT count(*) FROM actual)=6 AND NOT EXISTS (SELECT 1 FROM expected e LEFT JOIN actual a ON a.rolname=e.name WHERE a.rolname IS NULL OR a.rolcanlogin<>e.can_login OR a.rolbypassrls<>e.bypass OR a.rolinherit OR a.rolsuper OR a.rolcreatedb OR a.rolcreaterole OR a.rolreplication) AND (SELECT count(*) FROM relevant_memberships)=1 AND EXISTS (SELECT 1 FROM relevant_memberships WHERE parent_name='rag_owner' AND member_name='rag_migrator') THEN 'pass' ELSE 'fail' END"
    $roleProof = @(& $dockerPath exec $PostgresContainer psql -X -A -t -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDatabase -c $roleProofSql 2>$null)
    Add-Check 'PostgreSQL role contract' (
        $LASTEXITCODE -eq 0 -and $roleProof.Count -eq 1 -and $roleProof[0].Trim() -ceq 'pass'
    ) 'exact owner/migrator/API/worker/maintenance/backup role attributes'

    $grantProofSql = @'
WITH public_objects AS (
    SELECT relation.relname AS table_name
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND relation.relkind IN ('r', 'v', 'm', 'p', 'f')
),
expected(grantee, table_name, privilege_type, is_grantable) AS (
    SELECT 'rag_api', table_name, 'SELECT', false
    FROM unnest(ARRAY[
        'documents', 'chunks', 'library_nodes', 'chats', 'chat_scopes',
        'v4_current_user', 'v4_visible_library_nodes', 'v4_chat_history',
        'v4_authorized_turn_sources', 'v4_authorized_turn_citations',
        'v4_admin_users', 'v4_admin_teams', 'v4_admin_grants', 'v4_admin_audit'
    ]) AS table_name
    UNION ALL
    SELECT 'rag_backup', table_name, 'SELECT', false FROM public_objects
),
actual(grantee, table_name, privilege_type, is_grantable) AS (
    SELECT
        CASE
            WHEN acl.grantee = 0 THEN 'PUBLIC'
            ELSE COALESCE(grantee.rolname, acl.grantee::text)
        END,
        relation.relname,
        acl.privilege_type,
        acl.is_grantable
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = relation.relnamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(
        COALESCE(
            relation.relacl,
            pg_catalog.acldefault('r', relation.relowner)
        )
    ) AS acl
    LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = acl.grantee
    WHERE namespace.nspname = 'public'
      AND relation.relkind IN ('r', 'v', 'm', 'p', 'f')
      AND acl.grantee <> relation.relowner
)
SELECT CASE WHEN NOT EXISTS (
    (SELECT * FROM actual EXCEPT SELECT * FROM expected)
    UNION ALL
    (SELECT * FROM expected EXCEPT SELECT * FROM actual)
) THEN 'pass' ELSE 'fail' END
'@
    $grantProof = @(& $dockerPath exec $PostgresContainer psql -X -A -t -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDatabase -c $grantProofSql 2>$null)
    Add-Check 'PostgreSQL table grants' (
        $LASTEXITCODE -eq 0 -and $grantProof.Count -eq 1 -and $grantProof[0].Trim() -ceq 'pass'
    ) 'exact schema-qualified runtime and backup table grants'

    $schemaProofSql = "WITH expected(grantee,privilege_type,is_grantable) AS (VALUES ('PUBLIC','USAGE',false),('rag_owner','CREATE',false),('rag_owner','USAGE',false),('rag_migrator','USAGE',false),('rag_api','USAGE',false),('rag_worker','USAGE',false),('rag_maintenance','USAGE',false),('rag_backup','USAGE',false)), target AS (SELECT namespace.nspacl,namespace.nspowner FROM pg_catalog.pg_namespace namespace WHERE namespace.nspname='public' AND namespace.nspowner='pg_database_owner'::regrole), actual AS (SELECT CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE COALESCE(role.rolname,acl.grantee::text) END,acl.privilege_type,acl.is_grantable FROM target namespace CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(namespace.nspacl,pg_catalog.acldefault('n',namespace.nspowner))) acl LEFT JOIN pg_catalog.pg_roles role ON role.oid=acl.grantee WHERE acl.grantee<>'pg_database_owner'::regrole) SELECT CASE WHEN (SELECT count(*) FROM target)=1 AND NOT EXISTS ((SELECT * FROM actual EXCEPT SELECT * FROM expected) UNION ALL (SELECT * FROM expected EXCEPT SELECT * FROM actual)) THEN 'pass' ELSE 'fail' END"
    $schemaProof = @(& $dockerPath exec $PostgresContainer psql -X -A -t -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDatabase -c $schemaProofSql 2>$null)
    Add-Check 'PostgreSQL schema privileges' (
        $LASTEXITCODE -eq 0 -and
        $schemaProof.Count -eq 1 -and
        $schemaProof[0].Trim() -ceq 'pass'
    ) 'PUBLIC/runtime cannot CREATE and exact public schema USAGE remains'

    $sequenceProofSql = "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class sequence JOIN pg_catalog.pg_namespace namespace ON namespace.oid=sequence.relnamespace CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(sequence.relacl,pg_catalog.acldefault('S',sequence.relowner))) acl WHERE namespace.nspname='public' AND sequence.relkind='S' AND acl.grantee<>sequence.relowner) THEN 'pass' ELSE 'fail' END"
    $sequenceProof = @(& $dockerPath exec $PostgresContainer psql -X -A -t -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDatabase -c $sequenceProofSql 2>$null)
    Add-Check 'PostgreSQL sequence privileges' (
        $LASTEXITCODE -eq 0 -and
        $sequenceProof.Count -eq 1 -and
        $sequenceProof[0].Trim() -ceq 'pass'
    ) 'PUBLIC/runtime roles have no public sequence privileges'

    $triggerProofSql = "SELECT CASE WHEN count(*)=1 AND bool_and(trigger.tgname='trg_v4_turn_source_immutability' AND trigger.tgenabled='O' AND trigger.tgtype=27 AND trigger.tgfoid='public.v4_enforce_turn_source_immutability()'::regprocedure) THEN 'pass' ELSE 'fail' END FROM pg_catalog.pg_trigger trigger WHERE trigger.tgrelid='public.turn_sources'::regclass AND NOT trigger.tgisinternal"
    $triggerProof = @(& $dockerPath exec $PostgresContainer psql -X -A -t -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDatabase -c $triggerProofSql 2>$null)
    Add-Check 'PostgreSQL turn-source immutability trigger' (
        $LASTEXITCODE -eq 0 -and
        $triggerProof.Count -eq 1 -and
        $triggerProof[0].Trim() -ceq 'pass'
    ) 'exact enabled BEFORE ROW UPDATE/DELETE trigger and function binding'

    # Compare exact schema/signature/grantee/grant-option ACL tuples.
    $functionProofSql = @'
WITH expected(grantee, signature, is_grantable) AS (
    SELECT 'rag_api', unnest(ARRAY[
        'public.v4_activate_actor(text)',
        'public.v4_current_actor_id()',
        'public.v4_current_actor_is_admin()',
        'public.v4_can_read_document(uuid)',
        'public.v4_can_read_folder(uuid)',
        'public.v4_can_create_children(uuid)',
        'public.v4_can_view_library_node(uuid)',
        'public.v4_schema_revision()',
        'public.v4_readiness()',
        'public.v5_readiness()',
        'public.v5_citation_evidence(uuid,uuid,smallint)',
        'public.v4_runtime_identity(text)',
        'public.v4_auth_lookup(text,text)',
        'public.v4_login_blocked_until(text)',
        'public.v4_session_view(text)',
        'public.v4_refresh_session(text,text,timestampwithtimezone)',
        'public.v4_record_login_failure(text)',
        'public.v4_clear_login_failures(text)',
        'public.v4_issue_login_session(uuid,bigint,text,text,timestampwithtimezone,timestampwithtimezone)',
        'public.v4_logout(text)',
        'public.v4_consume_activation(text,text,text,text,timestampwithtimezone,timestampwithtimezone)',
        'public.v4_password_change_lookup(text)',
        'public.v4_change_password(text,bigint,text,text,text,timestampwithtimezone,timestampwithtimezone)',
        'public.v4_admin_create_user(text,text,text,text,timestampwithtimezone)',
        'public.v4_admin_reset_user(uuid,text,timestampwithtimezone)',
        'public.v4_admin_set_user(uuid,text,text)',
        'public.v4_admin_create_team(text,text)',
        'public.v4_account_active_teams()',
        'public.v4_document_team_recipients(uuid[])',
        'public.v4_admin_access_context(uuid)',
        'public.v4_admin_preview_acl(jsonb)',
        'public.v4_admin_apply_acl(uuid,text)',
        'public.v4_admin_create_folder(uuid,uuid,text,text)',
        'public.v4_create_folder(uuid,uuid,text,text)',
        'public.v4_admin_rename_library_node(uuid,text,text)',
        'public.v4_admin_delete_folder(uuid)',
        'public.v4_admin_upload_preflight(text,text,text,text,text,text,bigint,text,text,text,uuid,uuid[])',
        'public.v4_admin_commit_upload(uuid,uuid,uuid,uuid,text,text,text,text,text,text,bigint,text,text,text,uuid,uuid[])',
        'public.v4_admin_delete_document(uuid,uuid)',
        'public.v4_prepare_document_reingest(uuid)',
        'public.v4_commit_document_reingest(uuid,text,uuid)',
        'public.v4_get_job(uuid)',
        'public.v4_list_chats()',
        'public.v4_create_chat(text,text)',
        'public.v4_rename_chat(uuid,text)',
        'public.v4_delete_chat(uuid)',
        'public.v4_replace_chat_scope(uuid,uuid[])',
        'public.v4_begin_turn(uuid,text,uuid,text)',
        'public.v4_store_turn_sources(uuid,uuid,jsonb)',
        'public.v4_fail_turn(uuid,uuid,text)',
        'public.v4_interrupt_turn(uuid,uuid,uuid)',
        'public.v4_retry_turn(uuid,uuid,uuid)',
        'public.v4_finalize_turn(uuid,uuid,text,boolean,smallint[])',
        'public.v4_mark_turn_access_revoked(uuid,uuid)',
        'public.v4_mark_turn_access_revoked_trusted(uuid,uuid)',
        'public.v4_mark_turn_citation_failed(uuid,uuid,uuid,text)',
        'public.v4_mark_turn_length_limited(uuid,uuid,uuid,text)',
        'public.cosine_distance(vector,vector)'
    ]), false
    UNION ALL
    SELECT 'rag_worker', unnest(ARRAY[
        'public.v4_runtime_identity(text)',
        'public.v4_claim_service_lease(text,text,integer)',
        'public.v4_heartbeat_service_lease(text,text,uuid,bigint,integer)',
        'public.v4_claim_ingestion_job(text,integer)',
        'public.v4_heartbeat_ingestion_job(uuid,uuid,bigint,integer)',
        'public.v4_update_ingestion_progress(uuid,uuid,bigint,text,integer,integer)',
        'public.v4_commit_ingestion_job(uuid,uuid,bigint,integer,jsonb)',
        'public.v4_requeue_ingestion_job(uuid,uuid,bigint,timestampwithtimezone)',
        'public.v4_poison_ingestion_job(uuid,uuid,bigint,text)',
        'public.v4_queue_expired_upload_orphans(integer)',
        'public.v4_claim_object_deletion(text,integer)',
        'public.v4_heartbeat_object_deletion(uuid,uuid,bigint,integer)',
        'public.v4_finish_object_deletion(uuid,uuid,bigint,boolean,text)'
    ]), false
    UNION ALL
    SELECT 'rag_maintenance', unnest(ARRAY[
        'public.v4_runtime_identity(text)',
        'public.v4_rebuild_effective_document_access()',
        'public.v4_maintenance_get_document(uuid)',
        'public.v4_maintenance_list_documents()',
        'public.v4_maintenance_requeue_document(uuid,text,uuid,boolean)',
        'public.v4_maintenance_storage_snapshot()',
        'public.v4_repair_interrupted_turns()',
        'public.v4_queue_expired_upload_orphans(integer)',
        'public.v4_bootstrap_admin(text,text,text)',
        'public.v4_schema_revision()',
        'public.v4_readiness()',
        'public.v5_readiness()',
        'public.v4_claim_service_lease(text,text,integer)',
        'public.v4_heartbeat_service_lease(text,text,uuid,bigint,integer)',
        'public.v4_begin_backup_run(text)',
        'public.v4_finish_backup_run(uuid,boolean,text,text,bigint,bigint,text)'
    ]), false
    UNION ALL SELECT 'rag_migrator', 'public.v4_schema_revision()', false
    UNION ALL SELECT 'rag_backup', 'public.v4_schema_revision()', false
),
actual(grantee, signature, is_grantable) AS (
    SELECT
        CASE
            WHEN acl.grantee = 0 THEN 'PUBLIC'
            ELSE COALESCE(grantee.rolname, acl.grantee::text)
        END,
        regexp_replace(
            format(
                '%I.%I(%s)',
                namespace.nspname,
                procedure.proname,
                COALESCE(
                    (
                        SELECT string_agg(
                            pg_catalog.format_type(argument.type_oid, NULL),
                            ','
                            ORDER BY argument.ordinality
                        )
                        FROM unnest(procedure.proargtypes::oid[])
                            WITH ORDINALITY AS argument(type_oid, ordinality)
                    ),
                    ''
                )
            ),
            '\s+',
            '',
            'g'
        ),
        acl.is_grantable
    FROM pg_catalog.pg_proc AS procedure
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = procedure.pronamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(
        COALESCE(
            procedure.proacl,
            pg_catalog.acldefault('f', procedure.proowner)
        )
    ) AS acl
    LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = acl.grantee
    WHERE namespace.nspname = 'public'
      AND acl.privilege_type = 'EXECUTE'
      AND acl.grantee <> procedure.proowner
      AND COALESCE(grantee.rolname, '') <> 'rag_owner'
)
SELECT CASE WHEN NOT EXISTS (
    (SELECT * FROM actual EXCEPT SELECT * FROM expected)
    UNION ALL
    (SELECT * FROM expected EXCEPT SELECT * FROM actual)
) THEN 'pass' ELSE 'fail' END
'@
    $functionProof = @(& $dockerPath exec $PostgresContainer psql -X -A -t -v ON_ERROR_STOP=1 -U $PostgresUser -d $PostgresDatabase -c $functionProofSql 2>$null)
    Add-Check 'PostgreSQL function grants' (
        $LASTEXITCODE -eq 0 -and
        $functionProof.Count -eq 1 -and
        $functionProof[0].Trim() -ceq 'pass'
    ) 'exact controlled runtime functions and no PUBLIC extension/function EXECUTE'
}

if (-not (Test-Path -LiteralPath $ApiPython -PathType Leaf)) {
    Add-Check 'Pinned dependency verifier' $false 'API Python executable is absent'
} elseif (-not (Test-Path -LiteralPath $OcrPython -PathType Leaf)) {
    Add-Check 'Pinned dependency verifier' $false 'OCR Python executable is absent'
} elseif (-not (Test-Path -LiteralPath $OcrFixture -PathType Leaf)) {
    Add-Check 'Pinned dependency verifier' $false 'fixed OCR fixture is absent'
} elseif (-not (Test-Path -LiteralPath $OcrTempRoot -PathType Container)) {
    Add-Check 'Pinned dependency verifier' $false 'OCR temp root is absent'
} elseif (Test-Path -LiteralPath $OcrOutput) {
    Add-Check 'Pinned dependency verifier' $false 'OCR output must not already exist'
} else {
    $parsedRustfsAddress = [Net.IPAddress]::Parse($RustfsEndpoint.DnsSafeHost)
    if (
        -not [Net.IPAddress]::IsLoopback($parsedRustfsAddress) -or
        $RustfsEndpoint.UserInfo -or
        $RustfsEndpoint.Query -or
        $RustfsEndpoint.Fragment -or
        $RustfsEndpoint.AbsolutePath -cne '/'
    ) {
        throw 'RustFS endpoint must be a credential-free loopback origin'
    }
    $apiPythonHash = (Get-FileHash -LiteralPath $ApiPython -Algorithm SHA256).Hash.ToLowerInvariant()
    $ocrPythonHash = (Get-FileHash -LiteralPath $OcrPython -Algorithm SHA256).Hash.ToLowerInvariant()
    if (
        $apiPythonHash -cne $release.runtimes.api_python_sha256 -or
        $ocrPythonHash -cne $release.runtimes.ocr_python_sha256
    ) {
        throw 'Dependency verifier Python runtimes do not match signed release pins'
    }
    foreach ($artifact in $updateResult.artifacts) {
        $stagedArtifact = Join-Path $stagePath $artifact.filename
        $stagedItem = Get-Item -LiteralPath $stagedArtifact
        $stagedHash = (Get-FileHash -LiteralPath $stagedArtifact -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($stagedItem.Length -ne $artifact.size -or $stagedHash -cne $artifact.sha256) {
            throw "Immutable staged artifact changed before execution: $($artifact.filename)"
        }
    }
    $verifierErrorPath = Join-Path $OcrTempRoot 'dependency-verifier.stderr.log'
    Remove-Item -LiteralPath $verifierErrorPath -Force -ErrorAction SilentlyContinue
    $proofText = @(& $ApiPython $verifierPath `
        --release-evidence $releasePath `
        --rustfs-endpoint $RustfsEndpoint.AbsoluteUri `
        --rustfs-api-credentials $RustfsApiCredentials `
        --rustfs-ingestion-credentials $RustfsIngestionCredentials `
        --rustfs-deletion-credentials $RustfsDeletionCredentials `
        --rustfs-maintenance-credentials $RustfsMaintenanceCredentials `
        --ocr-python $OcrPython `
        --ocr-fixture $OcrFixture `
        --ocr-temp-root $OcrTempRoot `
        --ocr-model-root $OcrModelRoot `
        --reranker-model-root $RerankerModelRoot `
        --machine-fingerprint (Get-RagMachineFingerprint) `
        --ocr-output $OcrOutput 2>$verifierErrorPath)
    $proofExit = $LASTEXITCODE
    try {
        if ($proofExit -ne 0 -or $proofText.Count -ne 1) {
            $verifierError = if (Test-Path -LiteralPath $verifierErrorPath) {
                ([IO.File]::ReadAllText(
                    $verifierErrorPath,
                    [Text.Encoding]::UTF8
                )).Trim()
            } else {
                ''
            }
            if ($verifierError.Length -gt 2048) {
                $verifierError = $verifierError.Substring(
                    $verifierError.Length - 2048
                )
            }
            throw "pinned dependency verifier did not return one proof object: $verifierError"
        }
        $proof = $proofText[0] | ConvertFrom-Json
        $captured = [DateTimeOffset]::Parse($proof.ocr.captured_at)
        $age = ([DateTimeOffset]::UtcNow - $captured).TotalSeconds
        Add-Check 'Signed release binding' (
            $proof.release_manifest_sha256 -ceq (Get-FileHash -LiteralPath $releasePath -Algorithm SHA256).Hash.ToLowerInvariant() -and
            $proof.verifier_sha256 -ceq $release.verifier_sha256 -and
            $proof.api_python_sha256 -ceq $release.runtimes.api_python_sha256 -and
            $proof.ocr_python_sha256 -ceq $release.runtimes.ocr_python_sha256 -and
            $proof.machine_fingerprint -ceq (Get-RagMachineFingerprint)
        ) 'proof is generated by the signed verifier and release evidence'
        Add-Check 'RustFS authenticated object/inventory' (
            $proof.rustfs.authenticated_object_matches -ceq $true -and
            $proof.rustfs.inventory_exact -ceq $true
        ) 'actual signed probe object GET/hash and exact prefix inventory'
        Add-Check 'RustFS anonymous privacy' (
            $proof.rustfs.anonymous_object_get_denied -ceq $true -and
            $proof.rustfs.anonymous_list_denied -ceq $true -and
            $proof.rustfs.anonymous_policy_denied -ceq $true
        ) 'actual anonymous object GET, list, and policy requests return 403'
        Add-Check 'RustFS scoped IAM' (
            $proof.rustfs_scoped_iam.credentials_distinct -ceq $true -and
            $proof.rustfs_scoped_iam.api_get -ceq $true -and
            $proof.rustfs_scoped_iam.api_list -ceq $true -and
            $proof.rustfs_scoped_iam.api_put -ceq $true -and
            $proof.rustfs_scoped_iam.api_delete_denied -ceq $true -and
            $proof.rustfs_scoped_iam.ingestion_get -ceq $true -and
            $proof.rustfs_scoped_iam.ingestion_head -ceq $true -and
            $proof.rustfs_scoped_iam.ingestion_put_denied -ceq $true -and
            $proof.rustfs_scoped_iam.ingestion_list_denied -ceq $true -and
            $proof.rustfs_scoped_iam.ingestion_delete_denied -ceq $true -and
            $proof.rustfs_scoped_iam.deletion_get_denied -ceq $true -and
            $proof.rustfs_scoped_iam.deletion_put_denied -ceq $true -and
            $proof.rustfs_scoped_iam.deletion_list_denied -ceq $true -and
            $proof.rustfs_scoped_iam.deletion_delete -ceq $true -and
            $proof.rustfs_scoped_iam.maintenance_put_list_delete -ceq $true -and
            $proof.rustfs_scoped_iam.root_credentials_used -ceq $false
        ) 'separate API/ingestion/deletion/maintenance keys have exact capabilities'
        Add-Check 'Ollama exact models and embeddings' (
            $proof.ollama.models_match -ceq $true -and
            $proof.ollama.embedding_dimension -eq 1024
        ) 'signed exact qwen digests and live 1024-dimensional embedding'
        Add-Check 'BGE reranker' (
            $proof.reranker.identity -ceq 'BAAI/bge-reranker-v2-m3' -and
            $proof.reranker.device -ceq 'cpu' -and
            $proof.reranker.model_assets_sha256 -ceq
                $release.reranker.model_assets_sha256 -and
            $proof.reranker.smoke_completed -ceq $true
        ) 'signed local model assets and CPU relevance-ordering smoke'
        Add-Check 'PaddleOCR-VL' (
            $proof.ocr.paddleocr_version -ceq $release.ocr.paddleocr_version -and
            $proof.ocr.pipeline_version -ceq '1.6' -and
            $proof.ocr.device -ceq 'cpu' -and
            $proof.ocr.fixture_sha256 -ceq $release.ocr.fixture_sha256 -and
            $proof.ocr.output_sha256 -cmatch '^[0-9a-f]{64}$' -and
            $proof.ocr.output_sha256 -ceq $release.ocr.expected_output_sha256 -and
            $proof.ocr.structured_sha256 -ceq $release.ocr.expected_structured_sha256 -and
            $proof.ocr.text_sha256 -ceq $release.ocr.expected_text_sha256 -and
            $proof.ocr.page_count -eq $release.ocr.expected_page_count -and
            $proof.ocr.model_assets_sha256 -ceq $release.ocr.model_assets_sha256 -and
            $proof.ocr.smoke_completed -ceq $true -and
            $age -ge -5 -and
            $age -le $release.max_evidence_age_seconds
        ) 'fresh pinned-verifier fixed-fixture CPU v1.6 output proof'
    } catch {
        Add-Check 'Pinned dependency verifier' $false $_.Exception.Message
    } finally {
        Remove-Item -LiteralPath $verifierErrorPath `
            -Force -ErrorAction SilentlyContinue
    }
}

$ErrorActionPreference = $strictErrorActionPreference
$resultObject = [pscustomobject]@{
    schema_version = 1
    mode = 'verification'
    result = if (@($checks | Where-Object { $_.result -ne 'pass' }).Count -eq 0) {
        'pass'
    } else {
        'fail'
    }
    checks = $checks
    release_evidence_sha256 = (
        Get-FileHash -LiteralPath $releasePath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    verifier_sha256 = (
        Get-FileHash -LiteralPath $verifierPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    preliminary_manifest_sha256 = (
        Get-FileHash -LiteralPath (Join-Path $stagePath 'update-manifest.json') `
            -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    machine_fingerprint = if ($null -ne $proof) { $proof.machine_fingerprint } else { $null }
    captured_at = if ($null -ne $proof) { $proof.ocr.captured_at } else { $null }
    changes_applied = (Test-Path -LiteralPath $OcrOutput)
    mutation_scope = 'new operator-selected OCR evidence directory only'
}
$resultObject | ConvertTo-Json -Depth 7
if ($resultObject.result -cne 'pass') {
    $failedChecks = @(
        $checks |
            Where-Object result -cne 'pass' |
            ForEach-Object { "$($_.dependency): $($_.detail)" }
    )
    throw "Dependency verification failed: $($failedChecks -join '; ')"
}
} finally {
    $cleanupFailures = [Collections.Generic.List[string]]::new()
    foreach ($ownedRoot in @(
        [pscustomobject]@{ Path = $dependencyPayloadRoot; Owned = $ownsDependencyPayload },
        [pscustomobject]@{ Path = $dependencyStagePath; Owned = $ownsDependencyStage }
    )) {
        if ($ownedRoot.Owned -and $null -ne $ownedRoot.Path) {
            try {
                Remove-OwnedDependencyRoot -Path $ownedRoot.Path
            } catch {
                $cleanupFailures.Add("$($ownedRoot.Path): $($_.Exception.Message)")
            }
        }
    }
    if ($cleanupFailures.Count -ne 0) {
        throw "Dependency temporary-root cleanup failed: $($cleanupFailures -join '; ')"
    }
}
