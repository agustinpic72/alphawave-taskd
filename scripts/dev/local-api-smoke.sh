#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${ALPHAWAVE_BASE_URL:-http://127.0.0.1:8711}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Local API smoke base URL: ${BASE_URL}"

if ! curl -fsS "${BASE_URL}/api/health" >/dev/null; then
  cat >&2 <<EOF
alphawave-taskd is not responding at ${BASE_URL}.

Start or restart the local app first:
  ./scripts/start.sh
or:
  ./scripts/restart.sh
EOF
  exit 2
fi

cd "${ROOT_DIR}"
backend/.venv/bin/python scripts/dev/local_api_smoke.py --base-url "${BASE_URL}" "$@"
