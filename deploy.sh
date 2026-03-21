#!/bin/bash
# deploy.sh — Build and deploy Imou Portal

set -e

echo "=== Imou Portal Deploy ==="

# Check .env exists
if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill in your credentials."
  echo "  cp .env.example .env"
  exit 1
fi

# Check required env vars
source .env
if [ -z "$IMOU_APP_ID" ] || [ "$IMOU_APP_ID" = "your_app_id_here" ]; then
  echo "WARNING: IMOU_APP_ID not set in .env"
fi

echo "Building Docker image..."
docker compose build

echo "Starting services..."
docker compose up -d

echo "Waiting for app to start..."
sleep 5

# Show logs briefly
echo ""
echo "=== Recent logs ==="
docker compose logs --tail=20

echo ""
echo "=== Imou Portal is running ==="
echo "URL: http://localhost:$(grep '^    ports:' docker-compose.yml -A1 | grep -o '[0-9]*:5000' | cut -d: -f1 || echo '5010')"
echo "Default login: admin / changeme123"
echo ""
echo "To view logs:    docker compose logs -f"
echo "To stop:         docker compose down"
echo "To restart:      docker compose restart"
