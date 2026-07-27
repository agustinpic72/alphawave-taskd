#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${ALPHAWAVE_BASE_URL:-http://127.0.0.1:8711}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Live manual checklist base URL: ${BASE_URL}"
cd "${ROOT_DIR}"
backend/.venv/bin/python scripts/dev/live_manual_checklist.py --base-url "${BASE_URL}" "$@"
