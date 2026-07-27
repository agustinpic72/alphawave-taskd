#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

ROOT="$(repo_root)"
ensure_repo_root
cd "$ROOT"

python3 scripts/lib/onboarding.py trello-discover "$@"
