#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${ALPHAWAVE_BASE_URL:-http://127.0.0.1:8711}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Trello read-only smoke base URL: ${BASE_URL}"

if ! curl -fsS "${BASE_URL}/api/health" >/dev/null; then
  cat >&2 <<EOF
alphawave-taskd is not responding at ${BASE_URL}.

Start or restart the local app first:
  ./scripts/start.sh
or:
  ./scripts/restart.sh

Then rerun:
  ALPHAWAVE_BASE_URL=${BASE_URL} ./scripts/dev/trello-readonly-smoke.sh
EOF
  exit 2
fi

cd "${ROOT_DIR}"
ALPHAWAVE_BASE_URL="${BASE_URL}" backend/.venv/bin/python scripts/dev/trello_readonly_smoke.py "$@"
