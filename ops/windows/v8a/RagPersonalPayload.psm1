Set-StrictMode -Version Latest

function Test-RagPersonalPayloadDeniedName {
    param([Parameter(Mandatory)][string]$Name)
    return (
        $Name -ieq '.env' -or
        $Name -ilike '.env.*' -or
        $Name -cmatch '(?i)^(credentials|secrets?|password)\.(json|txt|ini|yaml|yml)$' -or
        $Name -cmatch '(?i)^installation-(secrets|journal)\.json$' -or
        $Name -cmatch '(?i)^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$' -or
        $Name -cmatch '(?i)\.(key|pfx|p12|pyc|pyo)$'
    )
}

function Test-RagPersonalPayloadPrivatePem {
    param([Parameter(Mandatory)][string]$Path)
    if ([IO.Path]::GetExtension($Path) -ine '.pem') { return $false }
    $stream = [IO.File]::OpenRead($Path)
    try {
        $buffer = [byte[]]::new([Math]::Min(8192,[int]$stream.Length))
        $read = $stream.Read($buffer,0,$buffer.Length)
        $prefix = [Text.Encoding]::ASCII.GetString($buffer,0,$read)
        return $prefix -match '-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----'
    }
    finally { $stream.Dispose() }
}

function Get-RagPersonalPayloadFiles {
    param([Parameter(Mandatory)][string]$Root)
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $rootItem = Get-Item -LiteralPath $resolved -Force
    if (-not $rootItem.PSIsContainer -or
        ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Personal payload root must be a regular directory.'
    }
    $relativeNames = @{}
    $files = [Collections.Generic.List[object]]::new()
    foreach ($entry in Get-ChildItem -LiteralPath $resolved -Recurse -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Personal payload contains a reparse point: $($entry.FullName)"
        }
        if ($entry.PSIsContainer) { continue }
        if ((Test-RagPersonalPayloadDeniedName $entry.Name) -or
            (Test-RagPersonalPayloadPrivatePem $entry.FullName)) {
            throw "Personal payload contains a denied secret-shaped filename: $($entry.FullName)"
        }
        if ($entry.Name -ceq 'pyvenv.cfg') {
            throw "Personal payload contains a non-relocatable Python runtime: $($entry.FullName)"
        }
        $relative = $entry.FullName.Substring($resolved.Length).TrimStart('\').Replace('\','/')
        if ($relative -ceq 'personal-payload-inventory.json') { continue }
        $folded = $relative.ToLowerInvariant()
        if ($relativeNames.ContainsKey($folded)) {
            throw "Personal payload contains a case-colliding path: $relative"
        }
        $relativeNames[$folded] = $true
        $files.Add([pscustomobject]@{
            path=$relative
            sha256=(Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            size=[int64]$entry.Length
        })
    }
    return @($files | Sort-Object path -CaseSensitive)
}

function Get-RagPersonalPayloadTreeSha256 {
    param([Parameter(Mandatory)][object[]]$Files)
    $lines = @($Files | ForEach-Object { "$($_.path)`0$($_.sha256)`0$($_.size)" })
    $content = [Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($hasher.ComputeHash($content))).Replace('-','').ToLowerInvariant()
    }
    finally { $hasher.Dispose() }
}

function New-RagPersonalPayloadInventory {
    param([Parameter(Mandatory)][string]$Root)
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $files = @(Get-RagPersonalPayloadFiles -Root $resolved)
    if ($files.Count -eq 0) { throw 'Personal payload inventory cannot be empty.' }
    [int64]$bytes = 0
    foreach ($file in $files) { $bytes += [int64]$file.size }
    $inventory = [ordered]@{
        schema_version=1
        file_count=$files.Count
        byte_count=$bytes
        tree_sha256=(Get-RagPersonalPayloadTreeSha256 -Files $files)
        files=$files
    }
    [IO.File]::WriteAllText(
        (Join-Path $resolved 'personal-payload-inventory.json'),
        (($inventory | ConvertTo-Json -Depth 5) + "`n"),
        [Text.UTF8Encoding]::new($false)
    )
    return [pscustomobject]$inventory
}

function Test-RagPersonalPayloadInventory {
    param([Parameter(Mandatory)][string]$Root)
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $inventoryPath = Join-Path $resolved 'personal-payload-inventory.json'
    $inventoryItem = Get-Item -LiteralPath $inventoryPath -Force -ErrorAction Stop
    if ($inventoryItem.PSIsContainer -or
        ($inventoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Personal payload inventory must be a regular file.'
    }
    try { $inventory = Get-Content -Raw -LiteralPath $inventoryPath | ConvertFrom-Json }
    catch { throw 'Personal payload inventory is invalid JSON.' }
    $actual = @(Get-RagPersonalPayloadFiles -Root $resolved)
    if ($inventory.schema_version -ne 1 -or
        $inventory.file_count -ne $actual.Count -or
        @($inventory.files).Count -ne $actual.Count) {
        throw 'Personal payload inventory file set is not exact.'
    }
    for ($index = 0; $index -lt $actual.Count; $index++) {
        $expected = @($inventory.files)[$index]
        $observed = $actual[$index]
        if ([string]$expected.path -cne [string]$observed.path -or
            [string]$expected.sha256 -cne [string]$observed.sha256 -or
            [int64]$expected.size -ne [int64]$observed.size) {
            throw "Personal payload inventory mismatch: $($observed.path)"
        }
    }
    $treeHash = Get-RagPersonalPayloadTreeSha256 -Files $actual
    [int64]$bytes = 0
    foreach ($file in $actual) { $bytes += [int64]$file.size }
    if ([string]$inventory.tree_sha256 -cne $treeHash -or
        [int64]$inventory.byte_count -ne $bytes) {
        throw 'Personal payload inventory hash or size is invalid.'
    }
    return [pscustomobject]@{
        result='pass';file_count=$actual.Count;byte_count=$bytes;tree_sha256=$treeHash
    }
}

Export-ModuleMember -Function @(
    'New-RagPersonalPayloadInventory','Test-RagPersonalPayloadInventory'
)
