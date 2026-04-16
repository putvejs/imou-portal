Set-StrictMode -Version Latest

function Assert-LastExitCode([string]$Action) {
  if ($LASTEXITCODE -ne 0) {
    throw "$Action failed with exit code $LASTEXITCODE"
  }
}

function New-SshArgs {
  param(
    [Parameter(Mandatory = $true)]
    [int]$Port,

    [string]$KeyPath,
    [switch]$AcceptNewHostKey,
    [switch]$BatchMode
  )

  $args = @('-p', $Port.ToString())

  if ($AcceptNewHostKey) {
    $args += @('-o', 'StrictHostKeyChecking=accept-new')
  }

  if ($BatchMode) {
    $args += @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20')
  }

  if ($KeyPath) {
    if (-not (Test-Path $KeyPath)) {
      throw "SSH key not found at '$KeyPath'."
    }
    $args += @('-i', $KeyPath)
  }

  return $args
}

function New-AwsArgsText {
  param(
    [string]$AwsProfile,
    [string]$AwsRegion
  )

  $awsArgs = @()
  if ($AwsProfile) {
    $awsArgs += "--profile '$AwsProfile'"
  }
  if ($AwsRegion) {
    $awsArgs += "--region '$AwsRegion'"
  }

  return ($awsArgs -join ' ')
}

function Invoke-RemoteBash {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptText,

    [Parameter(Mandatory = $true)]
    [string[]]$SshArgs,

    [Parameter(Mandatory = $true)]
    [string]$SshTarget,

    [Parameter(Mandatory = $true)]
    [string]$ActionLabel
  )

  $normalized = ($ScriptText -replace "`r`n", "`n") -replace "`r", "`n"
  $output = $normalized | ssh @SshArgs $SshTarget 'tr -d ''\r'' | bash -s'
  Assert-LastExitCode $ActionLabel
  return $output
}
