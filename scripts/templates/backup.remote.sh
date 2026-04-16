set -euo pipefail

if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI is not installed on remote host" >&2
  exit 10
fi

TS=$(date +%Y%m%d-%H%M%S)
WORKROOT="__REMOTE_DIR__/.ops/work"
WORKDIR="$WORKROOT/imou-backup-$TS"
OUTDIR=$WORKDIR/payload
mkdir -p "$WORKROOT"
mkdir -p "$OUTDIR/volumes" "$OUTDIR/files"

cp "__REMOTE_DIR__/.env" "$OUTDIR/files/.env"
cp "__REMOTE_DIR__/docker-compose.yml" "$OUTDIR/files/docker-compose.yml"

# Back up the imou-data Docker volume (SQLite DB + alarm images)
docker run --rm -v imou-data:/source -v "$OUTDIR/volumes":/backup alpine:3.20 \
  sh -lc 'tar -czf /backup/imou-data.tar.gz -C /source .'

cat > "$OUTDIR/manifest.txt" << EOF
project=imou-portal
created_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
host=$(hostname)
remote_dir=__REMOTE_DIR__
volumes=imou-data
EOF

ARCHIVE="$WORKROOT/imou-backup-$TS.tar.gz"
tar -czf "$ARCHIVE" -C "$OUTDIR" .

S3_URI="s3://__S3_BUCKET__/__S3_PREFIX__/imou-backup-$TS.tar.gz"
aws s3 cp "$ARCHIVE" "$S3_URI" --no-progress __AWS_ARGS__

rm -rf "$WORKDIR" "$ARCHIVE"
echo "Backup uploaded: $S3_URI"
