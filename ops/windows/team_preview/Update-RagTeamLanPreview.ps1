[CmdletBinding(SupportsShouldProcess,ConfirmImpact='High')]
param(
    [Parameter(Mandatory)][string]$CandidateRoot,
    [string]$BackupRoot,
    [switch]$Plan,
    [ValidateSet('none','after_backup_verified','after_candidate_staged_verified','after_release_switched','after_candidate_head_verified','after_migration_upgrade','after_candidate_started')]
    [string]$FaultPoint='none',
    [string]$SyntheticTestToken
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'RagTeamLanPreview.psm1') -Force
$programData='C:\ProgramData\LocalRAG';$programFiles='C:\Program Files\LocalRAG';$current=Join-Path $programFiles 'current'
$statePath=Join-Path $programData 'team-preview-state.json';$candidate=(Resolve-Path -LiteralPath $CandidateRoot).Path.TrimEnd('\')
$candidateContractPath=Join-Path $candidate 'team-preview-release.json'
$inventory=Test-RagTeamPreviewInventory -Root $candidate
$contract=Get-Content -Raw -LiteralPath $candidateContractPath|ConvertFrom-Json
if([string]$contract.profile -cne 'team_lan_preview_unsigned' -or [string]$contract.payload_state -cne 'assembled_unsigned' -or
    [string]$contract.authenticity -cne 'unverified_unsigned' -or $contract.automatic_updates_available -isnot [bool] -or $contract.automatic_updates_available){throw 'Manual preview update requires an assembled unsigned Team/LAN preview candidate.'}
$candidateRevision=[string]$contract.alembic_revision
if($candidateRevision -cnotmatch '^[0-9]{4}_[a-z0-9_]+$'){throw 'Candidate Alembic revision identifier is invalid.'}
if($FaultPoint -cne 'none'){
    if([string]::IsNullOrWhiteSpace($SyntheticTestToken) -or $env:RAG_TEAM_PREVIEW_UPDATE_TEST_SEAM -cne $SyntheticTestToken){throw 'Fault injection is restricted to an explicitly bound synthetic test run.'}
}
if($Plan){
    [ordered]@{result='plan';mutations_performed=$false;profile='team_lan_preview_unsigned';candidate_tree_sha256=$inventory.tree_sha256;
        candidate_alembic_revision=$candidateRevision;backup_root=if([string]::IsNullOrWhiteSpace($BackupRoot)){'<required-attended-external-root>'}else{[IO.Path]::GetFullPath($BackupRoot)};
        journal_states=@('prepared','service_stopped','backup_verified','candidate_staged_verified','release_switched','candidate_head_verified','migration_started','migration_verified','candidate_started','graph_ready','verified','committed','rollback_started','rollback_data_restored','rolled_back','recovery_failed');
        schema_upgrade='exact packaged Alembic head under restore-verified transaction';blocked_changes=@('storage_contract','caddy','service_host');rollback='prior release, state/config, data backup, and original service state';verified_backup_retained=$true}|ConvertTo-Json -Depth 5
    return
}
$state=Assert-RagTeamPreviewProfileState -ProgramDataRoot $programData -Expected InstalledPreview
if([string]$state.alembic_revision -cnotmatch '^[0-9]{4}_[a-z0-9_]+$'){throw 'Installed Alembic revision identifier is invalid.'}
# "Alembic revisions must be identical" was the former unsafe gate; differing
# revisions now require the restore-verified migration transaction below.
if([string]$inventory.tree_sha256 -ceq [string]$state.release_tree_sha256){throw 'Candidate preview release is already installed.'}
if([string]::IsNullOrWhiteSpace($BackupRoot)){$BackupRoot=Read-Host 'Enter an existing protected backup folder outside Local RAG application/data paths'}
if([string]::IsNullOrWhiteSpace($BackupRoot)){throw 'A protected external backup root is required.'}

function Get-Hash([string]$Path){if(-not(Test-Path -LiteralPath $Path -PathType Leaf)){throw "Update compatibility contract is missing: $Path"};(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function Assert-UnchangedPath([string]$OldRoot,[string]$NewRoot,[string]$Relative,[string]$Description){$old=Get-Hash (Join-Path $OldRoot $Relative);$new=Get-Hash (Join-Path $NewRoot $Relative);if($old -cne $new){throw "Preview update blocks $Description changes: $Relative"}}
function Assert-StagedReleaseInventory([string]$Stage,$PayloadInventory){
    $expected=@($PayloadInventory.files|Where-Object{[string]$_.path -clike 'release/*'}|ForEach-Object{[pscustomobject]@{path=([string]$_.path).Substring(8);sha256=[string]$_.sha256;size=[int64]$_.size}}|Sort-Object path -CaseSensitive)
    $actual=@(Get-RagTeamPreviewFiles -Root $Stage)
    if($expected.Count -eq 0 -or $actual.Count -ne $expected.Count){throw 'Privileged candidate stage does not match the inventoried release file set.'}
    for($index=0;$index -lt $actual.Count;$index++){
        if([string]$actual[$index].path -cne [string]$expected[$index].path -or [string]$actual[$index].sha256 -cne [string]$expected[$index].sha256 -or [int64]$actual[$index].size -ne [int64]$expected[$index].size){throw "Privileged candidate stage inventory mismatch: $($actual[$index].path)"}
    }
    $expectedTree=Get-RagTeamPreviewTreeSha256 -Files $expected;$actualTree=Get-RagTeamPreviewTreeSha256 -Files $actual
    if($actualTree -cne $expectedTree){throw 'Privileged candidate stage tree digest is not bound to the payload inventory.'}
    return $actualTree
}
function Get-PreviewStartupDiagnosticSummary([string]$ProgramDataRoot,[string]$ProfilesRoot){
    $candidates=@([pscustomobject]@{path=(Join-Path $ProgramDataRoot 'supervisor-startup-failure.json');service=$null})
    foreach($service in @('api','ingestion','deletion','inference','ocr')){$candidates += [pscustomobject]@{path=(Join-Path $ProfilesRoot "$service\tmp\startup-failure.json");service=$service}}
    $allowed=@('caddy','web','api','ingestion','deletion','inference','ocr');$summaries=[Collections.Generic.List[string]]::new()
    foreach($candidateDiagnostic in $candidates){
        $item=Get-Item -LiteralPath $candidateDiagnostic.path -Force -ErrorAction SilentlyContinue
        if($null -eq $item -or $item.PSIsContainer -or ($item.Attributes-band[IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 64KB){continue}
        try{$document=[Text.UTF8Encoding]::new($false,$true).GetString([IO.File]::ReadAllBytes($item.FullName))|ConvertFrom-Json}catch{continue}
        $service=[string]$document.service
        if($document.schema_version -ne 1 -or $service -notin $allowed -or ($null -ne $candidateDiagnostic.service -and $service -cne $candidateDiagnostic.service)){continue}
        $details=[Collections.Generic.List[string]]::new();$stageName=[string]$document.startup_stage
        if($stageName -match '^[a-z][a-z0-9_]{0,63}$'){$details.Add("stage=$stageName")}
        foreach($entry in @($document.exception_chain)|Select-Object -First 8){$type=[string]$entry.type;if($type -match '^[A-Za-z_][A-Za-z0-9_.-]{0,127}$'){$details.Add($type)}}
        if($details.Count -gt 0){$summaries.Add("$service["+($details -join ' <- ')+']')}
    }
    return $summaries -join '; '
}
function Wait-RagTeamPreviewGraphReady([string]$ProgramDataRoot,[string]$ProfilesRoot,[string]$LocalAddress,[int]$TimeoutSeconds=300,[scriptblock]$ServiceProbe={[string](Get-Service RagSupervisor).Status},[scriptblock]$ListenerProbe={param($Address) @(Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue|Where-Object{[string]$_.LocalAddress -ceq $Address})},[scriptblock]$Delay={Start-Sleep -Seconds 1},[scriptblock]$DiagnosticProbe={param($Data,$Profiles) Get-PreviewStartupDiagnosticSummary $Data $Profiles}){
    if($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 300){throw 'Graph readiness timeout must be bounded to 1-300 seconds.'}
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do{
        if((& $ServiceProbe) -cne 'Running'){$diagnostic=& $DiagnosticProbe $ProgramDataRoot $ProfilesRoot;$suffix=if([string]::IsNullOrWhiteSpace($diagnostic)){'; no bounded startup diagnostic was produced'}else{"; startup diagnostic: $diagnostic"};throw "RagSupervisor stopped during Team preview graph startup$suffix"}
        $listeners=@(& $ListenerProbe $LocalAddress)
        if($listeners.Count -eq 1){return}
        & $Delay
    }while([DateTime]::UtcNow -lt $deadline)
    $diagnostic=& $DiagnosticProbe $ProgramDataRoot $ProfilesRoot;$suffix=if([string]::IsNullOrWhiteSpace($diagnostic)){'; no bounded startup diagnostic was produced'}else{"; startup diagnostic: $diagnostic"}
    throw "Team preview graph did not become ready within five minutes$suffix"
}
$candidateReleaseRoot=Join-Path $candidate 'release';$candidateControlPlane=Join-Path $candidate 'ops\windows\team_preview';$candidateReleaseControlPlane=Join-Path $candidateReleaseRoot 'ops\windows\team_preview';$currentControlPlane=Join-Path $current 'ops\windows\team_preview'
$expectedUpdater=[IO.Path]::GetFullPath((Join-Path $candidateControlPlane 'Update-RagTeamLanPreview.ps1'))
if(-not [string]::Equals([IO.Path]::GetFullPath($MyInvocation.MyCommand.Path),$expectedUpdater,[StringComparison]::OrdinalIgnoreCase)){throw 'Updater must execute from the candidate control-plane root bound by its payload inventory.'}
if((Get-Hash (Join-Path $candidateReleaseRoot 'caddy.exe')) -cne [string]$contract.caddy_sha256 -or (Get-Hash (Join-Path $candidateReleaseRoot 'tools\openssl\openssl.exe')) -cne [string]$contract.openssl_sha256){throw 'Candidate release binaries do not match the root release contract.'}
$installedDeploymentPath=Join-Path $programData 'installed-deployment.json';$installedServiceHostPath=Join-Path $programFiles 'service\RagSupervisorService.exe'
$installedDeploymentItem=Get-Item -LiteralPath $installedDeploymentPath -Force -ErrorAction Stop
if($installedDeploymentItem.PSIsContainer -or ($installedDeploymentItem.Attributes-band[IO.FileAttributes]::ReparsePoint) -or $installedDeploymentItem.Length -lt 2 -or $installedDeploymentItem.Length -gt 4MB){throw 'Installed control-plane deployment contract must be a bounded regular file.'}
$installedDeployment=Get-Content -Raw -LiteralPath $installedDeploymentPath|ConvertFrom-Json
if($installedDeployment.schema_version -ne 2 -or [string]$installedDeployment.product_profile -cne 'team_lan_preview_unsigned' -or [string]$installedDeployment.deployment_readiness.state -cne 'installed'){throw 'Installed control-plane deployment contract is invalid.'}
$installedServiceHostHash=Get-Hash $installedServiceHostPath
if($installedServiceHostHash -cne (Get-Hash (Join-Path $current 'RagSupervisorService.exe')) -or $installedServiceHostHash -cne (Get-Hash (Join-Path $candidateReleaseRoot 'RagSupervisorService.exe'))){throw 'Candidate/current release service host is not bound to the installed control-plane executable.'}
foreach($entry in @(
    @('caddy.exe','Caddy executable'),@('Caddyfile','Caddy configuration'),@('RagSupervisorService.exe','service-host binary'),
    @('deployment.json','service-host configuration'),
    @('apps\api\app\maintenance_cli.py','storage maintenance contract'),
    @('apps\api\app\services\storage_transfer.py','storage transfer contract'),
    @('apps\api\app\services\object_storage.py','object storage contract'),
    @('apps\api\app\services\object_lifecycle.py','object integrity contract')
)){Assert-UnchangedPath -OldRoot $current -NewRoot $candidateReleaseRoot -Relative $entry[0] -Description $entry[1]}
foreach($entry in @(@('compose.team-preview.yaml','storage topology'),@('team-preview-provisioning.json','storage provisioning contract'),@('Initialize-RagTeamPreviewPostgres.ps1','PostgreSQL security provisioning'),@('Initialize-RagTeamPreviewRustfs.ps1','RustFS security provisioning'))){
    Assert-UnchangedPath -OldRoot $currentControlPlane -NewRoot $candidateControlPlane -Relative $entry[0] -Description $entry[1]
    Assert-UnchangedPath -OldRoot $candidateControlPlane -NewRoot $candidateReleaseControlPlane -Relative $entry[0] -Description "candidate control-plane/release binding for $($entry[1])"
}
foreach($name in @('Update-RagTeamLanPreview.ps1','Backup-RagTeamLanPreview.ps1','Restore-Verify-RagTeamLanPreviewBackup.ps1','Restore-RagTeamLanPreviewBackup.ps1','Restore-RagTeamLanPreview.compose.yaml')){Assert-UnchangedPath -OldRoot $candidateControlPlane -NewRoot $candidateReleaseControlPlane -Relative $name -Description 'candidate control-plane/release contract binding'}
$journalRoot=Join-Path $programData 'updates';$activeJournal=Join-Path $journalRoot 'team-preview-update.json'
if(Test-Path -LiteralPath $activeJournal){$prior=Get-Content -Raw -LiteralPath $activeJournal|ConvertFrom-Json;if([string]$prior.state -cnotin @('committed','rolled_back')){throw 'A nonterminal preview update journal requires attended recovery before another update.'}}
if(-not $PSCmdlet.ShouldProcess($current,'Create and restore-verify backup, then install transactional Team/LAN preview update')){return}
[IO.Directory]::CreateDirectory($journalRoot)|Out-Null
$id=[guid]::NewGuid().ToString('N');$stage=Join-Path $programFiles "candidate-$id";$previous=Join-Path $programFiles "previous-$id";$failed=Join-Path $programFiles "failed-$id";$stateBackup=Join-Path $journalRoot "team-preview-state-$id.json"
$service=Get-Service RagSupervisor -ErrorAction Stop;$originalRunning=$service.Status -eq 'Running'
$journal=[ordered]@{schema_version=4;profile='team_lan_preview_unsigned';update_id=$id;state='prepared';created_at=[DateTimeOffset]::UtcNow.ToString('o');original_service_running=$originalRunning;candidate_tree_sha256=$inventory.tree_sha256;candidate_release_tree_sha256=$null;current_tree_sha256=[string]$state.release_tree_sha256;installed_deployment_sha256=(Get-Hash $installedDeploymentPath);installed_service_host_sha256=$installedServiceHostHash;prior_alembic_revision=[string]$state.alembic_revision;candidate_alembic_revision=$candidateRevision;current_path=$current;candidate_path=$stage;previous_path=$previous;state_backup_path=$stateBackup;backup_bundle=$null;backup_manifest_sha256=$null;restore_evidence_path=$null;restore_evidence_sha256=$null;migration_attempted=$false;failure=$null}
function Protect-UpdatePath([string]$Path){& icacls.exe $Path /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)'|Out-Null;if($LASTEXITCODE -ne 0){throw 'Preview update journal ACL protection failed.'}}
function Set-PreviewUpdateState([string]$Value,[AllowNull()][string]$Failure){$journal.state=$Value;$journal.failure=$Failure;Write-RagTeamPreviewJsonAtomic -Path $activeJournal -Value $journal;Protect-UpdatePath $activeJournal}
function Invoke-Fault([string]$Point){if($FaultPoint -ceq $Point){throw "Synthetic preview update fault: $Point"}}
function Invoke-WithEnvironment([Collections.IDictionary]$Values,[scriptblock]$Action){$prior=@{};try{foreach($key in $Values.Keys){$prior[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,[string]$Values[$key],'Process')};& $Action}finally{foreach($key in $Values.Keys){[Environment]::SetEnvironmentVariable($key,$prior[$key],'Process')}}}
[IO.File]::Copy($statePath,$stateBackup,$false);Protect-UpdatePath $stateBackup
Set-PreviewUpdateState 'prepared' $null
$switched=$false;$stateChanged=$false;$dataMayHaveMutated=$false;$backupBundle=$null
try{
    if($originalRunning){Stop-Service RagSupervisor -Force}
    if((Get-Service RagSupervisor).Status -ne 'Stopped'){throw 'RagSupervisor did not stop before backup capture.'}
    Set-PreviewUpdateState 'service_stopped' $null
    $capture=(& (Join-Path $PSScriptRoot 'Backup-RagTeamLanPreview.ps1') -BackupRoot $BackupRoot -ProgramDataRoot $programData -ReleaseRoot $current -CandidateRoot $candidate -ExpectedRevision ([string]$state.alembic_revision) -InstallationId ([string]$state.installation_id) -ConfirmServiceStopped)|ConvertFrom-Json
    if([string]$capture.result -cne 'captured'){throw 'Team/LAN preview backup capture did not complete.'}
    $backupBundle=[string]$capture.bundle
    $verified=(& (Join-Path $PSScriptRoot 'Restore-Verify-RagTeamLanPreviewBackup.ps1') -BackupBundle $backupBundle -ProgramDataRoot $programData -ReleaseRoot $current -ExpectedRevision ([string]$state.alembic_revision) -InstallationId ([string]$state.installation_id))|ConvertFrom-Json
    $bundleFull=[IO.Path]::GetFullPath($backupBundle).TrimEnd('\');$evidenceFull=[IO.Path]::GetFullPath([string]$verified.evidence_path)
    if([string]$verified.result -cne 'pass' -or [string]$verified.evidence_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$verified.manifest_sha256 -cne [string]$capture.manifest_sha256 -or
        [string]$verified.database_sha256 -cne [string]$capture.database_sha256 -or
        -not $evidenceFull.StartsWith($bundleFull+'\',[StringComparison]::OrdinalIgnoreCase) -or
        (Get-Hash $evidenceFull) -cne [string]$verified.evidence_sha256){throw 'Team/LAN preview backup was not restore-verified and bound to the captured pair.'}
    $evidenceDocument=Get-Content -Raw -LiteralPath $evidenceFull|ConvertFrom-Json
    if([string]$evidenceDocument.result -cne 'pass' -or [string]$evidenceDocument.profile -cne 'team_lan_preview_unsigned' -or
        [string]$evidenceDocument.installation_id -cne [string]$state.installation_id -or
        [string]$evidenceDocument.backup_id -cne [string]$capture.backup_id){throw 'Restore evidence installation/profile binding is invalid.'}
    $journal.backup_bundle=$backupBundle;$journal.backup_manifest_sha256=[string]$verified.manifest_sha256;$journal.restore_evidence_path=[string]$verified.evidence_path;$journal.restore_evidence_sha256=[string]$verified.evidence_sha256
    Set-PreviewUpdateState 'backup_verified' $null;Invoke-Fault 'after_backup_verified'
    Copy-Item -LiteralPath (Join-Path $candidate 'release') -Destination $stage -Recurse
    $journal.candidate_release_tree_sha256=Assert-StagedReleaseInventory -Stage $stage -PayloadInventory $inventory
    Set-PreviewUpdateState 'candidate_staged_verified' $null;Invoke-Fault 'after_candidate_staged_verified'
    [IO.Directory]::Move($current,$previous);[IO.Directory]::Move($stage,$current);$switched=$true
    Set-PreviewUpdateState 'release_switched' $null;Invoke-Fault 'after_release_switched'
    $secretPath=Join-Path $programData 'secrets\team-preview-secrets.json';$storeEnvironment=Join-Path $programData 'secrets\stores.env';$composeFile=Join-Path $current 'ops\windows\team_preview\compose.team-preview.yaml';$python=Join-Path $current 'runtimes\api-python\python.exe'
    foreach($path in @($secretPath,$storeEnvironment,$composeFile,$python)){if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "Candidate migration contract is missing: $path"}}
    $secret=Get-Content -Raw -LiteralPath $secretPath|ConvertFrom-Json
    if([string]$secret.installation_id -cne [string]$state.installation_id){throw 'Protected migrator credential is not bound to this installation.'}
    $headEnvironment=[ordered]@{PYTHONPATH=(Join-Path $current 'apps\api')}
    $migrationEnvironment=[ordered]@{MIGRATION_DATABASE_URL="postgresql+psycopg://rag_migrator:$($secret.values.postgres_migrator)@127.0.0.1:5432/rag";PYTHONPATH=(Join-Path $current 'apps\api')}
    $dataMayHaveMutated=$true
    $script:candidateHeadOutput=$null;$script:candidateHeadCode=$null
    Invoke-WithEnvironment $headEnvironment {
        Push-Location (Join-Path $current 'apps\api')
        try{$script:candidateHeadOutput=@(& $python -m alembic heads 2>&1);$script:candidateHeadCode=$LASTEXITCODE}
        finally{Pop-Location}
    }
    $headLines=@($script:candidateHeadOutput|ForEach-Object{[string]$_}|Where-Object{$_ -match '^([0-9]{4}_[a-z0-9_]+) \(head\)$'})
    $script:candidateHeadOutput=$null
    if($script:candidateHeadCode -ne 0 -or $headLines.Count -ne 1 -or $headLines[0] -cne "$candidateRevision (head)"){throw 'Packaged candidate Alembic head does not exactly match its release contract.'}
    Set-PreviewUpdateState 'candidate_head_verified' $null;Invoke-Fault 'after_candidate_head_verified'
    if($candidateRevision -cne [string]$state.alembic_revision){
        $journal.migration_attempted=$true;Set-PreviewUpdateState 'migration_started' $null
        $script:candidateMigrationOutput=$null;$script:candidateMigrationCode=$null
        Invoke-WithEnvironment $migrationEnvironment {
            Push-Location (Join-Path $current 'apps\api')
            try{$script:candidateMigrationOutput=@(& $python -m alembic upgrade head 2>&1);$script:candidateMigrationCode=$LASTEXITCODE}
            finally{Pop-Location}
        }
        $script:candidateMigrationOutput=$null
        if($script:candidateMigrationCode -ne 0){throw 'Candidate Alembic migration failed.'}
        Invoke-Fault 'after_migration_upgrade'
    }
    $docker=(Get-Command docker.exe -ErrorAction Stop).Source;$project='localrag-team-'+([string]$state.installation_id).Replace('-','').Substring(0,12)
    $revisionOutput=@('SELECT version_num FROM alembic_version;'|& $docker compose -p $project --env-file $storeEnvironment -f $composeFile exec -T postgres psql -X -A -t -v ON_ERROR_STOP=1 -U rag_cluster_admin -d rag 2>&1);$revisionCode=$LASTEXITCODE
    $actualRevision=[string](@($revisionOutput|Where-Object{-not[string]::IsNullOrWhiteSpace([string]$_)})[-1])
    if($revisionCode -ne 0 -or $actualRevision.Trim() -cne $candidateRevision){throw 'Database did not reach the exact candidate Alembic revision.'}
    Set-PreviewUpdateState 'migration_verified' $null
    try{Start-Service RagSupervisor}
    catch{$startupDiagnostic=Get-PreviewStartupDiagnosticSummary -ProgramDataRoot $programData -ProfilesRoot (Join-Path $programData 'profiles');$diagnosticSuffix=if([string]::IsNullOrWhiteSpace($startupDiagnostic)){'; no bounded startup diagnostic was produced'}else{"; startup diagnostic: $startupDiagnostic"};throw "RagSupervisor failed to start the candidate graph$diagnosticSuffix"}
    Set-PreviewUpdateState 'candidate_started' $null;Invoke-Fault 'after_candidate_started'
    Wait-RagTeamPreviewGraphReady -ProgramDataRoot $programData -ProfilesRoot (Join-Path $programData 'profiles') -LocalAddress ([string]$state.local_address)
    Set-PreviewUpdateState 'graph_ready' $null
    & (Join-Path $PSScriptRoot 'Test-RagTeamPreviewNetwork.ps1') -LocalAddress ([string]$state.local_address) -PinnedCaddyProgram (Join-Path $current 'caddy.exe') -PinnedCaddySha256 ([string]$contract.caddy_sha256)|Out-Null
    $state.release_tree_sha256=$inventory.tree_sha256;$state.alembic_revision=$candidateRevision;$state.installed_at=[DateTimeOffset]::UtcNow.ToString('o');Write-RagTeamPreviewJsonAtomic -Path $statePath -Value $state;$stateChanged=$true
    Set-PreviewUpdateState 'verified' $null
    if(-not $originalRunning){Stop-Service RagSupervisor -Force}
    Set-PreviewUpdateState 'committed' $null
    $switched=$false
    [ordered]@{result='updated';profile='team_lan_preview_unsigned';mode='manual_attended';automatic_updates_available=$false;schema_changed=($journal.prior_alembic_revision -cne $candidateRevision);prior_alembic_revision=$journal.prior_alembic_revision;alembic_revision=$state.alembic_revision;release_tree_sha256=$inventory.tree_sha256;backup_bundle=$backupBundle;backup_manifest_sha256=$journal.backup_manifest_sha256;restore_evidence_path=$journal.restore_evidence_path;restore_evidence_sha256=$journal.restore_evidence_sha256;data_preserved=$true;users_preserved=$true;secrets_preserved=$true;ca_preserved=$true;service_identities_preserved=$true;original_service_running=$originalRunning}|ConvertTo-Json -Depth 4
}catch{
    $failure=$_.Exception.Message
    try{
        Set-PreviewUpdateState 'rollback_started' $failure
        Stop-Service RagSupervisor -Force -ErrorAction SilentlyContinue
        if($switched -and (Test-Path -LiteralPath $previous -PathType Container)){
            if(Test-Path -LiteralPath $current -PathType Container){[IO.Directory]::Move($current,$failed)}
            [IO.Directory]::Move($previous,$current);$switched=$false
        }
        [IO.File]::Copy($stateBackup,$statePath,$true)
        if($dataMayHaveMutated){
            $restored=(& (Join-Path $PSScriptRoot 'Restore-RagTeamLanPreviewBackup.ps1') `
                -BackupBundle $backupBundle -EvidenceSha256 ([string]$journal.restore_evidence_sha256) `
                -ProgramDataRoot $programData -ReleaseRoot $current `
                -ExpectedRevision ([string]$journal.prior_alembic_revision) `
                -InstallationId ([string]$state.installation_id) -ConfirmServiceStopped)|ConvertFrom-Json
            if([string]$restored.result -cne 'restored_and_verified'){
                throw 'Verified live data rollback did not complete.'
            }
            Set-PreviewUpdateState 'rollback_data_restored' $failure
        }
        if($originalRunning){Start-Service RagSupervisor}else{Stop-Service RagSupervisor -Force -ErrorAction SilentlyContinue}
        Set-PreviewUpdateState 'rolled_back' $failure
    }catch{Set-PreviewUpdateState 'recovery_failed' "$failure; recovery: $($_.Exception.Message)";throw "Preview update failed and recovery is incomplete; verified backup retained at '$backupBundle': $failure"}
    throw "Preview update failed; release/config rollback restored the prior release and original service state. Verified backup retained at '$backupBundle': $failure"
}
