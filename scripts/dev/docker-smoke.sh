#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SMOKE_RUN_ID="${ALPHAWAVE_DOCKER_SMOKE_ID:-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$$}"
SMOKE_RUN_ID="$(printf '%s' "${SMOKE_RUN_ID}" | tr -cs '[:alnum:]_.-' '-')"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-alphawave_m20m_smoke_${SMOKE_RUN_ID}}"
ALPHAWAVE_DOCKER_SMOKE_PORT="${ALPHAWAVE_DOCKER_SMOKE_PORT:-18080}"
ALPHAWAVE_WEB_PORT="${ALPHAWAVE_WEB_PORT:-${ALPHAWAVE_DOCKER_SMOKE_PORT}}"
ALPHAWAVE_DOCKER_SMOKE_DATA_VOLUME="${ALPHAWAVE_DOCKER_SMOKE_DATA_VOLUME:-${COMPOSE_PROJECT_NAME}_data}"
ALPHAWAVE_DOCKER_SMOKE_BACKUPS_VOLUME="${ALPHAWAVE_DOCKER_SMOKE_BACKUPS_VOLUME:-${COMPOSE_PROJECT_NAME}_backups}"
ALPHAWAVE_DOCKER_SMOKE_API_IMAGE="${ALPHAWAVE_DOCKER_SMOKE_API_IMAGE:-alphawave-taskd-api:smoke-${SMOKE_RUN_ID}}"
ALPHAWAVE_DOCKER_SMOKE_WEB_IMAGE="${ALPHAWAVE_DOCKER_SMOKE_WEB_IMAGE:-alphawave-taskd-web:smoke-${SMOKE_RUN_ID}}"
BASE_URL="http://127.0.0.1:${ALPHAWAVE_WEB_PORT}"
SMOKE_TITLE="[DOCKER SMOKE] persistence ${COMPOSE_PROJECT_NAME}-$$-$(date +%s)"
COMPOSE_OVERRIDE="$(mktemp "${TMPDIR:-/tmp}/alphawave-docker-smoke.XXXXXX.yaml")"
TASK_ID=""
FAILED=0

export COMPOSE_PROJECT_NAME ALPHAWAVE_WEB_PORT
export ALPHAWAVE_WEB_BIND_ADDRESS=127.0.0.1
export ALPHAWAVE_DEPLOYMENT_MODE=local
export APP_DEV_ENDPOINTS_ENABLED=false
export ALPHAWAVE_AUTH_ENABLED=false
export ALPHAWAVE_AUTH_COOKIE_SECURE=false
export TELEGRAM_ENABLED=false
export TRELLO_ENABLED=false
export TRELLO_WRITE_ENABLED=false

compose() {
  docker compose --project-directory "${ROOT_DIR}" -p "${COMPOSE_PROJECT_NAME}" \
    -f "${ROOT_DIR}/compose.yaml" -f "${COMPOSE_OVERRIDE}" "$@"
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if (( exit_code != 0 || FAILED != 0 )); then
    echo "Docker smoke failed; recent Compose state and logs follow." >&2
    compose ps --all >&2 || true
    compose logs --no-color --tail=200 >&2 || true
  fi
  compose down -v --remove-orphans --timeout 15 >/dev/null 2>&1 || true
  docker image rm "${ALPHAWAVE_DOCKER_SMOKE_API_IMAGE}" "${ALPHAWAVE_DOCKER_SMOKE_WEB_IMAGE}" >/dev/null 2>&1 || true
  rm -f "${COMPOSE_OVERRIDE}"
  if (( exit_code != 0 )); then
    exit "${exit_code}"
  fi
}
trap cleanup EXIT INT TERM

json_field() {
  local field="$1"
  python3 -c 'import json, sys; value=json.load(sys.stdin); print(value[sys.argv[1]])' "${field}"
}

wait_for_url() {
  local url="$1"
  local attempts="${2:-60}"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl --connect-timeout 2 --max-time 5 -fsS "${url}" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for ${url}." >&2
  return 1
}

assert_task_present() {
  local phase="$1"
  local payload
  payload="$(curl --connect-timeout 2 --max-time 10 -fsS \
    --get --data-urlencode 'status=active' --data-urlencode "q=${SMOKE_TITLE}" \
    "${BASE_URL}/api/tasks")"
  TASK_ID="${TASK_ID}" SMOKE_TITLE="${SMOKE_TITLE}" PHASE="${phase}" python3 -c '
import json, os, sys
tasks = json.load(sys.stdin).get("tasks", [])
expected_id = os.environ["TASK_ID"]
expected_title = os.environ["SMOKE_TITLE"]
if not any(task.get("id") == expected_id and task.get("title") == expected_title for task in tasks):
    raise SystemExit("Task missing during " + os.environ["PHASE"])
' <<<"${payload}"
  echo "Persistence verified: ${phase}."
}

assert_task_absent() {
  local status payload
  for status in active completed deleted; do
    payload="$(curl --connect-timeout 2 --max-time 10 -fsS \
      --get --data-urlencode "status=${status}" --data-urlencode "q=${SMOKE_TITLE}" \
      "${BASE_URL}/api/tasks")"
    SMOKE_TITLE="${SMOKE_TITLE}" STATUS="${status}" python3 -c '
import json, os, sys
tasks = json.load(sys.stdin).get("tasks", [])
if any(task.get("title") == os.environ["SMOKE_TITLE"] for task in tasks):
    raise SystemExit("Task remains in " + os.environ["STATUS"])
' <<<"${payload}"
  done
}

cd "${ROOT_DIR}"

cat >"${COMPOSE_OVERRIDE}" <<EOF
services:
  api:
    image: ${ALPHAWAVE_DOCKER_SMOKE_API_IMAGE}
  web:
    image: ${ALPHAWAVE_DOCKER_SMOKE_WEB_IMAGE}
volumes:
  alphawave_data:
    name: ${ALPHAWAVE_DOCKER_SMOKE_DATA_VOLUME}
  alphawave_backups:
    name: ${ALPHAWAVE_DOCKER_SMOKE_BACKUPS_VOLUME}
EOF

echo "Docker smoke project: ${COMPOSE_PROJECT_NAME}"
echo "Docker smoke URL: ${BASE_URL}"
echo "Docker smoke images: ${ALPHAWAVE_DOCKER_SMOKE_API_IMAGE}, ${ALPHAWAVE_DOCKER_SMOKE_WEB_IMAGE}"
echo "Docker smoke volumes: ${ALPHAWAVE_DOCKER_SMOKE_DATA_VOLUME}, ${ALPHAWAVE_DOCKER_SMOKE_BACKUPS_VOLUME}"
compose down -v --remove-orphans --timeout 15 >/dev/null 2>&1 || true
compose config --quiet
compose build
compose up -d --wait
docker volume inspect "${ALPHAWAVE_DOCKER_SMOKE_DATA_VOLUME}" "${ALPHAWAVE_DOCKER_SMOKE_BACKUPS_VOLUME}" >/dev/null
docker image inspect "${ALPHAWAVE_DOCKER_SMOKE_API_IMAGE}" "${ALPHAWAVE_DOCKER_SMOKE_WEB_IMAGE}" >/dev/null
wait_for_url "${BASE_URL}/healthz"
wait_for_url "${BASE_URL}/api/health"
curl --connect-timeout 2 --max-time 10 -fsS "${BASE_URL}/" >/dev/null

created="$(curl --connect-timeout 2 --max-time 10 -fsS \
  -H 'Content-Type: application/json' \
  -d "$(SMOKE_TITLE="${SMOKE_TITLE}" python3 -c 'import json, os; print(json.dumps({"title": os.environ["SMOKE_TITLE"], "scope": "Inbox", "auto_classify": False}))')" \
  "${BASE_URL}/api/tasks")"
TASK_ID="$(json_field id <<<"${created}")"
[[ -n "${TASK_ID}" ]]
assert_task_present "initial creation"

compose restart api
wait_for_url "${BASE_URL}/api/health"
assert_task_present "API restart"

compose down --remove-orphans --timeout 15
compose up -d --wait
wait_for_url "${BASE_URL}/healthz"
wait_for_url "${BASE_URL}/api/health"
assert_task_present "Compose down/up with named volumes"

curl --connect-timeout 2 --max-time 10 -fsS -X DELETE "${BASE_URL}/api/tasks/${TASK_ID}" >/dev/null
deleted="$(curl --connect-timeout 2 --max-time 10 -fsS -X DELETE "${BASE_URL}/api/tasks/${TASK_ID}/permanent")"
[[ "$(json_field deleted_count <<<"${deleted}")" == "1" ]]
assert_task_absent

if command -v python3 >/dev/null; then
  python3 scripts/dev/local_api_smoke.py --base-url "${BASE_URL}"
fi

if [[ "${ALPHAWAVE_DOCKER_RUN_BROWSER_SMOKE:-0}" == "1" ]]; then
  ALPHAWAVE_BASE_URL="${BASE_URL}" scripts/dev/browser-smoke.sh
fi

echo "Docker smoke: PASS"
echo "Named-volume persistence: PASS"
echo "Smoke task cleanup: PASS"
