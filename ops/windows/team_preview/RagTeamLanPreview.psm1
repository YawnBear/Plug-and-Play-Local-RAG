Set-StrictMode -Version Latest

$script:PreviewProfile = 'team_lan_preview_unsigned'
$script:PreviewStateName = 'team-preview-state.json'
$script:SignedStateNames = @(
    'installed-release-state.json',
    'installed-release-evidence.json',
    'installation-dependency-evidence.json'
)

function Resolve-RagTeamPreviewFileSystemPath {
    param([Parameter(Mandatory)][string]$Path)
    $resolved = Resolve-Path -LiteralPath $Path
    if ($resolved.Provider.Name -cne 'FileSystem') {
        throw 'Team/LAN preview paths must use the FileSystem provider.'
    }
    return [IO.Path]::GetFullPath($resolved.ProviderPath)
}

function Test-RagTeamPreviewDeniedFile {
    param(
        [Parameter(Mandatory)][IO.FileInfo]$File,
        [Parameter(Mandatory)][string]$RelativePath
    )
    $allowedEnvironmentTemplates = @(
        'release/ops/windows/environments/caddy.env.example',
        'release/ops/windows/environments/web.env.example',
        'release/ops/windows/environments/api.env.example',
        'release/ops/windows/environments/ingestion.env.example',
        'release/ops/windows/environments/deletion.env.example',
        'release/ops/windows/environments/inference.env.example',
        'release/ops/windows/environments/ocr.env.example'
    )
    if ($File.Name -ilike '*.env*' -and $RelativePath -cnotin $allowedEnvironmentTemplates) {
        return $true
    }
    if (
        $File.Name -cmatch '(?i)^(credentials|secrets?|password)\.(json|txt|ini|yaml|yml)$' -or
        $File.Name -cmatch '(?i)^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$' -or
        $File.Name -cmatch '(?i)\.(key|pfx|p12)$') { return $true }
    if ($File.Extension -ine '.pem') { return $false }
    $stream = [IO.File]::OpenRead($File.FullName)
    try {
        $buffer = [byte[]]::new([Math]::Min(8192,[int]$stream.Length))
        $read = $stream.Read($buffer,0,$buffer.Length)
        return [Text.Encoding]::ASCII.GetString($buffer,0,$read) -match
            '-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----'
    } finally { $stream.Dispose() }
}

function Assert-RagTeamPreviewRfc1918Address {
    param([Parameter(Mandatory)][string]$Address)
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address,[ref]$parsed) -or
        $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw 'Team/LAN preview requires exactly one literal IPv4 address.'
    }
    $bytes = $parsed.GetAddressBytes()
    $rfc1918 = $bytes[0] -eq 10 -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
    if (-not $rfc1918) {
        throw 'Team/LAN preview address must be an attended RFC1918 IPv4 address.'
    }
    return $parsed.ToString()
}

function Get-RagTeamPreviewFiles {
    param([Parameter(Mandatory)][string]$Root)
    $resolved = (Resolve-RagTeamPreviewFileSystemPath -Path $Root).TrimEnd('\')
    $rootPrefix = $resolved + [IO.Path]::DirectorySeparatorChar
    $rootItem = Get-Item -LiteralPath $resolved -Force
    if (-not $rootItem.PSIsContainer -or
        ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Team/LAN preview payload root must be a regular directory.'
    }
    $names = @{}
    $files = [Collections.Generic.List[object]]::new()
    foreach ($entry in Get-ChildItem -LiteralPath $resolved -Recurse -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Team/LAN preview payload contains a reparse point: $($entry.FullName)"
        }
        if ($entry.PSIsContainer) { continue }
        $fullPath = [IO.Path]::GetFullPath($entry.FullName)
        if (-not $fullPath.StartsWith($rootPrefix,[StringComparison]::OrdinalIgnoreCase)) {
            throw "Team/LAN preview file escapes its payload root: $fullPath"
        }
        $relative = $fullPath.Substring($rootPrefix.Length).Replace('\','/')
        if (Test-RagTeamPreviewDeniedFile -File $entry -RelativePath $relative) {
            throw "Team/LAN preview payload contains private material: $($entry.FullName)"
        }
        if ($relative -ceq 'team-preview-inventory.json') { continue }
        $folded = $relative.ToLowerInvariant()
        if ($names.ContainsKey($folded)) {
            throw "Team/LAN preview payload contains a case-colliding path: $relative"
        }
        $names[$folded] = $true
        $files.Add([pscustomobject]@{
            path=$relative
            sha256=(Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
            size=[int64]$entry.Length
        })
    }
    return @($files | Sort-Object path -CaseSensitive)
}

function Get-RagTeamPreviewTreeSha256 {
    param([Parameter(Mandatory)][object[]]$Files)
    $content = [Text.Encoding]::UTF8.GetBytes((@($Files | ForEach-Object {
        "$($_.path)`0$($_.sha256)`0$($_.size)"
    }) -join "`n"))
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($content)) -replace '-','').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function New-RagTeamPreviewInventory {
    param([Parameter(Mandatory)][string]$Root)
    $resolved = Resolve-RagTeamPreviewFileSystemPath -Path $Root
    $files = @(Get-RagTeamPreviewFiles -Root $resolved)
    if ($files.Count -eq 0) { throw 'Team/LAN preview inventory cannot be empty.' }
    [int64]$bytes = 0
    foreach ($file in $files) { $bytes += [int64]$file.size }
    $inventory = [ordered]@{
        schema_version=1
        profile=$script:PreviewProfile
        authenticity='unverified_unsigned'
        automatic_updates_available=$false
        file_count=$files.Count
        byte_count=$bytes
        tree_sha256=(Get-RagTeamPreviewTreeSha256 -Files $files)
        files=$files
    }
    [IO.File]::WriteAllText(
        (Join-Path $resolved 'team-preview-inventory.json'),
        (($inventory | ConvertTo-Json -Depth 6) + "`n"),
        [Text.UTF8Encoding]::new($false)
    )
    return [pscustomobject]$inventory
}

function Test-RagTeamPreviewInventory {
    param([Parameter(Mandatory)][string]$Root)
    $resolved = Resolve-RagTeamPreviewFileSystemPath -Path $Root
    $path = Join-Path $resolved 'team-preview-inventory.json'
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.Length -lt 2 -or $item.Length -gt 256MB) {
        throw 'Team/LAN preview inventory must be a bounded regular file.'
    }
    try {
        $inventory = [Text.UTF8Encoding]::new($false,$true).GetString(
            [IO.File]::ReadAllBytes($item.FullName)
        ) | ConvertFrom-Json
    } catch { throw 'Team/LAN preview inventory is invalid JSON.' }
    $fields = @($inventory.PSObject.Properties.Name | Sort-Object)
    $expectedFields = @(
        'authenticity','automatic_updates_available','byte_count','file_count',
        'files','profile','schema_version','tree_sha256'
    ) | Sort-Object
    if (($fields -join ',') -cne ($expectedFields -join ',') -or
        $inventory.schema_version -ne 1 -or
        [string]$inventory.profile -cne $script:PreviewProfile -or
        [string]$inventory.authenticity -cne 'unverified_unsigned' -or
        $inventory.automatic_updates_available -isnot [bool] -or
        $inventory.automatic_updates_available) {
        throw 'Team/LAN preview inventory contract is invalid.'
    }
    $actual = @(Get-RagTeamPreviewFiles -Root $resolved)
    $expected = @($inventory.files)
    if ($expected.Count -ne $actual.Count -or $inventory.file_count -ne $actual.Count) {
        throw 'Team/LAN preview inventory file set is not exact.'
    }
    [int64]$bytes = 0
    for ($index=0; $index -lt $actual.Count; $index++) {
        $entryFields = @($expected[$index].PSObject.Properties.Name | Sort-Object)
        if (($entryFields -join ',') -cne 'path,sha256,size' -or
            [string]$expected[$index].path -cne [string]$actual[$index].path -or
            [string]$expected[$index].sha256 -cne [string]$actual[$index].sha256 -or
            [int64]$expected[$index].size -ne [int64]$actual[$index].size) {
            throw "Team/LAN preview inventory mismatch: $($actual[$index].path)"
        }
        $bytes += [int64]$actual[$index].size
    }
    $tree = Get-RagTeamPreviewTreeSha256 -Files $actual
    if ($tree -cne [string]$inventory.tree_sha256 -or $bytes -ne [int64]$inventory.byte_count) {
        throw 'Team/LAN preview inventory tree hash or size is invalid.'
    }
    [pscustomobject]@{
        result='pass';profile=$script:PreviewProfile;authenticity='unverified_unsigned'
        automatic_updates_available=$false;file_count=$actual.Count;byte_count=$bytes
        tree_sha256=$tree;root=$resolved;files=$actual
    }
}

function Test-RagTeamPreviewInstalledRelease {
    param(
        [Parameter(Mandatory)][string]$PayloadRoot,
        [Parameter(Mandatory)][string]$InstalledReleaseRoot,
        [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')]
        [string]$ExpectedTreeSha256
    )
    $payloadEvidence = Test-RagTeamPreviewInventory -Root $PayloadRoot
    if ([string]$payloadEvidence.tree_sha256 -cne $ExpectedTreeSha256) {
        throw 'Team/LAN preview payload changed after its initial verification.'
    }
    $installed = (Resolve-RagTeamPreviewFileSystemPath -Path $InstalledReleaseRoot).
        TrimEnd('\')
    $installedPrefix = $installed + [IO.Path]::DirectorySeparatorChar
    $installedItem = Get-Item -LiteralPath $installed -Force
    if (-not $installedItem.PSIsContainer -or
        ($installedItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Installed Team/LAN preview release root must be a regular directory.'
    }
    $expected = @($payloadEvidence.files | Where-Object {
        $_.path.StartsWith('release/',[StringComparison]::Ordinal)
    } | ForEach-Object {
        [pscustomobject]@{
            path=$_.path.Substring('release/'.Length)
            sha256=$_.sha256
            size=$_.size
        }
    })
    $actual = @(
        Get-ChildItem -LiteralPath $installed -Recurse -Force |
            ForEach-Object {
                if ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                    throw "Installed Team/LAN preview release contains a reparse point: $($_.FullName)"
                }
                if ($_.PSIsContainer) { return }
                $fullPath = [IO.Path]::GetFullPath($_.FullName)
                if (-not $fullPath.StartsWith(
                    $installedPrefix,
                    [StringComparison]::OrdinalIgnoreCase
                )) {
                    throw "Installed Team/LAN preview file escapes its release root: $fullPath"
                }
                $relative = $fullPath.Substring($installedPrefix.Length).Replace('\','/')
                [pscustomobject]@{
                    path=$relative
                    sha256=(Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).
                        Hash.ToLowerInvariant()
                    size=[int64]$_.Length
                }
            } | Where-Object { $null -ne $_ } | Sort-Object path -CaseSensitive
    )
    if ($expected.Count -eq 0 -or $actual.Count -ne $expected.Count) {
        throw 'Installed Team/LAN preview release file set is not exact.'
    }
    for ($index=0; $index -lt $expected.Count; $index++) {
        if ($expected[$index].path -cne $actual[$index].path -or
            $expected[$index].sha256 -cne $actual[$index].sha256 -or
            $expected[$index].size -ne $actual[$index].size) {
            throw "Installed Team/LAN preview release mismatch: $($actual[$index].path)"
        }
    }
    [pscustomobject]@{
        result='pass';payload_tree_sha256=$ExpectedTreeSha256
        installed_release_root=$installed;file_count=$actual.Count
    }
}

function Test-RagTeamPreviewReleaseCopyRequired {
    param(
        [Parameter(Mandatory)][string]$CurrentReleaseRoot,
        [Parameter(Mandatory)][bool]$DurableStoreResume
    )
    if (-not (Test-Path -LiteralPath $CurrentReleaseRoot)) { return $true }
    $item = Get-Item -LiteralPath $CurrentReleaseRoot -Force
    if (-not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Existing Team/LAN preview current release root is unsafe.'
    }
    if (-not $DurableStoreResume) {
        throw 'Existing Team/LAN preview release cannot be adopted by a fresh install.'
    }
    return $false
}

function Wait-RagTeamPreviewGraphReady {
    param(
        [Parameter(Mandatory)][string]$ProgramDataRoot,
        [Parameter(Mandatory)][string]$LocalAddress,
        [Parameter(Mandatory)][DateTimeOffset]$AttemptStartedAtUtc,
        [ValidateRange(1,600)][int]$TimeoutSeconds=300
    )
    $address = Assert-RagTeamPreviewRfc1918Address -Address $LocalAddress
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    $diagnostics = @(
        (Join-Path $ProgramDataRoot 'supervisor-startup-failure.json'),
        (Join-Path $ProgramDataRoot 'profiles\api\tmp\startup-failure.json'),
        (Join-Path $ProgramDataRoot 'profiles\ingestion\tmp\startup-failure.json'),
        (Join-Path $ProgramDataRoot 'profiles\deletion\tmp\startup-failure.json'),
        (Join-Path $ProgramDataRoot 'profiles\inference\tmp\startup-failure.json'),
        (Join-Path $ProgramDataRoot 'profiles\ocr\tmp\startup-failure.json')
    )
    $curl = Join-Path ([Environment]::SystemDirectory) 'curl.exe'
    if (-not (Test-Path -LiteralPath $curl -PathType Leaf)) {
        throw 'Windows system curl.exe is required for bounded preview readiness checks.'
    }
    $certificates = Join-Path $ProgramDataRoot 'certificates'
    $secrets = Join-Path $ProgramDataRoot 'secrets'
    $currentDiagnostic = $null
    do {
        $service = Get-Service -Name RagSupervisor -ErrorAction SilentlyContinue
        foreach ($path in $diagnostics) {
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $item = Get-Item -LiteralPath $path -Force
                if ($item.Length -gt 256KB -or
                    ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw "Preview startup failure diagnostic is unsafe: $path"
                }
                if ($item.LastWriteTimeUtc -ge $AttemptStartedAtUtc.UtcDateTime) {
                    $currentDiagnostic = [IO.File]::ReadAllText($path)
                }
            }
        }
        if ($null -ne $service -and $service.Status -eq 'Stopped') {
            if ($null -ne $currentDiagnostic) {
                throw "Preview graph reported startup failure: $currentDiagnostic"
            }
            throw 'RagSupervisor stopped during the current preview startup attempt.'
        }
        if ($null -ne $service -and $service.Status -eq 'Running') {
            & $curl --silent --show-error --fail --max-time 5 `
                --cacert (Join-Path $certificates 'local-rag-ca.crt') `
                --resolve "rag.home.arpa:443:$address" 'https://rag.home.arpa/' | Out-Null
            $webReady = $LASTEXITCODE -eq 0
            & $curl --silent --show-error --fail --max-time 5 `
                --cacert (Join-Path $certificates 'loopback-ca.crt') `
                --cert (Join-Path $certificates 'supervisor-api-client.crt') `
                --key (Join-Path $secrets 'supervisor-api-client.key') `
                -H 'Host: rag.home.arpa' 'https://127.0.0.1:8443/ready' | Out-Null
            $apiReady = $LASTEXITCODE -eq 0
            if ($webReady -and $apiReady) {
                return [pscustomobject]@{
                    result='pass';service='Running';https_ready=$true
                    api_mtls_ready=$true;local_address=$address
                }
            }
        }
        Start-Sleep -Milliseconds 500
    } until ([DateTimeOffset]::UtcNow -ge $deadline)
    if ($null -ne $currentDiagnostic) {
        throw "Preview graph readiness timed out after startup failure: $currentDiagnostic"
    }
    throw "Team/LAN preview graph did not reach HTTPS and API mTLS readiness in $TimeoutSeconds seconds."
}

function Assert-RagTeamPreviewArchive {
    param([Parameter(Mandatory)][string]$Archive)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $Archive).Path)
    try {
        $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        [int64]$expanded = 0
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('/','\').TrimEnd('\')
            if ([string]::IsNullOrWhiteSpace($name) -or [IO.Path]::IsPathRooted($name) -or
                $name.Contains(':') -or $name -match '(^|\\)\.\.(\\|$)' -or
                -not $seen.Add($name)) {
                throw 'Team/LAN preview archive contains an unsafe or case-colliding path.'
            }
            $expanded += [int64]$entry.Length
            if ($expanded -gt 48GB) { throw 'Team/LAN preview archive exceeds its expansion limit.' }
        }
        if ($zip.Entries.Count -eq 0) { throw 'Team/LAN preview archive is empty.' }
    } finally { $zip.Dispose() }
}

function Assert-RagTeamPreviewProfileState {
    param(
        [Parameter(Mandatory)][string]$ProgramDataRoot,
        [Parameter(Mandatory)][ValidateSet('FreshInstall','InstalledPreview')][string]$Expected
    )
    foreach ($name in $script:SignedStateNames) {
        if (Test-Path -LiteralPath (Join-Path $ProgramDataRoot $name)) {
            throw 'Signed Team state is present; unsigned Team/LAN preview operation is refused.'
        }
    }
    $previewPath = Join-Path $ProgramDataRoot $script:PreviewStateName
    if ($Expected -ceq 'FreshInstall') {
        if (Test-Path -LiteralPath $previewPath) {
            throw 'An unsigned Team/LAN preview is already installed.'
        }
        return $null
    }
    try { $state = Get-Content -Raw -LiteralPath $previewPath | ConvertFrom-Json }
    catch { throw 'Unsigned Team/LAN preview state is missing or invalid.' }
    $fields = @($state.PSObject.Properties.Name | Sort-Object)
    $expectedFields = @(
        'alembic_revision','authenticity','automatic_updates_available','installed_at',
        'connector_generation','installation_id','local_address','profile',
        'release_tree_sha256','schema_version'
    ) | Sort-Object
    if (($fields -join ',') -cne ($expectedFields -join ',') -or
        $state.schema_version -ne 1 -or [string]$state.profile -cne $script:PreviewProfile -or
        [string]$state.authenticity -cne 'unverified_unsigned' -or
        $state.automatic_updates_available -isnot [bool] -or
        $state.automatic_updates_available -or
        [string]$state.installation_id -cnotmatch '^[0-9a-f-]{36}$' -or
        [int]$state.connector_generation -lt 1 -or
        [string]$state.release_tree_sha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Unsigned Team/LAN preview state contract is invalid.'
    }
    [void](Assert-RagTeamPreviewRfc1918Address -Address ([string]$state.local_address))
    return $state
}

function Write-RagTeamPreviewJsonAtomic {
    param([Parameter(Mandatory)][string]$Path,[Parameter(Mandatory)]$Value)
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temporary,(($Value | ConvertTo-Json -Depth 12)+"`n"),
        [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary,$Path,$null,$true) }
    else { [IO.File]::Move($temporary,$Path) }
}

function Invoke-RagTeamPreviewConnector {
    param(
        [string]$LocalAddress,
        [string]$CaCertificate,
        [string]$OutputRoot,
        [string]$InstallationId,
        [int]$ConnectorGeneration
    )
    $generator = Join-Path $PSScriptRoot 'New-RagTeamLanConnector.ps1'
    if (Test-Path -LiteralPath $generator -PathType Leaf) {
        if (Test-Path -LiteralPath $OutputRoot) {
            throw "Connector generation output already exists: $OutputRoot"
        }
        [IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
        try {
            & $generator -LocalAddress $LocalAddress -CaCertificate $CaCertificate `
                -OutputRoot (Join-Path $OutputRoot 'connector') `
                -ZipPath (Join-Path $OutputRoot 'Local-RAG-LAN-Connector.zip') `
                -InstallationId $InstallationId `
                -ConnectorGeneration $ConnectorGeneration
        }
        catch {
            if (Test-Path -LiteralPath $OutputRoot -PathType Container) {
                [IO.Directory]::Delete($OutputRoot,$true)
            }
            throw
        }
    }
}

Export-ModuleMember -Function @(
    'Assert-RagTeamPreviewArchive','Assert-RagTeamPreviewProfileState',
    'Assert-RagTeamPreviewRfc1918Address','Get-RagTeamPreviewFiles',
    'Get-RagTeamPreviewTreeSha256','Invoke-RagTeamPreviewConnector',
    'New-RagTeamPreviewInventory','Test-RagTeamPreviewInventory',
    'Test-RagTeamPreviewInstalledRelease','Test-RagTeamPreviewReleaseCopyRequired',
    'Wait-RagTeamPreviewGraphReady',
    'Write-RagTeamPreviewJsonAtomic'
)
