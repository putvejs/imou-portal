param(
  [string]$S3Bucket = 'imou-portal',
  [string]$S3Prefix = 'imou-portal/prod',
  [string]$BackupKey,
  [string]$Remote = "$($env:DEPLOY_USER)@$($env:DEPLOY_HOST)",
  [int]$Port = $(if ($env:DEPLOY_PORT) { [int]$env:DEPLOY_PORT } else { 0 }),
  [string]$RemoteDir = "/home/$($env:DEPLOY_USER)/imou-portal",
  [string]$KeyPath = $env:DEPLOY_SSH_KEY,
  [string]$AwsProfile,
  [string]$AwsRegion,
  [switch]$AcceptNewHostKey,
  [switch]$BatchMode
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'backup-script-common.ps1')

$sshArgs = New-SshArgs -Port $Port -KeyPath $KeyPath -AcceptNewHostKey:$AcceptNewHostKey -BatchMode:$BatchMode
$awsArgsText = New-AwsArgsText -AwsProfile $AwsProfile -AwsRegion $AwsRegion

$resolvedBackupKey = $BackupKey
if (-not $resolvedBackupKey) {
  $discoverTemplatePath = Join-Path $PSScriptRoot 'templates/restore-discover.remote.sh'
  $remoteDiscoverTemplate = Get-Content -Path $discoverTemplatePath -Raw

  $remoteDiscoverScript = $remoteDiscoverTemplate.Replace('__S3_BUCKET__', $S3Bucket)
  $remoteDiscoverScript = $remoteDiscoverScript.Replace('__S3_PREFIX__', $S3Prefix)
  $remoteDiscoverScript = $remoteDiscoverScript.Replace('__AWS_ARGS__', $awsArgsText)

  $discoveryOutput = Invoke-RemoteBash -ScriptText $remoteDiscoverScript -SshArgs $sshArgs -SshTarget $Remote -ActionLabel 'Backup discovery'
  $resolvedBackupKey = (($discoveryOutput | Select-Object -Last 1) -as [string]).Trim()
}

if (-not $resolvedBackupKey) {
  throw 'Could not resolve backup key to restore.'
}

Write-Host "Restoring backup key: $resolvedBackupKey"

$restoreTemplatePath = Join-Path $PSScriptRoot 'templates/restore.remote.sh'
$remoteRestoreTemplate = Get-Content -Path $restoreTemplatePath -Raw

$remoteRestoreScript = $remoteRestoreTemplate.Replace('__S3_BUCKET__', $S3Bucket)
$remoteRestoreScript = $remoteRestoreScript.Replace('__BACKUP_KEY__', $resolvedBackupKey)
$remoteRestoreScript = $remoteRestoreScript.Replace('__AWS_ARGS__', $awsArgsText)
$remoteRestoreScript = $remoteRestoreScript.Replace('__REMOTE_DIR__', $RemoteDir)

Invoke-RemoteBash -ScriptText $remoteRestoreScript -SshArgs $sshArgs -SshTarget $Remote -ActionLabel 'Remote restore execution' | Out-Null

Write-Host 'Restore completed successfully.'
