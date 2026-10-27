#!/usr/bin/env bash
# Linux/macOS one-command start (Docker path): seekdb + app.
# App URL: http://localhost:3001
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found. Alternative (Node 20+): npm run install:all && npm start" >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  docker compose up --build -d
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose up --build -d
else
  echo "docker compose plugin not found" >&2
  exit 1
fi

echo "Waiting for the app to become healthy..."
for i in $(seq 1 60); do
  if curl -fsS http://localhost:3001/api/health >/dev/null 2>&1; then
    echo "ready: http://localhost:3001"
    exit 0
  fi
  sleep 2
done
echo "app did not become healthy in time; check: docker compose logs app" >&2
exit 1
