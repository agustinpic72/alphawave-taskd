#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

make_database() {
  local path=$1 marker=$2
  python3 - "$path" "$marker" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as connection:
    connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES (?)", (sys.argv[2],))
PY
}

read_marker() {
  python3 - "$1" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as connection:
    print(connection.execute("SELECT value FROM marker").fetchone()[0])
PY
}

FAKE_BIN="$WORK/bin"
REAL_PYTHON="$(command -v python3)"
mkdir -p "$FAKE_BIN"
cat >"$FAKE_BIN/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'docker %s\n' "$*" >>"$FAKE_LOG"
case "${1:-}" in
  info) exit 0 ;;
  volume)
    if [[ "${2:-}" == inspect ]]; then
      printf '[{"Name":"%s","Driver":"local","Options":null}]\n' "${3:?volume name required}"
    fi
    exit 0
    ;;
  compose) exit 0 ;;
  run)
    export_mount=""
    for argument in "$@"; do
      [[ "$argument" == *:/export ]] && export_mount="${argument%:/export}"
    done
    [[ -n "$export_mount" ]]
    cp "$FAKE_DOCKER_DATABASE" "$export_mount/alphawave-taskd.sqlite"
    ;;
  *) exit 1 ;;
esac
SH
cat >"$FAKE_BIN/systemctl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'systemctl %s\n' "$*" >>"$FAKE_LOG"
action=""
for argument in "$@"; do
  case "$argument" in start|stop|enable|unmask|is-active) action="$argument"; break ;; esac
done
case "$action" in
  start)
    count=0; [[ -f "$FAKE_START_COUNT" ]] && read -r count <"$FAKE_START_COUNT"
    printf '%s\n' "$((count + 1))" >"$FAKE_START_COUNT"
    ;;
  is-active)
    count=0; [[ -f "$FAKE_START_COUNT" ]] && read -r count <"$FAKE_START_COUNT"
    [[ $count -gt 0 ]] || exit 1
    if [[ "${FAKE_HEALTH_MODE:-success}" == success ]]; then exit 0; fi
    if [[ "${FAKE_HEALTH_MODE:-success}" == recover && $count -ge 2 ]]; then exit 0; fi
    exit 1
    ;;
esac
SH
cat >"$FAKE_BIN/curl" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat >"$FAKE_BIN/python3" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${FAKE_FAIL_RESTORE:-0}" == 1 && "$*" == *"rollback_transaction.py atomic-replace"* ]]; then
  count=0; [[ -f "$FAKE_ATOMIC_COUNT" ]] && read -r count <"$FAKE_ATOMIC_COUNT"
  count=$((count + 1))
  printf '%s\n' "$count" >"$FAKE_ATOMIC_COUNT"
  [[ $count -eq 2 ]] && exit 73
fi
exec "$REAL_PYTHON" "$@"
SH
chmod +x "$FAKE_BIN/docker" "$FAKE_BIN/systemctl" "$FAKE_BIN/curl" "$FAKE_BIN/python3"

run_case() {
  local name=$1 health_mode=$2 fail_restore=${3:-0} schema_mode=${4:-matching}
  local case_dir="$WORK/$name"
  mkdir -p "$case_dir"
  make_database "$case_dir/systemd.sqlite" previous
  make_database "$case_dir/docker.sqlite" candidate
  if [[ "$schema_mode" == unrelated ]]; then
    rm -f "$case_dir/docker.sqlite"
    python3 - "$case_dir/docker.sqlite" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as connection:
    connection.execute("CREATE TABLE unrelated_payload (id INTEGER PRIMARY KEY)")
PY
  fi
  : >"$case_dir/docker.env"
  : >"$case_dir/log"
  rm -f "$case_dir/start-count"
  rm -f "$case_dir/atomic-count"
  set +e
  PATH="$FAKE_BIN:$PATH" \
    FAKE_LOG="$case_dir/log" \
    FAKE_DOCKER_DATABASE="$case_dir/docker.sqlite" \
    FAKE_START_COUNT="$case_dir/start-count" \
    FAKE_HEALTH_MODE="$health_mode" \
    FAKE_FAIL_RESTORE="$fail_restore" \
    FAKE_ATOMIC_COUNT="$case_dir/atomic-count" \
    REAL_PYTHON="$REAL_PYTHON" \
    ALPHAWAVE_CONFIRM_DOCKER_ROLLBACK=1 \
    ALPHAWAVE_SYSTEMD_HEALTH_TIMEOUT_SECONDS=1 \
    ALPHAWAVE_ROLLBACK_LOCK_FILE="$case_dir/rollback.lock" \
    "$ROOT/scripts/admin/rollback-docker-to-systemd.sh" --execute --yes \
      --database "$case_dir/systemd.sqlite" \
      --env-file "$case_dir/docker.env" \
      --staging-dir "$case_dir/staging" >"$case_dir/output" 2>&1
  CASE_STATUS=$?
  set -e
  CASE_DIR="$case_dir"
}

run_case success success
[[ $CASE_STATUS -eq 0 ]]
[[ "$(read_marker "$CASE_DIR/systemd.sqlite")" == candidate ]]
[[ "$(stat -c '%a' "$CASE_DIR/systemd.sqlite")" == 644 ]]
grep -Fq "Rollback completed" "$CASE_DIR/output"
grep -Fq "docker compose" "$CASE_DIR/log"
! grep -Eq '^docker compose .* up' "$CASE_DIR/log"

run_case restore-failure fail 1
[[ $CASE_STATUS -ne 0 ]]
[[ "$(read_marker "$CASE_DIR/systemd.sqlite")" == candidate ]]
compgen -G "$CASE_DIR/staging/transaction.*/docker-export.*.sqlite" >/dev/null
compgen -G "$CASE_DIR/staging/transaction.*/systemd-db.pre-rollback.*.sqlite" >/dev/null
compgen -G "$CASE_DIR/staging/transaction.*/systemd-db.failed-new.*.sqlite" >/dev/null
grep -Fq "Emergency recovery failed while restoring the previous database" "$CASE_DIR/output"
[[ "$(cat "$CASE_DIR/atomic-count")" -eq 2 ]]
[[ "$(grep -c '^systemctl .* start ' "$CASE_DIR/log")" -eq 1 ]]
! grep -Eq '^docker compose .* up' "$CASE_DIR/log"

run_case recovery recover
[[ $CASE_STATUS -ne 0 ]]
[[ "$(read_marker "$CASE_DIR/systemd.sqlite")" == previous ]]
compgen -G "$CASE_DIR/staging/transaction.*/docker-export.*.sqlite" >/dev/null
compgen -G "$CASE_DIR/staging/transaction.*/systemd-db.pre-rollback.*.sqlite" >/dev/null
compgen -G "$CASE_DIR/staging/transaction.*/systemd-db.failed-new.*.sqlite" >/dev/null
grep -Fq "previous systemd database was restored and is healthy" "$CASE_DIR/output"
! grep -Fq "$CASE_DIR/staging" "$CASE_DIR/output"

run_case emergency fail
[[ $CASE_STATUS -ne 0 ]]
[[ "$(read_marker "$CASE_DIR/systemd.sqlite")" == previous ]]
compgen -G "$CASE_DIR/staging/transaction.*/docker-export.*.sqlite" >/dev/null
compgen -G "$CASE_DIR/staging/transaction.*/systemd-db.pre-rollback.*.sqlite" >/dev/null
compgen -G "$CASE_DIR/staging/transaction.*/systemd-db.failed-new.*.sqlite" >/dev/null
grep -Fq "Emergency recovery restored the previous database, but systemd is not healthy" "$CASE_DIR/output"
[[ "$(grep -c '^systemctl .* start ' "$CASE_DIR/log")" -eq 2 ]]
! grep -Eq '^docker compose .* up' "$CASE_DIR/log"

run_case invalid-schema success 0 unrelated
[[ $CASE_STATUS -ne 0 ]]
[[ "$(read_marker "$CASE_DIR/systemd.sqlite")" == previous ]]
grep -Fq "expected runtime schema" "$CASE_DIR/output"
grep -Eq '^docker compose .* up' "$CASE_DIR/log"
[[ ! -f "$CASE_DIR/start-count" ]]

lock_dir="$WORK/locked"
mkdir -p "$lock_dir"
make_database "$lock_dir/systemd.sqlite" previous
make_database "$lock_dir/docker.sqlite" candidate
: >"$lock_dir/docker.env"
: >"$lock_dir/log"
exec 8>"$lock_dir/rollback.lock"
flock -n 8
set +e
PATH="$FAKE_BIN:$PATH" \
  FAKE_LOG="$lock_dir/log" \
  FAKE_DOCKER_DATABASE="$lock_dir/docker.sqlite" \
  FAKE_START_COUNT="$lock_dir/start-count" \
  FAKE_FAIL_RESTORE=0 \
  FAKE_ATOMIC_COUNT="$lock_dir/atomic-count" \
  REAL_PYTHON="$REAL_PYTHON" \
  ALPHAWAVE_CONFIRM_DOCKER_ROLLBACK=1 \
  ALPHAWAVE_ROLLBACK_LOCK_FILE="$lock_dir/rollback.lock" \
  "$ROOT/scripts/admin/rollback-docker-to-systemd.sh" --execute --yes \
    --database "$lock_dir/systemd.sqlite" \
    --env-file "$lock_dir/docker.env" \
    --staging-dir "$lock_dir/staging" >"$lock_dir/output" 2>&1
lock_status=$?
set -e
[[ $lock_status -ne 0 ]]
grep -Fq "already in progress" "$lock_dir/output"
! grep -Fq "docker compose" "$lock_dir/log"
[[ "$(read_marker "$lock_dir/systemd.sqlite")" == previous ]]

ownership_dir="$WORK/ownership-conversion"
mkdir -p "$ownership_dir"
make_database "$ownership_dir/systemd.sqlite" previous
make_database "$ownership_dir/docker.sqlite" candidate
chmod 640 "$ownership_dir/systemd.sqlite"
"$REAL_PYTHON" "$ROOT/scripts/admin/rollback_transaction.py" capture-metadata \
  --database "$ownership_dir/systemd.sqlite" --output "$ownership_dir/metadata.json"
docker run --rm --network none --user 0 -v "$ownership_dir:/work" alpine:3.20 \
  sh -ec 'chown 10001:10001 /work/docker.sqlite; chmod 600 /work/docker.sqlite'
[[ "$(stat -c '%u:%g:%a' "$ownership_dir/docker.sqlite")" == "10001:10001:600" ]]
docker run --rm --network none --user 0 \
  -v "$ROOT/scripts/admin:/tools:ro" -v "$ownership_dir:/work" python:3.12-slim \
  python /tools/rollback_transaction.py atomic-replace \
    --source /work/docker.sqlite --target /work/systemd.sqlite --metadata /work/metadata.json
[[ "$(stat -c '%u:%g:%a' "$ownership_dir/systemd.sqlite")" == "$(id -u):$(id -g):640" ]]
[[ "$(read_marker "$ownership_dir/systemd.sqlite")" == candidate ]]

printf 'transactional rollback drills: PASS\n'
