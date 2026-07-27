#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${ALPHAWAVE_BASE_URL:-http://127.0.0.1:8711}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Diagnostics base URL: ${BASE_URL}"
cd "${ROOT_DIR}"
ALPHAWAVE_BASE_URL="${BASE_URL}" backend/.venv/bin/python scripts/dev/export_diagnostics.py "$@"
