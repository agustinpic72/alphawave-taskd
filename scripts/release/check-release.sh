#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${ALPHAWAVE_BASE_URL:-http://127.0.0.1:8711}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "AlphaWave release check"
echo "Base URL: ${BASE_URL}"
echo

cd "${ROOT_DIR}"

echo "1/5 Backend tests with durations"
time backend/.venv/bin/pytest -q --durations=50
echo

echo "2/5 Frontend production build"
./scripts/release/build-frontend.sh
echo

echo "3/5 Service health"
ALPHAWAVE_BASE_URL="${BASE_URL}" ./scripts/service/health.sh
echo

echo "4/5 RC check"
ALPHAWAVE_BASE_URL="${BASE_URL}" ./scripts/dev/rc-check.sh
echo

echo "5/5 Diagnostics export"
ALPHAWAVE_BASE_URL="${BASE_URL}" ./scripts/dev/export-diagnostics.sh
echo

echo "Release check passed for ${BASE_URL}"
