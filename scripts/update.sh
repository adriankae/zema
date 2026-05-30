#!/usr/bin/env bash
set -euo pipefail

ref="origin/main"
skip_backup=0

usage() {
  cat <<'USAGE'
Usage: scripts/update.sh [--ref REF] [--skip-backup]

Updates a Docker Compose Zema install from git, rebuilds containers, and checks health.

Options:
  --ref REF        Git ref to fast-forward to. Default: origin/main
  --skip-backup   Continue without exporting a JSON backup first.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ref)
      ref="${2:-}"
      if [ -z "$ref" ]; then
        echo "Missing value for --ref" >&2
        exit 2
      fi
      shift 2
      ;;
    --skip-backup)
      skip_backup=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-zema}"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Compose is required." >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required." >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Refusing to update with local tracked changes. Commit or stash them first." >&2
  git status --short >&2
  exit 1
fi

env_value() {
  local key="$1"
  if [ -f .env ]; then
    awk -F= -v key="$key" '$1 == key { value = substr($0, length(key) + 2) } END { print value }' .env \
      | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
  fi
}

zema_port="${ZEMA_PORT:-$(env_value ZEMA_PORT)}"
zema_port="${zema_port:-28173}"
base_url="http://127.0.0.1:${zema_port}"
api_key="${CZM_API_KEY:-$(env_value CZM_API_KEY)}"

if [ "$skip_backup" -eq 0 ]; then
  if [ -n "${api_key}" ]; then
    backup_dir="${ZEMA_BACKUP_DIR:-backups}"
    mkdir -p "$backup_dir"
    backup_path="${backup_dir}/zema-backup-$(date -u +%Y%m%dT%H%M%SZ).json"
    echo "Exporting backup to ${backup_path}"
    curl -fsS "${base_url}/export" -H "X-API-Key: ${api_key}" -o "$backup_path"
  else
    echo "No CZM_API_KEY found in the environment or .env, so automatic backup cannot run."
    printf "Continue without a backup? [y/N] "
    read -r answer
    case "$answer" in
      y|Y|yes|YES) ;;
      *) echo "Update cancelled."; exit 1 ;;
    esac
  fi
fi

echo "Fetching origin"
git fetch origin

echo "Fast-forwarding to ${ref}"
git merge --ff-only "$ref"

compose_args=()
if docker compose ps --services --filter status=running 2>/dev/null | grep -qx "zema-telegram"; then
  compose_args+=(--profile telegram)
fi

echo "Rebuilding and restarting Zema"
docker compose "${compose_args[@]}" up -d --build

echo "Waiting for health check at ${base_url}/health"
for _ in $(seq 1 30); do
  if curl -fsS "${base_url}/health" >/dev/null 2>&1; then
    echo "Zema is healthy."
    docker compose "${compose_args[@]}" ps
    exit 0
  fi
  sleep 2
done

echo "Zema did not become healthy in time. Recent backend logs:" >&2
docker compose logs --tail=80 zema-be >&2
exit 1
