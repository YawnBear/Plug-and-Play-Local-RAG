[CmdletBinding()]
param(
    [string]$ConnectorRoot = $PSScriptRoot,
    [string]$HostsPath = "$env:SystemRoot\System32\drivers\etc\hosts",
    [string]$StateRoot = "$env:ProgramData\LocalRAG-ClientConnector",
    [ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedCaSha256,
    [switch]$Plan, [switch]$SkipBrowser, [switch]$SkipNetworkVerification
)
Set-StrictMode -Version Latest; $ErrorActionPreference = 'Stop'
$HostName = 'rag.home.arpa'; $BeginMarker = '# BEGIN LOCAL-RAG TEAM CONNECTOR'; $EndMarker = '# END LOCAL-RAG TEAM CONNECTOR'

function Assert-Rfc1918([string]$Value) {
    $ip = $null
    if (-not [Net.IPAddress]::TryParse($Value, [ref]$ip) -or $ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'Connector IPv4 is invalid.' }
    $b = $ip.GetAddressBytes(); if (-not (($b[0] -eq 10) -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or ($b[0] -eq 192 -and $b[1] -eq 168))) { throw 'Connector IPv4 is not RFC1918 private.' }; $ip.ToString()
}
function Get-Inventory([string]$Root) {
    $resolvedPath = Resolve-Path -LiteralPath $Root
    if ($resolvedPath.Provider.Name -cne 'FileSystem') { throw 'Connector root must use the filesystem provider.' }
    $resolved = [IO.Path]::GetFullPath($resolvedPath.ProviderPath); $rootPrefix = $resolved.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar; $path = Join-Path $resolved 'inventory.json'; if (-not (Test-Path -LiteralPath $path)) { throw 'Connector inventory is missing.' }
    try { $m = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json } catch { throw 'Connector inventory is invalid.' }
    if ($m.version -ne 1 -or -not $m.files -or [string]$m.tree_sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Connector inventory schema is invalid.' }
    $actual=@(); $seen=@{}
    foreach ($f in Get-ChildItem -LiteralPath $resolved -Recurse -Force) {
        if ($f.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Connector reparse point is not allowed: $($f.FullName)" }; if ($f.PSIsContainer) { continue }
        $fullPath=[IO.Path]::GetFullPath($f.FullName); if (-not $fullPath.StartsWith($rootPrefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Connector file escapes its root.' }
        $rel=$fullPath.Substring($rootPrefix.Length).Replace('\','/'); if ($rel -ieq 'inventory.json') { continue }; $key=$rel.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { throw "Connector case collision: $rel" }; $seen[$key]=$true
        if ($rel -match '(?i)(private[_-]?key|secret|\.pfx$|\.p12$|\.key$)') { throw "Connector private material is not allowed: $rel" }
        $bytes=[IO.File]::ReadAllBytes($fullPath); if ([Text.Encoding]::ASCII.GetString($bytes) -match '-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----') { throw 'Connector private material is not allowed.' }
        $actual += [pscustomobject]@{path=$rel;length=$bytes.Length;sha256=(Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()}
    }
    $required=@('connector.json','rag-local-ca.cer','Install-RagTeamConnector.ps1','Uninstall-RagTeamConnector.ps1','Install-RagTeamConnector.cmd','Uninstall-RagTeamConnector.cmd','Connect-to-Local-RAG.cmd','Disconnect-from-Local-RAG.cmd'); $manifest=((@($m.files|% path|Sort-Object)-join "`n")); if ($manifest -cne (($required|Sort-Object)-join "`n")) { throw 'Connector inventory file set is not exact.' }
    $expected=@($m.files|Sort-Object path); $actual=@($actual|Sort-Object path); if ($expected.Count -ne $actual.Count) { throw 'Connector inventory contains extra or missing files.' }
    for ($i=0;$i -lt $actual.Count;$i++) { if ($expected[$i].path -cne $actual[$i].path -or [int64]$expected[$i].length -ne $actual[$i].length -or $expected[$i].sha256 -cne $actual[$i].sha256) { throw "Connector inventory mismatch: $($actual[$i].path)" } }
    $lines=@($actual|%{"$($_.path)|$($_.length)|$($_.sha256)"}); $tree=([BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash([Text.Encoding]::UTF8.GetBytes(($lines -join "`n")+"`n")))).Replace('-','').ToLowerInvariant(); if ($tree -cne [string]$m.tree_sha256) { throw 'Connector tree hash mismatch.' }; $m
}
function Write-AtomicText([string]$Path,[string]$Text) { $tmp="$Path.$([guid]::NewGuid().ToString('N')).tmp"; [IO.File]::WriteAllText($tmp,$Text,[Text.UTF8Encoding]::new($false)); Move-Item -LiteralPath $tmp -Destination $Path -Force }
function Read-Text([byte[]]$Bytes) { if ($Bytes.Length -ge 2 -and $Bytes[0]-eq 0xff -and $Bytes[1]-eq 0xfe) { return [Text.Encoding]::Unicode.GetString($Bytes,2,$Bytes.Length-2) }; if ($Bytes.Length -ge 3 -and $Bytes[0]-eq 0xef -and $Bytes[1]-eq 0xbb -and $Bytes[2]-eq 0xbf) { return [Text.Encoding]::UTF8.GetString($Bytes,3,$Bytes.Length-3) }; [Text.Encoding]::UTF8.GetString($Bytes) }
function Test-ManagedBlock([string]$Value,[string]$Address) { return [regex]::IsMatch($Value,'\A'+[regex]::Escape($BeginMarker)+'\r?\n'+[regex]::Escape($Address)+'[ \t]+'+[regex]::Escape($HostName)+'\r?\n'+[regex]::Escape($EndMarker)+'\z',[Text.RegularExpressions.RegexOptions]::IgnoreCase) }

$resolvedConnectorRoot = Resolve-Path -LiteralPath $ConnectorRoot
if ($resolvedConnectorRoot.Provider.Name -cne 'FileSystem') { throw 'Connector root must use the filesystem provider.' }
$ConnectorRoot = [IO.Path]::GetFullPath($resolvedConnectorRoot.ProviderPath)
$meta=Get-Inventory $ConnectorRoot; $metadata=Get-Content -LiteralPath (Join-Path $ConnectorRoot 'connector.json') -Raw|ConvertFrom-Json
$fields=@($metadata.PSObject.Properties.Name|Sort-Object); $requiredMeta=@('ca_sha256','ca_subject','ca_thumbprint','connector_generation','host','installation_id','lan_ipv4','unsigned_connector','version')|Sort-Object
if (($fields -join ',') -cne ($requiredMeta -join ',') -or $metadata.version -ne 1 -or [string]$metadata.host -cne $HostName -or [string]$metadata.ca_subject -cne 'CN=Local RAG Private CA' -or [string]$metadata.ca_thumbprint -notmatch '^[0-9A-Fa-f]{40}$' -or [string]$metadata.ca_sha256 -cnotmatch '^[0-9a-f]{64}$' -or -not $metadata.unsigned_connector -or [int]$metadata.connector_generation -lt 1) { throw 'Connector metadata schema or expected CA contract is invalid.' }
$installationId=([guid]::Parse([string]$metadata.installation_id)).ToString('D'); $ip=Assert-Rfc1918 ([string]$metadata.lan_ipv4); $certPath=Join-Path $ConnectorRoot 'rag-local-ca.cer'; $cert=[Security.Cryptography.X509Certificates.X509Certificate2]::new($certPath); $certSha=(Get-FileHash -LiteralPath $certPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($cert.HasPrivateKey -or $cert.Subject -cne [string]$metadata.ca_subject -or $cert.Thumbprint.ToUpperInvariant() -cne ([string]$metadata.ca_thumbprint).ToUpperInvariant() -or $certSha -cne [string]$metadata.ca_sha256) { throw 'Connector certificate does not match its metadata.' }; $basic=$cert.Extensions|?{$_.Oid.Value -eq '2.5.29.19'}; if (-not $basic -or -not ([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]$basic).CertificateAuthority) { throw 'Connector certificate is not a CA.' }
$expectedBlock="$BeginMarker`r`n$ip`t$HostName`r`n$EndMarker"; $hostBytes=if(Test-Path -LiteralPath $HostsPath){[IO.File]::ReadAllBytes($HostsPath)}else{[byte[]]@()}; $hostText=Read-Text $hostBytes
$matches=[regex]::Matches($hostText,[regex]::Escape($BeginMarker)+'.*?'+[regex]::Escape($EndMarker),[Text.RegularExpressions.RegexOptions]::Singleline); if($matches.Count -gt 1){throw 'Multiple managed hosts blocks are not allowed.'}
$outside=[regex]::Replace($hostText,[regex]::Escape($BeginMarker)+'.*?'+[regex]::Escape($EndMarker),'',[Text.RegularExpressions.RegexOptions]::Singleline); if([regex]::IsMatch($outside,'(?im)^\s*(?:\d{1,3}\.){3}\d{1,3}\s+rag\.home\.arpa(?:\s|#|$)')){throw 'An unmanaged rag.home.arpa hosts entry already exists.'}
$statePath=Join-Path $StateRoot 'state.json'; $state=if(Test-Path -LiteralPath $statePath){try{Get-Content -LiteralPath $statePath -Raw|ConvertFrom-Json}catch{throw 'Managed connector state is invalid.'}}else{$null}; $oldState=$state
if($matches.Count -eq 1 -and -not $state){throw 'Managed hosts block has no managed ledger.'}
$generation=[int]$metadata.connector_generation; $upgrade=$false
if($state){if([string]$state.installation_id -cne $installationId){throw 'Connector installation ID mismatch.'}; if($generation -lt [int]$state.connector_generation){throw 'Connector generation is older than the installed generation.'}; if($generation -eq [int]$state.connector_generation -and [string]$state.certificate_sha256 -cne $certSha){throw 'Equal connector generation has mismatched content.'}; $upgrade=$generation -gt [int]$state.connector_generation}
if($upgrade){$oldAddress=([regex]::Match([string]$state.block,'(?m)^((?:\d{1,3}\.){3}\d{1,3})\s+rag\.home\.arpa$')).Groups[1].Value;if([string]::IsNullOrWhiteSpace($oldAddress)-or $matches.Count-ne 1 -or -not(Test-ManagedBlock $matches[0].Value $oldAddress)){throw 'Managed hosts block drift detected; refusing generation upgrade.'}}
if($matches.Count -eq 1 -and -not $upgrade -and -not (Test-ManagedBlock $matches[0].Value $ip)){throw 'Managed hosts block drift detected.'}; $already=($state -and -not $upgrade -and $matches.Count -eq 1 -and (Test-ManagedBlock $matches[0].Value $ip))
$planResult=[pscustomobject]@{Host=$HostName;LanIPv4=$ip;InstallationId=$installationId;ConnectorGeneration=$generation;CertificateSha256=$certSha;HostsPath=$HostsPath;StateRoot=$StateRoot;AlreadyConfigured=[bool]$already;Upgrade=[bool]$upgrade;Actions=@('validate exact connector inventory','confirm the host-displayed CA SHA-256','install CA in LocalMachine Root','atomically add or update managed hosts block','verify DNS and HTTPS','open https://rag.home.arpa')}; if($Plan){$planResult|ConvertTo-Json -Depth 5;return}
if([string]::IsNullOrWhiteSpace($ExpectedCaSha256)){$ExpectedCaSha256=(Read-Host 'Type the CA SHA-256 shown independently on the Local RAG host').Trim().ToLowerInvariant()}; if($ExpectedCaSha256.ToLowerInvariant() -cne $certSha){throw 'The out-of-band CA SHA-256 confirmation does not match.'}; if(-not([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Team connector installation must run elevated.'}
New-Item -ItemType Directory -Path $StateRoot -Force|Out-Null; $certAdded=$false; $changed=$false; $newText=$hostText; $oldThumb=if($state){([string]$state.certificate_thumbprint).ToUpperInvariant()}else{$null}
try { $store=New-Object Security.Cryptography.X509Certificates.X509Store('Root','LocalMachine');$store.Open('ReadWrite');try{$found=@($store.Certificates|?{$_.Thumbprint -eq $cert.Thumbprint});if($found.Count -eq 0){$store.Add($cert);$certAdded=$true}elseif(@($found|?{$_.Subject -cne $cert.Subject}).Count -gt 0){throw 'Certificate thumbprint is already used by a different subject.'}}finally{$store.Close()}
    if(-not $already){$newText=if($matches.Count -eq 1){$hostText.Remove($matches[0].Index,$matches[0].Length).Insert($matches[0].Index,$expectedBlock)}else{$hostText.TrimEnd("`r","`n")+"`r`n`r`n"+$expectedBlock+"`r`n"}; Write-AtomicText $HostsPath $newText;$changed=$true}
    $priorBytes=if($state){[Convert]::FromBase64String([string]$state.prior_bytes_base64)}else{$hostBytes}; $priorHash=if($state){[string]$state.prior_sha256}else{([BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash($hostBytes))).Replace('-','').ToLowerInvariant()}; $postHash=([BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash([Text.Encoding]::UTF8.GetBytes($newText)))).Replace('-','').ToLowerInvariant()
    [ordered]@{version=1;installation_id=$installationId;connector_generation=$generation;hosts_path=[IO.Path]::GetFullPath($HostsPath);prior_sha256=$priorHash;prior_bytes_base64=[Convert]::ToBase64String($priorBytes);hosts_post_sha256=$postHash;certificate_sha256=$certSha;certificate_thumbprint=$cert.Thumbprint.ToUpperInvariant();certificate_subject=$cert.Subject;block=$expectedBlock}|ConvertTo-Json|Set-Content -LiteralPath $statePath -Encoding UTF8
    if(-not $SkipNetworkVerification){$resolved=[Net.Dns]::GetHostAddresses($HostName)|?{$_.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork};if(-not($resolved|?{$_.ToString()-eq $ip})){throw 'Resolver did not return the configured LAN address.'};$response=Invoke-WebRequest -Uri "https://$HostName" -UseBasicParsing -TimeoutSec 15;if($response.StatusCode -lt 200 -or $response.StatusCode -ge 500){throw 'HTTPS verification returned an invalid status.'}}
    if($upgrade -and $oldThumb -and $oldThumb -ne $cert.Thumbprint.ToUpperInvariant()){$oldStore=New-Object Security.Cryptography.X509Certificates.X509Store('Root','LocalMachine');$oldStore.Open('ReadWrite');try{foreach($oldCert in @($oldStore.Certificates|?{$_.Thumbprint.ToUpperInvariant()-eq$oldThumb})){if($oldCert.Subject-cne[string]$state.certificate_subject){throw 'Previous connector certificate drift detected.'};$oldStore.Remove($oldCert)}}finally{$oldStore.Close()}}
    if(-not $SkipBrowser){Start-Process "https://$HostName"}
}catch{if($changed){try{Write-AtomicText $HostsPath $hostText}catch{}};if(Test-Path -LiteralPath $statePath){if($oldState){$oldState|ConvertTo-Json|Set-Content -LiteralPath $statePath -Encoding UTF8}else{Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue}};if($certAdded){try{$s=New-Object Security.Cryptography.X509Certificates.X509Store('Root','LocalMachine');$s.Open('ReadWrite');foreach($c in @($s.Certificates|?{$_.Thumbprint-eq$cert.Thumbprint})){$s.Remove($c)};$s.Close()}catch{}};throw}
$planResult|ConvertTo-Json -Depth 5
