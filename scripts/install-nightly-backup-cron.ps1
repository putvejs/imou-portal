param(
  [string]$S3Bucket = 'imou-portal',
  [string]$S3Prefix = 'imou-portal/prod',
  [string]$Remote = 'udzerins@srvop.duckdns.org',
  [int]$Port = 1979,
  [string]$RemoteDir = '/home/udzerins/imou-portal',
  [string]$KeyPath = $env:MADONA_SSH_KEY,
  [string]$AwsProfile,
  [string]$AwsRegion,
  [int]$RetentionDays = 90,
  [string]$Schedule = '15 2 * * *',
  [switch]$AcceptNewHostKey,
  [switch]$BatchMode
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'backup-script-common.ps1')

$sshArgs = New-SshArgs -Port $Port -KeyPath $KeyPath -AcceptNewHostKey:$AcceptNewHostKey -BatchMode:$BatchMode

$profileArg = if ($AwsProfile) { "--profile '$AwsProfile'" } else { '' }
$regionArg = if ($AwsRegion) { "--region '$AwsRegion'" } else { '' }

$templatePath = Join-Path $PSScriptRoot 'templates/install-nightly.remote.sh'
$remoteSetupTemplate = Get-Content -Path $templatePath -Raw

$remoteSetup = $remoteSetupTemplate.Replace('__S3_BUCKET__', $S3Bucket)
$remoteSetup = $remoteSetup.Replace('__S3_PREFIX__', $S3Prefix)
$remoteSetup = $remoteSetup.Replace('__REMOTE_DIR__', $RemoteDir)
$remoteSetup = $remoteSetup.Replace('__RETENTION_DAYS__', $RetentionDays.ToString())
$remoteSetup = $remoteSetup.Replace('__AWS_PROFILE_ARG__', $profileArg)
$remoteSetup = $remoteSetup.Replace('__AWS_REGION_ARG__', $regionArg)
$remoteSetup = $remoteSetup.Replace('__SCHEDULE__', $Schedule)

$remoteSetup = ($remoteSetup -replace "`r`n", "`n") -replace "`r", "`n"
$remoteSetupBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteSetup))
ssh @sshArgs $Remote "echo '$remoteSetupBase64' | base64 -d | bash"
Assert-LastExitCode 'Install nightly backup cron'

Write-Host 'Nightly backup automation installed for imou-portal.'
