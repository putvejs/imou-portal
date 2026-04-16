param(
  [string]$S3Bucket = 'imou-portal',
  [string]$S3Prefix = 'imou-portal/prod',
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

$templatePath = Join-Path $PSScriptRoot 'templates/backup.remote.sh'
$remoteScriptTemplate = Get-Content -Path $templatePath -Raw

$remoteScript = $remoteScriptTemplate.Replace('__REMOTE_DIR__', $RemoteDir)
$remoteScript = $remoteScript.Replace('__S3_BUCKET__', $S3Bucket)
$remoteScript = $remoteScript.Replace('__S3_PREFIX__', $S3Prefix)
$remoteScript = $remoteScript.Replace('__AWS_ARGS__', $awsArgsText)

Write-Host "Starting remote backup for imou-portal..."
Invoke-RemoteBash -ScriptText $remoteScript -SshArgs $sshArgs -SshTarget $Remote -ActionLabel 'Remote backup execution' | Out-Null

Write-Host 'Backup completed successfully.'
