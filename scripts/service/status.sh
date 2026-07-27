#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${ALPHAWAVE_SERVICE_NAME:-alphawave-taskd}"
SYSTEMD_ARGS=(status "${SERVICE_NAME}" --no-pager)

if [[ "${ALPHAWAVE_SYSTEMD_USER:-0}" == "1" ]]; then
  SYSTEMD_ARGS=(--user "${SYSTEMD_ARGS[@]}")
fi

echo "Inspecting systemd service: ${SERVICE_NAME}"
if [[ "${ALPHAWAVE_SYSTEMD_USER:-0}" == "1" ]]; then
  echo "Scope: user"
else
  echo "Scope: system"
fi

set +e
systemctl "${SYSTEMD_ARGS[@]}"
code=$?
set -e

if [[ "${code}" -ne 0 ]]; then
  echo
  echo "systemctl status failed. This script is read-only and did not modify the service."
  echo "Set ALPHAWAVE_SYSTEMD_USER=1 for the local user-service install."
  exit "${code}"
fi
