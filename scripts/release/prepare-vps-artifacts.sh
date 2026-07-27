#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="${ALPHAWAVE_ARTIFACT_DIR:-${ROOT_DIR}/reports/release-artifacts/alphawave-taskd-vps-scaffold-${STAMP}}"

cd "${ROOT_DIR}"
mkdir -p "${OUT_DIR}"

cp -R deploy "${OUT_DIR}/deploy"
mkdir -p "${OUT_DIR}/docs"
for doc in \
  docs/vps-private-beta-runbook.md \
  docs/release-process.md \
  docs/production-readiness.md \
  docs/web-vps-deployment-architecture.md \
  docs/operations.md
do
  if [[ -f "${doc}" ]]; then
    cp "${doc}" "${OUT_DIR}/docs/"
  fi
done

if [[ -d frontend/dist ]]; then
  cp -R frontend/dist "${OUT_DIR}/frontend-dist"
else
  mkdir -p "${OUT_DIR}/frontend-dist"
  printf '%s\n' "frontend/dist was not present. Run scripts/release/build-frontend.sh first." > "${OUT_DIR}/frontend-dist/README.txt"
fi

{
  echo "AlphaWave TaskD VPS private beta artifact manifest"
  echo "Generated: $(date -Is)"
  echo "Branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  echo "Commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo
  echo "Included:"
  echo "- deploy templates"
  echo "- selected operational docs"
  echo "- frontend/dist when already built"
  echo
  echo "Excluded intentionally:"
  echo "- .env and all secrets"
  echo "- SQLite data and backups"
  echo "- logs, reports and diagnostics bundles"
  echo "- node_modules and Python virtualenvs"
} > "${OUT_DIR}/manifest.txt"

echo "Prepared VPS scaffolding artifacts at:"
echo "${OUT_DIR}"
