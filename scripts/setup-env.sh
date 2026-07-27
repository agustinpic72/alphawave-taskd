#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

FORCE=false
PRINT_CHECKLIST=false
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=true ;;
    --print-checklist) PRINT_CHECKLIST=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

ROOT="$(repo_root)"
ensure_repo_root
cd "$ROOT"

if [[ "$PRINT_CHECKLIST" == "false" ]]; then
  if [[ -f .env && "$FORCE" != "true" ]]; then
    echo ".env already exists. Not overwriting. Use --force to recreate from .env.example."
  else
    cp .env.example .env
    echo "Created .env from .env.example."
  fi
fi

cat <<'EOF'
First-run checklist:
1. Crear bot con BotFather.
2. Pegar TELEGRAM_BOT_TOKEN en .env.
3. Ejecutar scripts/telegram-whoami.sh.
4. Completar TELEGRAM_ALLOWED_USER_ID.
5. Completar Trello API key/token.
6. Ejecutar scripts/trello-discover.sh.
7. Completar TRELLO_MEMBER_ID y board IDs.
8. Ejecutar scripts/preflight.sh --strict.
EOF
