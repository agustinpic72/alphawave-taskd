#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE=plan
OUTPUT_DIR=""
DATABASE_PATH="${ALPHAWAVE_SYSTEMD_DATABASE_PATH:-$ROOT/data/alphawave-taskd.sqlite}"
DATA_VOLUME="${ALPHAWAVE_DATA_VOLUME:-alphawave_data}"
BACKUP_VOLUME="${ALPHAWAVE_BACKUPS_VOLUME:-alphawave_backups}"
VOLUME_MODE="${ALPHAWAVE_VOLUME_MODE:-local-driver-managed}"
DATA_VOLUME_DEVICE="${ALPHAWAVE_DATA_VOLUME_DEVICE:-}"
BACKUP_VOLUME_DEVICE="${ALPHAWAVE_BACKUPS_VOLUME_DEVICE:-}"
ENV_FILE=""
COMPOSE_OVERRIDE=""

usage() {
  echo "Usage: $0 [--plan|--dry-run|--execute] [--output-dir DIR] [--database PATH] [--env-file PATH] [--compose-override PATH] [--data-volume NAME] [--backup-volume NAME] [--volume-mode MODE] [--data-volume-device PATH] [--backup-volume-device PATH]"
}

while (($#)); do
  case "$1" in
    --plan|--dry-run|--execute) MODE="${1#--}" ;;
    --output-dir) OUTPUT_DIR="${2:?missing output directory}"; shift ;;
    --database) DATABASE_PATH="${2:?missing database path}"; shift ;;
    --env-file) ENV_FILE="${2:?missing environment path}"; shift ;;
    --compose-override) COMPOSE_OVERRIDE="${2:?missing override path}"; shift ;;
    --data-volume) DATA_VOLUME="${2:?missing volume name}"; shift ;;
    --backup-volume) BACKUP_VOLUME="${2:?missing volume name}"; shift ;;
    --volume-mode) VOLUME_MODE="${2:?missing volume mode}"; shift ;;
    --data-volume-device) DATA_VOLUME_DEVICE="${2:?missing device path}"; shift ;;
    --backup-volume-device) BACKUP_VOLUME_DEVICE="${2:?missing device path}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

OUTPUT_DIR="${OUTPUT_DIR:-$ROOT/data/migration-baselines/$(date -u +%Y%m%dT%H%M%SZ)}"
SNAPSHOT="$OUTPUT_DIR/alphawave-taskd.sqlite"
MANIFEST="$OUTPUT_DIR/manifest.json"
echo "Mode: $MODE"
echo "Source database: $DATABASE_PATH"
echo "Baseline directory: $OUTPUT_DIR"
echo "Docker volumes: data=$DATA_VOLUME backups=$BACKUP_VOLUME"

[[ "$MODE" == plan ]] && { echo "Plan complete; no files were written."; exit 0; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
[[ -f "$DATABASE_PATH" ]] || { echo "Database not found: $DATABASE_PATH" >&2; exit 1; }
python3 - "$DATABASE_PATH" <<'PY'
import sqlite3, sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as db:
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("Source SQLite integrity check failed")
PY
[[ "$MODE" == dry-run ]] && { echo "Dry run complete; prerequisites and source integrity passed."; exit 0; }

mkdir -p "$OUTPUT_DIR"
python3 "$ROOT/scripts/admin/runtime_migration.py" snapshot "$DATABASE_PATH" "$SNAPSHOT"
service_state="inactive"
if command -v systemctl >/dev/null && systemctl --user is-active --quiet alphawave-taskd; then service_state="active"; fi
revision="$(git -C "$ROOT" rev-parse --verify HEAD 2>/dev/null || true)"
image_tag="${ALPHAWAVE_IMAGE_TAG:-local}"
api_image_id="$(docker image inspect "alphawave-taskd-api:$image_tag" --format '{{.Id}}' 2>/dev/null || true)"
web_image_id="$(docker image inspect "alphawave-taskd-web:$image_tag" --format '{{.Id}}' 2>/dev/null || true)"
compose_args=(docker compose --project-directory "$ROOT")
[[ -z "$ENV_FILE" ]] || compose_args+=(--env-file "$ENV_FILE")
[[ -z "$COMPOSE_OVERRIDE" ]] || compose_args+=(-f "$ROOT/compose.yaml" -f "$COMPOSE_OVERRIDE")
compose_hash="$("${compose_args[@]}" config 2>/dev/null | sha256sum | awk '{print $1}')"
python3 "$ROOT/scripts/admin/runtime_migration.py" manifest \
  --snapshot "$SNAPSHOT" --output "$MANIFEST" --service alphawave-taskd \
  --service-active "$service_state" --git-revision "$revision" \
  --data-volume "$DATA_VOLUME" --backup-volume "$BACKUP_VOLUME" \
  --volume-mode "$VOLUME_MODE" --data-volume-device "$DATA_VOLUME_DEVICE" \
  --backup-volume-device "$BACKUP_VOLUME_DEVICE" \
  --compose-config-sha256 "$compose_hash" --api-image-id "$api_image_id" --web-image-id "$web_image_id"
chmod 700 "$OUTPUT_DIR"
chmod 600 "$SNAPSHOT" "$MANIFEST"
echo "Baseline captured: $MANIFEST"
