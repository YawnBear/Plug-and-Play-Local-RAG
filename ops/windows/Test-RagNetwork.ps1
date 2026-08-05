[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$PinnedCaddyProgram,
    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$PinnedCaddySha256,
    [Parameter(Mandatory)]
    [string]$PinnedServiceHostProgram,
    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$PinnedServiceHostSha256,
    [Parameter(Mandatory)]
    [string]$PinnedSupervisorPython,
    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9._-]{1,64}$')]
    [string]$DeploymentId,
    [Parameter(Mandatory)]
    [ValidateCount(2, 2)]
    [string[]]$ExpectedLocalAddresses
)

$ErrorActionPreference = 'Stop'
function ConvertTo-RagNormalizedIPAddress {
    param([AllowNull()][object]$Value)
    $parsed = $null
    if ($null -eq $Value -or
        -not [Net.IPAddress]::TryParse([string]$Value, [ref]$parsed)) {
        return $null
    }
    $parsed.ToString()
}

$pinnedProgram = [IO.Path]::GetFullPath($PinnedCaddyProgram)
if ((Get-FileHash -LiteralPath $pinnedProgram -Algorithm SHA256).Hash.ToLowerInvariant() -cne $PinnedCaddySha256) {
    throw 'Pinned Caddy SHA-256 verification failed'
}
$serviceHostProgram = [IO.Path]::GetFullPath($PinnedServiceHostProgram)
$supervisorPython = [IO.Path]::GetFullPath($PinnedSupervisorPython)
if ((Get-FileHash -LiteralPath $serviceHostProgram -Algorithm SHA256).Hash.ToLowerInvariant() -cne
    $PinnedServiceHostSha256) {
    throw 'Pinned RagSupervisor service-host SHA-256 verification failed'
}
$scmService = Get-CimInstance Win32_Service -Filter "Name='RagSupervisor'"
if ($null -eq $scmService -or $scmService.State -cne 'Running' -or
    [int]$scmService.ProcessId -le 0) {
    throw 'RagSupervisor SCM service is not running with a process ID'
}
$serviceHostPid = [int]$scmService.ProcessId
$serviceHostProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$serviceHostPid"
$serviceHostPath = [IO.Path]::GetFullPath($serviceHostProcess.ExecutablePath)
$serviceHostBound = $serviceHostPath -ceq $serviceHostProgram
$parsedExpectedAddresses = @($ExpectedLocalAddresses | ForEach-Object {
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($_, [ref]$parsed)) {
        throw "ExpectedLocalAddresses contains an invalid IP address: $_"
    }
    $parsed
})
$addressFamilies = @($parsedExpectedAddresses |
    ForEach-Object { $_.AddressFamily.ToString() } |
    Sort-Object -Unique)
if (($addressFamilies -join ',') -cne 'InterNetwork,InterNetworkV6') {
    throw 'ExpectedLocalAddresses must contain one configured IPv4 and one IPv6 address'
}
$normalizedExpectedAddresses = @($parsedExpectedAddresses |
    ForEach-Object { $_.ToString() } |
    Sort-Object -Unique)

. (Join-Path $PSScriptRoot 'RagFirewallClassification.ps1')
$listeners = Get-NetTCPConnection -State Listen | ForEach-Object {
    $process = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    [pscustomobject]@{
        address = $_.LocalAddress
        normalized_address = ConvertTo-RagNormalizedIPAddress $_.LocalAddress
        port = $_.LocalPort
        process_name = if ($null -eq $process) { '<unknown>' } else { $process.ProcessName }
        process_path = if ($null -eq $process -or [string]::IsNullOrWhiteSpace($process.Path)) {
            '<unknown>'
        } else {
            [IO.Path]::GetFullPath($process.Path)
        }
        process_id = $_.OwningProcess
    }
}
$profiles = Get-NetFirewallProfile -PolicyStore ActiveStore -Name Private,Public |
    ForEach-Object {
    [pscustomobject]@{
        name = $_.Name
        enabled = [bool]$_.Enabled
        default_inbound_action = $_.DefaultInboundAction.ToString()
    }
}

$rules = Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True `
    -Direction Inbound -Action Allow |
    ForEach-Object {
        $port = $_ | Get-NetFirewallPortFilter
        $application = $_ | Get-NetFirewallApplicationFilter
        $service = $_ | Get-NetFirewallServiceFilter
        $address = $_ | Get-NetFirewallAddressFilter
        $interface = $_ | Get-NetFirewallInterfaceTypeFilter
        [pscustomobject]@{
            name = $_.DisplayName
            package_family_name = $_.PackageFamilyName
            profile = $_.Profile.ToString()
            protocol = $port.Protocol.ToString()
            local_port = (@($port.LocalPort) -join ',')
            program = $application.Program
            service = $service.Service
            local_address = (@($address.LocalAddress) -join ',')
            remote_address = (@($address.RemoteAddress) -join ',')
            interface_type = (@($interface.InterfaceType) -join ',')
            edge_traversal = $_.EdgeTraversalPolicy.ToString()
        }
    }


$unexpected443Listeners = @($listeners | Where-Object {
    $_.address -notin @('127.0.0.1', '::1') -and
    $_.port -eq 443 -and
    (
        $_.process_path -cne $pinnedProgram -or
        $null -eq $_.normalized_address -or
        $_.normalized_address -notin $normalizedExpectedAddresses
    )
})
$exposedInternalListeners = @($listeners | Where-Object {
    $_.address -notin @('127.0.0.1', '::1') -and
    $_.port -in @(3000,8000,8443,8100,8101)
})
$ambientListeners = @($listeners | Where-Object {
    $_.address -notin @('127.0.0.1', '::1') -and
    $_.port -ne 443 -and
    $_.port -notin @(3000,8000,8443,8100,8101)
})
$caddyListeners = @($listeners | Where-Object {
    $_.address -notin @('127.0.0.1', '::1') -and
    $_.port -eq 443 -and
    $_.process_path -ceq $pinnedProgram
})
$observedCaddyAddresses = @($caddyListeners.normalized_address |
    Where-Object { $null -ne $_ } |
    Sort-Object -Unique)
$expectedCaddyAddresses = $normalizedExpectedAddresses
$validProfiles = @($profiles | Where-Object {
    $_.enabled -and $_.default_inbound_action -ceq 'Block'
})
$ragRules = @($rules | Where-Object { $_.name -ceq 'Local RAG HTTPS' })
$validRagRules = @($ragRules | Where-Object {
    $_.profile -ceq 'Private' -and
    $_.protocol -ceq 'TCP' -and
    $_.local_port -ceq '443' -and
    [IO.Path]::GetFullPath($_.program) -ceq $pinnedProgram -and
    $_.service -ceq 'Any' -and
    [string]::IsNullOrWhiteSpace($_.package_family_name) -and
    (
        (@($_.local_address -split ',' | ForEach-Object { $_.Trim() } | Sort-Object) -join ',') -ceq
        (@($ExpectedLocalAddresses | Sort-Object) -join ',')
    ) -and
    $_.remote_address -ceq 'LocalSubnet' -and
    (
        (@($_.interface_type -split ',' | ForEach-Object { $_.Trim() } | Sort-Object) -join ',') -ceq
        'Wired,Wireless'
    ) -and
    $_.edge_traversal -ceq 'Block'
})
$explicit443Rules = @($rules | Where-Object {
    $_.protocol -in @('TCP', 'Any', '256') -and
    (Test-RagPortExplicitlyScopes443 $_.local_port)
})
$ambientAnyPortRules = @($rules | Where-Object {
    $_.protocol -in @('TCP', 'Any', '256') -and
    (Test-RagPortIsAmbientAny $_.local_port)
})
$unsafe443Rules = @($rules | Where-Object {
    (Test-RagRuleCanAdmitPinnedCaddy443 $_ $pinnedProgram) -and
    -not (
        $_.name -ceq 'Local RAG HTTPS' -and
        $_.profile -ceq 'Private' -and $_.protocol -ceq 'TCP' -and
        $_.local_port -ceq '443' -and
        [IO.Path]::GetFullPath($_.program) -ceq $pinnedProgram -and
        $_.service -ceq 'Any' -and $_.remote_address -ceq 'LocalSubnet' -and
        [string]::IsNullOrWhiteSpace($_.package_family_name) -and
        $_.edge_traversal -ceq 'Block' -and
        ((@($_.local_address -split ',' | ForEach-Object { $_.Trim() } | Sort-Object) -join ',') -ceq
            (@($normalizedExpectedAddresses | Sort-Object) -join ',')) -and
        ((@($_.interface_type -split ',' | ForEach-Object { $_.Trim() } | Sort-Object) -join ',') -ceq 'Wired,Wireless')
    )
})
$caddyProcessIds = @($caddyListeners.process_id | Sort-Object -Unique)
$sameCaddyProcess = $caddyProcessIds.Count -eq 1
$caddyProcess = if ($sameCaddyProcess) {
    Get-CimInstance Win32_Process -Filter "ProcessId=$($caddyProcessIds[0])"
} else { $null }
$pythonProcess = if ($null -ne $caddyProcess) {
    Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$caddyProcess.ParentProcessId)"
} else { $null }
$supervisedAncestry = (
    $serviceHostBound -and
    $null -ne $pythonProcess -and
    [IO.Path]::GetFullPath($pythonProcess.ExecutablePath) -ceq $supervisorPython -and
    [int]$pythonProcess.ParentProcessId -eq $serviceHostPid
)
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class RagJobEvidence {
  [DllImport("kernel32.dll", SetLastError=true)]
  static extern IntPtr OpenProcess(uint access, bool inherit, int processId);
  [DllImport("kernel32.dll", SetLastError=true)] static extern
    IntPtr OpenJobObject(uint access, bool inherit, string name);
  [DllImport("kernel32.dll", SetLastError=true)] static extern
    bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  public static bool InNamedJob(int processId, string name) {
    IntPtr process=OpenProcess(0x1000,false,processId);
    if(process==IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    IntPtr job=OpenJobObject(0x0004,false,name);
    if(job==IntPtr.Zero) { CloseHandle(process); throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error()); }
    try { bool result; if(!IsProcessInJob(process,job,out result))
      throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error()); return result;
    } finally { CloseHandle(job); CloseHandle(process); }
  }
}
'@
$jobName = "Global\LocalRagSupervisorJob-$DeploymentId"
$caddyJobMembership = $sameCaddyProcess -and
    [RagJobEvidence]::InNamedJob($caddyProcessIds[0], $jobName)

$resultObject = [pscustomobject]@{
    schema_version = 1
    mode = 'read_only'
    result = if (
        $unexpected443Listeners.Count -eq 0 -and
        $exposedInternalListeners.Count -eq 0 -and
        ($observedCaddyAddresses -join ',') -ceq ($expectedCaddyAddresses -join ',') -and
        $profiles.Count -eq 2 -and
        $validProfiles.Count -eq 2 -and
        $ragRules.Count -eq 1 -and
        $validRagRules.Count -eq 1 -and
        $unsafe443Rules.Count -eq 0 -and
        $sameCaddyProcess -and
        $supervisedAncestry -and
        $caddyJobMembership
    ) { 'pass' } else { 'fail' }
    listeners = $listeners
    inbound_allow_rules = $rules
    firewall_profiles = $profiles
    findings = @(
        if ($unexpected443Listeners.Count -gt 0) {
            'unexpected non-loopback TCP 443 listener, owner, or address exists'
        }
        if ($exposedInternalListeners.Count -gt 0) {
            'Local RAG internal service port is bound non-loopback'
        }
        if (($observedCaddyAddresses -join ',') -cne ($expectedCaddyAddresses -join ',')) {
            'pinned Caddy TCP 443 listeners do not exactly match configured IPv4 and IPv6 addresses'
        }
        if ($profiles.Count -ne 2 -or $validProfiles.Count -ne 2) {
            'Private and Public firewall profiles must be enabled with default inbound Block'
        }
        if ($ragRules.Count -ne 1) {
            'exactly one enabled Local RAG HTTPS allow rule is required'
        }
        if ($validRagRules.Count -ne 1) {
            'Local RAG HTTPS rule does not match the pinned scoped contract'
        }
        if ($unsafe443Rules.Count -gt 0) {
            'an enabled inbound allow rule can admit pinned Caddy TCP 443 outside the exact private Local RAG scope'
        }
        if (-not $sameCaddyProcess) {
            'IPv4 and IPv6 TCP 443 listeners are not owned by one Caddy PID'
        }
        if (-not $supervisedAncestry) {
            'Caddy parent is not the expected Python supervisor process'
        }
        if (-not $caddyJobMembership) {
            'Caddy is not a member of a Windows Job Object'
        }
    )
    warnings = @(
        if ($ambientAnyPortRules.Count -gt 0) {
            'ambient enabled Any-port inbound allow rules exist; actual TCP 443 ownership remains strictly gated'
        }
        if ($ambientListeners.Count -gt 0) {
            'unrelated non-loopback listeners exist outside Local RAG ports'
        }
    )
    ambient_any_port_rules = $ambientAnyPortRules
    unsafe_caddy_443_rules = $unsafe443Rules
    ambient_unrelated_listeners = $ambientListeners
    caddy_process_id = if ($sameCaddyProcess) { $caddyProcessIds[0] } else { $null }
    service_host_process_id = $serviceHostPid
    supervisor_process_id = if ($null -ne $pythonProcess) { [int]$pythonProcess.ProcessId } else { $null }
    service_host_path_hash_bound = $serviceHostBound
    named_job = $jobName
    same_caddy_pid = $sameCaddyProcess
    supervised_ancestry = $supervisedAncestry
    job_membership = $caddyJobMembership
    second_lan_device = 'unverified'
}
$resultObject | ConvertTo-Json -Depth 8
if ($resultObject.result -cne 'pass') {
    exit 1
}
