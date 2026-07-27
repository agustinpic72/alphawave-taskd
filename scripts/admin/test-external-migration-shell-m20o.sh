#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_STORAGE_ROOT="${ALPHAWAVE_M20O_TEST_STORAGE_ROOT:-$(python3 - <<'PY'
import os
from pathlib import Path

candidates = []
for raw in Path("/proc/self/mounts").read_text().splitlines():
    fields = raw.split()
    if len(fields) < 3 or fields[2] not in {"ext4", "xfs", "btrfs", "zfs"}:
        continue
    mount = Path(fields[1].replace("\\040", " "))
    try:
        filesystem = os.statvfs(mount)
    except OSError:
        continue
    available = filesystem.f_bavail * filesystem.f_frsize
    if available >= 100 * 1024**3 and os.access(mount, os.W_OK | os.X_OK):
        candidates.append((available, mount))
if not candidates:
    raise SystemExit("No writable non-root filesystem satisfies the M20O test gate")
print(max(candidates)[1])
PY
)}"
TMP="$(mktemp -d "$TEST_STORAGE_ROOT/alphawave-m20o-test.XXXXXXXX")"
ROOT_FS_BASE="$(python3 - <<'PY'
import os
from pathlib import Path

root_device = os.stat("/").st_dev
for candidate in (Path("/tmp"), Path.home(), Path("/var/tmp")):
    if candidate.is_dir() and os.stat(candidate).st_dev == root_device and os.access(candidate, os.W_OK | os.X_OK):
        print(candidate)
        break
else:
    raise SystemExit("No writable directory exists on the host root filesystem")
PY
)"
ROOT_FS_TMP="$(mktemp -d "$ROOT_FS_BASE/alphawave-m20o-rootfs.XXXXXXXX")"
trap 'rm -rf "$TMP" "$ROOT_FS_TMP"' EXIT

FAKE_BIN="$TMP/bin"
mkdir -p "$FAKE_BIN"

cat >"$FAKE_BIN/df" <<'SH'
#!/usr/bin/env bash
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
printf '/dev/fake 524288000 52428800 471859200 10%% /\n'
SH

cat >"$FAKE_BIN/systemctl" <<'SH'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >>"$FAKE_SYSTEMCTL_LOG"
if [[ "$*" == *"is-active"* ]]; then
  exit 1
fi
exit 0
SH

cat >"$FAKE_BIN/curl" <<'SH'
#!/usr/bin/env bash
exit 0
SH

cat >"$FAKE_BIN/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

printf '%q ' "$@" >>"$FAKE_DOCKER_LOG"
printf '\n' >>"$FAKE_DOCKER_LOG"

if [[ "${1:-}" == info ]]; then
  exit 0
fi

if [[ "${1:-}" == compose ]]; then
  if [[ " $* " == *" config "* && " $* " == *" --format json "* ]]; then
    printf '%s\n' '{"services":{"api":{"image":"alphawave-taskd-api:test"}}}'
  elif [[ " $* " == *" config "* ]]; then
    printf '%s\n' 'services: {}'
  elif [[ " $* " == *" port web 8080 "* ]]; then
    printf '%s\n' '127.0.0.1:18080'
  fi
  exit 0
fi

if [[ "${1:-}" == image && "${2:-}" == inspect ]]; then
  printf '%s\n' 'sha256:fake-api-image'
  exit 0
fi

if [[ "${1:-}" == volume && "${2:-}" == create ]]; then
  name="${!#}"
  device=""
  for argument in "$@"; do
    [[ "$argument" != device=* ]] || device="${argument#device=}"
  done
  printf '%s\n' "$device" >"$FAKE_VOLUME_STATE/$name"
  printf '%s\n' "$name"
  exit 0
fi

if [[ "${1:-}" == volume && "${2:-}" == inspect ]]; then
  name="${3:?missing volume name}"
  state="$FAKE_VOLUME_STATE/$name"
  [[ -f "$state" ]] || exit 1
  device="$(<"$state")"
  if [[ "${FAKE_WRONG_DEVICE_VOLUME:-}" == "$name" ]]; then
    device="$FAKE_WRONG_DEVICE_PATH"
  fi
  if [[ -n "$device" ]]; then
    python3 - "$name" "$device" <<'PY'
import json, sys
print(json.dumps([{"Name": sys.argv[1], "Driver": "local", "Options": {
    "type": "none", "o": "bind", "device": sys.argv[2]
}}]))
PY
  else
    python3 - "$name" <<'PY'
import json, sys
print(json.dumps([{"Name": sys.argv[1], "Driver": "local", "Options": None}]))
PY
  fi
  exit 0
fi

if [[ "${1:-}" == run ]]; then
  baseline=""
  for argument in "$@"; do
    case "$argument" in
      *:/baseline|*:/baseline:ro) baseline="${argument%%:/baseline*}" ;;
    esac
  done

  if [[ " $* " == *" /target/.alphawave-taskd.sqlite.migrating "* ]]; then
    cp "$FAKE_BASELINE_DIR/alphawave-taskd.sqlite" "$FAKE_DATA_DEVICE/alphawave-taskd.sqlite"
    exit 0
  fi

  if [[ " $* " == *" sha256sum /data/alphawave-taskd.sqlite "* ]]; then
    if [[ "${FAKE_HASH_MISMATCH:-0}" == 1 ]]; then
      printf '%064d  /data/alphawave-taskd.sqlite\n' 0
    else
      sha256sum "$FAKE_DATA_DEVICE/alphawave-taskd.sqlite" | sed 's# .*#  /data/alphawave-taskd.sqlite#'
    fi
    exit 0
  fi

  if [[ "$*" == *"destination-after-seed.sqlite"* ]]; then
    cp "$FAKE_DATA_DEVICE/alphawave-taskd.sqlite" "$baseline/destination-after-seed.sqlite"
    case "${FAKE_DESTINATION_MUTATION:-none}" in
      missing-table)
        python3 - "$baseline/destination-after-seed.sqlite" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as database:
    database.execute("DROP TABLE secondary")
PY
        ;;
      count-mismatch)
        python3 - "$baseline/destination-after-seed.sqlite" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as database:
    database.execute("INSERT INTO tasks(title) VALUES ('synthetic mismatch')")
PY
        ;;
    esac
    chmod 600 "$baseline/destination-after-seed.sqlite"
    exit 0
  fi

  exit 0
fi

echo "unsupported fake docker invocation" >&2
exit 97
SH

chmod +x "$FAKE_BIN/df" "$FAKE_BIN/systemctl" "$FAKE_BIN/curl" "$FAKE_BIN/docker"

make_source_database() {
  local database=$1
  python3 - "$database" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as database:
    database.execute("PRAGMA user_version = 7")
    database.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
    database.execute("CREATE TABLE secondary(id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id))")
    database.execute("CREATE INDEX secondary_task_idx ON secondary(task_id)")
    database.execute("CREATE TRIGGER tasks_touch AFTER INSERT ON tasks BEGIN UPDATE tasks SET title = title WHERE id = NEW.id; END")
    database.execute("INSERT INTO tasks(title) VALUES ('row-secret-m20o')")
    database.execute("INSERT INTO secondary(task_id) VALUES (1)")
PY
}

prepare_case() {
  local name=$1
  CASE_DIR="$TMP/$name"
  DB="$CASE_DIR/source.sqlite"
  ENV_FILE="$CASE_DIR/.env.docker"
  BASELINE="$CASE_DIR/baseline"
  DATA_DEVICE="$CASE_DIR/external/data"
  BACKUP_DEVICE="$CASE_DIR/external/backups"
  VOLUME_STATE="$CASE_DIR/volume-state"
  DOCKER_LOG="$CASE_DIR/docker.log"
  SYSTEMCTL_LOG="$CASE_DIR/systemctl.log"
  mkdir -p "$DATA_DEVICE" "$BACKUP_DEVICE" "$VOLUME_STATE"
  : >"$ENV_FILE"
  : >"$DOCKER_LOG"
  : >"$SYSTEMCTL_LOG"
  make_source_database "$DB"
}

run_migration() {
  local mutation=${1:-none}
  PATH="$FAKE_BIN:$PATH" \
  FAKE_VOLUME_STATE="$VOLUME_STATE" \
  FAKE_DOCKER_LOG="$DOCKER_LOG" \
  FAKE_SYSTEMCTL_LOG="$SYSTEMCTL_LOG" \
  FAKE_BASELINE_DIR="$BASELINE" \
  FAKE_DATA_DEVICE="$DATA_DEVICE" \
  FAKE_DESTINATION_MUTATION="$mutation" \
  ALPHAWAVE_CONFIRM_DOCKER_CUTOVER=1 \
    "$ROOT/scripts/admin/migrate-systemd-runtime-to-docker.sh" \
      --execute --yes \
      --database "$DB" --env-file "$ENV_FILE" --baseline-dir "$BASELINE" \
      --data-volume "m20o-${CASE_NAME}-data" \
      --backup-volume "m20o-${CASE_NAME}-backups" \
      --volume-mode external-bind-backed \
      --data-volume-device "$DATA_DEVICE" \
      --backup-volume-device "$BACKUP_DEVICE"
}

prepare_case success
CASE_NAME=success
if ! run_migration >"$CASE_DIR/output.log" 2>&1; then
  cat "$CASE_DIR/output.log" >&2
  exit 1
fi
cmp "$BASELINE/alphawave-taskd.sqlite" "$DATA_DEVICE/alphawave-taskd.sqlite"
grep -Fq -- '--opt type=none --opt o=bind' "$DOCKER_LOG"
grep -Fq -- "--opt device=$DATA_DEVICE m20o-success-data" "$DOCKER_LOG"
grep -Fq -- "--opt device=$BACKUP_DEVICE m20o-success-backups" "$DOCKER_LOG"
python3 - "$BASELINE/manifest.json" "$TMP" <<'PY'
import json, sys
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text())
verification = manifest["migration_verification"]
serialized = json.dumps(verification, sort_keys=True)
assert verification["comparison"]["matched"] is True
assert verification["source_snapshot"]["table_counts"] == {"secondary": 1, "tasks": 1}
assert verification["source_snapshot"]["sha256"] == verification["destination_snapshot"]["sha256"]
assert verification["source_snapshot"]["indexes"]["count"] == 1
assert verification["source_snapshot"]["triggers"]["count"] == 1
assert "row-secret-m20o" not in serialized
assert sys.argv[2] not in serialized
assert "path" not in serialized.lower()
PY

prepare_case wrong-device
CASE_NAME=wrong-device
printf '%s\n' "$DATA_DEVICE" >"$VOLUME_STATE/m20o-wrong-device-data"
printf '%s\n' "$BACKUP_DEVICE" >"$VOLUME_STATE/m20o-wrong-device-backups"
set +e
wrong_output="$({
  PATH="$FAKE_BIN:$PATH" \
  FAKE_VOLUME_STATE="$VOLUME_STATE" FAKE_DOCKER_LOG="$DOCKER_LOG" \
  FAKE_SYSTEMCTL_LOG="$SYSTEMCTL_LOG" FAKE_BASELINE_DIR="$BASELINE" \
  FAKE_DATA_DEVICE="$DATA_DEVICE" FAKE_WRONG_DEVICE_VOLUME=m20o-wrong-device-data \
  FAKE_WRONG_DEVICE_PATH="$CASE_DIR/external/other" \
    "$ROOT/scripts/admin/migrate-systemd-runtime-to-docker.sh" \
      --dry-run --resume --database "$DB" --env-file "$ENV_FILE" --baseline-dir "$BASELINE" \
      --data-volume m20o-wrong-device-data --backup-volume m20o-wrong-device-backups \
      --volume-mode external-bind-backed --data-volume-device "$DATA_DEVICE" \
      --backup-volume-device "$BACKUP_DEVICE"
} 2>&1)"
wrong_status=$?
set -e
[[ $wrong_status -ne 0 ]]
grep -Fq "options differ" <<<"$wrong_output"
[[ ! -s "$SYSTEMCTL_LOG" ]]

printf '%s\n' "$DATA_DEVICE" >"$VOLUME_STATE/m20o-same-device-data"
printf '%s\n' "$DATA_DEVICE" >"$VOLUME_STATE/m20o-same-device-backups"
set +e
same_device_output="$(PATH="$FAKE_BIN:$PATH" FAKE_VOLUME_STATE="$VOLUME_STATE" \
  FAKE_DOCKER_LOG="$DOCKER_LOG" FAKE_SYSTEMCTL_LOG="$SYSTEMCTL_LOG" \
  "$ROOT/scripts/admin/rollback-docker-to-systemd.sh" --dry-run \
    --database "$DB" --env-file "$ENV_FILE" \
    --data-volume m20o-same-device-data --backup-volume m20o-same-device-backups \
    --volume-mode external-bind-backed --data-volume-device "$DATA_DEVICE" \
    --backup-volume-device "$DATA_DEVICE" 2>&1)"
same_device_status=$?
set -e
[[ $same_device_status -ne 0 ]]
grep -Fq "must be distinct" <<<"$same_device_output"

root_db="$ROOT_FS_TMP/source.sqlite"
root_env="$ROOT_FS_TMP/.env.docker"
root_data="$ROOT_FS_TMP/data"
root_backups="$ROOT_FS_TMP/backups"
mkdir -p "$root_data" "$root_backups" "$ROOT_FS_TMP/volumes"
: >"$root_env"; : >"$ROOT_FS_TMP/docker.log"; : >"$ROOT_FS_TMP/systemctl.log"
make_source_database "$root_db"
set +e
root_output="$(PATH="$FAKE_BIN:$PATH" FAKE_VOLUME_STATE="$ROOT_FS_TMP/volumes" \
  FAKE_DOCKER_LOG="$ROOT_FS_TMP/docker.log" FAKE_SYSTEMCTL_LOG="$ROOT_FS_TMP/systemctl.log" \
  "$ROOT/scripts/admin/migrate-systemd-runtime-to-docker.sh" --dry-run \
    --database "$root_db" --env-file "$root_env" --baseline-dir "$ROOT_FS_TMP/baseline" \
    --data-volume m20o-root-data --backup-volume m20o-root-backups \
    --volume-mode external-bind-backed --data-volume-device "$root_data" \
    --backup-volume-device "$root_backups" 2>&1)"
root_status=$?
set -e
[[ $root_status -ne 0 ]]
grep -Fq "host root filesystem" <<<"$root_output"
printf '%s\n' "$root_data" >"$ROOT_FS_TMP/volumes/m20o-root-rollback-data"
printf '%s\n' "$root_backups" >"$ROOT_FS_TMP/volumes/m20o-root-rollback-backups"
set +e
root_rollback_output="$(PATH="$FAKE_BIN:$PATH" FAKE_VOLUME_STATE="$ROOT_FS_TMP/volumes" \
  FAKE_DOCKER_LOG="$ROOT_FS_TMP/docker.log" FAKE_SYSTEMCTL_LOG="$ROOT_FS_TMP/systemctl.log" \
  "$ROOT/scripts/admin/rollback-docker-to-systemd.sh" --dry-run \
    --database "$root_db" --env-file "$root_env" \
    --data-volume m20o-root-rollback-data --backup-volume m20o-root-rollback-backups \
    --volume-mode external-bind-backed --data-volume-device "$root_data" \
    --backup-volume-device "$root_backups" 2>&1)"
root_rollback_status=$?
set -e
[[ $root_rollback_status -ne 0 ]]
grep -Fq "host root filesystem" <<<"$root_rollback_output"

prepare_case sha-mismatch
CASE_NAME=sha-mismatch
set +e
PATH="$FAKE_BIN:$PATH" \
FAKE_VOLUME_STATE="$VOLUME_STATE" FAKE_DOCKER_LOG="$DOCKER_LOG" \
FAKE_SYSTEMCTL_LOG="$SYSTEMCTL_LOG" FAKE_BASELINE_DIR="$BASELINE" \
FAKE_DATA_DEVICE="$DATA_DEVICE" FAKE_HASH_MISMATCH=1 \
ALPHAWAVE_CONFIRM_DOCKER_CUTOVER=1 \
  "$ROOT/scripts/admin/migrate-systemd-runtime-to-docker.sh" \
    --execute --yes --database "$DB" --env-file "$ENV_FILE" --baseline-dir "$BASELINE" \
    --data-volume m20o-sha-mismatch-data --backup-volume m20o-sha-mismatch-backups \
    --volume-mode external-bind-backed --data-volume-device "$DATA_DEVICE" \
    --backup-volume-device "$BACKUP_DEVICE" >"$CASE_DIR/output.log" 2>&1
sha_status=$?
set -e
[[ $sha_status -ne 0 ]]
grep -Fq "Source snapshot and destination hashes differ." "$CASE_DIR/output.log"
! grep -Fq "up -d api" "$DOCKER_LOG"

for mutation in missing-table count-mismatch; do
  prepare_case "$mutation"
  CASE_NAME="$mutation"
  set +e
  run_migration "$mutation" >"$CASE_DIR/output.log" 2>&1
  status=$?
  set -e
  [[ $status -ne 0 ]]
  [[ -f "$BASELINE/migration-verification.json" ]]
  python3 - "$BASELINE/migration-verification.json" "$mutation" "$TMP" <<'PY'
import json, sys
from pathlib import Path
report = json.loads(Path(sys.argv[1]).read_text())
mutation = sys.argv[2]
mismatches = set(report["comparison"]["mismatches"])
assert report["comparison"]["matched"] is False
assert "sha256" in mismatches
assert "table_counts" in mismatches
if mutation == "missing-table":
    assert "schema_digest" in mismatches
serialized = json.dumps(report, sort_keys=True)
assert "row-secret-m20o" not in serialized
assert "synthetic mismatch" not in serialized
assert sys.argv[3] not in serialized
PY
  ! grep -Fq "up -d api" "$DOCKER_LOG"
done

echo "external migration shell M20O drills: PASS"
