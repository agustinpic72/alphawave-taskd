#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

DRY_RUN=false
TRELLO_WRITE=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --trello-write) TRELLO_WRITE=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

ROOT="$(repo_root)"
ensure_repo_root
cd "$ROOT"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "Live smoke dry-run:"
  echo "- Run ./scripts/smoke.sh"
  echo "- Run Trello read-only sync if TRELLO_ENABLED=true"
  echo "- Trello write remains disabled unless --trello-write is used"
  exit 0
fi

echo "This live smoke may create local [SMOKE] tasks/reminders."
read -r -p "Type LIVE SMOKE to continue: " confirmation
if [[ "$confirmation" != "LIVE SMOKE" ]]; then
  echo "Cancelled."
  exit 1
fi

"$SCRIPT_DIR/smoke.sh"

if grep -q '^TRELLO_ENABLED=true' .env 2>/dev/null; then
  echo "Running Trello read-only sync."
  curl -sS -X POST http://127.0.0.1:8711/api/trello/sync
  echo
fi

if [[ "$TRELLO_WRITE" == "true" ]]; then
  if ! grep -q '^TRELLO_WRITE_ENABLED=true' .env 2>/dev/null; then
    echo "Trello write smoke requested, but TRELLO_WRITE_ENABLED is not true."
    exit 1
  fi
  echo "Trello write smoke is intentionally not automatic."
  echo "Use the UI or Telegram to create a confirmation, then confirm it explicitly."
fi
