#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

START_AFTER_INSTALL=false
if [[ "${1:-}" == "--start" ]]; then
  START_AFTER_INSTALL=true
fi

ROOT="$(repo_root)"
ensure_repo_root
cd "$ROOT"

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v npm >/dev/null || { echo "npm is required" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemctl is required" >&2; exit 1; }

if [[ ! -d backend/.venv ]]; then
  python3 -m venv backend/.venv
fi
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/python -m pip install -e "backend[dev]"

(cd frontend && npm install && npm run build)

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Fill tokens there when needed."
fi

mkdir -p data/backups logs

USER_SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$USER_SYSTEMD_DIR"
PYTHON_BIN="$(backend_python)"
DEFAULT_SERVICE_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SERVICE_PATH="$DEFAULT_SERVICE_PATH"
sed \
  -e "s|__REPO_PATH__|$ROOT|g" \
  -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
  -e "s|__SERVICE_PATH__|$SERVICE_PATH|g" \
  systemd/alphawave-taskd.service > "$USER_SYSTEMD_DIR/alphawave-taskd.service"

systemctl --user daemon-reload
systemctl --user enable alphawave-taskd

echo "Installed user service: $USER_SYSTEMD_DIR/alphawave-taskd.service"
echo "Use ./scripts/start.sh to start it."

if [[ "$START_AFTER_INSTALL" == "true" ]]; then
  "$SCRIPT_DIR/start.sh"
fi
