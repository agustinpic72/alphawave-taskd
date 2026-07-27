#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${ALPHAWAVE_BASE_URL:-http://127.0.0.1:8711}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Browser smoke base URL: ${BASE_URL}"

if ! curl -fsS "${BASE_URL}/api/health" >/dev/null; then
  cat >&2 <<EOF
alphawave-taskd is not responding at ${BASE_URL}.

Start or restart the local app first:
  ./scripts/start.sh
or:
  ./scripts/restart.sh

Then rerun:
  ALPHAWAVE_BASE_URL=${BASE_URL} ./scripts/dev/browser-smoke.sh
EOF
  exit 1
fi

if ! curl -fsS "${BASE_URL}/" >/dev/null; then
  cat >&2 <<EOF
The backend is healthy, but the frontend is not responding at ${BASE_URL}/.
Run a production build or use the configured frontend server before browser smoke:
  cd frontend && npm run build
EOF
  exit 1
fi

if [[ "${PLAYWRIGHT_BROWSER_CHANNEL:-chrome}" != "bundled" ]] && ! command -v google-chrome >/dev/null && ! command -v google-chrome-stable >/dev/null; then
  cat >&2 <<EOF
Google Chrome was not found. Install Chrome, or install Playwright Chromium:
  cd frontend && npx playwright install chromium

Then run with:
  PLAYWRIGHT_BROWSER_CHANNEL=bundled ./scripts/dev/browser-smoke.sh
EOF
  exit 1
fi

cd "${ROOT_DIR}/frontend"
ALPHAWAVE_BASE_URL="${BASE_URL}" npx playwright test tests/smoke "$@"
