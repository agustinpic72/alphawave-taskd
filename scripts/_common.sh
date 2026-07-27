#!/usr/bin/env bash
set -euo pipefail

repo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

service_name() {
  printf '%s\n' "alphawave-taskd"
}

backend_python() {
  local root
  root="$(repo_root)"
  printf '%s\n' "$root/backend/.venv/bin/python"
}

ensure_repo_root() {
  local root
  root="$(repo_root)"
  if [[ ! -f "$root/backend/pyproject.toml" || ! -f "$root/frontend/package.json" ]]; then
    echo "Run this from the alphawave-taskd repo." >&2
    exit 1
  fi
}
