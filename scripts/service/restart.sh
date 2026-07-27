#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${ALPHAWAVE_SERVICE_NAME:-alphawave-taskd}"
SYSTEMD_PREFIX=()
if [[ "${ALPHAWAVE_SYSTEMD_USER:-0}" == "1" ]]; then
  SYSTEMD_PREFIX=(--user)
fi

echo "Requested restart for service: ${SERVICE_NAME}"
if [[ "${ALPHAWAVE_SYSTEMD_USER:-0}" == "1" ]]; then
  echo "Scope: user"
else
  echo "Scope: system"
fi

if [[ "${ALPHAWAVE_CONFIRM_RESTART:-0}" != "1" ]]; then
  echo
  echo "Refusing to restart without explicit confirmation."
  echo "Run this exact command only when you intend to restart the service:"
  if [[ "${ALPHAWAVE_SYSTEMD_USER:-0}" == "1" ]]; then
    echo "  ALPHAWAVE_CONFIRM_RESTART=1 ALPHAWAVE_SYSTEMD_USER=1 $0"
  else
    echo "  ALPHAWAVE_CONFIRM_RESTART=1 $0"
  fi
  exit 2
fi

systemctl "${SYSTEMD_PREFIX[@]}" restart "${SERVICE_NAME}"
systemctl "${SYSTEMD_PREFIX[@]}" status "${SERVICE_NAME}" --no-pager
