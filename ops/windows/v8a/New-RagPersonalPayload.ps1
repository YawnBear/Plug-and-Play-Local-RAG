[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$SourceRoot,
    [Parameter(Mandatory)][string]$ApiRuntimeRoot,
    [Parameter(Mandatory)][string]$OcrRuntimeRoot,
    [Parameter(Mandatory)][string]$NodeRuntimeRoot,
    [Parameter(Mandatory)][string]$McExecutable,
    [Parameter(Mandatory)][string]$RerankerModelRoot,
    [Parameter(Mandatory)][string]$OcrModelRoot,
    [Parameter(Mandatory)][string]$OutputRoot,
    [Parameter(Mandatory)][string]$ValidationPython
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagPersonalPayload.psm1') -Force

function Test-RagPersonalDeniedPayloadName {
    param([Parameter(Mandatory)][string]$Name)
    return (
        $Name -ieq '.env' -or
        $Name -ilike '.env.*' -or
        $Name -cmatch '(?i)^(credentials|secrets?|password)\.(json|txt|ini|yaml|yml)$' -or
        $Name -cmatch '(?i)^installation-(secrets|journal)\.json$' -or
        $Name -cmatch '(?i)^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$' -or
        $Name -cmatch '(?i)\.(key|pfx|p12)$'
    )
}

function Test-RagPersonalPrivatePem {
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

function Test-RagPersonalRuntimeTestPath {
    param([Parameter(Mandatory)][string]$RelativePath)
    return @($RelativePath -split '\\') -match '^(?i:test|tests)$'
}

function Assert-RagPersonalRegularFile {
    param([Parameter(Mandatory)][string]$Path)
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $item = Get-Item -LiteralPath $resolved -Force
    if ($item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Personal payload input must be a regular file: $Path"
    }
    return $resolved
}

function Assert-RagPersonalRegularTree {
    param(
        [Parameter(Mandatory)][string]$Path,
        [bool]$PruneRuntimeTests = $false
    )
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $item = Get-Item -LiteralPath $resolved -Force
    if (-not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Personal payload input must be a regular directory: $Path"
    }
    foreach ($entry in Get-ChildItem -LiteralPath $resolved -Recurse -Force) {
        $relative = $entry.FullName.Substring($resolved.Length).TrimStart('\')
        if ($PruneRuntimeTests -and
            (Test-RagPersonalRuntimeTestPath $relative)) {
            continue
        }
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Personal payload input contains a reparse point: $($entry.FullName)"
        }
        if (-not $entry.PSIsContainer -and (
            (Test-RagPersonalDeniedPayloadName $entry.Name) -or
            (Test-RagPersonalPrivatePem $entry.FullName)
        )) {
            throw "Personal payload input contains a denied secret-shaped filename: $($entry.FullName)"
        }
    }
    return $resolved
}

function Copy-RagPersonalTree {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination,
        [bool]$PruneRuntimeTests = $false
    )
    $resolvedSource = Assert-RagPersonalRegularTree $Source $PruneRuntimeTests
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    foreach ($entry in Get-ChildItem -LiteralPath $resolvedSource -Recurse -Force) {
        $relative = $entry.FullName.Substring($resolvedSource.Length).TrimStart('\')
        if (($PruneRuntimeTests -and
                (Test-RagPersonalRuntimeTestPath $relative)) -or
            @($relative -split '\\') -match '^(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache)$' -or
            $entry.Extension -cin @('.pyc','.pyo')) {
            continue
        }
        $target = Join-Path $Destination $relative
        if ($entry.PSIsContainer) {
            [IO.Directory]::CreateDirectory($target) | Out-Null
        }
        else {
            [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
            Copy-Item -LiteralPath $entry.FullName -Destination $target
        }
    }
}

function Copy-RagPersonalMaterializedTree {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination
    )
    $visited = @{}
    $expandedPackages = @{}
    function Assert-RagPersonalPnpmTarget {
        param([Parameter(Mandatory)][string]$Path)
        $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
        if ($fullPath -ceq $script:RagPersonalPnpmStore -or
            $fullPath.StartsWith(
                $script:RagPersonalPnpmStore + '\',
                [StringComparison]::OrdinalIgnoreCase
            )) {
            return $fullPath
        }
        throw "Personal web package target is outside SourceRoot\node_modules\.pnpm: $Path"
    }
    function Read-RagPersonalPackageManifest {
        param([Parameter(Mandatory)][string]$PackageRoot)
        $manifestPath = Join-Path $PackageRoot 'package.json'
        $item = Get-Item -LiteralPath $manifestPath -Force -ErrorAction Stop
        if ($item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $item.Length -le 0 -or $item.Length -gt 2097152) {
            throw "Materialized package manifest is not a bounded regular file: $manifestPath"
        }
        try {
            $text = [Text.UTF8Encoding]::new($false,$true).GetString(
                [IO.File]::ReadAllBytes($manifestPath)
            )
            $manifest = $text | ConvertFrom-Json
        }
        catch { throw "Materialized package manifest is invalid: $manifestPath" }
        if ($null -eq $manifest -or $manifest -isnot [pscustomobject]) {
            throw "Materialized package manifest must be an object: $manifestPath"
        }
        return $manifest
    }
    function Assert-RagPersonalPackageName {
        param([Parameter(Mandatory)][string]$Name)
        if ($Name -cnotmatch '^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$') {
            throw "Materialized package has an invalid dependency name: $Name"
        }
    }
    function Get-RagPersonalDependencyMap {
        param([Parameter(Mandatory)][pscustomobject]$Manifest)
        $requirements = @{}
        $sections = @{}
        foreach ($sectionName in @('dependencies','optionalDependencies','peerDependencies')) {
            $sectionProperty = $Manifest.PSObject.Properties[$sectionName]
            $section = if ($null -eq $sectionProperty) { $null } else {
                $sectionProperty.Value
            }
            if ($null -ne $section -and $section -isnot [pscustomobject]) {
                throw "Materialized package $sectionName must be an object."
            }
            $sections[$sectionName] = $section
        }
        $peerMetaProperty = $Manifest.PSObject.Properties['peerDependenciesMeta']
        $peerMeta = if ($null -eq $peerMetaProperty) { $null } else {
            $peerMetaProperty.Value
        }
        if ($null -ne $peerMeta -and $peerMeta -isnot [pscustomobject]) {
            throw 'Materialized package peerDependenciesMeta must be an object.'
        }
        $dependencyProperties = if ($null -eq $sections['dependencies']) {
            @()
        } else { @($sections['dependencies'].PSObject.Properties) }
        foreach ($property in $dependencyProperties) {
            Assert-RagPersonalPackageName ([string]$property.Name)
            if ($property.Value -isnot [string] -or
                [string]::IsNullOrWhiteSpace([string]$property.Value)) {
                throw "Materialized package dependency has an invalid specifier: $($property.Name)"
            }
            $requirements[[string]$property.Name] = $false
        }
        $optionalProperties = if ($null -eq $sections['optionalDependencies']) {
            @()
        } else { @($sections['optionalDependencies'].PSObject.Properties) }
        foreach ($property in $optionalProperties) {
            Assert-RagPersonalPackageName ([string]$property.Name)
            if ($property.Value -isnot [string] -or
                [string]::IsNullOrWhiteSpace([string]$property.Value)) {
                throw "Materialized optional dependency has an invalid specifier: $($property.Name)"
            }
            $requirements[[string]$property.Name] = $true
        }
        $peerProperties = if ($null -eq $sections['peerDependencies']) {
            @()
        } else { @($sections['peerDependencies'].PSObject.Properties) }
        foreach ($property in $peerProperties) {
            $name = [string]$property.Name
            Assert-RagPersonalPackageName $name
            if ($property.Value -isnot [string] -or
                [string]::IsNullOrWhiteSpace([string]$property.Value)) {
                throw "Materialized peer dependency has an invalid specifier: $name"
            }
            $metaProperty = if ($null -eq $peerMeta) { $null } else {
                $peerMeta.PSObject.Properties[$name]
            }
            if ($null -ne $metaProperty) {
                $optionalProperty = if ($metaProperty.Value -is [pscustomobject]) {
                    $metaProperty.Value.PSObject.Properties['optional']
                } else { $null }
                if ($null -eq $optionalProperty -or
                    $optionalProperty.Value -isnot [bool]) {
                    throw "Materialized peer dependency metadata is invalid: $name"
                }
                if ($optionalProperty.Value) { continue }
            }
            $requirements[$name] = $false
        }
        return $requirements
    }
    function Get-RagPersonalPackageNodeModules {
        param(
            [Parameter(Mandatory)][string]$PackageRoot,
            [Parameter(Mandatory)][string]$PackageDestination,
            [Parameter(Mandatory)][string]$PackageName
        )
        $parts = @($PackageName -split '/')
        if ($parts.Count -eq 1) {
            if ((Split-Path -Leaf $PackageRoot) -cne $parts[0] -or
                (Split-Path -Leaf $PackageDestination) -cne $parts[0]) {
                throw "Materialized package path does not match its name: $PackageName"
            }
            return @(
                (Split-Path -Parent $PackageRoot),
                (Split-Path -Parent $PackageDestination)
            )
        }
        if ($parts.Count -ne 2 -or
            (Split-Path -Leaf $PackageRoot) -cne $parts[1] -or
            (Split-Path -Leaf (Split-Path -Parent $PackageRoot)) -cne $parts[0] -or
            (Split-Path -Leaf $PackageDestination) -cne $parts[1] -or
            (Split-Path -Leaf (Split-Path -Parent $PackageDestination)) -cne $parts[0]) {
            throw "Materialized scoped package path does not match its name: $PackageName"
        }
        return @(
            (Split-Path -Parent (Split-Path -Parent $PackageRoot)),
            (Split-Path -Parent (Split-Path -Parent $PackageDestination))
        )
    }
    function Copy-RagPersonalMaterializedPackage {
        param([string]$PackageRoot,[string]$PackageDestination)
        $resolvedPackage = Assert-RagPersonalPnpmTarget (
            Resolve-Path -LiteralPath $PackageRoot
        ).Path
        $packageKey = $resolvedPackage + '|' + [IO.Path]::GetFullPath($PackageDestination)
        if ($expandedPackages.ContainsKey($packageKey)) { return }
        $expandedPackages[$packageKey] = $true
        $manifest = Read-RagPersonalPackageManifest $resolvedPackage
        $nameProperty = $manifest.PSObject.Properties['name']
        if ($null -eq $nameProperty) {
            throw "Materialized package manifest has no name: $resolvedPackage"
        }
        $packageName = [string]$nameProperty.Value
        Assert-RagPersonalPackageName $packageName
        $nodeModules = @(Get-RagPersonalPackageNodeModules `
            $resolvedPackage $PackageDestination $packageName)
        Copy-RagPersonalMaterializedTreeInner `
            $resolvedPackage $PackageDestination $true
        $requirements = Get-RagPersonalDependencyMap $manifest
        foreach ($dependencyName in @($requirements.Keys | Sort-Object -CaseSensitive)) {
            $relativeDependency = $dependencyName.Replace('/','\')
            $dependencySource = Join-Path $nodeModules[0] $relativeDependency
            if (-not (Test-Path -LiteralPath $dependencySource)) {
                if ($requirements[$dependencyName]) { continue }
                throw "Required materialized package dependency is missing: $dependencyName"
            }
            $dependencyItem = Get-Item -LiteralPath $dependencySource -Force
            if (-not $dependencyItem.PSIsContainer) {
                throw "Materialized package dependency is not a directory: $dependencyName"
            }
            $resolvedDependency = if (
                $dependencyItem.Attributes -band [IO.FileAttributes]::ReparsePoint
            ) {
                $dependencyTarget = @($dependencyItem.Target)[0]
                if ([string]::IsNullOrWhiteSpace($dependencyTarget)) {
                    throw "Materialized package dependency target is unavailable: $dependencyName"
                }
                (Resolve-Path -LiteralPath $dependencyTarget).Path
            } else { $dependencyItem.FullName }
            $resolvedDependency = Assert-RagPersonalPnpmTarget $resolvedDependency
            $dependencyDestination = Join-Path $nodeModules[1] $relativeDependency
            Copy-RagPersonalMaterializedPackage `
                $resolvedDependency $dependencyDestination
        }
    }
    function Copy-RagPersonalMaterializedTreeInner {
        param(
            [string]$CurrentSource,
            [string]$CurrentDestination,
            [bool]$SkipPackageNodeModules = $false
        )
        $sourceItem = Get-Item -LiteralPath (Resolve-Path -LiteralPath $CurrentSource).Path -Force
        $sourceKey = [IO.Path]::GetFullPath($sourceItem.FullName) + '|' +
            [IO.Path]::GetFullPath($CurrentDestination)
        if ($visited.ContainsKey($sourceKey)) { return }
        $visited[$sourceKey] = $true
        if (-not $sourceItem.PSIsContainer -or
            ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Materialized Personal web source must be a regular directory: $CurrentSource"
        }
        [IO.Directory]::CreateDirectory($CurrentDestination) | Out-Null
        foreach ($entry in Get-ChildItem -LiteralPath $sourceItem.FullName -Force) {
            if ($SkipPackageNodeModules -and $entry.PSIsContainer -and
                $entry.Name -ceq 'node_modules') {
                continue
            }
            $target = Join-Path $CurrentDestination $entry.Name
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                $resolved = Get-Item -LiteralPath $entry.FullName -Force
                $targetPath = @($resolved.Target)[0]
                if ([string]::IsNullOrWhiteSpace($targetPath)) {
                    throw "Reparse target is unavailable: $($entry.FullName)"
                }
                $targetPath = (Resolve-Path -LiteralPath $targetPath).Path
                $targetPath = Assert-RagPersonalPnpmTarget $targetPath
                if ($resolved.PSIsContainer) {
                    Copy-RagPersonalMaterializedPackage $targetPath $target
                }
                else {
                    [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
                    Copy-Item -LiteralPath $targetPath -Destination $target -Force
                }
                continue
            }
            if (Test-RagPersonalDeniedPayloadName $entry.Name) {
                throw "Materialized Personal web source contains a denied secret-shaped filename: $($entry.FullName)"
            }
            if ($entry.PSIsContainer) {
                Copy-RagPersonalMaterializedTreeInner `
                    $entry.FullName $target $SkipPackageNodeModules
            }
            else {
                [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
                Copy-Item -LiteralPath $entry.FullName -Destination $target -Force
            }
        }
    }
    Copy-RagPersonalMaterializedTreeInner $Source $Destination
}

function Test-RagPersonalPathsOverlap {
    param([Parameter(Mandatory)][string]$First,[Parameter(Mandatory)][string]$Second)
    $firstPath = [IO.Path]::GetFullPath($First).TrimEnd('\')
    $secondPath = [IO.Path]::GetFullPath($Second).TrimEnd('\')
    return (
        $firstPath -ceq $secondPath -or
        $firstPath.StartsWith($secondPath + '\',[StringComparison]::OrdinalIgnoreCase) -or
        $secondPath.StartsWith($firstPath + '\',[StringComparison]::OrdinalIgnoreCase)
    )
}

function Assert-RagPersonalPayloadHasNoInputPathLeakage {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string[]]$ForbiddenRoots
    )
    $textExtensions = @(
        '.pth','.cfg','.conf','.ini','.json','.yaml','.yml','.toml','.txt',
        '.ps1','.psm1','.cmd','.bat','.py','.js','.cjs','.mjs'
    )
    $decoder = [Text.UTF8Encoding]::new($false,$true)
    $legacyDecoder = [Text.Encoding]::GetEncoding(28591)
    foreach ($file in Get-ChildItem -LiteralPath $Root -Recurse -File -Force) {
        $extension = [IO.Path]::GetExtension($file.Name).ToLowerInvariant()
        if ($extension -cnotin $textExtensions) { continue }
        $bytes = [IO.File]::ReadAllBytes($file.FullName)
        try { $text = $decoder.GetString($bytes) }
        catch {
            if ($extension -ceq '.pth') {
                throw "Personal payload .pth configuration is not valid UTF-8: $($file.FullName)"
            }
            $text = $legacyDecoder.GetString($bytes)
        }
        if ($extension -ceq '.pth') {
            foreach ($rawLine in ($text -split "`r?`n")) {
                $line = $rawLine.Trim()
                if ([string]::IsNullOrWhiteSpace($line) -or
                    $line.StartsWith('#') -or $line.StartsWith('import ')) {
                    continue
                }
                if ([IO.Path]::IsPathRooted($line) -or
                    $line -match '^[A-Za-z]:[\\/]' -or $line.StartsWith('\\')) {
                    throw "Personal payload contains an absolute-path .pth entry: $($file.FullName)"
                }
            }
        }
        foreach ($forbiddenRoot in $ForbiddenRoots) {
            $variants = @(
                $forbiddenRoot,
                $forbiddenRoot.Replace('\','/'),
                $forbiddenRoot.Replace('\','\\')
            )
            foreach ($variant in $variants) {
                if ($text.IndexOf($variant,[StringComparison]::OrdinalIgnoreCase) -ge 0) {
                    throw "Personal payload text configuration leaks an input root: $($file.FullName)"
                }
            }
        }
    }
}

function Convert-RagPersonalNextServerRoots {
    param(
        [Parameter(Mandatory)][string]$ServerPath,
        [Parameter(Mandatory)][string]$CanonicalSourceRoot
    )
    $server = Assert-RagPersonalRegularFile $ServerPath
    $decoder = [Text.UTF8Encoding]::new($false,$true)
    try { $text = $decoder.GetString([IO.File]::ReadAllBytes($server)) }
    catch { throw 'Generated Next standalone server is not valid UTF-8.' }
    $jsonString = '"(?:\\.|[^"\\])*"'
    $outputPattern = '"outputFileTracingRoot"\s*:\s*(' + $jsonString + ')'
    $turbopackPattern = '"turbopack"\s*:\s*\{\s*"root"\s*:\s*(' +
        $jsonString + ')'
    $outputMatches = @([regex]::Matches($text,$outputPattern))
    $turbopackMatches = @([regex]::Matches($text,$turbopackPattern))
    if ($outputMatches.Count -ne 1 -or $turbopackMatches.Count -ne 1) {
        throw 'Generated Next standalone server must contain both exact root fields once.'
    }
    try {
        $outputRoot = $outputMatches[0].Groups[1].Value | ConvertFrom-Json
        $turbopackRoot = $turbopackMatches[0].Groups[1].Value | ConvertFrom-Json
    }
    catch { throw 'Generated Next standalone server root fields are invalid JSON.' }
    if ([string]$outputRoot -cne $CanonicalSourceRoot -or
        [string]$turbopackRoot -cne $CanonicalSourceRoot) {
        throw 'Generated Next standalone server roots do not equal SourceRoot.'
    }
    $text = [regex]::new($outputPattern).Replace(
        $text,'"outputFileTracingRoot":"."',1
    )
    $text = [regex]::new($turbopackPattern).Replace(
        $text,'"turbopack":{"root":"."',1
    )
    foreach ($variant in @(
        $CanonicalSourceRoot,
        $CanonicalSourceRoot.Replace('\','/'),
        $CanonicalSourceRoot.Replace('\','\\')
    )) {
        if ($text.IndexOf($variant,[StringComparison]::OrdinalIgnoreCase) -ge 0) {
            throw 'Generated Next standalone server still contains SourceRoot after rewrite.'
        }
    }
    [IO.File]::WriteAllText($server,$text,[Text.UTF8Encoding]::new($false))
}

function Convert-RagPersonalNextRequiredManifestRoots {
    param(
        [Parameter(Mandatory)][string]$ManifestPath,
        [Parameter(Mandatory)][string]$CanonicalSourceRoot
    )
    $manifestFile = Assert-RagPersonalRegularFile $ManifestPath
    $decoder = [Text.UTF8Encoding]::new($false,$true)
    try { $text = $decoder.GetString([IO.File]::ReadAllBytes($manifestFile)) }
    catch { throw 'Generated Next required-server-files manifest is not valid UTF-8.' }
    foreach ($field in @('outputFileTracingRoot','turbopack','appDir','relativeAppDir')) {
        if ([regex]::Matches($text,'"' + $field + '"\s*:').Count -ne 1) {
            throw "Generated Next required-server-files manifest must contain $field exactly once."
        }
    }
    try { $manifest = $text | ConvertFrom-Json }
    catch { throw 'Generated Next required-server-files manifest is invalid JSON.' }
    $expectedAppDirectory = Join-Path $CanonicalSourceRoot 'apps\web'
    $expectedRelativeDirectory = 'apps\web'
    if ($null -eq $manifest.config -or $null -eq $manifest.config.turbopack -or
        [string]$manifest.config.outputFileTracingRoot -cne $CanonicalSourceRoot -or
        [string]$manifest.config.turbopack.root -cne $CanonicalSourceRoot -or
        [string]$manifest.appDir -cne $expectedAppDirectory -or
        [string]$manifest.relativeAppDir -cne $expectedRelativeDirectory) {
        throw 'Generated Next required-server-files paths do not match SourceRoot.'
    }
    $manifest.config.outputFileTracingRoot = '.'
    $manifest.config.turbopack.root = '.'
    $manifest.appDir = '.'
    $serialized = ($manifest | ConvertTo-Json -Depth 100 -Compress) + "`n"
    try { $null = $serialized | ConvertFrom-Json }
    catch { throw 'Sanitized Next required-server-files manifest is invalid JSON.' }
    foreach ($variant in @(
        $CanonicalSourceRoot,
        $CanonicalSourceRoot.Replace('\','/'),
        $CanonicalSourceRoot.Replace('\','\\')
    )) {
        if ($serialized.IndexOf($variant,[StringComparison]::OrdinalIgnoreCase) -ge 0) {
            throw 'Generated Next required-server-files manifest still contains SourceRoot.'
        }
    }
    [IO.File]::WriteAllText(
        $manifestFile,$serialized,[Text.UTF8Encoding]::new($false)
    )
}

function Invoke-RagPersonalStagedRuntimeProbes {
    param([Parameter(Mandatory)][string]$Stage)
    $environmentKeys = @(
        'PYTHONPATH','PYTHONHOME','PYTHONNOUSERSITE','PYTHONUTF8','PATH'
    )
    $priorEnvironment = @{}
    try {
        foreach ($key in $environmentKeys) {
            $priorEnvironment[$key] = [Environment]::GetEnvironmentVariable(
                $key,[EnvironmentVariableTarget]::Process
            )
        }
        [Environment]::SetEnvironmentVariable(
            'PYTHONPATH',$null,[EnvironmentVariableTarget]::Process
        )
        [Environment]::SetEnvironmentVariable(
            'PYTHONHOME',$null,[EnvironmentVariableTarget]::Process
        )
        [Environment]::SetEnvironmentVariable(
            'PYTHONNOUSERSITE','1',[EnvironmentVariableTarget]::Process
        )
        [Environment]::SetEnvironmentVariable(
            'PYTHONUTF8','1',[EnvironmentVariableTarget]::Process
        )
        $probePath = @(
            (Join-Path $Stage 'runtimes\api-python'),
            (Join-Path $Stage 'runtimes\ocr-python'),
            (Join-Path $Stage 'runtimes\node'),
            (Join-Path $Stage 'tools\mc'),
            [Environment]::SystemDirectory
        ) -join ';'
        [Environment]::SetEnvironmentVariable(
            'PATH',$probePath,[EnvironmentVariableTarget]::Process
        )

        $apiPython = Join-Path $Stage 'runtimes\api-python\python.exe'
        $contractValidator = Join-Path $Stage 'ops\windows\v8a\validate_contracts.py'
        $apiOutput = @(& $apiPython -B $contractValidator 2>&1)
        if ($LASTEXITCODE -ne 0) { throw 'Staged API Python contract probe failed.' }
        try { $apiResult = ($apiOutput -join "`n") | ConvertFrom-Json }
        catch { throw 'Staged API Python contract probe returned invalid JSON.' }
        if ($apiResult.result -cne 'pass' -or
            $apiResult.payload_state -cne 'assembled_unsigned' -or
            $apiResult.mutations_performed -ne $false) {
            throw 'Staged API Python contract probe did not return a read-only pass.'
        }

        $ocrPython = Join-Path $Stage 'runtimes\ocr-python\python.exe'
        $probeErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $ocrOutput = @(& $ocrPython -I -B -c 'import paddle; import paddleocr' 2>&1)
            $ocrExitCode = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $probeErrorActionPreference }
        if ($ocrExitCode -ne 0) {
            throw "Staged OCR Python import probe failed: $($ocrOutput -join "`n")"
        }

        $nodeOutput = @(& (Join-Path $Stage 'runtimes\node\node.exe') --version 2>&1)
        if ($LASTEXITCODE -ne 0 -or ($nodeOutput -join '').Trim() -cnotmatch '^v[0-9]+\.') {
            throw 'Staged Node runtime version probe failed.'
        }
        $mcOutput = @(& (Join-Path $Stage 'tools\mc\mc.exe') --version 2>&1)
        if ($LASTEXITCODE -ne 0 -or
            [string]::IsNullOrWhiteSpace(($mcOutput -join '').Trim())) {
            throw 'Staged mc runtime version probe failed.'
        }
        return [pscustomobject]@{
            api_contract='pass';ocr_imports='pass';node_version='pass';mc_version='pass'
        }
    }
    finally {
        foreach ($key in $environmentKeys) {
            [Environment]::SetEnvironmentVariable(
                $key,$priorEnvironment[$key],[EnvironmentVariableTarget]::Process
            )
        }
    }
}

$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$sourceItem = Get-Item -LiteralPath $source -Force
if (-not $sourceItem.PSIsContainer -or
    ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'SourceRoot must be a regular directory.'
}
$apiRuntime = Assert-RagPersonalRegularTree $ApiRuntimeRoot $true
$ocrRuntime = Assert-RagPersonalRegularTree $OcrRuntimeRoot $true
$nodeRuntime = Assert-RagPersonalRegularTree $NodeRuntimeRoot $true
$rerankerModel = Assert-RagPersonalRegularTree $RerankerModelRoot
$ocrModel = Assert-RagPersonalRegularTree $OcrModelRoot
$mc = Assert-RagPersonalRegularFile $McExecutable
$validation = Assert-RagPersonalRegularFile $ValidationPython
$output = [IO.Path]::GetFullPath($OutputRoot).TrimEnd('\')

foreach ($inputPath in @(
    $source,$apiRuntime,$ocrRuntime,$nodeRuntime,$rerankerModel,$ocrModel,
    $mc,$validation
)) {
    if (Test-RagPersonalPathsOverlap $output $inputPath) {
        throw 'Personal payload output must be outside every input.'
    }
}
if (Test-Path -LiteralPath $output) {
    $outputItem = Get-Item -LiteralPath $output -Force
    if (-not $outputItem.PSIsContainer -or
        ($outputItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        @(Get-ChildItem -LiteralPath $output -Force).Count -gt 0) {
        throw 'Personal payload output must be absent or an empty regular directory.'
    }
}

$treeMappings = @(
    @((Join-Path $source 'apps\api\app'),'apps\api\app',$false),
    @((Join-Path $source 'apps\api\alembic'),'apps\api\alembic',$false),
    @($apiRuntime,'runtimes\api-python',$true),
    @($ocrRuntime,'runtimes\ocr-python',$true),
    @($nodeRuntime,'runtimes\node',$true),
    @($rerankerModel,'models\bge-reranker-v2-m3',$false),
    @($ocrModel,'models\paddleocr-vl-1.6',$false)
)
$sourceFiles = @(
    'LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','MODEL_LICENSES.md','README.md',
    'SECURITY.md','CONTRIBUTING.md','CODE_OF_CONDUCT.md',
    'apps\api\alembic.ini','apps\api\pyproject.toml','apps\api\uv.lock',
    'ops\windows\release-allowed-signers','ops\windows\validate_json_schema.py'
)
$personalFiles = @(
    'capability-profiles.json','capability-profiles.schema.json',
    'Check-for-Updates.cmd','compose.personal.yaml','compose.restore-verifier.yaml',
    'Initialize-RagPersonalPostgres.ps1','Initialize-RagPersonalRustfs.ps1',
    'Install-Local-RAG.cmd','Install-RagPersonal.ps1','Issue-New-Setup-Code.cmd',
    'Issue-RagPersonalSetupCode.ps1','personal-release.json',
    'personal-release.schema.json','product-profiles.json',
    'product-profiles.schema.json','RagPersonal.psm1',
    'release-trust-metadata.schema.json','Show-RagPersonalSetupCode.ps1',
    'Start-Local-RAG.cmd','Start-RagPersonal.ps1','Test-RagPersonal.ps1',
    'trust-policy.json','trust-policy.schema.json',
    'Uninstall-Local-RAG.cmd','Uninstall-RagPersonal.ps1',
    'Update-RagPersonal.ps1','validate_contracts.py',
    'Verify-and-Install-Local-RAG.ps1'
)
foreach ($file in $personalFiles) { $sourceFiles += "ops\windows\v8a\$file" }
foreach ($mapping in $treeMappings) {
    if (-not (Test-Path -LiteralPath $mapping[0] -PathType Container)) {
        throw "Required Personal payload source tree is missing: $($mapping[0])"
    }
}
foreach ($relative in $sourceFiles) {
    $file = Assert-RagPersonalRegularFile (Join-Path $source $relative)
    if ((Test-RagPersonalDeniedPayloadName ([IO.Path]::GetFileName($file))) -or
        (Test-RagPersonalPrivatePem $file)) {
        throw "Personal payload source has a denied secret-shaped filename: $relative"
    }
}
Assert-RagPersonalRegularFile (Join-Path $source 'SBOM.cdx.json') | Out-Null
$sbomGenerator = Assert-RagPersonalRegularFile (
    Join-Path $source 'ops\release\generate_v8f_artifacts.py'
)
$standaloneSource = Join-Path $source 'apps\web\.next\standalone'
$staticSource = Join-Path $source 'apps\web\.next\static'
$publicSource = Join-Path $source 'apps\web\public'
foreach ($webSource in @($standaloneSource,$staticSource,$publicSource)) {
    if (-not (Test-Path -LiteralPath $webSource -PathType Container)) {
        throw "Required production web output is missing: $webSource"
    }
}
$script:RagPersonalPnpmStore = [IO.Path]::GetFullPath(
    (Join-Path $source 'node_modules\.pnpm')
).TrimEnd('\')

if (-not $PSCmdlet.ShouldProcess($output, 'Assemble unsigned Local RAG Personal payload')) {
    return
}

$outputParent = Split-Path -Parent $output
$createdParent = -not (Test-Path -LiteralPath $outputParent)
[IO.Directory]::CreateDirectory($outputParent) | Out-Null
$stage = Join-Path $outputParent ('.personal-payload-' + [guid]::NewGuid().ToString('N'))
try {
    [IO.Directory]::CreateDirectory($stage) | Out-Null
    foreach ($mapping in $treeMappings) {
        Copy-RagPersonalTree `
            $mapping[0] (Join-Path $stage $mapping[1]) ([bool]$mapping[2])
    }
    $supervisorDestination = Join-Path $stage 'apps\supervisor'
    [IO.Directory]::CreateDirectory($supervisorDestination) | Out-Null
    foreach ($supervisorFile in Get-ChildItem -LiteralPath (
        Join-Path $source 'apps\supervisor'
    ) -File -Filter '*.py') {
        if (Test-RagPersonalDeniedPayloadName $supervisorFile.Name) {
            throw "Personal supervisor source has a denied filename: $($supervisorFile.FullName)"
        }
        Copy-Item -LiteralPath $supervisorFile.FullName -Destination $supervisorDestination
    }
    if (@(Get-ChildItem -LiteralPath $supervisorDestination -File).Count -eq 0) {
        throw 'Personal supervisor Python modules are missing.'
    }
    foreach ($relative in $sourceFiles) {
        $target = Join-Path $stage $relative
        [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
        Copy-Item -LiteralPath (Join-Path $source $relative) -Destination $target
    }
    Copy-Item -LiteralPath (Join-Path $source `
        'ops\windows\v8a\Install-Local-RAG.cmd') `
        -Destination (Join-Path $stage 'Install-Local-RAG.cmd')
    Copy-RagPersonalMaterializedTree $standaloneSource (
        Join-Path $stage 'apps\web\.next\standalone'
    )
    Copy-RagPersonalTree $staticSource (
        Join-Path $stage 'apps\web\.next\standalone\apps\web\.next\static'
    )
    Copy-RagPersonalTree $publicSource (
        Join-Path $stage 'apps\web\.next\standalone\apps\web\public'
    )
    Convert-RagPersonalNextServerRoots -ServerPath (
        Join-Path $stage 'apps\web\.next\standalone\apps\web\server.js'
    ) -CanonicalSourceRoot $source
    Convert-RagPersonalNextRequiredManifestRoots -ManifestPath (
        Join-Path $stage (
            'apps\web\.next\standalone\apps\web\.next\required-server-files.json'
        )
    ) -CanonicalSourceRoot $source
    [IO.Directory]::CreateDirectory((Join-Path $stage 'tools\mc')) | Out-Null
    Copy-Item -LiteralPath $mc -Destination (Join-Path $stage 'tools\mc\mc.exe')

    foreach ($entry in Get-ChildItem -LiteralPath $stage -Recurse -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Assembled Personal payload contains a reparse point: $($entry.FullName)"
        }
        if (-not $entry.PSIsContainer) {
            if ((Test-RagPersonalDeniedPayloadName $entry.Name) -or
                (Test-RagPersonalPrivatePem $entry.FullName)) {
                throw "Assembled Personal payload contains a denied secret-shaped filename: $($entry.FullName)"
            }
            if ($entry.Name -ceq 'pyvenv.cfg') {
                throw "Assembled Personal payload contains a non-relocatable Python runtime: $($entry.FullName)"
            }
        }
    }

    $manifestPath = Join-Path $stage 'ops\windows\v8a\personal-release.json'
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.payload_state -cne 'development_template') {
        throw 'Personal payload assembly requires the development_template contract state.'
    }
    $manifest.payload_state = 'assembled_unsigned'
    [IO.File]::WriteAllText(
        $manifestPath,
        (($manifest | ConvertTo-Json -Depth 12) + "`n"),
        [Text.UTF8Encoding]::new($false)
    )
    foreach ($artifact in @($manifest.artifacts | Where-Object { $_.required })) {
        $artifactPath = Join-Path $stage ([string]$artifact.relative_path)
        if ([string]$artifact.kind -ceq 'directory') {
            if (-not (Test-Path -LiteralPath $artifactPath -PathType Container) -or
                @(Get-ChildItem -LiteralPath $artifactPath -Recurse -File -Force).Count -eq 0) {
                throw "Required Personal payload artifact is missing or empty: $($artifact.artifact_id)"
            }
        }
        elseif (-not (Test-Path -LiteralPath $artifactPath -PathType Leaf) -or
            (Get-Item -LiteralPath $artifactPath).Length -eq 0) {
            throw "Required Personal payload artifact is missing or empty: $($artifact.artifact_id)"
        }
    }
    foreach ($requiredDirectory in @(
        'apps\web\.next\standalone\apps\web\.next\static',
        'apps\web\.next\standalone\apps\web\public'
    )) {
        $requiredPath = Join-Path $stage $requiredDirectory
        if (@(Get-ChildItem -LiteralPath $requiredPath -Recurse -File -Force).Count -eq 0) {
            throw "Required Personal payload directory is empty: $requiredDirectory"
        }
    }

    Assert-RagPersonalPayloadHasNoInputPathLeakage -Root $stage -ForbiddenRoots @(
        $source,$apiRuntime,$ocrRuntime,$nodeRuntime,$rerankerModel,$ocrModel
    )

    $contractOutput = @(& $validation -B (
        Join-Path $stage 'ops\windows\v8a\validate_contracts.py'
    ) 2>&1)
    if ($LASTEXITCODE -ne 0) { throw 'Assembled Personal contracts failed validation.' }
    try { $contractState = ($contractOutput -join "`n") | ConvertFrom-Json }
    catch { throw 'Assembled Personal contract validation returned invalid JSON.' }
    if ($contractState.result -cne 'pass' -or
        $contractState.payload_state -cne 'assembled_unsigned' -or
        $contractState.mutations_performed -ne $false) {
        throw 'Assembled Personal contract validation did not return a read-only pass.'
    }
    $null = @(& $validation -B $sbomGenerator --check 2>&1)
    if ($LASTEXITCODE -ne 0) { throw 'The checked-in release SBOM is stale.' }
    $runtimeProbes = Invoke-RagPersonalStagedRuntimeProbes -Stage $stage
    Assert-RagPersonalPayloadHasNoInputPathLeakage -Root $stage -ForbiddenRoots @(
        $source,$apiRuntime,$ocrRuntime,$nodeRuntime,$rerankerModel,$ocrModel
    )

    $null = New-RagPersonalPayloadInventory -Root $stage
    $verifiedEvidence = Test-RagPersonalPayloadInventory -Root $stage
    if (Test-Path -LiteralPath $output) {
        [IO.Directory]::Delete($output,$false)
    }
    Move-Item -LiteralPath $stage -Destination $output
    [pscustomobject]@{
        result='assembled_unsigned'
        payload_root=$output
        contract_state=[string]$contractState.payload_state
        contract_validated=$true
        file_count=$verifiedEvidence.file_count
        byte_count=$verifiedEvidence.byte_count
        tree_sha256=$verifiedEvidence.tree_sha256
        runtime_probes=$runtimeProbes
        signing_required=$true
        unsigned_preview_ready=$true
        automatic_updates_available=$false
        clean_machine_qualification_required=$true
    } | ConvertTo-Json -Depth 4
}
finally {
    if (Test-Path -LiteralPath $stage -PathType Container) {
        [IO.Directory]::Delete($stage,$true)
    }
    if ($createdParent -and (Test-Path -LiteralPath $outputParent -PathType Container) -and
        @(Get-ChildItem -LiteralPath $outputParent -Force).Count -eq 0) {
        [IO.Directory]::Delete($outputParent,$false)
    }
}
