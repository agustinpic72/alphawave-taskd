#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

YES=false
RUNTIME=auto
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --yes) YES=true ;;
    --runtime) RUNTIME="${2:?missing runtime}"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

BACKUP_PATH="${1:-}"
if [[ -z "$BACKUP_PATH" ]]; then
  echo "Usage: ./scripts/restore.sh [--yes] [--runtime auto|systemd|docker] data/backups/file.sqlite.gz" >&2
  exit 1
fi
if [[ ! -f "$BACKUP_PATH" ]]; then
  echo "Backup not found: $BACKUP_PATH" >&2
  exit 1
fi

ROOT="$(repo_root)"
ensure_repo_root
case "$RUNTIME" in auto|systemd|docker) ;; *) echo "Invalid runtime: $RUNTIME" >&2; exit 2 ;; esac
if [[ "$RUNTIME" == auto ]]; then
  if command -v docker >/dev/null && docker compose --project-directory "$ROOT" ps --status running --services 2>/dev/null | grep -qx api; then
    RUNTIME=docker
  else
    RUNTIME=systemd
  fi
fi

if [[ "$YES" != "true" ]]; then
  echo "This will restore SQLite from: $BACKUP_PATH"
  echo "A safety backup of the current database will be created first."
  read -r -p "Type RESTORE to continue: " confirmation
  if [[ "$confirmation" != "RESTORE" ]]; then
    echo "Cancelled."
    exit 1
  fi
fi

cd "$ROOT"
if [[ "$RUNTIME" == docker ]]; then
  absolute_backup="$(realpath "$BACKUP_PATH")"
  docker compose --project-directory "$ROOT" stop api
  restore_incomplete=true
  restart_api() {
    if [[ "$restore_incomplete" == true ]]; then
      docker compose --project-directory "$ROOT" up -d api >/dev/null 2>&1 || true
    fi
  }
  trap restart_api EXIT
  docker compose --project-directory "$ROOT" run --rm --no-deps \
    -v "$absolute_backup:/tmp/selected-backup.sqlite.gz:ro" \
    api python -m app.cli restore /tmp/selected-backup.sqlite.gz --confirm "RESTAURAR BACKUP"
  docker compose --project-directory "$ROOT" up -d api
  restore_incomplete=false
  trap - EXIT
else
  PYTHON="$(backend_python)"
  [[ -x "$PYTHON" ]] || { echo "Backend virtualenv missing. Run ./scripts/install.sh first." >&2; exit 1; }
  was_active=false
  if systemctl --user is-active --quiet alphawave-taskd; then
    was_active=true
    echo "Stopping alphawave-taskd before restore."
    systemctl --user stop alphawave-taskd
  fi
  restart_systemd() { [[ "$was_active" == true ]] && systemctl --user start alphawave-taskd >/dev/null 2>&1 || true; }
  trap restart_systemd EXIT
  PYTHONPATH="$ROOT/backend" "$PYTHON" -m app.cli restore "$BACKUP_PATH" --confirm "RESTAURAR BACKUP"
  restart_systemd
  trap - EXIT
fi
