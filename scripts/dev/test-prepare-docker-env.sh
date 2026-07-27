#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

source_env="$work/source.env"
target_env="$work/docker.env"
secret='do-not-print-this-value'
printf '%s\n' \
  'ALPHAWAVE_WEB_PORT=9090' \
  "TELEGRAM_BOT_TOKEN=$secret" \
  'UNSUPPORTED_VALUE=discard-me' >"$source_env"

dry_output="$($root/scripts/admin/prepare-docker-env.sh --dry-run "$source_env" "$target_env")"
[[ ! -e "$target_env" ]]
[[ "$dry_output" != *"$secret"* ]]

output="$($root/scripts/admin/prepare-docker-env.sh "$source_env" "$target_env")"
[[ "$output" != *"$secret"* ]]
[[ "$(stat -c '%a' "$target_env")" == "600" ]]
grep -qx 'ALPHAWAVE_WEB_PORT=9090' "$target_env"
grep -qx "TELEGRAM_BOT_TOKEN=$secret" "$target_env"
! grep -q 'UNSUPPORTED_VALUE' "$target_env"

printf 'prepare-docker-env tests passed\n'
