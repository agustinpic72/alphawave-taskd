#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [--dry-run] [SOURCE_ENV [TARGET_ENV]]\n' "${0##*/}"
}

dry_run=false
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=true
  shift
fi
if [[ $# -gt 2 ]]; then
  usage >&2
  exit 2
fi

source_env="${1:-.env}"
target_env="${2:-.env.docker}"
if [[ ! -f "$source_env" ]]; then
  printf 'Source environment file not found: %s\n' "$source_env" >&2
  exit 1
fi

readonly allowlist='ALPHAWAVE_WEB_BIND ALPHAWAVE_WEB_PORT ALPHAWAVE_IMAGE_TAG ALPHAWAVE_DATA_VOLUME ALPHAWAVE_BACKUPS_VOLUME ALPHAWAVE_VOLUMES_EXTERNAL ALPHAWAVE_INSTANCE_LOCK_PATH ALPHAWAVE_DEPLOYMENT_MODE ALPHAWAVE_AUTH_ENABLED ALPHAWAVE_AUTH_COOKIE_SECURE ALPHAWAVE_AUTH_COOKIE_NAME ALPHAWAVE_AUTH_SESSION_TTL_HOURS ALPHAWAVE_SECRET_ENCRYPTION_KEY APP_TIMEZONE REMINDERS_ENABLED REMINDERS_POLL_INTERVAL_SECONDS DAILY_BRIEFING_ENABLED DAILY_BRIEFING_TIME DAILY_BRIEFING_LATE_CUTOFF DAILY_BRIEFING_TIMEZONE DAILY_BRIEFING_SEND_ON_STARTUP BACKUP_ENABLED BACKUP_RETENTION_DAYS TELEGRAM_ENABLED TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USER_ID TELEGRAM_POLL_INTERVAL_SECONDS TRELLO_ENABLED TRELLO_API_KEY TRELLO_TOKEN TRELLO_MEMBER_ID TRELLO_BOARD_ALPHA_ID TRELLO_BOARD_BETA_ID TRELLO_SYNC_INTERVAL_MINUTES TRELLO_WRITE_ENABLED TRELLO_CONFIRMATION_TTL_HOURS'

tmp_file="$(mktemp "${TMPDIR:-/tmp}/alphawave-docker-env.XXXXXX")"
trap 'rm -f "$tmp_file"' EXIT
chmod 600 "$tmp_file"

awk -v allowed="$allowlist" '
  BEGIN {
    count = split(allowed, names, " ")
    for (i = 1; i <= count; i++) allow[names[i]] = 1
  }
  /^[[:space:]]*($|#)/ { next }
  {
    line = $0
    sub(/^[[:space:]]*export[[:space:]]+/, "", line)
    equals = index(line, "=")
    if (!equals) next
    key = substr(line, 1, equals - 1)
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
    if (key in allow) values[key] = substr(line, equals + 1)
  }
  END {
    for (i = 1; i <= count; i++)
      if (names[i] in values) print names[i] "=" values[names[i]]
  }
' "$source_env" >"$tmp_file"

entry_count="$(wc -l <"$tmp_file" | tr -d ' ')"
if "$dry_run"; then
  printf 'Dry run: would write %s allowlisted variable(s) to %s (mode 600).\n' "$entry_count" "$target_env"
  exit 0
fi

mkdir -p "$(dirname "$target_env")"
mv -f "$tmp_file" "$target_env"
chmod 600 "$target_env"
trap - EXIT
printf 'Wrote %s allowlisted variable(s) to %s (mode 600).\n' "$entry_count" "$target_env"
