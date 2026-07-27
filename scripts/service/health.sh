#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${ALPHAWAVE_BASE_URL:-http://127.0.0.1:8711}"

python3 - "${BASE_URL}" <<'PY'
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

base_url = sys.argv[1].rstrip("/")


def fetch_json(path: str) -> dict:
    url = f"{base_url}{path}"
    with urlopen(url, timeout=10) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


try:
    health = fetch_json("/api/health")
    status = fetch_json("/api/system/status")
except HTTPError as exc:
    print(f"AlphaWave health check failed: HTTP {exc.code} {exc.reason}")
    sys.exit(1)
except URLError as exc:
    print(f"AlphaWave health check failed: {exc.reason}")
    sys.exit(1)
except TimeoutError:
    print("AlphaWave health check failed: request timed out")
    sys.exit(1)
except Exception as exc:
    print(f"AlphaWave health check failed: {exc}")
    sys.exit(1)

overall_raw = status.get("overall") or {}
if isinstance(overall_raw, dict):
    overall = str(overall_raw.get("status") or "").lower()
    overall_summary = overall_raw.get("summary")
else:
    overall = str(overall_raw).lower()
    overall_summary = None
health_status = str(health.get("status") or "").lower()
runtime = status.get("runtime") or {}
services = status.get("services") or {}

print(f"Base URL: {base_url}")
print(f"/api/health: {health_status or 'unknown'}")
print(f"System status: {overall or 'unknown'}")
if overall_summary:
    print(f"Summary: {overall_summary}")
if runtime.get("version"):
    print(f"Version: {runtime.get('version')}")
if runtime.get("git"):
    git = runtime.get("git") or {}
    print(
        "Git: "
        f"{git.get('branch', 'unknown')} "
        f"{git.get('commit', 'unknown')}"
    )

for name in sorted(services):
    service = services.get(name) or {}
    service_status = service.get("status") or service.get("state") or "unknown"
    print(f"- {name}: {service_status}")

if health_status not in {"ok", "healthy"}:
    sys.exit(1)
if overall in {"error", "critical", "unhealthy"}:
    sys.exit(1)
PY
