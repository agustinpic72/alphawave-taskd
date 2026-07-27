#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8711}"
INCLUDE_DEV_ENDPOINTS="${ALPHAWAVE_SMOKE_DEV_ENDPOINTS:-0}"

if [[ "${1:-}" == "--dry-run" ]]; then
  cat <<EOF
Smoke dry-run for $BASE_URL
- GET /api/system/health
- POST /api/tasks with [SMOKE] task
- GET /api/tasks
- POST /api/dev/simulate-message with "agregá [SMOKE] ..." only when ALPHAWAVE_SMOKE_DEV_ENDPOINTS=1 and APP_DEV_ENDPOINTS_ENABLED=true
- POST /api/dev/simulate-message with "/todo" only when ALPHAWAVE_SMOKE_DEV_ENDPOINTS=1 and APP_DEV_ENDPOINTS_ENABLED=true
- POST /api/reminders
- GET /api/planning/today
- POST /api/briefing/generate
- cleanup [SMOKE] tasks via DELETE /api/tasks/{id}
EOF
  exit 0
fi

python3 - "$BASE_URL" "$INCLUDE_DEV_ENDPOINTS" <<'PY'
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

base = sys.argv[1].rstrip("/")
include_dev_endpoints = sys.argv[2] == "1"


def request(method, path, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


created_ids = []
try:
    health = request("GET", "/api/system/health")
    assert health["status"] == "ok", health
    task = request("POST", "/api/tasks", {"title": "[SMOKE] API task", "auto_classify": False})
    created_ids.append(task["id"])
    tasks = request("GET", "/api/tasks?status=active")
    assert any(item["id"] == task["id"] for item in tasks["tasks"])
    if include_dev_endpoints:
        request("POST", "/api/dev/simulate-message", {"text": "agregá [SMOKE] telegram task", "chat_id": "smoke", "user_id": "smoke"})
        request("POST", "/api/dev/simulate-message", {"text": "/todo", "chat_id": "smoke", "user_id": "smoke"})
    remind_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    request("POST", "/api/reminders", {"message": "[SMOKE] reminder", "remind_at": remind_at, "source": "dev"})
    request("GET", "/api/planning/today")
    request("POST", "/api/briefing/generate", {"manual": True})
    smoke_tasks = request("GET", "/api/tasks?status=active&q=" + urllib.parse.quote("[SMOKE]"))
    for item in smoke_tasks["tasks"]:
        created_ids.append(item["id"])
    for task_id in sorted(set(created_ids)):
        try:
            request("DELETE", f"/api/tasks/{task_id}")
        except urllib.error.HTTPError:
            pass
    print("Smoke: ok")
except Exception as exc:  # noqa: BLE001
    print(f"Smoke: failed: {exc}", file=sys.stderr)
    raise
PY
