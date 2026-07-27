#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE=plan
YES=false
RESUME=false
DATABASE_PATH="${ALPHAWAVE_SYSTEMD_DATABASE_PATH:-$ROOT/data/alphawave-taskd.sqlite}"
DATA_VOLUME="${ALPHAWAVE_DATA_VOLUME:-alphawave_data_soak_v1}"
BACKUP_VOLUME="${ALPHAWAVE_BACKUPS_VOLUME:-alphawave_backups_soak_v1}"
VOLUME_MODE="${ALPHAWAVE_VOLUME_MODE:-local-driver-managed}"
DATA_VOLUME_DEVICE="${ALPHAWAVE_DATA_VOLUME_DEVICE:-}"
BACKUP_VOLUME_DEVICE="${ALPHAWAVE_BACKUPS_VOLUME_DEVICE:-}"
ENV_FILE="${ALPHAWAVE_DOCKER_ENV_FILE:-$ROOT/.env.docker}"
BASELINE_DIR=""

usage() { echo "Usage: $0 [--plan|--dry-run|--execute] [--yes] [--resume] [--database PATH] [--env-file PATH] [--baseline-dir DIR] [--data-volume NAME] [--backup-volume NAME] [--volume-mode MODE] [--data-volume-device PATH] [--backup-volume-device PATH]"; }
while (($#)); do
  case "$1" in
    --plan|--dry-run|--execute) MODE="${1#--}" ;;
    --yes) YES=true ;;
    --resume) RESUME=true ;;
    --database) DATABASE_PATH="${2:?missing path}"; shift ;;
    --env-file) ENV_FILE="${2:?missing path}"; shift ;;
    --baseline-dir) BASELINE_DIR="${2:?missing path}"; shift ;;
    --data-volume) DATA_VOLUME="${2:?missing name}"; shift ;;
    --backup-volume) BACKUP_VOLUME="${2:?missing name}"; shift ;;
    --volume-mode) VOLUME_MODE="${2:?missing mode}"; shift ;;
    --data-volume-device) DATA_VOLUME_DEVICE="${2:?missing path}"; shift ;;
    --backup-volume-device) BACKUP_VOLUME_DEVICE="${2:?missing path}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac; shift
done
BASELINE_DIR="${BASELINE_DIR:-$ROOT/data/migration-baselines/$(date -u +%Y%m%dT%H%M%SZ)}"
echo "Mode: $MODE"
echo "Systemd database: $DATABASE_PATH"
echo "Baseline directory: $BASELINE_DIR"
echo "Target volumes: data=$DATA_VOLUME backups=$BACKUP_VOLUME"
echo "Volume mode: $VOLUME_MODE"
echo "Docker environment file: $ENV_FILE"
echo "Actions: capture baseline, stop systemd, seed empty Docker volumes, start Compose, verify health."
[[ "$MODE" == plan ]] && { echo "Plan complete; runtime state was not changed."; exit 0; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemctl is required" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Docker environment file not found. Run scripts/admin/prepare-docker-env.sh first." >&2; exit 1; }
available_kib="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
used_percent="$(df -Pk "$ROOT" | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
(( available_kib >= 20 * 1024 * 1024 )) || { echo "Storage gate failed: less than 20 GiB available." >&2; exit 1; }
(( used_percent <= 93 )) || { echo "Storage gate failed: filesystem usage exceeds 93%." >&2; exit 1; }
OVERRIDE="$(mktemp)"
trap 'rm -f "$OVERRIDE"' EXIT
python3 "$ROOT/scripts/admin/runtime_migration.py" compose-override --output "$OVERRIDE" --data-volume "$DATA_VOLUME" --backup-volume "$BACKUP_VOLUME"
COMPOSE=(docker compose --env-file "$ENV_FILE" --project-directory "$ROOT" -f "$ROOT/compose.yaml" -f "$OVERRIDE")
volume_args=(--mode "$VOLUME_MODE")
if [[ "$VOLUME_MODE" == external-bind-backed ]]; then
  [[ -n "$DATA_VOLUME_DEVICE" && -n "$BACKUP_VOLUME_DEVICE" ]] || { echo "External bind-backed mode requires both volume devices." >&2; exit 1; }
  python3 "$ROOT/scripts/admin/runtime_migration.py" volume-pair-check --data-device "$DATA_VOLUME_DEVICE" --backup-device "$BACKUP_VOLUME_DEVICE" >/dev/null
  for volume_pair in "$DATA_VOLUME|$DATA_VOLUME_DEVICE" "$BACKUP_VOLUME|$BACKUP_VOLUME_DEVICE"; do
    volume_name="${volume_pair%%|*}"; volume_device="${volume_pair#*|}"
    if [[ -d "$volume_device" ]]; then
      python3 "$ROOT/scripts/admin/runtime_migration.py" volume-device-check --name "$volume_name" "${volume_args[@]}" --device "$volume_device"
    else
      python3 "$ROOT/scripts/admin/runtime_migration.py" volume-device-check --name "$volume_name" "${volume_args[@]}" --device "$volume_device" --validate-only
    fi
  done
  external_storage_gate() {
    local device=$1 probe=$1 available used root_device target_device
    while [[ ! -e "$probe" ]]; do probe="$(dirname "$probe")"; done
    root_device="$(stat -c %d /)"
    target_device="$(stat -c %d "$probe")"
    [[ "$target_device" != "$root_device" ]] || { echo "External storage gate failed: device is on the host root filesystem." >&2; exit 1; }
    available="$(df -Pk "$probe" | awk 'NR==2 {print $4}')"
    used="$(df -Pk "$probe" | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
    (( available >= 100 * 1024 * 1024 )) || { echo "External storage gate failed: less than 100 GiB available." >&2; exit 1; }
    (( used <= 85 )) || { echo "External storage gate failed: filesystem usage exceeds 85%." >&2; exit 1; }
  }
  external_storage_gate "$DATA_VOLUME_DEVICE"
  external_storage_gate "$BACKUP_VOLUME_DEVICE"
elif [[ "$VOLUME_MODE" != local-driver-managed || -n "$DATA_VOLUME_DEVICE" || -n "$BACKUP_VOLUME_DEVICE" ]]; then
  echo "Invalid volume mode or devices for local-driver-managed mode." >&2; exit 1
fi
"$ROOT/scripts/admin/capture-docker-baseline.sh" --dry-run --database "$DATABASE_PATH" --env-file "$ENV_FILE" --compose-override "$OVERRIDE" --output-dir "$BASELINE_DIR" --data-volume "$DATA_VOLUME" --backup-volume "$BACKUP_VOLUME" --volume-mode "$VOLUME_MODE" --data-volume-device "$DATA_VOLUME_DEVICE" --backup-volume-device "$BACKUP_VOLUME_DEVICE"
docker info >/dev/null
"${COMPOSE[@]}" config --quiet
data_exists=false; backup_exists=false
docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1 && data_exists=true
docker volume inspect "$BACKUP_VOLUME" >/dev/null 2>&1 && backup_exists=true
verify_existing_volume() {
  local name=$1 device=$2 inspection
  inspection="$(mktemp)"
  docker volume inspect "$name" >"$inspection"
  inspect_args=(--name "$name" --mode "$VOLUME_MODE" --input "$inspection")
  [[ -z "$device" ]] || inspect_args+=(--device "$device")
  python3 "$ROOT/scripts/admin/runtime_migration.py" volume-inspect-check "${inspect_args[@]}"
  rm -f "$inspection"
}
[[ "$data_exists" == false ]] || verify_existing_volume "$DATA_VOLUME" "$DATA_VOLUME_DEVICE"
[[ "$backup_exists" == false ]] || verify_existing_volume "$BACKUP_VOLUME" "$BACKUP_VOLUME_DEVICE"
if [[ "$data_exists" == true || "$backup_exists" == true ]]; then
  if [[ "$RESUME" != true || "$data_exists" != true || "$backup_exists" != true ]]; then
    echo "Target volumes already exist; use --resume only for a previously interrupted migration with both named volumes." >&2; exit 1
  fi
  echo "Resume mode: existing target volumes will be revalidated and atomically reseeded."
elif [[ "$VOLUME_MODE" == external-bind-backed ]]; then
  for volume_pair in "$DATA_VOLUME|$DATA_VOLUME_DEVICE" "$BACKUP_VOLUME|$BACKUP_VOLUME_DEVICE"; do
    volume_name="${volume_pair%%|*}"; volume_device="${volume_pair#*|}"
    if [[ -d "$volume_device" ]]; then
      python3 "$ROOT/scripts/admin/runtime_migration.py" volume-device-check --name "$volume_name" --mode "$VOLUME_MODE" --device "$volume_device" --require-empty >/dev/null
    else
      python3 "$ROOT/scripts/admin/runtime_migration.py" volume-device-check --name "$volume_name" --mode "$VOLUME_MODE" --device "$volume_device" --validate-only >/dev/null
      echo "External device will be created after explicit execution confirmation."
    fi
  done
fi
[[ "$MODE" == dry-run ]] && { echo "Dry run complete; no services or volumes were changed."; exit 0; }
[[ "${ALPHAWAVE_CONFIRM_DOCKER_CUTOVER:-}" == 1 ]] || { echo "Set ALPHAWAVE_CONFIRM_DOCKER_CUTOVER=1 for execution." >&2; exit 1; }
if [[ "$YES" != true ]]; then
  read -r -p "Type MIGRATE TO DOCKER to continue: " confirmation
  [[ "$confirmation" == "MIGRATE TO DOCKER" ]] || { echo "Cancelled."; exit 1; }
fi

preflight_dir="${BASELINE_DIR}-online"
"$ROOT/scripts/admin/capture-docker-baseline.sh" --execute --database "$DATABASE_PATH" --env-file "$ENV_FILE" --compose-override "$OVERRIDE" --output-dir "$preflight_dir" --data-volume "$DATA_VOLUME" --backup-volume "$BACKUP_VOLUME" --volume-mode "$VOLUME_MODE" --data-volume-device "$DATA_VOLUME_DEVICE" --backup-volume-device "$BACKUP_VOLUME_DEVICE"
"${COMPOSE[@]}" build api web
systemd_quiesced=false
cleanup_on_error() {
  echo "Migration failed; restoring the original systemd runtime. Baselines remain at $preflight_dir and $BASELINE_DIR." >&2
  "${COMPOSE[@]}" down >/dev/null 2>&1 || true
  if [[ "$systemd_quiesced" == true ]]; then
    systemctl --user unmask alphawave-taskd.service >/dev/null 2>&1 || true
    systemctl --user unmask --runtime alphawave-taskd.service >/dev/null 2>&1 || true
    systemctl --user enable alphawave-taskd.service >/dev/null 2>&1 || true
    systemctl --user start alphawave-taskd.service >/dev/null 2>&1 || true
  fi
}
trap cleanup_on_error ERR
fail_after_quiesce() {
  echo "$1" >&2
  cleanup_on_error
  trap - ERR
  exit 1
}
systemctl --user stop alphawave-taskd
systemd_quiesced=true
systemctl --user disable alphawave-taskd.service >/dev/null
systemctl --user mask alphawave-taskd.service >/dev/null
systemctl --user is-active --quiet alphawave-taskd && fail_after_quiesce "systemd did not stop"
if command -v lsof >/dev/null && lsof "$DATABASE_PATH" 2>/dev/null | grep -q .; then
  fail_after_quiesce "Database still has open file descriptors."
fi
"$ROOT/scripts/admin/capture-docker-baseline.sh" --execute --database "$DATABASE_PATH" --env-file "$ENV_FILE" --compose-override "$OVERRIDE" --output-dir "$BASELINE_DIR" --data-volume "$DATA_VOLUME" --backup-volume "$BACKUP_VOLUME" --volume-mode "$VOLUME_MODE" --data-volume-device "$DATA_VOLUME_DEVICE" --backup-volume-device "$BACKUP_VOLUME_DEVICE"
python3 "$ROOT/scripts/admin/rollback_transaction.py" capture-metadata --database "$DATABASE_PATH" --output "$BASELINE_DIR/systemd-ownership.json"
create_volume() {
  local name=$1 device=$2
  if [[ "$VOLUME_MODE" == external-bind-backed ]]; then
    python3 "$ROOT/scripts/admin/runtime_migration.py" volume-device-check --name "$name" --mode "$VOLUME_MODE" --device "$device" --create --require-empty >/dev/null
    docker volume create --driver local --opt type=none --opt o=bind --opt "device=$device" "$name" >/dev/null
  else
    docker volume create --driver local "$name" >/dev/null
  fi
  verify_existing_volume "$name" "$device"
}
[[ "$data_exists" == true ]] || create_volume "$DATA_VOLUME" "$DATA_VOLUME_DEVICE"
[[ "$backup_exists" == true ]] || create_volume "$BACKUP_VOLUME" "$BACKUP_VOLUME_DEVICE"
docker run --rm --network none --user 0 -e "HOST_GID=$(id -g)" \
  -v "$DATA_VOLUME:/target" -v "$BACKUP_VOLUME:/target-backups" \
  -v "$BASELINE_DIR:/baseline:ro" -v "$ROOT/data/backups:/source-backups:ro" \
  alpine:3.20 sh -ec '
    chown 10001:"$HOST_GID" /target /target-backups
    chmod 770 /target /target-backups
    cp /baseline/alphawave-taskd.sqlite /target/.alphawave-taskd.sqlite.migrating
    chown 10001:10001 /target/.alphawave-taskd.sqlite.migrating
    chmod 600 /target/.alphawave-taskd.sqlite.migrating
    mv /target/.alphawave-taskd.sqlite.migrating /target/alphawave-taskd.sqlite
    find /source-backups -maxdepth 1 -type f \( -name "*.sqlite.gz" -o -name "*.sqlite.gz.json" \) -exec cp -p {} /target-backups/ \;
    chown -R 10001:"$HOST_GID" /target-backups
    find /target-backups -type f -exec chmod 600 {} \;
  '
source_snapshot="$BASELINE_DIR/alphawave-taskd.sqlite"
source_hash="$(sha256sum "$source_snapshot" | awk '{print $1}')"
destination_hash="$(docker run --rm --network none --read-only --user 10001:10001 -v "$DATA_VOLUME:/data:ro" alpine:3.20 sha256sum /data/alphawave-taskd.sqlite | awk '{print $1}')"
[[ "$source_hash" == "$destination_hash" ]] || fail_after_quiesce "Source snapshot and destination hashes differ."
destination_snapshot="$BASELINE_DIR/destination-after-seed.sqlite"
docker run --rm --network none --user 0 -e "HOST_UID=$(id -u)" -e "HOST_GID=$(id -g)" \
  -v "$DATA_VOLUME:/data:ro" -v "$BASELINE_DIR:/baseline" alpine:3.20 sh -ec \
  'cp /data/alphawave-taskd.sqlite /baseline/destination-after-seed.sqlite; chown "$HOST_UID:$HOST_GID" /baseline/destination-after-seed.sqlite; chmod 600 /baseline/destination-after-seed.sqlite'
python3 "$ROOT/scripts/admin/sqlite_snapshot_verifier.py" compare "$source_snapshot" "$destination_snapshot" --output "$BASELINE_DIR/migration-verification.json" || fail_after_quiesce "Exhaustive source/destination verification failed."
api_image_ref="$("${COMPOSE[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["api"]["image"])')"
api_image_id="$(docker image inspect "$api_image_ref" --format '{{.Id}}' 2>/dev/null || true)"
[[ -n "$api_image_id" ]] || fail_after_quiesce "Built API image was not found."
python3 - "$BASELINE_DIR/manifest.json" "$BASELINE_DIR/migration-verification.json" "$BASELINE_DIR/systemd-ownership.json" "$VOLUME_MODE" "$DATA_VOLUME_DEVICE" "$BACKUP_VOLUME_DEVICE" "$destination_hash" <<'PY'
import json, os, sys
from pathlib import Path
manifest_path, verification_path, ownership_path = map(Path, sys.argv[1:4])
manifest = json.loads(manifest_path.read_text())
manifest["migration_verification"] = json.loads(verification_path.read_text())
manifest["systemd_ownership"] = json.loads(ownership_path.read_text())
manifest["volumes"]["mode"] = sys.argv[4]
manifest["volumes"]["data"]["device"] = sys.argv[5] or None
manifest["volumes"]["backups"]["device"] = sys.argv[6] or None
manifest["migration_verification"]["destination_owned_sha256"] = sys.argv[7]
temporary = manifest_path.with_name(f".{manifest_path.name}.tmp-{os.getpid()}")
temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
os.replace(temporary, manifest_path)
PY
"${COMPOSE[@]}" up -d api
wait_for_api_health() {
  local attempt
  for attempt in $(seq 1 30); do
    if "${COMPOSE[@]}" exec -T api python -c 'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:8711/api/health", timeout=2).status == 200' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}
wait_for_api_health || fail_after_quiesce "Docker API did not become healthy within 30 seconds."
"${COMPOSE[@]}" up -d web
gateway="$("${COMPOSE[@]}" port web 8080)"
wait_for_web_health() {
  local attempt
  for attempt in $(seq 1 30); do
    if curl --fail --silent --show-error --max-time 2 "http://$gateway/healthz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}
wait_for_web_health || fail_after_quiesce "Docker web service did not become healthy within 30 seconds."
trap - ERR
echo "Migration completed. Baseline manifest: $BASELINE_DIR/manifest.json"
