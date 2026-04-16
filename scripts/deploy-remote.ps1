param(
  [ValidateSet('deploy', 'deploy-build', 'stop', 'logs', 'status', 'restart')]
  [string]$Action = 'deploy',

  [string]$RemoteHost = $env:DEPLOY_HOST,
  [string]$User = $env:DEPLOY_USER,
  [int]$Port = $(if ($env:DEPLOY_PORT) { [int]$env:DEPLOY_PORT } else { 0 }),
  [string]$RemoteDir  = "/home/$($env:DEPLOY_USER)/imou-portal",
  [string]$KeyPath    = $env:DEPLOY_SSH_KEY   # reuse same key as madona-portal
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

function Assert-GitSync {
  $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
  Write-Host '[git] Checking sync with origin...'
  git -C $repoRoot fetch origin 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Warning '[git] Could not reach GitHub — skipping sync check.'
    return
  }
  $behind = [int](& git -C $repoRoot rev-list --count 'HEAD..@{u}' 2>&1).Trim()
  $ahead  = [int](& git -C $repoRoot rev-list --count '@{u}..HEAD' 2>&1).Trim()
  if ($behind -gt 0) {
    throw "Local branch is $behind commit(s) behind origin. Run 'git pull' before deploying."
  }
  if ($ahead -gt 0) {
    Write-Warning "[git] Local has $ahead unpushed commit(s). Push to GitHub so the other machine stays in sync."
  }
  Write-Host '[git] Local is in sync with origin.'
}

function Invoke-RemoteLocked([string]$LockName, [string]$CommandText, [int]$LockTimeoutSec = 300) {
  $lockPrefix = @'
lock='/tmp/__LOCKNAME__.deploy.lock'
max_wait=__TIMEOUT__
waited=0
if [ -e "$lock" ]; then
  lock_pid=$(cat "$lock" 2>/dev/null || true)
  if [ -n "$lock_pid" ] && ! kill -0 "$lock_pid" 2>/dev/null; then
    rm -f "$lock"
  fi
fi
while [ -e "$lock" ]; do
  echo "Deploy locked by PID $(cat $lock 2>/dev/null). Waiting..."
  sleep 5
  waited=$((waited + 5))
  if [ "$waited" -ge "$max_wait" ]; then
    echo "Deploy lock timeout." >&2; exit 2
  fi
done
echo $$ > "$lock"
trap 'rm -f "$lock"' EXIT

'@
  $lockPrefix = $lockPrefix.Replace('__LOCKNAME__', $LockName).Replace('__TIMEOUT__', $LockTimeoutSec.ToString())
  Invoke-Remote ($lockPrefix + $CommandText)
}

switch ($Action) {
  'deploy' {
    Assert-GitSync
    Sync-Files
    Invoke-RemoteLocked 'imou-portal' @"
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
    Assert-GitSync
    Sync-Files
    Invoke-RemoteLocked 'imou-portal' "cd '$RemoteDir' && docker compose down && docker compose build --pull && docker compose up -d --remove-orphans && docker compose ps"
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
