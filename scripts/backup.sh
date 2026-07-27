#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

ROOT="$(repo_root)"
ensure_repo_root
PYTHON="$(backend_python)"

if [[ ! -x "$PYTHON" ]]; then
  echo "Backend virtualenv missing. Run ./scripts/install.sh first." >&2
  exit 1
fi

cd "$ROOT"
PYTHONPATH="$ROOT/backend" "$PYTHON" -m app.cli backup
