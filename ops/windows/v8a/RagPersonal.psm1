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

function Assert-RagPersonalProtectedPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [switch]$Directory
    )
    $resolved = Assert-RagPersonalPathSafe -Path $Path
    $item = Get-Item -LiteralPath $resolved -Force
    if ([bool]$item.PSIsContainer -ne [bool]$Directory) {
        throw 'A protected Personal path has the wrong type.'
    }
    $acl = Get-Acl -LiteralPath $resolved
    if (-not $acl.AreAccessRulesProtected) {
        throw 'A protected Personal path still inherits access rules.'
    }
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $expectedSids = @($currentSid,'S-1-5-18')
    $rules = @($acl.GetAccessRules(
        $true,$false,[Security.Principal.SecurityIdentifier]
    ))
    if ($rules.Count -ne 2) {
        throw 'A protected Personal path has an unexpected access-rule count.'
    }
    foreach ($expectedSid in $expectedSids) {
        $matching = @($rules | Where-Object {
            $_.IdentityReference.Value -ceq $expectedSid
        })
        if ($matching.Count -ne 1 -or
            $matching[0].AccessControlType -ne
                [Security.AccessControl.AccessControlType]::Allow -or
            $matching[0].FileSystemRights -ne
                [Security.AccessControl.FileSystemRights]::FullControl) {
            throw 'A protected Personal path ACL is not limited to owner and SYSTEM.'
        }
    }
}

function Assert-RagPersonalSecretDocument {
    param(
        [Parameter(Mandatory)][pscustomobject]$Document,
        [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{32}$')]
        [string]$InstallationId
    )
    $expectedDocumentFields = @('installation_id','schema_version','values') | Sort-Object
    if ((@($Document.PSObject.Properties.Name | Sort-Object) -join ',') -cne
        ($expectedDocumentFields -join ',') -or $Document.schema_version -ne 1 -or
        [string]$Document.installation_id -cne $InstallationId) {
        throw 'Personal secret document identity/schema is invalid.'
    }
    $expectedValueFields = @(
        'postgres_cluster_admin','postgres_migrator','postgres_api','postgres_worker',
        'postgres_maintenance','rustfs_root_access','rustfs_root_secret',
        'rustfs_api_access','rustfs_api_secret','rustfs_ingestion_access',
        'rustfs_ingestion_secret','rustfs_deletion_access','rustfs_deletion_secret',
        'rustfs_maintenance_access','rustfs_maintenance_secret','csrf_signing_secret',
        'coordinator_service_token','controller_service_token','ocr_service_token'
    ) | Sort-Object
    if ((@($Document.values.PSObject.Properties.Name | Sort-Object) -join ',') -cne
        ($expectedValueFields -join ',')) {
        throw 'Personal secret document fields are invalid.'
    }
    foreach ($property in $Document.values.PSObject.Properties) {
        $value = [string]$property.Value
        if ([string]::IsNullOrWhiteSpace($value) -or $value.Length -gt 256 -or
            $value -cnotmatch '^[A-Za-z0-9_-]+$') {
            throw 'Personal secret document contains an invalid value.'
        }
    }
}

function Get-RagPersonalReinstallCapsulePath {
    param([Parameter(Mandatory)][string]$DataRoot)
    return Join-Path (Assert-RagPersonalPathSafe -Path $DataRoot) `
        '.localrag-personal-reinstall.dpapi'
}

function Test-RagPersonalReinstallCapsuleRequired {
    param(
        [Parameter(Mandatory)][ValidateSet('Preserve','Export','Delete')]
        [string]$DataAction
    )
    return $DataAction -cin @('Preserve','Export')
}

function Get-RagPersonalReinstallFileSet {
    param([switch]$DevelopmentSource)
    $files = @(
        'config\compose.personal.yaml',
        'config\personal-release.json',
        'secrets\installation-secrets.json',
        'state\installation-journal.json'
    )
    if (-not $DevelopmentSource) { $files += 'state\release-state.json' }
    return @($files | Sort-Object)
}

function Get-RagPersonalDpapiEntropy {
    return [Text.UTF8Encoding]::new($false).GetBytes(
        'LocalRAG.Personal.ReinstallCapsule.v1'
    )
}

function Get-RagPersonalBytesSha256 {
    param([Parameter(Mandatory)][byte[]]$Bytes)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Test-RagPersonalBytesEqual {
    param(
        [Parameter(Mandatory)][byte[]]$Left,
        [Parameter(Mandatory)][byte[]]$Right
    )
    if ($Left.Length -ne $Right.Length) { return $false }
    [byte]$difference = 0
    for ($index=0; $index -lt $Left.Length; $index++) {
        $difference = $difference -bor ($Left[$index] -bxor $Right[$index])
    }
    return $difference -eq 0
}

function ConvertFrom-RagPersonalPayloadJson {
    param([Parameter(Mandatory)]$Entry)
    $bytes = [Convert]::FromBase64String([string]$Entry.content_base64)
    try {
        if ($bytes.Length -ne [int64]$Entry.size -or
            (Get-RagPersonalBytesSha256 -Bytes $bytes) -cne [string]$Entry.sha256) {
            throw 'The Personal reinstall capsule payload was changed.'
        }
        return [Text.UTF8Encoding]::new($false,$true).GetString($bytes) |
            ConvertFrom-Json
    }
    finally { [Array]::Clear($bytes,0,$bytes.Length) }
}

function Assert-RagPersonalDataRootCapsuleAllowlist {
    param(
        [Parameter(Mandatory)][string]$DataRoot,
        [switch]$DevelopmentSource
    )
    $data = Assert-RagPersonalPathSafe -Path $DataRoot
    $allowedDirectories = @(
        'application','backup-work','ocr-work','postgres','restore-verification','rustfs'
    )
    if ($DevelopmentSource) { $allowedDirectories += @('cache','models') }
    $allowedFiles = @('backup-catalog.json','.localrag-personal-reinstall.dpapi')
    foreach ($item in @(Get-ChildItem -LiteralPath $data -Force)) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'The preserved Personal data root contains a reparse point.'
        }
        if ($item.PSIsContainer) {
            if ($item.Name -cnotin $allowedDirectories) {
                throw 'The preserved Personal data root contains an unknown directory.'
            }
        }
        elseif ($item.Name -cnotin $allowedFiles) {
            throw 'The preserved Personal data root contains an unknown file.'
        }
    }
}

function Assert-RagPersonalReinstallReleaseIdentity {
    param(
        [Parameter(Mandatory)][string]$ReleaseRoot,
        [Parameter(Mandatory)]$Identity,
        [switch]$DevelopmentSource
    )
    $release = Assert-RagPersonalPathSafe -Path $ReleaseRoot
    $expectedFields = @(
        'archive_sha256','compose_sha256','development_source',
        'personal_release_sha256','release_root','release_state_sha256'
    ) | Sort-Object
    if ((@($Identity.PSObject.Properties.Name | Sort-Object) -join ',') -cne
        ($expectedFields -join ',') -or
        [bool]$Identity.development_source -ne [bool]$DevelopmentSource -or
        [string]$Identity.release_root -cne $release) {
        throw 'The reinstall capsule release identity is invalid.'
    }
    $releaseContract = Join-Path $release 'ops\windows\v8a\personal-release.json'
    $composeContract = Join-Path $release 'ops\windows\v8a\compose.personal.yaml'
    if ((Get-FileHash -LiteralPath $releaseContract -Algorithm SHA256).Hash.ToLowerInvariant() `
            -cne [string]$Identity.personal_release_sha256 -or
        (Get-FileHash -LiteralPath $composeContract -Algorithm SHA256).Hash.ToLowerInvariant() `
            -cne [string]$Identity.compose_sha256) {
        throw 'The reinstall capsule does not match this release contract.'
    }
    if ($DevelopmentSource) {
        if ($null -ne $Identity.archive_sha256 -or $null -ne $Identity.release_state_sha256) {
            throw 'A source reinstall capsule contains packaged release identity.'
        }
        return
    }
    $marker = Join-Path $release '.verified-archive-sha256'
    $trustPath = Join-Path $release 'release-trust-metadata.json'
    $markerItem = Get-Item -LiteralPath $marker -Force -ErrorAction Stop
    if ([string]$Identity.archive_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $markerItem.PSIsContainer -or
        ($markerItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $markerItem.Length -gt 128 -or
        [IO.File]::ReadAllText($marker).Trim() -cne [string]$Identity.archive_sha256 -or
        [IO.Path]::GetFileName($release) -cne [string]$Identity.archive_sha256 -or
        [string]$Identity.release_state_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        -not (Test-Path -LiteralPath $trustPath -PathType Leaf)) {
        throw 'The packaged reinstall capsule archive identity is invalid.'
    }
}

function Read-RagPersonalReinstallCapsule {
    param(
        [Parameter(Mandatory)][string]$DataRoot,
        [Parameter(Mandatory)][string]$InstallRoot,
        [Parameter(Mandatory)][string]$ReleaseRoot,
        [switch]$DevelopmentSource
    )
    $data = Assert-RagPersonalPathSafe -Path $DataRoot
    $install = Assert-RagPersonalPathSafe -Path $InstallRoot -AllowMissing
    $capsule = Get-RagPersonalReinstallCapsulePath -DataRoot $data
    Assert-RagPersonalProtectedPath -Path $data -Directory
    Assert-RagPersonalDataRootCapsuleAllowlist -DataRoot $data `
        -DevelopmentSource:$DevelopmentSource
    $capsuleItem = Get-Item -LiteralPath $capsule -Force -ErrorAction Stop
    if ($capsuleItem.PSIsContainer -or
        ($capsuleItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $capsuleItem.Length -lt 32 -or $capsuleItem.Length -gt 6MB) {
        throw 'The Personal reinstall capsule is not a bounded regular file.'
    }
    Assert-RagPersonalProtectedPath -Path $capsule
    Add-Type -AssemblyName System.Security -ErrorAction Stop
    $ciphertext = [IO.File]::ReadAllBytes($capsule)
    $entropy = Get-RagPersonalDpapiEntropy
    $plaintext = $null
    try {
        $plaintext = [Security.Cryptography.ProtectedData]::Unprotect(
            $ciphertext,$entropy,
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        if ($plaintext.Length -lt 2 -or $plaintext.Length -gt 4MB) {
            throw 'The decrypted Personal reinstall capsule is outside its size limit.'
        }
        $document = [Text.UTF8Encoding]::new($false,$true).GetString($plaintext) |
            ConvertFrom-Json
    }
    finally {
        [Array]::Clear($ciphertext,0,$ciphertext.Length)
        [Array]::Clear($entropy,0,$entropy.Length)
        if ($null -ne $plaintext) { [Array]::Clear($plaintext,0,$plaintext.Length) }
    }
    $expectedFields = @(
        'created_at_utc','data_action','data_root','installation_id','install_root',
        'payloads','profile_id','release_identity','schema_version','windows_sid'
    ) | Sort-Object
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    [DateTimeOffset]$created = [DateTimeOffset]::MinValue
    if ((@($document.PSObject.Properties.Name | Sort-Object) -join ',') -cne
        ($expectedFields -join ',') -or $document.schema_version -ne 1 -or
        [string]$document.profile_id -cne 'personal' -or
        [string]$document.installation_id -cnotmatch '^[0-9a-f]{32}$' -or
        [string]$document.install_root -cne $install -or
        [string]$document.data_root -cne $data -or
        [string]$document.data_action -cnotin @('preserve','export') -or
        [string]$document.windows_sid -cne $currentSid -or
        -not [DateTimeOffset]::TryParseExact([string]$document.created_at_utc,'o',
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind,[ref]$created) -or
        $created.Offset -ne [TimeSpan]::Zero -or $created -gt [DateTimeOffset]::UtcNow) {
        throw 'The Personal reinstall capsule schema/identity is invalid.'
    }
    Assert-RagPersonalReinstallReleaseIdentity -ReleaseRoot $ReleaseRoot `
        -Identity $document.release_identity -DevelopmentSource:$DevelopmentSource
    $expectedFiles = Get-RagPersonalReinstallFileSet -DevelopmentSource:$DevelopmentSource
    $entries = @($document.payloads)
    $entryNames = @($entries | ForEach-Object { [string]$_.path } | Sort-Object)
    if ($entries.Count -ne $expectedFiles.Count -or
        ($entryNames -join ',') -cne ($expectedFiles -join ',')) {
        throw 'The Personal reinstall capsule file set is invalid.'
    }
    $byPath = @{}
    foreach ($entry in $entries) { $byPath[[string]$entry.path] = $entry }
    if ([string]$byPath['config\personal-release.json'].sha256 -cne
            [string]$document.release_identity.personal_release_sha256 -or
        [string]$byPath['config\compose.personal.yaml'].sha256 -cne
            [string]$document.release_identity.compose_sha256) {
        throw 'The Personal reinstall capsule contracts do not match its release identity.'
    }
    [int64]$total = 0
    foreach ($entry in $entries) {
        if ((@($entry.PSObject.Properties.Name | Sort-Object) -join ',') -cne
            'content_base64,path,sha256,size' -or
            [string]$entry.content_base64 -cnotmatch '^[A-Za-z0-9+/]+={0,2}$' -or
            [string]$entry.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $entry.size -isnot [long] -and $entry.size -isnot [int] -or
            [int64]$entry.size -lt 1 -or [int64]$entry.size -gt 1MB) {
            throw 'The Personal reinstall capsule file entry is invalid.'
        }
        $content = [Convert]::FromBase64String([string]$entry.content_base64)
        if ($content.Length -ne [int64]$entry.size -or
            (Get-RagPersonalBytesSha256 -Bytes $content) -cne [string]$entry.sha256) {
            [Array]::Clear($content,0,$content.Length)
            throw 'The Personal reinstall capsule payload was changed.'
        }
        $total += $content.Length
        [Array]::Clear($content,0,$content.Length)
    }
    if ($total -gt 4MB) { throw 'The Personal reinstall capsule payload is too large.' }
    $journal = ConvertFrom-RagPersonalPayloadJson `
        -Entry $byPath['state\installation-journal.json']
    Assert-RagPersonalJournal -Journal $journal
    if ([string]$journal.installation_id -cne [string]$document.installation_id -or
        [string]$journal.install_root -cne $install -or
        [string]$journal.data_root -cne $data -or
        [string]$journal.release_root -cne [string]$document.release_identity.release_root) {
        throw 'The Personal reinstall capsule journal binding is invalid.'
    }
    $secrets = ConvertFrom-RagPersonalPayloadJson `
        -Entry $byPath['secrets\installation-secrets.json']
    Assert-RagPersonalSecretDocument -Document $secrets `
        -InstallationId ([string]$journal.installation_id)
    if (-not $DevelopmentSource) {
        $releaseStateEntry = $byPath['state\release-state.json']
        if ([string]$releaseStateEntry.sha256 -cne
            [string]$document.release_identity.release_state_sha256) {
            throw 'The packaged reinstall capsule release state was changed.'
        }
        $releaseState = ConvertFrom-RagPersonalPayloadJson -Entry $releaseStateEntry
        if ((@($releaseState.PSObject.Properties.Name | Sort-Object) -join ',') -cne
            'release_id,release_sequence,schema_version,trust_metadata_sha256' -or
            $releaseState.schema_version -ne 1 -or
            [string]$releaseState.release_id -cnotmatch '^[a-z0-9][a-z0-9._-]{5,127}$' -or
            $releaseState.release_sequence -isnot [int] -or
            [string]$releaseState.trust_metadata_sha256 -cnotmatch '^[0-9a-f]{64}$') {
            throw 'The packaged reinstall capsule release state is invalid.'
        }
        $trustPath = Join-Path $ReleaseRoot 'release-trust-metadata.json'
        $trust = Read-RagPersonalJson -Path $trustPath
        if ((Get-FileHash -LiteralPath $trustPath -Algorithm SHA256).Hash.ToLowerInvariant() `
                -cne [string]$releaseState.trust_metadata_sha256 -or
            [string]$trust.release_id -cne [string]$releaseState.release_id -or
            [int]$trust.release_sequence -ne [int]$releaseState.release_sequence) {
            throw 'The packaged reinstall capsule trust identity does not match the release.'
        }
    }
    return [pscustomobject]@{ path=$capsule; document=$document; journal=$journal }
}

function New-RagPersonalReinstallCapsule {
    param(
        [Parameter(Mandatory)][ValidateSet('Preserve','Export')][string]$DataAction,
        [Parameter(Mandatory)][string]$InstallRoot,
        [Parameter(Mandatory)][string]$DataRoot,
        [Parameter(Mandatory)][string]$ReleaseRoot,
        [switch]$DevelopmentSource
    )
    $install = Assert-RagPersonalPathSafe -Path $InstallRoot
    $data = Assert-RagPersonalPathSafe -Path $DataRoot
    $release = Assert-RagPersonalPathSafe -Path $ReleaseRoot
    $capsule = Get-RagPersonalReinstallCapsulePath -DataRoot $data
    if (Test-Path -LiteralPath $capsule) {
        $existing = Read-RagPersonalReinstallCapsule -DataRoot $data `
            -InstallRoot $install -ReleaseRoot $release `
            -DevelopmentSource:$DevelopmentSource
        if ([string]$existing.document.data_action -cne $DataAction.ToLowerInvariant()) {
            throw 'An existing reinstall capsule was created for another uninstall action.'
        }
        return $existing
    }
    $journal = Read-RagPersonalJson -Path (Join-Path $install `
        'state\installation-journal.json')
    Assert-RagPersonalJournal -Journal $journal
    if ([string]$journal.install_root -cne $install -or
        [string]$journal.data_root -cne $data -or
        [string]$journal.release_root -cne $release) {
        throw 'The Personal installation journal does not match the reinstall capsule roots.'
    }
    Assert-RagPersonalProtectedPath -Path $data -Directory
    Assert-RagPersonalDataRootCapsuleAllowlist -DataRoot $data `
        -DevelopmentSource:$DevelopmentSource
    $sourceFiles = Get-RagPersonalReinstallFileSet -DevelopmentSource:$DevelopmentSource
    $temporary = Join-Path $data ('.localrag-personal-reinstall.' +
        [guid]::NewGuid().ToString('N') + '.tmp')
    $plaintext = $null
    $ciphertext = $null
    $entropy = $null
    try {
        $entries = @()
        foreach ($relative in $sourceFiles) {
            $source = [IO.Path]::GetFullPath((Join-Path $install $relative))
            if (-not $source.StartsWith($install + '\',[StringComparison]::OrdinalIgnoreCase)) {
                throw 'The Personal reinstall capsule source escapes the install root.'
            }
            $sourceItem = Get-Item -LiteralPath $source -Force -ErrorAction Stop
            if ($sourceItem.PSIsContainer -or
                ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                $sourceItem.Length -lt 1 -or $sourceItem.Length -gt 1MB) {
                throw 'A Personal reinstall capsule source file is invalid.'
            }
            $bytes = [IO.File]::ReadAllBytes($source)
            $entries += [pscustomobject][ordered]@{
                path=$relative
                size=[int64]$sourceItem.Length
                sha256=(Get-RagPersonalBytesSha256 -Bytes $bytes)
                content_base64=[Convert]::ToBase64String($bytes)
            }
            [Array]::Clear($bytes,0,$bytes.Length)
        }
        $secrets = Read-RagPersonalJson -Path (Join-Path $install `
            'secrets\installation-secrets.json')
        Assert-RagPersonalSecretDocument -Document $secrets `
            -InstallationId ([string]$journal.installation_id)
        $releaseIdentity = [ordered]@{
            development_source=[bool]$DevelopmentSource
            release_root=$release
            personal_release_sha256=(Get-FileHash -LiteralPath (Join-Path $release `
                'ops\windows\v8a\personal-release.json') -Algorithm SHA256).Hash.ToLowerInvariant()
            compose_sha256=(Get-FileHash -LiteralPath (Join-Path $release `
                'ops\windows\v8a\compose.personal.yaml') -Algorithm SHA256).Hash.ToLowerInvariant()
            archive_sha256=$null
            release_state_sha256=$null
        }
        if (-not $DevelopmentSource) {
            $releaseIdentity.archive_sha256 = [IO.File]::ReadAllText(
                (Join-Path $release '.verified-archive-sha256')
            ).Trim()
            $releaseIdentity.release_state_sha256 = (Get-FileHash -LiteralPath `
                (Join-Path $install 'state\release-state.json') -Algorithm SHA256
            ).Hash.ToLowerInvariant()
        }
        $document = [ordered]@{
            schema_version=1
            profile_id='personal'
            windows_sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            created_at_utc=[DateTimeOffset]::UtcNow.ToString('o')
            installation_id=[string]$journal.installation_id
            install_root=$install
            data_root=$data
            data_action=$DataAction.ToLowerInvariant()
            release_identity=$releaseIdentity
            payloads=@($entries)
        }
        $plaintext = [Text.UTF8Encoding]::new($false).GetBytes(
            (($document | ConvertTo-Json -Depth 7 -Compress) + "`n")
        )
        if ($plaintext.Length -gt 4MB) { throw 'The reinstall capsule is too large.' }
        Add-Type -AssemblyName System.Security -ErrorAction Stop
        $entropy = Get-RagPersonalDpapiEntropy
        $ciphertext = [Security.Cryptography.ProtectedData]::Protect(
            $plaintext,$entropy,
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        [IO.File]::WriteAllBytes($temporary,$ciphertext)
        Protect-RagPersonalPath -Path $temporary
        $readbackCiphertext = [IO.File]::ReadAllBytes($temporary)
        $readback = $null
        try {
            $readback = [Security.Cryptography.ProtectedData]::Unprotect(
                $readbackCiphertext,$entropy,
                [Security.Cryptography.DataProtectionScope]::CurrentUser
            )
            if (-not (Test-RagPersonalBytesEqual -Left $plaintext -Right $readback)) {
                throw 'The DPAPI reinstall capsule failed encrypted readback validation.'
            }
        }
        finally {
            [Array]::Clear($readbackCiphertext,0,$readbackCiphertext.Length)
            if ($null -ne $readback) { [Array]::Clear($readback,0,$readback.Length) }
        }
        [IO.File]::Move($temporary,$capsule)
        Protect-RagPersonalPath -Path $capsule
        Assert-RagPersonalProtectedPath -Path $capsule
    }
    finally {
        if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) }
        if ($null -ne $plaintext) { [Array]::Clear($plaintext,0,$plaintext.Length) }
        if ($null -ne $ciphertext) { [Array]::Clear($ciphertext,0,$ciphertext.Length) }
        if ($null -ne $entropy) { [Array]::Clear($entropy,0,$entropy.Length) }
    }
    return Read-RagPersonalReinstallCapsule -DataRoot $data -InstallRoot $install `
        -ReleaseRoot $release -DevelopmentSource:$DevelopmentSource
}

function Restore-RagPersonalReinstallCapsule {
    param(
        [Parameter(Mandatory)]$Capsule,
        [Parameter(Mandatory)][string]$InstallRoot
    )
    $install = Assert-RagPersonalPathSafe -Path $InstallRoot -AllowMissing
    $markerPath = Join-Path $install 'state\reinstall-recovery.json'
    $capsuleSha256 = (Get-FileHash -LiteralPath $Capsule.path `
        -Algorithm SHA256).Hash.ToLowerInvariant()
    if (Test-Path -LiteralPath $install -PathType Container) {
        $journalPath = Join-Path $install 'state\installation-journal.json'
        if (Test-Path -LiteralPath $journalPath -PathType Leaf) {
            $current = Read-RagPersonalJson -Path $journalPath
            Assert-RagPersonalJournal -Journal $current
            if ([string]$current.installation_id -cne
                [string]$Capsule.document.installation_id) {
                throw 'An existing Personal install root belongs to another installation.'
            }
        }
        if (@(Get-ChildItem -LiteralPath $install -Force).Count -gt 0) {
            $marker = Read-RagPersonalJson -Path $markerPath
            if ((@($marker.PSObject.Properties.Name | Sort-Object) -join ',') -cne
                    'capsule_path,capsule_sha256,installation_id,schema_version' -or
                $marker.schema_version -ne 1 -or
                [string]$marker.installation_id -cne
                    [string]$Capsule.document.installation_id -or
                [string]$marker.capsule_path -cne [string]$Capsule.path -or
                [string]$marker.capsule_sha256 -cne $capsuleSha256) {
                throw 'An existing Personal install root is not a known reinstall recovery.'
            }
        }
    }
    else { [IO.Directory]::CreateDirectory($install) | Out-Null }
    Protect-RagPersonalPath -Path $install -Directory
    foreach ($relative in @('cache','config','logs','secrets','state')) {
        $directory = Join-Path $install $relative
        if (-not (Test-Path -LiteralPath $directory)) {
            [IO.Directory]::CreateDirectory($directory) | Out-Null
        }
        Protect-RagPersonalPath -Path $directory -Directory
    }
    Write-RagPersonalUtf8File -Path $markerPath -Protect -Value (([ordered]@{
        schema_version=1
        installation_id=[string]$Capsule.document.installation_id
        capsule_path=[string]$Capsule.path
        capsule_sha256=$capsuleSha256
    } | ConvertTo-Json -Compress) + "`n")
    foreach ($entry in @($Capsule.document.payloads)) {
        $destination = Join-Path $install ([string]$entry.path)
        $temporary = "$destination.$([guid]::NewGuid().ToString('N')).tmp"
        $bytes = $null
        try {
            $bytes = [Convert]::FromBase64String([string]$entry.content_base64)
            [IO.File]::WriteAllBytes($temporary,$bytes)
            if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant() `
                    -cne [string]$entry.sha256) {
                throw 'The staged Personal reinstall metadata failed hash validation.'
            }
            Protect-RagPersonalPath -Path $temporary
            if (Test-Path -LiteralPath $destination -PathType Leaf) {
                [IO.File]::Replace($temporary,$destination,$null,$true)
            }
            else { [IO.File]::Move($temporary,$destination) }
            Protect-RagPersonalPath -Path $destination
        }
        finally {
            if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) }
            if ($null -ne $bytes) { [Array]::Clear($bytes,0,$bytes.Length) }
        }
    }
}

function Remove-RagPersonalReinstallCapsule {
    param([Parameter(Mandatory)][string]$DataRoot)
    $capsule = Get-RagPersonalReinstallCapsulePath -DataRoot $DataRoot
    $item = Get-Item -LiteralPath $capsule -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'The Personal reinstall capsule cannot be removed safely.'
    }
    [IO.File]::Delete($capsule)
}

function Complete-RagPersonalReinstallRecovery {
    param(
        [Parameter(Mandatory)][string]$InstallRoot,
        [Parameter(Mandatory)][string]$DataRoot,
        [Parameter(Mandatory)][string]$ReleaseRoot
    )
    $install = Assert-RagPersonalPathSafe -Path $InstallRoot
    $installedRelease = Read-RagPersonalJson -Path (Join-Path $install `
        'config\personal-release.json')
    $developmentSource = [string]$installedRelease.payload_state -ceq 'development_template'
    $capsulePath = Get-RagPersonalReinstallCapsulePath -DataRoot $DataRoot
    $markerPath = Join-Path $install 'state\reinstall-recovery.json'
    $hasCapsule = Test-Path -LiteralPath $capsulePath -PathType Leaf
    $hasMarker = Test-Path -LiteralPath $markerPath -PathType Leaf
    if (-not $hasCapsule -and -not $hasMarker) { return $false }
    if (-not $hasCapsule -or -not $hasMarker) {
        throw 'Personal reinstall completion metadata is incomplete.'
    }
    $capsule = Read-RagPersonalReinstallCapsule -DataRoot $DataRoot `
        -InstallRoot $install -ReleaseRoot $ReleaseRoot `
        -DevelopmentSource:$developmentSource
    $marker = Read-RagPersonalJson -Path $markerPath
    $capsuleSha256 = (Get-FileHash -LiteralPath $capsule.path `
        -Algorithm SHA256).Hash.ToLowerInvariant()
    if ((@($marker.PSObject.Properties.Name | Sort-Object) -join ',') -cne
            'capsule_path,capsule_sha256,installation_id,schema_version' -or
        $marker.schema_version -ne 1 -or
        [string]$marker.installation_id -cne
            [string]$capsule.document.installation_id -or
        [string]$marker.capsule_path -cne [string]$capsule.path -or
        [string]$marker.capsule_sha256 -cne $capsuleSha256) {
        throw 'Personal reinstall completion marker is invalid.'
    }
    Remove-RagPersonalReinstallCapsule -DataRoot $DataRoot
    [IO.File]::Delete($markerPath)
    return $true
}

function Assert-RagPersonalRuntimeStopped {
    if ($null -eq (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        throw 'Windows listener inspection is unavailable; Personal maintenance cannot start safely.'
    }
    foreach ($port in @(3000,8000,8100,8101,8102)) {
        if ($null -ne (Get-NetTCPConnection -State Listen -LocalPort $port `
                -ErrorAction SilentlyContinue)) {
            throw 'Close the Local RAG application window, then run this operation again.'
        }
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
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        -not [Environment]::Is64BitOperatingSystem) {
        throw 'Local RAG Personal V8A requires 64-bit Windows 10 or 11.'
    }
    $operatingSystem = Get-CimInstance -ClassName Win32_OperatingSystem `
        -ErrorAction Stop
    if (-not (Test-RagPersonalSupportedWindows `
            -Version ([Environment]::OSVersion.Version) `
            -ProductType ([int]$operatingSystem.ProductType))) {
        throw 'Local RAG Personal V8A requires Windows 10/11 workstation build 10240 or newer.'
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

function Test-RagPersonalSupportedWindows {
    param(
        [Parameter(Mandatory)][Version]$Version,
        [Parameter(Mandatory)][int]$ProductType
    )
    return $Version.Major -eq 10 -and $Version.Build -ge 10240 -and
        $ProductType -eq 1
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

function Write-RagPersonalRuntimeEnvironments {
    param(
        [Parameter(Mandatory)][pscustomobject]$Secrets,
        [Parameter(Mandatory)][string]$ConfigurationRoot,
        [Parameter(Mandatory)][string]$PersonalDataRoot,
        [Parameter(Mandatory)][string]$InstallRoot,
        [Parameter(Mandatory)][string]$ReleaseRoot,
        [switch]$DevelopmentSource
    )
    $configuration = Assert-RagPersonalPathSafe -Path $ConfigurationRoot
    $data = Assert-RagPersonalPathSafe -Path $PersonalDataRoot
    $install = Assert-RagPersonalPathSafe -Path $InstallRoot
    $release = Assert-RagPersonalPathSafe -Path $ReleaseRoot
    Assert-RagPersonalSecretDocument -Document $Secrets `
        -InstallationId ([string]$Secrets.installation_id)
    $values = $Secrets.values
    $deploymentId = 'rag-personal-' + $Secrets.installation_id.Substring(0,12)
    $dbBase = '127.0.0.1:5432/rag'
    $ocrPython = if ($DevelopmentSource) {
        Join-Path $release '.venv-ocr\Scripts\python.exe'
    } else { Join-Path $release 'runtimes\ocr-python\python.exe' }
    $rerankerModel = if ($DevelopmentSource) {
        Join-Path $data 'models\bge-reranker-v2-m3'
    } else { Join-Path $release 'models\bge-reranker-v2-m3' }
    $ocrModel = if ($DevelopmentSource) {
        Join-Path $data 'models\paddleocr-vl-1.6'
    } else { Join-Path $release 'models\paddleocr-vl-1.6' }
    $inferenceCache = Join-Path $install 'cache\inference'
    $ocrCache = Join-Path $install 'cache\ocr'
    $cachePaths = @(
        $inferenceCache,(Join-Path $inferenceCache 'huggingface'),
        (Join-Path $inferenceCache 'huggingface\hub'),
        (Join-Path $inferenceCache 'transformers'),(Join-Path $inferenceCache 'xdg'),
        $ocrCache,(Join-Path $ocrCache 'huggingface'),
        (Join-Path $ocrCache 'huggingface\hub'),
        (Join-Path $ocrCache 'transformers'),(Join-Path $ocrCache 'xdg')
    )
    foreach ($path in $cachePaths) {
        if (-not (Test-Path -LiteralPath $path)) {
            [IO.Directory]::CreateDirectory($path) | Out-Null
        }
        Protect-RagPersonalPath -Path $path -Directory
    }
    $common = [ordered]@{
        ENVIRONMENT='production'; DEPLOYMENT_ID=$deploymentId
        DATA_ROOT=(Join-Path $data 'application')
        OLLAMA_BASE_URL='http://127.0.0.1:11434'
        GENERATION_MODEL='qwen3:8b'; EMBEDDING_MODEL='qwen3-embedding:0.6b'
        RERANKER_MODEL='BAAI/bge-reranker-v2-m3'
        OCR_PYTHON_EXECUTABLE=$ocrPython; OCR_PIPELINE_VERSION='v1.6'; OCR_DEVICE='cpu'
        OBJECT_STORAGE_ENDPOINT_URL='http://127.0.0.1:9000'
        OBJECT_STORAGE_REGION='us-east-1'; OBJECT_STORAGE_BUCKET='rag-originals'
        OBJECT_STORAGE_FORCE_PATH_STYLE='true'; OBJECT_STORAGE_USE_TLS='false'
    }
    $roles = [ordered]@{
        api=[ordered]@{
            PRODUCT_PROFILE='personal'; CANONICAL_ORIGIN='http://127.0.0.1:3000'
            CANONICAL_HOST='127.0.0.1'; CORS_ORIGINS='[]'
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
        ingestion=[ordered]@{
            WORKER_DATABASE_URL="postgresql+psycopg://rag_worker:$($values.postgres_worker)@$dbBase"
            COORDINATOR_BASE_URL='http://127.0.0.1:8100'
            COORDINATOR_SERVICE_TOKEN=$values.coordinator_service_token
            OCR_SERVICE_BASE_URL='http://127.0.0.1:8101'
            OCR_SERVICE_TOKEN=$values.ocr_service_token
            OBJECT_STORAGE_ACCESS_KEY_ID=$values.rustfs_ingestion_access
            OBJECT_STORAGE_SECRET_ACCESS_KEY=$values.rustfs_ingestion_secret
        }
        deletion=[ordered]@{
            WORKER_DATABASE_URL="postgresql+psycopg://rag_worker:$($values.postgres_worker)@$dbBase"
            OBJECT_STORAGE_ACCESS_KEY_ID=$values.rustfs_deletion_access
            OBJECT_STORAGE_SECRET_ACCESS_KEY=$values.rustfs_deletion_secret
        }
    }
    foreach ($role in $roles.Keys) {
        $document = [ordered]@{}
        foreach ($entry in $common.GetEnumerator()) { $document[$entry.Key]=$entry.Value }
        foreach ($entry in $roles[$role].GetEnumerator()) { $document[$entry.Key]=$entry.Value }
        Write-RagPersonalEnvironmentFile -Path (Join-Path $configuration "$role.env") `
            -Values $document
    }
    Write-RagPersonalEnvironmentFile -Path (Join-Path $configuration 'inference.env') `
        -Values ([ordered]@{
            ENVIRONMENT='production'; DEPLOYMENT_ID=$deploymentId
            OLLAMA_BASE_URL='http://127.0.0.1:11434'; GENERATION_MODEL='qwen3:8b'
            EMBEDDING_MODEL='qwen3-embedding:0.6b'
            RERANKER_MODEL='BAAI/bge-reranker-v2-m3'
            RERANKER_MODEL_PATH=$rerankerModel
            COORDINATOR_SERVICE_TOKEN=$values.coordinator_service_token
            COORDINATOR_OWNERSHIP_PATH=(Join-Path $install 'state\coordinator-ownership.json')
            HF_HOME=(Join-Path $inferenceCache 'huggingface')
            HF_HUB_CACHE=(Join-Path $inferenceCache 'huggingface\hub')
            TRANSFORMERS_CACHE=(Join-Path $inferenceCache 'transformers')
            XDG_CACHE_HOME=(Join-Path $inferenceCache 'xdg')
            HF_HUB_OFFLINE='true'; TRANSFORMERS_OFFLINE='true'
            TOKENIZERS_PARALLELISM='false'
        })
    Write-RagPersonalEnvironmentFile -Path (Join-Path $configuration 'ocr.env') `
        -Values ([ordered]@{
            ENVIRONMENT='production'; DEPLOYMENT_ID=$deploymentId
            OCR_PYTHON_EXECUTABLE=$ocrPython; OCR_PIPELINE_VERSION='v1.6'
            OCR_DEVICE='cpu'; OCR_CPU_THREADS='10'; OCR_PAGE_BATCH_SIZE='8'
            OCR_PROCESS_COUNT='1'; OCR_SERVICE_TOKEN=$values.ocr_service_token
            OCR_OWNERSHIP_PATH=(Join-Path $install 'state\ocr-ownership.json')
            OCR_WORKSPACE_ROOT=(Join-Path $data 'ocr-work')
            OCR_MODEL_ASSET_ROOT=$ocrModel; PADDLE_HOME=$ocrModel
            PADDLE_PDX_CACHE_HOME=$ocrModel
            HF_HOME=(Join-Path $ocrCache 'huggingface')
            HF_HUB_CACHE=(Join-Path $ocrCache 'huggingface\hub')
            TRANSFORMERS_CACHE=(Join-Path $ocrCache 'transformers')
            XDG_CACHE_HOME=(Join-Path $ocrCache 'xdg')
            HF_HUB_OFFLINE='true'; TRANSFORMERS_OFFLINE='true'
            TOKENIZERS_PARALLELISM='false'
        })
    Write-RagPersonalEnvironmentFile -Path (Join-Path $configuration 'web.env') `
        -Values ([ordered]@{
            NODE_ENV='production'; HOSTNAME='127.0.0.1'; PORT='3000'
            INTERNAL_API_URL='http://127.0.0.1:8000'
        })
}

Export-ModuleMember -Function @(
    'Assert-RagPersonalPathSafe',
    'Protect-RagPersonalPath',
    'Write-RagPersonalUtf8File',
    'New-RagPersonalSecret',
    'New-RagPersonalSecretDocument',
    'Assert-RagPersonalSecretDocument',
    'Read-RagPersonalJson',
    'Save-RagPersonalJournal',
    'Assert-RagPersonalJournal',
    'New-RagPersonalJournal',
    'Test-RagPersonalStepComplete',
    'Start-RagPersonalStep',
    'Complete-RagPersonalStep',
    'Set-RagPersonalFailure',
    'New-RagPersonalReinstallCapsule',
    'Test-RagPersonalReinstallCapsuleRequired',
    'Read-RagPersonalReinstallCapsule',
    'Restore-RagPersonalReinstallCapsule',
    'Remove-RagPersonalReinstallCapsule',
    'Complete-RagPersonalReinstallRecovery',
    'Assert-RagPersonalRuntimeStopped',
    'Get-RagPersonalPreflight',
    'Test-RagPersonalSupportedWindows',
    'Assert-RagPersonalPortsFree',
    'Write-RagPersonalEnvironmentFile',
    'Write-RagPersonalRuntimeEnvironments'
)
