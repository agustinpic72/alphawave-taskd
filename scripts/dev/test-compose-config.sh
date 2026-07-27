#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
touch "$work/empty.env"

docker compose --project-directory "$root" --env-file "$work/empty.env" config >"$work/default.yaml"
grep -q 'host_ip: 127.0.0.1' "$work/default.yaml"
grep -q 'published: "8711"' "$work/default.yaml"
grep -q 'image: alphawave-taskd-api:local' "$work/default.yaml"
grep -q 'image: alphawave-taskd-web:local' "$work/default.yaml"
grep -q 'ALPHAWAVE_INSTANCE_LOCK_PATH: /app/data/alphawave-taskd.runtime.lock' "$work/default.yaml"
grep -q 'TELEGRAM_ENABLED: "false"' "$work/default.yaml"
grep -q 'TRELLO_WRITE_ENABLED: "false"' "$work/default.yaml"
grep -q 'ALPHAWAVE_SECRET_ENCRYPTION_KEY: ""' "$work/default.yaml"
grep -A2 -q 'alphawave_data:' "$work/default.yaml"
grep -q 'name: alphawave_data' "$work/default.yaml"
! grep -A2 'alphawave_data:' "$work/default.yaml" | grep -q 'external: true'

ALPHAWAVE_WEB_BIND=192.0.2.10 \
ALPHAWAVE_WEB_PORT=9876 \
ALPHAWAVE_IMAGE_TAG=m20m \
ALPHAWAVE_DATA_VOLUME=m20m-data \
ALPHAWAVE_BACKUPS_VOLUME=m20m-backups \
ALPHAWAVE_VOLUMES_EXTERNAL=true \
TELEGRAM_ENABLED=true \
  docker compose --project-directory "$root" --env-file "$work/empty.env" config >"$work/override.yaml"
grep -q 'host_ip: 192.0.2.10' "$work/override.yaml"
grep -q 'published: "9876"' "$work/override.yaml"
grep -q 'image: alphawave-taskd-api:m20m' "$work/override.yaml"
grep -q 'name: m20m-data' "$work/override.yaml"
grep -q 'name: m20m-backups' "$work/override.yaml"
grep -A2 'alphawave_data:' "$work/override.yaml" | grep -q 'external: true'
grep -q 'TELEGRAM_ENABLED: "true"' "$work/override.yaml"

printf 'compose config tests passed\n'
