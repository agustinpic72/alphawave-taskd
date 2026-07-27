#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE=plan; YES=false
DATABASE_PATH="${ALPHAWAVE_SYSTEMD_DATABASE_PATH:-$ROOT/data/alphawave-taskd.sqlite}"
DATA_VOLUME="${ALPHAWAVE_DATA_VOLUME:-alphawave_data_soak_v1}"
BACKUP_VOLUME="${ALPHAWAVE_BACKUPS_VOLUME:-alphawave_backups_soak_v1}"
VOLUME_MODE="${ALPHAWAVE_VOLUME_MODE:-local-driver-managed}"
DATA_VOLUME_DEVICE="${ALPHAWAVE_DATA_VOLUME_DEVICE:-}"
BACKUP_VOLUME_DEVICE="${ALPHAWAVE_BACKUPS_VOLUME_DEVICE:-}"
ENV_FILE="${ALPHAWAVE_DOCKER_ENV_FILE:-$ROOT/.env.docker}"
STAGING_DIR=""
HEALTH_URL="${ALPHAWAVE_SYSTEMD_HEALTH_URL:-http://127.0.0.1:8711/api/health}"
HEALTH_TIMEOUT="${ALPHAWAVE_SYSTEMD_HEALTH_TIMEOUT_SECONDS:-30}"
usage() { echo "Usage: $0 [--plan|--dry-run|--execute] [--yes] [--database PATH] [--env-file PATH] [--staging-dir DIR] [--data-volume NAME] [--backup-volume NAME] [--volume-mode MODE] [--data-volume-device PATH] [--backup-volume-device PATH]"; }
while (($#)); do
  case "$1" in
    --plan|--dry-run|--execute) MODE="${1#--}" ;;
    --yes) YES=true ;;
    --database) DATABASE_PATH="${2:?missing path}"; shift ;;
    --env-file) ENV_FILE="${2:?missing path}"; shift ;;
    --staging-dir) STAGING_DIR="${2:?missing path}"; shift ;;
    --data-volume) DATA_VOLUME="${2:?missing name}"; shift ;;
    --backup-volume) BACKUP_VOLUME="${2:?missing name}"; shift ;;
    --volume-mode) VOLUME_MODE="${2:?missing mode}"; shift ;;
    --data-volume-device) DATA_VOLUME_DEVICE="${2:?missing path}"; shift ;;
    --backup-volume-device) BACKUP_VOLUME_DEVICE="${2:?missing path}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac; shift
done
STAGING_DIR="${STAGING_DIR:-$ROOT/data/rollback-staging/$(date -u +%Y%m%dT%H%M%SZ)}"
echo "Mode: $MODE"
echo "Docker volumes: data=$DATA_VOLUME backups=$BACKUP_VOLUME"
echo "Systemd database target: $DATABASE_PATH"
echo "Actions: stop Compose, export Docker database and backups, select newest valid data, preserve host DB, start systemd."
[[ "$MODE" == plan ]] && { echo "Plan complete; runtime state was not changed."; exit 0; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemctl is required" >&2; exit 1; }
service_user="$(systemctl --user show alphawave-taskd.service -p User --value 2>/dev/null || true)"
[[ -z "$service_user" || "$service_user" == "$(id -un)" ]] || {
  echo "The user service identity differs from the current user; refusing rollback." >&2; exit 1
}
OVERRIDE="$(mktemp)"
trap 'rm -f "$OVERRIDE"' EXIT
python3 "$ROOT/scripts/admin/runtime_migration.py" compose-override --output "$OVERRIDE" --data-volume "$DATA_VOLUME" --backup-volume "$BACKUP_VOLUME"
[[ -f "$ENV_FILE" ]] || { echo "Docker environment file not found." >&2; exit 1; }
COMPOSE=(docker compose --env-file "$ENV_FILE" --project-directory "$ROOT" -f "$ROOT/compose.yaml" -f "$OVERRIDE")
docker info >/dev/null
verify_rollback_volume() {
  local name=$1 device=$2 inspection
  inspection="$(mktemp)"
  docker volume inspect "$name" >"$inspection"
  inspect_args=(--name "$name" --mode "$VOLUME_MODE" --input "$inspection")
  [[ -z "$device" ]] || inspect_args+=(--device "$device")
  python3 "$ROOT/scripts/admin/runtime_migration.py" volume-inspect-check "${inspect_args[@]}"
  rm -f "$inspection"
}
if [[ "$VOLUME_MODE" == external-bind-backed ]]; then
  [[ -n "$DATA_VOLUME_DEVICE" && -n "$BACKUP_VOLUME_DEVICE" ]] || { echo "External bind-backed mode requires both volume devices." >&2; exit 1; }
  python3 "$ROOT/scripts/admin/runtime_migration.py" volume-pair-check --data-device "$DATA_VOLUME_DEVICE" --backup-device "$BACKUP_VOLUME_DEVICE" >/dev/null
  for volume_device in "$DATA_VOLUME_DEVICE" "$BACKUP_VOLUME_DEVICE"; do
    probe="$volume_device"
    while [[ ! -e "$probe" ]]; do probe="$(dirname "$probe")"; done
    [[ "$(stat -c %d "$probe")" != "$(stat -c %d /)" ]] || {
      echo "External storage gate failed: device is on the host root filesystem." >&2; exit 1
    }
  done
elif [[ "$VOLUME_MODE" != local-driver-managed || -n "$DATA_VOLUME_DEVICE" || -n "$BACKUP_VOLUME_DEVICE" ]]; then
  echo "Invalid volume mode or devices for local-driver-managed mode." >&2; exit 1
fi
verify_rollback_volume "$DATA_VOLUME" "$DATA_VOLUME_DEVICE"
verify_rollback_volume "$BACKUP_VOLUME" "$BACKUP_VOLUME_DEVICE"
if systemctl --user is-active --quiet alphawave-taskd.service; then
  echo "Systemd is unexpectedly active; refusing to replace its database." >&2
  exit 1
fi
[[ "$MODE" == dry-run ]] && { echo "Dry run complete; volumes are accessible and no state was changed."; exit 0; }
[[ "${ALPHAWAVE_CONFIRM_DOCKER_ROLLBACK:-}" == 1 ]] || { echo "Set ALPHAWAVE_CONFIRM_DOCKER_ROLLBACK=1 for execution." >&2; exit 1; }
if [[ "$YES" != true ]]; then
  read -r -p "Type ROLLBACK TO SYSTEMD to continue: " confirmation
  [[ "$confirmation" == "ROLLBACK TO SYSTEMD" ]] || { echo "Cancelled."; exit 1; }
fi

[[ "$HEALTH_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { echo "Health timeout must be a positive integer." >&2; exit 1; }
[[ -f "$DATABASE_PATH" && ! -L "$DATABASE_PATH" ]] || {
  echo "A regular previous systemd database is required for transactional recovery." >&2
  exit 1
}
mkdir -p "$STAGING_DIR" "$(dirname "$DATABASE_PATH")"
chmod 700 "$STAGING_DIR"
LOCK_FILE="${ALPHAWAVE_ROLLBACK_LOCK_FILE:-$(dirname "$DATABASE_PATH")/.alphawave-taskd.rollback.lock}"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another rollback operation is already in progress." >&2; exit 1; }

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
TRANSACTION_DIR="$(mktemp -d "$STAGING_DIR/transaction.XXXXXXXX")"
chmod 700 "$TRANSACTION_DIR"
mkdir -p "$TRANSACTION_DIR/export/backups"
candidate="$TRANSACTION_DIR/docker-export.$timestamp.sqlite"
preserved="$TRANSACTION_DIR/systemd-db.pre-rollback.$timestamp.sqlite"
failed_candidate="$TRANSACTION_DIR/systemd-db.failed-new.$timestamp.sqlite"
metadata="$TRANSACTION_DIR/systemd-db.metadata.$timestamp.json"
transaction_state=before_replace
recovery_attempted=false
systemd_became_active=false

ensure_systemd_inactive() {
  if systemctl --user is-active --quiet alphawave-taskd.service; then
    systemd_became_active=true
    echo "Systemd became active during rollback; refusing database replacement." >&2
    return 1
  fi
}

wait_for_systemd_health() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT))
  while ((SECONDS < deadline)); do
    if systemctl --user is-active --quiet alphawave-taskd.service \
      && curl --fail --silent --show-error --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

stop_systemd() {
  systemctl --user stop alphawave-taskd.service >/dev/null 2>&1 || true
}

start_systemd() {
  systemctl --user unmask alphawave-taskd.service >/dev/null 2>&1 || true
  systemctl --user unmask --runtime alphawave-taskd.service >/dev/null 2>&1 || true
  systemctl --user enable alphawave-taskd.service >/dev/null
  systemctl --user start alphawave-taskd.service
  wait_for_systemd_health
}

print_recovery_artifacts() {
  echo "Recovery artifacts retained in the configured staging directory:" >&2
  echo "  Transaction: $(basename "$TRANSACTION_DIR")" >&2
  echo "  Docker export: $(basename "$candidate")" >&2
  [[ -f "$preserved" ]] && echo "  Previous systemd database: $(basename "$preserved")" >&2
  [[ -f "$failed_candidate" ]] && echo "  Failed replacement: $(basename "$failed_candidate")" >&2
  echo "Docker remains stopped; inspect the retained artifacts before retrying." >&2
}

recover_previous_systemd_database() {
  [[ "$transaction_state" == replace_started || "$transaction_state" == replaced ]] || return 0
  [[ "$recovery_attempted" == false ]] || return 1
  recovery_attempted=true
  stop_systemd
  if [[ -f "$DATABASE_PATH" ]]; then
    python3 "$ROOT/scripts/admin/runtime_migration.py" snapshot "$DATABASE_PATH" "$failed_candidate" || true
  fi
  if [[ ! -f "$preserved" ]]; then
    echo "Emergency recovery failed: no previous systemd database was available." >&2
    print_recovery_artifacts
    return 1
  fi
  if ! python3 "$ROOT/scripts/admin/rollback_transaction.py" atomic-replace \
      --source "$preserved" --target "$DATABASE_PATH" --metadata "$metadata"; then
    echo "Emergency recovery failed while restoring the previous database." >&2
    print_recovery_artifacts
    return 1
  fi
  if ! start_systemd; then
    echo "Emergency recovery restored the previous database, but systemd is not healthy." >&2
    print_recovery_artifacts
    return 1
  fi
  transaction_state=recovered
  echo "Rollback candidate was rejected; the previous systemd database was restored and is healthy." >&2
  print_recovery_artifacts
  return 0
}

cleanup_on_exit() {
  local status=$?
  set +e
  if [[ $status -ne 0 ]]; then
    if [[ ( "$transaction_state" == replace_started || "$transaction_state" == replaced ) && "$recovery_attempted" == false ]]; then
      recover_previous_systemd_database
    elif [[ "$transaction_state" == before_replace ]]; then
      if [[ "$systemd_became_active" == true ]]; then
        echo "Docker remains stopped because systemd is active; verify singleton state manually." >&2
      else
        echo "Rollback failed before replacement; restarting the unchanged Docker runtime." >&2
        "${COMPOSE[@]}" up -d >/dev/null 2>&1 || echo "Docker restart failed; manual recovery is required." >&2
      fi
    fi
  fi
  rm -f "$OVERRIDE"
  return "$status"
}
trap cleanup_on_exit EXIT

systemctl --user mask alphawave-taskd.service >/dev/null
ensure_systemd_inactive
"${COMPOSE[@]}" stop
ensure_systemd_inactive
docker run --rm --user 0 \
  -e "HOST_UID=$(id -u)" -e "HOST_GID=$(id -g)" \
  -v "$DATA_VOLUME:/data:ro" -v "$BACKUP_VOLUME:/backups:ro" \
  -v "$TRANSACTION_DIR/export:/export" alpine:3.20 sh -c '
    cp /data/alphawave-taskd.sqlite /export/ 2>/dev/null || true
    cp /data/alphawave-taskd.sqlite-wal /data/alphawave-taskd.sqlite-shm /export/ 2>/dev/null || true
    cp /backups/*.sqlite.gz /export/backups/ 2>/dev/null || true
    chown -R "$HOST_UID:$HOST_GID" /export
    find /export -type d -exec chmod 700 {} +
    find /export -type f -exec chmod 600 {} +
  '
kind="$(python3 "$ROOT/scripts/admin/runtime_migration.py" newest --directory "$TRANSACTION_DIR/export" --output "$candidate")"
candidate_hash="$(sha256sum "$candidate" | awk '{print $1}')"
candidate_summary="$(python3 "$ROOT/scripts/admin/runtime_migration.py" summary "$candidate")"
python3 "$ROOT/scripts/admin/sqlite_snapshot_verifier.py" verify "$candidate" --output "$TRANSACTION_DIR/docker-export-verification.json"
python3 "$ROOT/scripts/admin/sqlite_snapshot_verifier.py" schema-compare "$DATABASE_PATH" "$candidate" --output "$TRANSACTION_DIR/docker-export-schema.json"
python3 "$ROOT/scripts/admin/rollback_transaction.py" capture-metadata --database "$DATABASE_PATH" --output "$metadata"
if [[ -f "$DATABASE_PATH" ]]; then
  python3 "$ROOT/scripts/admin/runtime_migration.py" snapshot "$DATABASE_PATH" "$preserved"
  chmod 600 "$preserved"
fi
ensure_systemd_inactive
transaction_state=replace_started
python3 "$ROOT/scripts/admin/rollback_transaction.py" atomic-replace --source "$candidate" --target "$DATABASE_PATH" --metadata "$metadata"
transaction_state=replaced
installed_hash="$(sha256sum "$DATABASE_PATH" | awk '{print $1}')"
[[ "$installed_hash" == "$candidate_hash" ]] || { echo "Installed database hash differs from the validated Docker export." >&2; recover_previous_systemd_database || true; exit 1; }
if ! start_systemd; then
  echo "The rollback candidate did not become healthy; restoring the previous systemd database." >&2
  recover_previous_systemd_database || true
  exit 1
fi
installed_summary="$(python3 "$ROOT/scripts/admin/runtime_migration.py" summary "$DATABASE_PATH")"
if [[ "$installed_summary" != "$candidate_summary" ]]; then
  echo "The healthy rollback candidate did not retain the expected database counts." >&2
  recover_previous_systemd_database || true
  exit 1
fi
transaction_state=committed
echo "Rollback completed from newest Docker $kind. Docker volumes were retained."
