set -euo pipefail

mkdir -p "__REMOTE_DIR__/.ops/bin" "__REMOTE_DIR__/.ops/logs" "__REMOTE_DIR__/.ops/work"
SCRIPT_PATH="__REMOTE_DIR__/.ops/bin/backup-imou-nightly.sh"
cat > "$SCRIPT_PATH" << 'EOS'
#!/usr/bin/env bash
set -euo pipefail

S3_BUCKET='__S3_BUCKET__'
S3_PREFIX='__S3_PREFIX__'
REMOTE_DIR='__REMOTE_DIR__'
RETENTION_DAYS='__RETENTION_DAYS__'
AWS_PROFILE_ARG="__AWS_PROFILE_ARG__"
AWS_REGION_ARG="__AWS_REGION_ARG__"
TS=$(date +%Y%m%d-%H%M%S)
WORKROOT="$REMOTE_DIR/.ops/work"
WORKDIR=$WORKROOT/imou-backup-$TS
OUTDIR=$WORKDIR/payload
LOG="$REMOTE_DIR/.ops/logs/imou-backup-nightly.log"

mkdir -p "$WORKROOT"
mkdir -p "$OUTDIR/volumes" "$OUTDIR/files"

if ! command -v aws >/dev/null 2>&1; then
  echo "$(date -Iseconds) aws CLI missing" >> "$LOG"
  exit 10
fi

cp "$REMOTE_DIR/.env" "$OUTDIR/files/.env"
cp "$REMOTE_DIR/docker-compose.yml" "$OUTDIR/files/docker-compose.yml"

docker run --rm -v imou-data:/source -v "$OUTDIR/volumes":/backup alpine:3.20 \
  sh -lc 'tar -czf /backup/imou-data.tar.gz -C /source .'

cat > "$OUTDIR/manifest.txt" << EOF
project=imou-portal
created_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
host=$(hostname)
remote_dir=$REMOTE_DIR
volumes=imou-data
EOF

ARCHIVE=$WORKROOT/imou-backup-$TS.tar.gz
tar -czf "$ARCHIVE" -C "$OUTDIR" .
KEY="$S3_PREFIX/imou-backup-$TS.tar.gz"
aws s3 cp "$ARCHIVE" "s3://$S3_BUCKET/$KEY" $AWS_PROFILE_ARG $AWS_REGION_ARG

echo "$(date -Iseconds) uploaded s3://$S3_BUCKET/$KEY" >> "$LOG"

if [ "$RETENTION_DAYS" -gt 0 ]; then
  CUTOFF=$(date -d "-$RETENTION_DAYS days" +%Y-%m-%d)
  aws s3 ls "s3://$S3_BUCKET/$S3_PREFIX/" --recursive $AWS_PROFILE_ARG $AWS_REGION_ARG \
    | awk -v cutoff="$CUTOFF" '/imou-backup-/ { if ($1 < cutoff) print $4 }' \
    | while read -r oldKey; do aws s3 rm "s3://$S3_BUCKET/$oldKey" $AWS_PROFILE_ARG $AWS_REGION_ARG || true; done
fi

rm -rf "$WORKDIR" "$ARCHIVE"
EOS
chmod +x "$SCRIPT_PATH"

CRON_LINE="__SCHEDULE__ __REMOTE_DIR__/.ops/bin/backup-imou-nightly.sh >> __REMOTE_DIR__/.ops/logs/imou-backup-nightly.log 2>&1"
CURRENT_CRONTAB=$(crontab -l 2>/dev/null || true)
{
  printf '%s\n' "$CURRENT_CRONTAB" | grep -v 'backup-imou-nightly.sh' || true
  printf '%s\n' "$CRON_LINE"
} | crontab -

echo "Installed nightly backup cron for imou-portal"
crontab -l | grep 'backup-imou-nightly.sh' || true
