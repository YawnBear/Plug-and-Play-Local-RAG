[CmdletBinding(DefaultParameterSetName = 'Copy')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Copy')]
    [string]$Manifest,
    [Parameter(Mandatory, ParameterSetName = 'Copy')]
    [string]$Signature,
    [Parameter(Mandatory, ParameterSetName = 'Copy')]
    [string]$ArtifactRoot,
    [Parameter(Mandatory, ParameterSetName = 'Copy')]
    [string]$SignedArtifactStageRoot,
    [Parameter(Mandatory, ParameterSetName = 'Existing')]
    [string]$ExistingSignedStage,
    [switch]$CleanupOnFailure,
    [switch]$CleanupOnSuccess
)

$ErrorActionPreference = 'Stop'
$PinnedAllowedSignersSha256 = 'c3dec800e21c240031dff4ab9d5e22625dd1841ac8a536b56f9267c97d06acb2'

function Assert-ExactProperties {
    param(
        [Parameter(Mandatory)]$Value,
        [Parameter(Mandatory)][string[]]$Expected,
        [Parameter(Mandatory)][string]$Label
    )
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $wanted = @($Expected | Sort-Object)
    if (($actual -join "`n") -cne ($wanted -join "`n")) {
        throw "$Label contains missing, duplicate, or unknown fields"
    }
}

function Skip-JsonWhitespace {
    param([hashtable]$State)
    while (
        $State.Index -lt $State.Text.Length -and
        $State.Text[$State.Index] -in @(" ", "`t", "`r", "`n")
    ) {
        $State.Index++
    }
}

function Read-JsonString {
    param([hashtable]$State)
    if ($State.Index -ge $State.Text.Length -or $State.Text[$State.Index] -ne '"') {
        throw 'Duplicate-aware JSON validator expected a string'
    }
    $State.Index++
    $builder = [Text.StringBuilder]::new()
    while ($State.Index -lt $State.Text.Length) {
        $character = $State.Text[$State.Index++]
        if ($character -eq '"') {
            return $builder.ToString()
        }
        if ([int]$character -lt 0x20) {
            throw 'Duplicate-aware JSON validator found a control character'
        }
        if ($character -ne '\') {
            [void]$builder.Append($character)
            continue
        }
        if ($State.Index -ge $State.Text.Length) {
            throw 'Duplicate-aware JSON validator found an incomplete escape'
        }
        $escape = $State.Text[$State.Index++]
        switch ($escape) {
            '"' { [void]$builder.Append('"') }
            '\' { [void]$builder.Append('\') }
            '/' { [void]$builder.Append('/') }
            'b' { [void]$builder.Append([char]0x08) }
            'f' { [void]$builder.Append([char]0x0c) }
            'n' { [void]$builder.Append("`n") }
            'r' { [void]$builder.Append("`r") }
            't' { [void]$builder.Append("`t") }
            'u' {
                if ($State.Index + 4 -gt $State.Text.Length) {
                    throw 'Duplicate-aware JSON validator found incomplete Unicode'
                }
                $hex = $State.Text.Substring($State.Index, 4)
                if ($hex -cnotmatch '^[0-9A-Fa-f]{4}$') {
                    throw 'Duplicate-aware JSON validator found invalid Unicode'
                }
                [void]$builder.Append([char][Convert]::ToInt32($hex, 16))
                $State.Index += 4
            }
            default { throw 'Duplicate-aware JSON validator found an invalid escape' }
        }
    }
    throw 'Duplicate-aware JSON validator found an unterminated string'
}

function Read-JsonValue {
    param([hashtable]$State)
    Skip-JsonWhitespace $State
    if ($State.Index -ge $State.Text.Length) {
        throw 'Duplicate-aware JSON validator expected a value'
    }
    $character = $State.Text[$State.Index]
    if ($character -eq '{') {
        Read-JsonObject $State
        return
    }
    if ($character -eq '[') {
        Read-JsonArray $State
        return
    }
    if ($character -eq '"') {
        Read-JsonString $State | Out-Null
        return
    }
    foreach ($literal in @('true', 'false', 'null')) {
        if ($State.Text.Substring($State.Index).StartsWith($literal, [StringComparison]::Ordinal)) {
            $State.Index += $literal.Length
            return
        }
    }
    $number = [regex]::Match(
        $State.Text.Substring($State.Index),
        '^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?'
    )
    if (-not $number.Success) {
        throw 'Duplicate-aware JSON validator found an invalid value'
    }
    $State.Index += $number.Length
}

function Read-JsonArray {
    param([hashtable]$State)
    $State.Index++
    Skip-JsonWhitespace $State
    if ($State.Index -lt $State.Text.Length -and $State.Text[$State.Index] -eq ']') {
        $State.Index++
        return
    }
    while ($true) {
        Read-JsonValue $State
        Skip-JsonWhitespace $State
        if ($State.Index -ge $State.Text.Length) {
            throw 'Duplicate-aware JSON validator found an unterminated array'
        }
        $separator = $State.Text[$State.Index++]
        if ($separator -eq ']') {
            return
        }
        if ($separator -ne ',') {
            throw 'Duplicate-aware JSON validator expected an array separator'
        }
    }
}

function Read-JsonObject {
    param([hashtable]$State)
    $State.Index++
    $keys = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    Skip-JsonWhitespace $State
    if ($State.Index -lt $State.Text.Length -and $State.Text[$State.Index] -eq '}') {
        $State.Index++
        return
    }
    while ($true) {
        Skip-JsonWhitespace $State
        $key = Read-JsonString $State
        if (-not $keys.Add($key)) {
            throw "Signed update manifest contains duplicate JSON key: $key"
        }
        Skip-JsonWhitespace $State
        if ($State.Index -ge $State.Text.Length -or $State.Text[$State.Index++] -ne ':') {
            throw 'Duplicate-aware JSON validator expected an object colon'
        }
        Read-JsonValue $State
        Skip-JsonWhitespace $State
        if ($State.Index -ge $State.Text.Length) {
            throw 'Duplicate-aware JSON validator found an unterminated object'
        }
        $separator = $State.Text[$State.Index++]
        if ($separator -eq '}') {
            return
        }
        if ($separator -ne ',') {
            throw 'Duplicate-aware JSON validator expected an object separator'
        }
    }
}

function Assert-NoDuplicateJsonKeys {
    param([Parameter(Mandatory)][string]$Text)
    $state = @{ Text = $Text; Index = 0 }
    Read-JsonValue $state
    Skip-JsonWhitespace $state
    if ($state.Index -ne $Text.Length) {
        throw 'Duplicate-aware JSON validator found trailing content'
    }
}

function Remove-VerifiedStage {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $cleanupSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $cleanupIcacls = Join-Path ([Environment]::SystemDirectory) 'icacls.exe'
    & $cleanupIcacls $Path /grant:r `
        "*$cleanupSid`:(OI)(CI)(F)" "*$cleanupSid`:(F)" /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not restore update-stage cleanup access'
    }
    [IO.Directory]::Delete($Path, $true)
    if (Test-Path -LiteralPath $Path) {
        throw 'Update stage remains after cleanup'
    }
}

$usingExistingStage = $PSCmdlet.ParameterSetName -ceq 'Existing'
if ($usingExistingStage) {
    $stagePath = (Resolve-Path -LiteralPath $ExistingSignedStage).Path
    $stageItem = Get-Item -LiteralPath $stagePath
    if (-not $stageItem.PSIsContainer -or
        ($stageItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        [IO.Path]::GetFileName($stagePath) -cnotmatch '^update-[0-9a-f]{32}$') {
        throw 'Existing signed stage must be a direct update-<32hex> directory'
    }
    $stageParent = (Resolve-Path -LiteralPath (Split-Path -Parent $stagePath)).Path
    if ((Join-Path $stageParent ([IO.Path]::GetFileName($stagePath))) -cne $stagePath) {
        throw 'Existing signed stage must be a direct child of its stage root'
    }
    $sourceManifestPath = Join-Path $stagePath 'update-manifest.json'
    $sourceSignaturePath = Join-Path $stagePath 'update-manifest.json.sig'
    $sourceAllowedSignersPath = Join-Path $stagePath 'allowed_signers'
    $artifactRootPath = $null
    $stageRootPath = $stageParent
} else {
    $sourceManifestPath = (Resolve-Path -LiteralPath $Manifest).Path
    $sourceSignaturePath = (Resolve-Path -LiteralPath $Signature).Path
    $artifactRootPath = (Resolve-Path -LiteralPath $ArtifactRoot).Path
    $sourceAllowedSignersPath = (
        Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'release-allowed-signers')
    ).Path
    $stageRootPath = (Resolve-Path -LiteralPath $SignedArtifactStageRoot).Path
}
$expectedSshKeygen = Join-Path ([Environment]::SystemDirectory) 'OpenSSH\ssh-keygen.exe'
$sshKeygenPath = (Resolve-Path -LiteralPath $expectedSshKeygen).Path
if ($sshKeygenPath -cne $expectedSshKeygen) {
    throw 'Windows OpenSSH verifier did not resolve to the exact System32 path'
}
if ([System.IO.Path]::GetFileName($sourceManifestPath) -cne 'update-manifest.json') {
    throw 'Signed manifest filename must be update-manifest.json'
}
if ([System.IO.Path]::GetFileName($sourceSignaturePath) -cne 'update-manifest.json.sig') {
    throw 'Signature filename must be update-manifest.json.sig'
}
foreach ($path in @($sourceManifestPath, $sourceSignaturePath, $sourceAllowedSignersPath)) {
    $item = Get-Item -LiteralPath $path
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Update trust and manifest inputs must be regular non-reparse files'
    }
}
if (-not $usingExistingStage -and
    -not (Test-Path -LiteralPath $artifactRootPath -PathType Container)) {
    throw 'Artifact root must be a directory'
}
if (-not (Test-Path -LiteralPath $stageRootPath -PathType Container)) {
    throw 'Signed artifact stage root must be a directory'
}

$manifestBytes = [IO.File]::ReadAllBytes($sourceManifestPath)
$signatureBytes = [IO.File]::ReadAllBytes($sourceSignaturePath)
$allowedSignersSourceBytes = [IO.File]::ReadAllBytes($sourceAllowedSignersPath)
$allowedSignersText = [Text.Encoding]::UTF8.GetString($allowedSignersSourceBytes)
$allowedSignersCanonicalText = $allowedSignersText.Replace("`r`n","`n")
if ($allowedSignersCanonicalText.Contains("`r")) {
    throw 'Offline Ed25519 allowed-signers file has invalid line endings'
}
$allowedSignersBytes = [Text.UTF8Encoding]::new($false).GetBytes(
    $allowedSignersCanonicalText
)
if ($manifestBytes.Length -gt 1048576 -or $signatureBytes.Length -gt 1048576 -or
    $allowedSignersBytes.Length -gt 1048576) {
    throw 'Update trust inputs exceed the one MiB bound'
}
$allowedHash = [Security.Cryptography.SHA256]::Create()
try {
    $actual = ([BitConverter]::ToString(
        $allowedHash.ComputeHash($allowedSignersBytes)
    ) -replace '-', '').ToLowerInvariant()
} finally {
    $allowedHash.Dispose()
}
$manifestHash = [Security.Cryptography.SHA256]::Create()
$signatureHash = [Security.Cryptography.SHA256]::Create()
try {
    $capturedManifestSha256 = ([BitConverter]::ToString(
        $manifestHash.ComputeHash($manifestBytes)
    ) -replace '-', '').ToLowerInvariant()
    $capturedSignatureSha256 = ([BitConverter]::ToString(
        $signatureHash.ComputeHash($signatureBytes)
    ) -replace '-', '').ToLowerInvariant()
} finally {
    $manifestHash.Dispose()
    $signatureHash.Dispose()
}
if ($actual -cne $PinnedAllowedSignersSha256) {
    throw 'Offline Ed25519 allowed-signers file does not match the pinned checksum'
}
$manifestText = [Text.Encoding]::UTF8.GetString($manifestBytes)
Assert-NoDuplicateJsonKeys $manifestText
$preliminary = $manifestText | ConvertFrom-Json
if ($preliminary.artifacts -isnot [Array] -or @($preliminary.artifacts).Count -eq 0) {
    throw 'Manifest must contain at least one artifact'
}

$stagePath = if ($usingExistingStage) {
    $stagePath
} else {
    Join-Path $stageRootPath ('update-' + [guid]::NewGuid().ToString('N'))
}
$stageSucceeded = $false
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if (-not $usingExistingStage) {
    $stageSecurity = [Security.AccessControl.DirectorySecurity]::new()
    $stageSecurity.SetAccessRuleProtection($true, $false)
    $inheritance = (
        [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    )
    foreach ($entry in @(
        [pscustomobject]@{ Sid = 'S-1-5-18'; Rights = 'FullControl' },
        [pscustomobject]@{ Sid = 'S-1-5-32-544'; Rights = 'FullControl' },
        [pscustomobject]@{ Sid = $currentSid; Rights = 'Modify' }
    )) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($entry.Sid),
            [Security.AccessControl.FileSystemRights]$entry.Rights,
            $inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void]$stageSecurity.AddAccessRule($rule)
    }
    $stageDirectory = [IO.DirectoryInfo]::new($stagePath)
    $stageDirectory.Create($stageSecurity)
}
try {
$manifestPath = Join-Path $stagePath 'update-manifest.json'
$signaturePath = Join-Path $stagePath 'update-manifest.json.sig'
$allowedSignersPath = Join-Path $stagePath 'allowed_signers'
if (-not $usingExistingStage) {
    [IO.File]::WriteAllBytes($manifestPath, $manifestBytes)
    [IO.File]::WriteAllBytes($signaturePath, $signatureBytes)
    [IO.File]::WriteAllBytes($allowedSignersPath, $allowedSignersBytes)
}
$preliminarySeen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($artifact in @($preliminary.artifacts)) {
    $artifactFields = @($artifact.PSObject.Properties.Name | Sort-Object)
    if (
        ($artifactFields -join ',') -cne 'filename,sha256,size' -or
        $artifact.filename -isnot [string] -or
        $artifact.filename -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or
        [IO.Path]::GetFileName($artifact.filename) -cne $artifact.filename -or
        -not $preliminarySeen.Add($artifact.filename) -or
        ($artifact.size -isnot [long] -and $artifact.size -isnot [int]) -or
        $artifact.size -lt 1
    ) {
        throw 'Artifact preflight is unsafe or duplicated'
    }
    if ($usingExistingStage) {
        continue
    }
    $source = [IO.Path]::GetFullPath((Join-Path $artifactRootPath $artifact.filename))
    if (-not [string]::Equals(
            [IO.Path]::GetDirectoryName($source).TrimEnd('\'),
            $artifactRootPath.TrimEnd('\'),
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Artifact path escapes the root: $($artifact.filename)"
    }
    $sourceItem = Get-Item -LiteralPath $source
    if (
        $sourceItem.PSIsContainer -or
        ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $sourceItem.Length -ne $artifact.size
    ) {
        throw "Artifact preflight failed: $($artifact.filename)"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $stagePath $artifact.filename)
}
if (-not $usingExistingStage) {
    $icacls = Join-Path ([Environment]::SystemDirectory) 'icacls.exe'
    & $icacls $stagePath /inheritance:r /grant:r `
        "*S-1-5-18:(OI)(CI)(RX)" `
        "*S-1-5-32-544:(OI)(CI)(RX)" `
        "*$currentSid`:(OI)(CI)(RX)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not freeze signed update staging ACL'
    }
}
if (
    (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
        $capturedManifestSha256 -or
    (Get-FileHash -LiteralPath $signaturePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
        $capturedSignatureSha256 -or
    (Get-FileHash -LiteralPath $allowedSignersPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
        $PinnedAllowedSignersSha256
) {
    throw 'Immutable staged update trust bytes changed'
}
if (@($signaturePath, $allowedSignersPath) | Where-Object { $_.Contains('"') }) {
    throw 'Update verification paths cannot contain quote characters'
}
$manifestBytes = [IO.File]::ReadAllBytes($manifestPath)
$manifestText = [Text.Encoding]::UTF8.GetString($manifestBytes)
$cmdPath = (Resolve-Path -LiteralPath (
    Join-Path ([Environment]::SystemDirectory) 'cmd.exe'
)).Path
$process = [System.Diagnostics.Process]::new()
$process.StartInfo.FileName = $cmdPath
$process.StartInfo.Arguments = (
    "/D /S /C `"`"$sshKeygenPath`" -Y verify -f allowed_signers " +
    '-I rag-release -n file -s update-manifest.json.sig < update-manifest.json"'
)
$process.StartInfo.WorkingDirectory = $stagePath
$process.StartInfo.UseShellExecute = $false
$process.StartInfo.RedirectStandardOutput = $true
$process.StartInfo.RedirectStandardError = $true
if (-not $process.Start()) {
    throw 'Windows OpenSSH Ed25519 verifier could not start'
}
$standardOutput = $process.StandardOutput.ReadToEnd()
$standardError = $process.StandardError.ReadToEnd()
$process.WaitForExit()
if ($process.ExitCode -ne 0) {
    $diagnostic = (($standardError + ' ' + $standardOutput) -replace '\s+', ' ').Trim()
    $diagnostic = ($diagnostic -replace '[^\x20-\x7e]', '?')
    if ($diagnostic.Length -gt 512) {
        $diagnostic = $diagnostic.Substring(0, 512)
    }
    if ([string]::IsNullOrWhiteSpace($diagnostic)) {
        $diagnostic = 'Windows OpenSSH returned no diagnostic output'
    }
    throw "Ed25519 update signature verification failed: $diagnostic"
}

try {
    $document = $manifestText | ConvertFrom-Json
} catch {
    throw 'Signed update manifest is invalid JSON'
}
Assert-ExactProperties $document @('schema_version', 'version', 'artifacts') 'manifest'
if ($document.schema_version -isnot [long] -and $document.schema_version -isnot [int]) {
    throw 'Manifest schema_version must be an integer'
}
if ($document.schema_version -ne 1) {
    throw 'Manifest schema_version is unsupported'
}
if (
    $document.version -isnot [string] -or
    [string]::IsNullOrWhiteSpace($document.version) -or
    $document.version.Length -gt 64 -or
    $document.version -match '\s'
) {
    throw 'Manifest version is invalid'
}
$artifacts = @($document.artifacts)
if ($document.artifacts -isnot [Array]) {
    throw 'Manifest artifacts must be a JSON array'
}
if ($artifacts.Count -eq 0) {
    throw 'Manifest must contain at least one artifact'
}
$seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$verified = [Collections.Generic.List[object]]::new()
$expectedDirectNames = @(
    (
        @('allowed_signers', 'update-manifest.json', 'update-manifest.json.sig') +
        @($artifacts | ForEach-Object filename)
    ) | Sort-Object
)
$directItems = @(Get-ChildItem -LiteralPath $stagePath -Force)
$actualDirectNames = @($directItems | ForEach-Object Name | Sort-Object)
if (($actualDirectNames -join "`n") -cne ($expectedDirectNames -join "`n") -or
    @($directItems | Where-Object {
        $_.PSIsContainer -or
        ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
    }).Count -ne 0) {
    throw 'Signed update stage direct-file set is not exact'
}
$writeCapableRightsMask = [long](
    0x2 -bor 0x4 -bor 0x10 -bor 0x40 -bor 0x100 -bor
    0x10000 -bor 0x40000 -bor 0x80000
)
$allowedStageSids = @('S-1-5-18', 'S-1-5-32-544', $currentSid)
foreach ($target in @((Get-Item -LiteralPath $stagePath)) + $directItems) {
    $acl = Get-Acl -LiteralPath $target.FullName
    if ($target.FullName -ceq $stagePath -and -not $acl.AreAccessRulesProtected) {
        throw 'Signed update stage ACL must be protected'
    }
    foreach ($rule in @($acl.Access)) {
        try {
            $ruleSid = $rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        } catch {
            throw 'Signed update stage ACL contains an unresolvable identity'
        }
        if ($rule.AccessControlType -cne 'Allow' -or
            $allowedStageSids -cnotcontains $ruleSid -or
            (([long]$rule.FileSystemRights -band $writeCapableRightsMask) -ne 0)) {
            throw 'Signed update stage ACL is not protected RX-only'
        }
    }
}
foreach ($artifact in $artifacts) {
    Assert-ExactProperties $artifact @('filename', 'sha256', 'size') 'artifact'
    $filename = $artifact.filename
    if (
        $filename -isnot [string] -or
        [string]::IsNullOrWhiteSpace($filename) -or
        [IO.Path]::GetFileName($filename) -cne $filename -or
        $filename -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or
        -not $seen.Add($filename)
    ) {
        throw 'Artifact filename is unsafe or duplicated'
    }
    if ($artifact.sha256 -isnot [string] -or $artifact.sha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw "Artifact SHA-256 is invalid: $filename"
    }
    if (
        ($artifact.size -isnot [long] -and $artifact.size -isnot [int]) -or
        $artifact.size -lt 1
    ) {
        throw "Artifact size is invalid: $filename"
    }
    $artifactPath = [IO.Path]::GetFullPath((Join-Path $stagePath $filename))
    if (-not [string]::Equals(
            [IO.Path]::GetDirectoryName($artifactPath).TrimEnd('\'),
            $stagePath.TrimEnd('\'),
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Artifact path escapes the root: $filename"
    }
    $item = Get-Item -LiteralPath $artifactPath
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Artifact is not a regular file: $filename"
    }
    if ($item.Length -ne $artifact.size) {
        throw "Artifact size verification failed: $filename"
    }
    $artifactHash = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($artifactHash -cne $artifact.sha256) {
        throw "Artifact SHA-256 verification failed: $filename"
    }
    $verified.Add([pscustomobject]@{
        filename = $filename
        size = $item.Length
        sha256 = $artifactHash
    })
}

$stageSucceeded = $true
[pscustomobject]@{
    result = 'pass'
    schema_version = 1
    version = $document.version
    signature = 'verified'
    artifacts = $verified
    automatic_install = $false
    changes_applied = $false
    stage_directory = $stagePath
} | ConvertTo-Json -Depth 5
} finally {
    $cleanupRequested = (
        ($CleanupOnFailure -and -not $stageSucceeded) -or
        ($CleanupOnSuccess -and $stageSucceeded)
    )
    if ($cleanupRequested -and (Test-Path -LiteralPath $stagePath)) {
        try {
            Remove-VerifiedStage -Path $stagePath
        } catch {
            throw "Update stage cleanup failed: $($_.Exception.Message)"
        }
    }
}
