$ErrorActionPreference = 'Stop'

function Assert-RagProcessSetStopped {
    param(
        [Parameter(Mandatory)]
        [AllowNull()]
        [AllowEmptyCollection()]
        [int[]]$ProcessId
    )
    $running = @(
        foreach ($id in @($ProcessId)) {
            if ($id -gt 0 -and $null -ne (Get-Process -Id $id -ErrorAction SilentlyContinue)) {
                $id
            }
        }
    )
    if ($running.Count -gt 0) {
        throw "Supervisor descendant processes remain running: $($running -join ',')"
    }
}

function Get-RagProcessTreeIds {
    param(
        [Parameter(Mandatory)][int]$RootProcessId,
        [object[]]$Snapshot = @(Get-CimInstance Win32_Process)
    )
    $known = [Collections.Generic.HashSet[int]]::new()
    [void]$known.Add($RootProcessId)
    do {
        $added = $false
        foreach ($process in $Snapshot) {
            if ($known.Contains([int]$process.ParentProcessId) -and
                $known.Add([int]$process.ProcessId)) {
                $added = $true
            }
        }
    } while ($added)
    return @($known)
}

function Stop-RagManagedProcesses {
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$AccountName,
        [string]$ServiceName = 'RagSupervisor'
    )
    $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" `
        -ErrorAction SilentlyContinue
    [int[]]$tracked = @()
    if ($null -ne $service -and [int]$service.ProcessId -gt 0) {
        $tracked = @(Get-RagProcessTreeIds -RootProcessId ([int]$service.ProcessId))
    }
    if ($null -ne $service) {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    do {
        try {
            Assert-RagProcessSetStopped -ProcessId $tracked
            $stopped = $true
        } catch {
            $stopped = $false
            Start-Sleep -Milliseconds 250
        }
    } while (-not $stopped -and [DateTime]::UtcNow -lt $deadline)
    Assert-RagProcessSetStopped -ProcessId $tracked

    $accountProcesses = [Collections.Generic.List[int]]::new()
    foreach ($process in @(Get-CimInstance Win32_Process)) {
        $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwner `
            -ErrorAction SilentlyContinue
        if ($owner.ReturnValue -eq 0 -and $owner.Domain -ceq $env:COMPUTERNAME -and
            $AccountName -ccontains $owner.User) {
            $accountProcesses.Add([int]$process.ProcessId)
        }
    }
    Assert-RagProcessSetStopped -ProcessId $accountProcesses.ToArray()
}
