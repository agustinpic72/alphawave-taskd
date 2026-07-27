#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${ALPHAWAVE_SERVICE_NAME:-alphawave-taskd}"
LINES="${ALPHAWAVE_LOG_LINES:-200}"
FOLLOW=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--follow)
      FOLLOW=1
      shift
      ;;
    -n|--lines)
      LINES="${2:?Missing line count after $1}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--follow] [--lines N]" >&2
      exit 2
      ;;
  esac
done

JOURNAL_ARGS=(-u "${SERVICE_NAME}" -n "${LINES}" --no-pager)
if [[ "${ALPHAWAVE_SYSTEMD_USER:-0}" == "1" ]]; then
  JOURNAL_ARGS=(--user "${JOURNAL_ARGS[@]}")
fi
if [[ "${FOLLOW}" == "1" ]]; then
  JOURNAL_ARGS+=(-f)
fi

echo "Reading logs for service: ${SERVICE_NAME}"
if [[ "${ALPHAWAVE_SYSTEMD_USER:-0}" == "1" ]]; then
  echo "Scope: user"
else
  echo "Scope: system"
fi

journalctl "${JOURNAL_ARGS[@]}"
