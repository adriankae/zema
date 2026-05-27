#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-zema}"

if docker compose version >/dev/null 2>&1; then
  docker compose --profile telegram up -d zema-telegram
  docker compose ps zema-telegram
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose --profile telegram up -d zema-telegram
  docker-compose ps zema-telegram
else
  echo "Docker Compose is required to start zema-telegram." >&2
  exit 1
fi
