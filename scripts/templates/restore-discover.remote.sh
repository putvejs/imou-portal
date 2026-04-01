set -euo pipefail

if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI is not installed on remote host" >&2
  exit 10
fi

LATEST=$(aws s3 ls "s3://__S3_BUCKET__/__S3_PREFIX__/" --recursive __AWS_ARGS__ | awk '{print $4}' | grep 'imou-backup-' | sort | tail -n 1)
if [ -z "$LATEST" ]; then
  echo "No backup archives found under s3://__S3_BUCKET__/__S3_PREFIX__/" >&2
  exit 11
fi
printf '%s' "$LATEST"
