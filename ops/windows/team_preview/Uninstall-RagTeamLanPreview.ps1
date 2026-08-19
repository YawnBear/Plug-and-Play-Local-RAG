[CmdletBinding(SupportsShouldProcess,ConfirmImpact='High')]
param([switch]$Plan)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagTeamLanPreview.psm1') -Force

$programData='C:\ProgramData\LocalRAG'
$programFiles='C:\Program Files\LocalRAG'
$current=Join-Path $programFiles 'current'
$accounts=@('RagProxySvc','RagWebSvc','RagApiSvc','RagIngestionSvc','RagDeletionSvc','RagInferenceSvc','RagOcrSvc')
$preserved=@(
    'data\postgres','data\rustfs','secrets\team-preview-secrets.json',
    'secrets\stores.env','state\team-preview-provisioning.json'
)
if($Plan){
    [ordered]@{
        result='plan';mutations_performed=$false;profile='team_lan_preview_unsigned'
        removes=@('RagSupervisor','firewall','managed host entry','exact private CA trust','seven service accounts','installed application and recreatable runtime state')
        preserves=$preserved;external_verified_backups_preserved=$true
    }|ConvertTo-Json -Depth 4
    return
}

$state=Assert-RagTeamPreviewProfileState -ProgramDataRoot $programData -Expected InstalledPreview
$allowedTop=@('backup-work','certificates','connectors','data','environments','identity-secrets','installed-deployment.json','profiles','repairs','restore-verification','secrets','state','team-preview-state.json','updates','work')
$unexpected=@(Get-ChildItem -LiteralPath $programData -Force|Where-Object{$_.Name -cnotin $allowedTop})
if($unexpected.Count){throw "Uninstall refuses unexpected ProgramData content: $($unexpected.Name -join ', ')"}
foreach($path in @($programData,$programFiles,$current)){
    if(Test-Path -LiteralPath $path){
        $item=Get-Item -LiteralPath $path -Force
        if($item.Attributes-band[IO.FileAttributes]::ReparsePoint){throw "Uninstall refuses a reparse-point managed path: $path"}
    }
}
$activeUpdate=Join-Path $programData 'updates\team-preview-update.json'
if(Test-Path -LiteralPath $activeUpdate){
    $update=Get-Content -Raw -LiteralPath $activeUpdate|ConvertFrom-Json
    if([string]$update.state -cnotin @('committed','rolled_back')){throw 'Uninstall refuses a nonterminal update journal.'}
}
$hosts=Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$hostsContent=[IO.File]::ReadAllText($hosts,[Text.UTF8Encoding]::new($false,$true))
$begin='# BEGIN LOCAL RAG TEAM PREVIEW HOST';$end='# END LOCAL RAG TEAM PREVIEW HOST'
$pattern="(?ms)^$([regex]::Escape($begin))\r?\n(.*?)\r?\n$([regex]::Escape($end))(?:\r?\n)?"
$blocks=[regex]::Matches($hostsContent,$pattern)
if($blocks.Count -ne 1 -or $blocks[0].Groups[1].Value.Trim() -cne "$($state.local_address) rag.home.arpa"){
    throw 'Managed host entry is missing or does not match installed preview state.'
}
$certificate=Join-Path $programData 'certificates\local-rag-ca.crt'
if(-not(Test-Path -LiteralPath $certificate -PathType Leaf)){throw 'Installed preview CA certificate is missing.'}
$ca=[Security.Cryptography.X509Certificates.X509Certificate2]::new($certificate)
$trusted=@(Get-ChildItem Cert:\LocalMachine\Root|Where-Object Thumbprint -ceq $ca.Thumbprint)
if($trusted.Count -ne 1){throw 'Installed preview CA trust does not match exactly one certificate.'}
foreach($account in $accounts){
    if($null -eq (Get-LocalUser -Name $account -ErrorAction SilentlyContinue)){throw "Managed service account is missing: $account"}
}
if(-not(Test-Path -LiteralPath (Join-Path $current 'caddy.exe') -PathType Leaf)){throw 'Installed Caddy executable is missing.'}
if(-not $PSCmdlet.ShouldProcess('Local RAG unsigned Team/LAN preview','Uninstall application while preserving PostgreSQL, RustFS, protected store secrets, and verified backups')){return}

$service=Get-Service RagSupervisor -ErrorAction Stop
& sc.exe config RagSupervisor start= disabled|Out-Null
if($LASTEXITCODE -ne 0){throw 'Could not disable RagSupervisor startup.'}
if($service.Status -ne 'Stopped'){Stop-Service RagSupervisor -Force}
$deadline=[DateTimeOffset]::UtcNow.AddSeconds(45)
do{$service=Get-Service RagSupervisor;Start-Sleep -Milliseconds 250}until($service.Status -eq 'Stopped' -or [DateTimeOffset]::UtcNow -ge $deadline)
if($service.Status -ne 'Stopped'){throw 'RagSupervisor did not stop.'}

$owners=@{}
foreach($account in $accounts){$owners[$account]=$true}
$ownedProcesses=[Collections.Generic.List[int]]::new()
foreach($process in @(Get-CimInstance Win32_Process)){
    $owner=Invoke-CimMethod -InputObject $process -MethodName GetOwner -ErrorAction SilentlyContinue
    if($null -ne $owner -and $owner.ReturnValue -eq 0 -and $owner.Domain -ceq $env:COMPUTERNAME -and $owners.ContainsKey([string]$owner.User)){$ownedProcesses.Add([int]$process.ProcessId)}
}
if($ownedProcesses.Count){throw "Managed service-account processes remain after stop: $($ownedProcesses -join ',')"}

& (Join-Path $PSScriptRoot 'Set-RagTeamPreviewFirewall.ps1') -Action Remove `
    -LocalAddress ([string]$state.local_address) -PinnedCaddyProgram (Join-Path $current 'caddy.exe') -Confirm:$false
$updatedHosts=[regex]::Replace($hostsContent,$pattern,'')
$temporaryHosts="$hosts.team-preview-uninstall-$([guid]::NewGuid().ToString('N')).tmp"
try{
    [IO.File]::WriteAllText($temporaryHosts,$updatedHosts,[Text.UTF8Encoding]::new($false))
    [IO.File]::Replace($temporaryHosts,$hosts,$null,$true)
}finally{if(Test-Path -LiteralPath $temporaryHosts){[IO.File]::Delete($temporaryHosts)}}

$storesEnvironment=Join-Path $programData 'secrets\stores.env'
$compose=Join-Path $current 'ops\windows\team_preview\compose.team-preview.yaml'
$installationId=[string]$state.installation_id
$project='localrag-team-'+$installationId.Replace('-','').Substring(0,12)
$docker=(Get-Command docker.exe -ErrorAction Stop).Source
& $docker compose -p $project --env-file $storesEnvironment -f $compose down --remove-orphans
if($LASTEXITCODE -ne 0){throw 'Could not stop Team preview data-store containers; uninstall halted with data intact.'}

& sc.exe delete RagSupervisor|Out-Null
if($LASTEXITCODE -ne 0){throw 'Could not delete RagSupervisor.'}
foreach($account in $accounts){
    & (Join-Path (Split-Path $PSScriptRoot -Parent) 'Set-RagAccountRights.ps1') -Account $account -Action RemoveRequired|Out-Null
    Remove-LocalUser -Name $account
}
$trusted|Remove-Item -Force

if(Test-Path -LiteralPath $programFiles){[IO.Directory]::Delete($programFiles,$true)}
foreach($path in @('certificates','connectors','environments','identity-secrets','profiles','repairs','updates','work','backup-work','restore-verification','installed-deployment.json','team-preview-state.json')){
    $target=Join-Path $programData $path
    if(Test-Path -LiteralPath $target){
        $item=Get-Item -LiteralPath $target -Force
        if($item.PSIsContainer){[IO.Directory]::Delete($target,$true)}else{[IO.File]::Delete($target)}
    }
}
foreach($path in @('local-rag-ca.key','local-rag-ca.srl','rag.home.arpa.key','api-loopback.key','caddy-api-client.key','supervisor-api-client.key')){
    $target=Join-Path (Join-Path $programData 'secrets') $path
    if(Test-Path -LiteralPath $target){[IO.File]::Delete($target)}
}
foreach($path in @('inference','ocr','rustfs-bootstrap')){
    $target=Join-Path (Join-Path $programData 'state') $path
    if(Test-Path -LiteralPath $target){[IO.Directory]::Delete($target,$true)}
}
[ordered]@{
    result='uninstalled';profile='team_lan_preview_unsigned';data_preserved=$true
    postgres_data_preserved=$true;rustfs_data_preserved=$true
    store_secrets_preserved=$true;provisioning_journal_preserved=$true
    verified_backups_preserved=$true;preserved_paths=$preserved
}|ConvertTo-Json -Depth 4
