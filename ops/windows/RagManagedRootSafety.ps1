$ErrorActionPreference = 'Stop'

function Assert-RagPathComponentsNotReparse {
    param([Parameter(Mandatory)][string]$Path)
    $current = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
    foreach ($segment in @(
        [IO.Path]::GetFullPath($Path).Substring($current.Length).Split(
            [char[]]@('\','/'),
            [StringSplitOptions]::RemoveEmptyEntries
        )
    )) {
        $current = Join-Path $current $segment
        if (-not (Test-Path -LiteralPath $current)) { continue }
        $item = Get-Item -LiteralPath $current -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Managed-root path contains a reparse point: $current"
        }
    }
}

function Assert-RagFreshManagedRoots {
    param(
        [Parameter(Mandatory)][string]$ProgramDataRoot,
        [Parameter(Mandatory)][string]$ProgramFilesRoot,
        [string[]]$AllowedExistingProgramDataRelativePath = @()
    )
    foreach ($root in @($ProgramDataRoot,$ProgramFilesRoot)) {
        Assert-RagPathComponentsNotReparse -Path $root
        if (-not (Test-Path -LiteralPath $root)) { continue }
        if ($root -ceq $ProgramDataRoot -and $AllowedExistingProgramDataRelativePath.Count -gt 0) {
            $resolvedRoot = (Resolve-Path -LiteralPath $root).Path.TrimEnd('\')
            foreach ($entry in Get-ChildItem -LiteralPath $resolvedRoot -Recurse -Force) {
                Assert-RagPathComponentsNotReparse -Path $entry.FullName
                $relative = $entry.FullName.Substring($resolvedRoot.Length).TrimStart('\')
                $allowed = @($AllowedExistingProgramDataRelativePath | Where-Object {
                    $candidate = $_.Trim('\')
                    $relative -ceq $candidate -or
                        $relative.StartsWith($candidate + '\', [StringComparison]::OrdinalIgnoreCase) -or
                        $candidate.StartsWith($relative + '\', [StringComparison]::OrdinalIgnoreCase)
                })
                if ($allowed.Count -eq 0) {
                    throw "Managed root contains unexpected pre-existing content: $($entry.FullName)"
                }
            }
            continue
        }
        if (Test-Path -LiteralPath $root) {
            throw "Managed root must not preexist: $root"
        }
    }
}

function Assert-RagUninstallManagedRoot {
    param(
        [Parameter(Mandatory)][string]$Root,
        [string[]]$ManagedRelativePath = @(),
        [string[]]$PreservedRelativePath = @()
    )
    if (-not (Test-Path -LiteralPath $Root)) { return }
    Assert-RagPathComponentsNotReparse -Path $Root
    $resolvedRoot = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
    foreach ($entry in Get-ChildItem -LiteralPath $resolvedRoot -Recurse -Force) {
        Assert-RagPathComponentsNotReparse -Path $entry.FullName
        $relative = $entry.FullName.Substring($resolvedRoot.Length).TrimStart('\')
        $isManaged = @($ManagedRelativePath | Where-Object {
            $candidate = $_.Trim('\')
            $relative -ceq $candidate -or
                $relative.StartsWith($candidate + '\', [StringComparison]::OrdinalIgnoreCase) -or
                $candidate.StartsWith($relative + '\', [StringComparison]::OrdinalIgnoreCase)
        }).Count -gt 0
        $isPreserved = @($PreservedRelativePath | Where-Object {
            $candidate = $_.Trim('\')
            $relative -ceq $candidate -or
                $relative.StartsWith($candidate + '\', [StringComparison]::OrdinalIgnoreCase) -or
                $candidate.StartsWith($relative + '\', [StringComparison]::OrdinalIgnoreCase)
        }).Count -gt 0
        if (-not $isManaged -and -not $isPreserved) {
            throw "Managed root contains an unexpected uninstall target: $($entry.FullName)"
        }
    }
}
