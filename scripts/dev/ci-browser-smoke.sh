#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/alphawave-browser-smoke.XXXXXX")"
PORT="${ALPHAWAVE_BROWSER_SMOKE_PORT:-18711}"
BASE_URL="http://127.0.0.1:${PORT}"
SERVER_PID=""

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "${SERVER_PID}" ]]; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
    wait "${SERVER_PID}" >/dev/null 2>&1 || true
  fi
  if (( exit_code != 0 )); then
    echo "Browser smoke server log:" >&2
    sed -n '1,200p' "${RUNTIME_DIR}/server.log" >&2 || true
  fi
  rm -rf "${RUNTIME_DIR}"
  exit "${exit_code}"
}
trap cleanup EXIT INT TERM

wait_for_health() {
  local attempt
  for attempt in $(seq 1 60); do
    if curl --connect-timeout 2 --max-time 5 -fsS "${BASE_URL}/api/health" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for ${BASE_URL}/api/health." >&2
  return 1
}

cd "${ROOT_DIR}"
test -x backend/.venv/bin/uvicorn
test -d frontend/dist

export ALPHAWAVE_DEPLOYMENT_MODE=local
export APP_HOST=127.0.0.1
export APP_PORT="${PORT}"
export APP_DEV_ENDPOINTS_ENABLED=false
export ALPHAWAVE_AUTH_ENABLED=false
export ALPHAWAVE_AUTH_COOKIE_SECURE=false
export ALPHAWAVE_INSTANCE_LOCK_PATH="${RUNTIME_DIR}/alphawave.lock"
export DATABASE_URL="sqlite:///${RUNTIME_DIR}/alphawave.sqlite"
export LOG_FILE="${RUNTIME_DIR}/alphawave.log"
export BACKUP_ENABLED=false
export REMINDERS_ENABLED=false
export TELEGRAM_ENABLED=false
export TRELLO_ENABLED=false
export TRELLO_WRITE_ENABLED=false
export LLM_ENABLED=false
export LLM_PROVIDER=none
export PYTHONPATH="${ROOT_DIR}/backend"

backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" \
  >"${RUNTIME_DIR}/server.log" 2>&1 &
SERVER_PID=$!

wait_for_health
curl --connect-timeout 2 --max-time 5 -fsS "${BASE_URL}/" >/dev/null

ALPHAWAVE_BASE_URL="${BASE_URL}" \
PLAYWRIGHT_BROWSER_CHANNEL="${PLAYWRIGHT_BROWSER_CHANNEL:-bundled}" \
  ./scripts/dev/browser-smoke.sh

echo "Isolated browser smoke: PASS"
