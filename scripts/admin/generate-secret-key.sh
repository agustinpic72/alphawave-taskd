#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "${ROOT_DIR}"
backend/.venv/bin/python - <<'PY'
from cryptography.fernet import Fernet

print(Fernet.generate_key().decode("utf-8"))
PY
