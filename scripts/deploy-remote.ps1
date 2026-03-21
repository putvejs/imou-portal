param(
  [ValidateSet('deploy', 'deploy-build', 'stop', 'logs', 'status', 'restart')]
  [string]$Action = 'deploy',

  [string]$RemoteHost = 'srvop.duckdns.org',
  [string]$User       = 'udzerins',
  [int]$Port          = 1979,
  [string]$RemoteDir  = '/home/udzerins/imou-portal',
  [string]$KeyPath    = $env:MADONA_SSH_KEY   # reuse same key as madona-portal
)

$ErrorActionPreference = 'Stop'
$target  = "$User@$RemoteHost"
$sshArgs = @('-p', $Port.ToString(), '-o', 'StrictHostKeyChecking=accept-new',
             '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2')
if ($KeyPath) { $sshArgs += @('-i', $KeyPath) }

function Invoke-Remote([string]$Cmd) {
    Write-Host "[ssh] $Cmd"
    $out = & ssh @sshArgs $target ($Cmd -replace "`r`n","`n") 2>&1
    $out | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE)" }
}

function Sync-Files {
    $scp  = Get-Command scp  -ErrorAction Stop
    $tar  = Get-Command tar  -ErrorAction Stop
    $root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

    $archive = Join-Path $env:TEMP "imou-sync-$([Guid]::NewGuid().ToString('N')).tar"
    $remote  = "/tmp/$(Split-Path $archive -Leaf)"

    Write-Host "[sync] Archiving $root"
    & $tar.Source -cf $archive --exclude='.git' --exclude='**/__pycache__' `
        --exclude='data' --exclude='.env' --exclude='.env.*' `
        --exclude='*.log' -C $root .
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }

    Write-Host "[sync] Uploading archive"
    $scpArgs = @('-P', $Port.ToString())
    if ($KeyPath) { $scpArgs += @('-i', $KeyPath) }
    & $scp.Source @scpArgs $archive "${target}:$remote"
    if ($LASTEXITCODE -ne 0) { throw "scp failed" }
    Remove-Item $archive -Force

    Write-Host "[sync] Extracting on remote"
    Invoke-Remote "mkdir -p '$RemoteDir' && tar -xf '$remote' -C '$RemoteDir' && rm -f '$remote'"
}

switch ($Action) {
  'deploy' {
    Sync-Files
    Invoke-Remote @"
set -e
cd '$RemoteDir'
if [ ! -f .env ]; then
  echo 'ERROR: .env not found on server. Copy .env.example to .env and fill in credentials.'
  exit 1
fi
docker compose build
docker compose up -d --force-recreate --remove-orphans
docker compose ps
"@
  }
  'deploy-build' {
    Sync-Files
    Invoke-Remote "cd '$RemoteDir' && docker compose down && docker compose build --pull && docker compose up -d --remove-orphans && docker compose ps"
  }
  'stop' {
    Invoke-Remote "cd '$RemoteDir' && docker compose down"
  }
  'restart' {
    Invoke-Remote "cd '$RemoteDir' && docker compose restart && docker compose ps"
  }
  'logs' {
    & ssh @sshArgs $target "cd '$RemoteDir' && docker compose logs -f --tail=100"
  }
  'status' {
    Invoke-Remote "cd '$RemoteDir' && docker compose ps"
  }
}

Write-Host "[done] Action '$Action' completed."
