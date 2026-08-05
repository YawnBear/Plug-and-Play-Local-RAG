$ErrorActionPreference = 'Stop'

function Initialize-RagUtf8PathComparer {
    if (-not ('RagUtf8PathComparer' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Text;
public sealed class RagUtf8PathComparer : IComparer<string> {
    public int Compare(string left, string right) {
        byte[] a = Encoding.UTF8.GetBytes(left);
        byte[] b = Encoding.UTF8.GetBytes(right);
        int length = Math.Min(a.Length, b.Length);
        for (int i = 0; i < length; i++) {
            int comparison = a[i].CompareTo(b[i]);
            if (comparison != 0) return comparison;
        }
        return a.Length.CompareTo(b.Length);
    }
}
'@
    }
}

function Get-RagTreeSha256 {
    param([Parameter(Mandatory)][string]$Root)
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    Initialize-RagUtf8PathComparer
    $relativeToFile = [Collections.Generic.Dictionary[string,object]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($entry in Get-ChildItem -LiteralPath $resolved -Recurse -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Verified release tree contains a reparse point: $($entry.FullName)"
        }
        if ($entry.PSIsContainer) { continue }
        $file = $entry
        $relative = $file.FullName.Substring($resolved.Length).TrimStart('\').Replace('\','/')
        $relativeToFile[$relative] = $file
    }
    $relativePaths = [string[]]@($relativeToFile.Keys)
    [Array]::Sort($relativePaths, [RagUtf8PathComparer]::new())
    $files = @($relativePaths | ForEach-Object { $relativeToFile[$_] })
    if ($files.Count -eq 0) { throw "Verified release tree is empty: $resolved" }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($resolved.Length).TrimStart('\').Replace('\','/')
            $relativeBytes = [Text.Encoding]::UTF8.GetBytes($relative)
            $lengthBytes = [BitConverter]::GetBytes([int]$relativeBytes.Length)
            if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($lengthBytes) }
            [void]$sha.TransformBlock($lengthBytes, 0, $lengthBytes.Length, $null, 0)
            [void]$sha.TransformBlock($relativeBytes, 0, $relativeBytes.Length, $null, 0)
            $hex = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
            $fileHash = [byte[]]::new(32)
            for ($offset = 0; $offset -lt $hex.Length; $offset += 2) {
                $fileHash[$offset / 2] = [Convert]::ToByte($hex.Substring($offset, 2), 16)
            }
            [void]$sha.TransformBlock($fileHash, 0, $fileHash.Length, $null, 0)
        }
        [void]$sha.TransformFinalBlock([byte[]]::new(0), 0, 0)
        return ([BitConverter]::ToString($sha.Hash) -replace '-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-RagZipTreeSha256 {
    param([Parameter(Mandatory)][string]$Archive)
    Initialize-RagUtf8PathComparer
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $entries = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    $windowsNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $zip = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $Archive).Path)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('\','/').TrimEnd('/')
            if ([string]::IsNullOrWhiteSpace($name)) { continue }
            $segments = @($name.Split('/') | Where-Object { $_ })
            if ($entry.FullName.StartsWith('/') -or $name.Contains(':') -or
                $segments -contains '.' -or $segments -contains '..' -or
                (($entry.ExternalAttributes -band 0x400) -ne 0)) {
                throw "Release archive contains an unsafe entry: $name"
            }
            if ($entry.FullName.EndsWith('/')) { continue }
            if ($entries.ContainsKey($name) -or -not $windowsNames.Add($name)) {
                throw "Release archive contains a duplicate file entry: $name"
            }
            $entries.Add($name, $entry)
        }
        if ($entries.Count -eq 0) { throw 'Release archive file tree is empty' }
        $paths = [string[]]@($entries.Keys)
        [Array]::Sort($paths, [RagUtf8PathComparer]::new())
        $treeSha = [Security.Cryptography.SHA256]::Create()
        try {
            foreach ($path in $paths) {
                $pathBytes = [Text.Encoding]::UTF8.GetBytes($path)
                $lengthBytes = [BitConverter]::GetBytes([int]$pathBytes.Length)
                if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($lengthBytes) }
                [void]$treeSha.TransformBlock($lengthBytes,0,$lengthBytes.Length,$null,0)
                [void]$treeSha.TransformBlock($pathBytes,0,$pathBytes.Length,$null,0)
                $fileSha = [Security.Cryptography.SHA256]::Create()
                try {
                    $stream = $entries[$path].Open()
                    try { $digest = $fileSha.ComputeHash($stream) } finally { $stream.Dispose() }
                } finally { $fileSha.Dispose() }
                [void]$treeSha.TransformBlock($digest,0,$digest.Length,$null,0)
            }
            [void]$treeSha.TransformFinalBlock([byte[]]::new(0),0,0)
            ([BitConverter]::ToString($treeSha.Hash) -replace '-','').ToLowerInvariant()
        } finally { $treeSha.Dispose() }
    } finally { $zip.Dispose() }
}

function Expand-RagVerifiedRelease {
    param(
        [Parameter(Mandatory)][string]$Archive,
        [Parameter(Mandatory)][string]$Destination
    )
    if (Test-Path -LiteralPath $Destination) {
        throw 'Fixed current release destination already exists'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archivePath = (Resolve-Path -LiteralPath $Archive).Path
    $destinationParent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    $pending = Join-Path $destinationParent ('.current-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $pending | Out-Null
    try {
        $zip = [IO.Compression.ZipFile]::OpenRead($archivePath)
        try {
            foreach ($entry in $zip.Entries) {
                $name = $entry.FullName.Replace('\','/')
                $segments = @($name.Split('/') | Where-Object { $_ })
                if (
                    [string]::IsNullOrWhiteSpace($name) -or
                    $name.StartsWith('/') -or
                    $name.Contains(':') -or
                    $segments -contains '.' -or
                    $segments -contains '..' -or
                    (($entry.ExternalAttributes -band 0x400) -ne 0)
                ) {
                    throw "Release archive contains an unsafe entry: $name"
                }
                $target = [IO.Path]::GetFullPath((Join-Path $pending $name))
                if (-not $target.StartsWith($pending + '\', [StringComparison]::OrdinalIgnoreCase)) {
                    throw "Release archive entry escapes the destination: $name"
                }
                if ($name.EndsWith('/')) {
                    New-Item -ItemType Directory -Path $target -Force | Out-Null
                    continue
                }
                New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
                $source = $entry.Open()
                $output = [IO.File]::Open($target, [IO.FileMode]::CreateNew)
                try { $source.CopyTo($output) } finally { $output.Dispose(); $source.Dispose() }
            }
        } finally {
            $zip.Dispose()
        }
        $requiredFiles = @(
            'runtimes\api-python\python.exe',
            'apps\api\app\main.py',
            'apps\supervisor\__main__.py',
            'apps\web\.next\standalone\apps\web\server.js',
            'runtimes\ocr-python\python.exe',
            'runtimes\node\node.exe',
            'tools\openssl\openssl.exe',
            'tools\openssl\openssl.cnf'
        )
        foreach ($relative in $requiredFiles) {
            if (-not (Test-Path -LiteralPath (Join-Path $pending $relative) -PathType Leaf)) {
                throw "Verified release is missing required file: $relative"
            }
        }
        foreach ($relative in @(
            'apps\web\.next\standalone\apps\web\.next\static',
            'apps\web\.next\standalone\apps\web\public',
            'signed-assets\bge-reranker-v2-m3',
            'signed-assets\paddleocr-vl-1.6',
            'tools\openssl',
            'runtimes\api-python',
            'runtimes\ocr-python',
            'runtimes\node'
        )) {
            $tree = Join-Path $pending $relative
            if (-not (Test-Path -LiteralPath $tree -PathType Container) -or
                @(Get-ChildItem -LiteralPath $tree -Recurse -File -Force).Count -eq 0) {
                throw "Verified release is missing required non-empty tree: $relative"
            }
        }
        foreach ($configuration in Get-ChildItem -LiteralPath $pending -Recurse -Filter pyvenv.cfg -File -Force) {
            throw "Verified release contains a non-relocatable external-base configuration: $($configuration.FullName)"
        }
        Move-Item -LiteralPath $pending -Destination $Destination
    } catch {
        if (Test-Path -LiteralPath $pending) {
            try {
                Remove-Item -LiteralPath $pending -Recurse -Force -ErrorAction Stop
            } catch {}
        }
        throw
    }
}

function Test-RagInstalledReleaseBinding {
    param(
        [Parameter(Mandatory)][string]$ReleaseRoot,
        [Parameter(Mandatory)]$ReleaseEvidence
    )
    $apiPythonRoot = Join-Path $ReleaseRoot 'runtimes\api-python'
    $ocrPythonRoot = Join-Path $ReleaseRoot 'runtimes\ocr-python'
    $nodeRoot = Join-Path $ReleaseRoot 'runtimes\node'
    $apiPython = Join-Path $apiPythonRoot 'python.exe'
    $ocrPython = Join-Path $ocrPythonRoot 'python.exe'
    if (
        (Get-FileHash -LiteralPath $apiPython -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            $ReleaseEvidence.runtimes.api_python_sha256 -or
        (Get-FileHash -LiteralPath $ocrPython -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            $ReleaseEvidence.runtimes.ocr_python_sha256
    ) {
        throw 'Installed API/OCR runtime hashes do not match signed release evidence'
    }
    if (
        (Get-RagTreeSha256 $apiPythonRoot) -cne
            $ReleaseEvidence.runtimes.api_python_tree_sha256 -or
        (Get-RagTreeSha256 $ocrPythonRoot) -cne
            $ReleaseEvidence.runtimes.ocr_python_tree_sha256 -or
        (Get-RagTreeSha256 $nodeRoot) -cne
            $ReleaseEvidence.runtimes.node_tree_sha256
    ) {
        throw 'Installed API/OCR/Node runtime trees do not match signed release evidence'
    }
    if (
        (Get-RagTreeSha256 (Join-Path $ReleaseRoot 'signed-assets\bge-reranker-v2-m3')) -cne
            $ReleaseEvidence.reranker.model_assets_sha256 -or
        (Get-RagTreeSha256 (Join-Path $ReleaseRoot 'signed-assets\paddleocr-vl-1.6')) -cne
            $ReleaseEvidence.ocr.model_assets_sha256
    ) {
        throw 'Installed model trees do not match signed release evidence'
    }
    foreach ($probe in @(
        [pscustomobject]@{
            Program=$apiPython; Arguments='-B -c "import fastapi, sqlalchemy, apps.supervisor"'
            Label='API Python'
        },
        [pscustomobject]@{
            Program=$ocrPython; Arguments='-B -I -c "import paddle, paddleocr"'
            Label='OCR Python'
        },
        [pscustomobject]@{
            Program=(Join-Path $nodeRoot 'node.exe'); Arguments='--version'
            Label='Node'
        }
    )) {
        $runtimeProbe = [Diagnostics.Process]::new()
        $runtimeProbe.StartInfo.FileName = $probe.Program
        $runtimeProbe.StartInfo.Arguments = $probe.Arguments
        $runtimeProbe.StartInfo.WorkingDirectory = $ReleaseRoot
        $runtimeProbe.StartInfo.UseShellExecute = $false
        $runtimeProbe.StartInfo.RedirectStandardOutput = $true
        $runtimeProbe.StartInfo.RedirectStandardError = $true
        $runtimeProbe.StartInfo.EnvironmentVariables['PATH'] = (
            Join-Path ([Environment]::GetFolderPath('Windows')) 'System32'
        )
        $runtimeProbe.StartInfo.EnvironmentVariables['PYTHONNOUSERSITE'] = '1'
        $runtimeProbe.StartInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
        foreach ($variable in @('PYTHONHOME','PYTHONPATH','NODE_PATH')) {
            [void]$runtimeProbe.StartInfo.EnvironmentVariables.Remove($variable)
        }
        if (-not $runtimeProbe.Start()) { throw "$($probe.Label) runtime could not start" }
        $runtimeProbe.WaitForExit()
        if ($runtimeProbe.ExitCode -ne 0) {
            throw "$($probe.Label) runtime is not self-contained: $($runtimeProbe.StandardError.ReadToEnd())"
        }
    }
    $opensslRoot = Join-Path $ReleaseRoot 'tools\openssl'
    if ((Get-RagTreeSha256 $opensslRoot) -cne
        $ReleaseEvidence.runtimes.openssl_tree_sha256) {
        throw 'Installed OpenSSL runtime tree does not match signed release evidence'
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo.FileName = Join-Path $opensslRoot 'openssl.exe'
    $opensslConfig = Join-Path $opensslRoot 'openssl.cnf'
    if (-not (Test-Path -LiteralPath $opensslConfig -PathType Leaf)) {
        throw 'Installed OpenSSL runtime is missing its signed adjacent openssl.cnf'
    }
    $process.StartInfo.Arguments = 'version'
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.EnvironmentVariables['PATH'] = (
        Join-Path ([Environment]::GetFolderPath('Windows')) 'System32'
    )
    $process.StartInfo.EnvironmentVariables['OPENSSL_CONF'] = $opensslConfig
    $modules = Join-Path $opensslRoot 'ossl-modules'
    if (Test-Path -LiteralPath $modules -PathType Container) {
        $process.StartInfo.EnvironmentVariables['OPENSSL_MODULES'] = $modules
    } else {
        [void]$process.StartInfo.EnvironmentVariables.Remove('OPENSSL_MODULES')
    }
    if (-not $process.Start()) { throw 'Installed OpenSSL runtime could not start' }
    $process.WaitForExit()
    if ($process.ExitCode -ne 0 -or
        $process.StandardOutput.ReadToEnd() -cnotmatch '^OpenSSL ') {
        throw 'Installed OpenSSL runtime is not self-contained'
    }
}
