#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUDIT_SCRIPT="$ROOT_DIR/scripts/dev/publication-audit.sh"
FIXTURE_DIR="$(mktemp -d)"
PRIVATE_TERMS_FILE="$(mktemp)"
trap 'rm -rf "$FIXTURE_DIR"; rm -f "$PRIVATE_TERMS_FILE"' EXIT

printf '%s\n' 'PRIVATE_TERM_ALPHA' 'PRIVATE_TERM_BETA' 'alias:PTA' > "$PRIVATE_TERMS_FILE"

cd "$FIXTURE_DIR"
git init -q
git config user.email audit-test@example.invalid
git config user.name "Publication Audit Test"
touch LICENSE
printf '%s\n' \
  '.env' '.env.*' '*.sqlite' '*.db' 'data/' 'backups/' 'logs/' 'reports/' '*.log' \
  '__pycache__/' '.pytest_cache/' '.venv/' 'node_modules/' 'dist/' \
  'frontend/playwright-report/' 'frontend/test-results/' 'audit/' \
  '.publication-audit-private-terms' > .gitignore
printf '%s\n' 'Synthetic public fixture.' > fixture.txt
git add LICENSE .gitignore fixture.txt
git commit -qm baseline

run_audit() {
  PUBLICATION_AUDIT_ROOT="$FIXTURE_DIR" \
    PUBLICATION_AUDIT_PRIVATE_TERMS_FILE="$PRIVATE_TERMS_FILE" \
    "$AUDIT_SCRIPT" 2>&1
}

baseline_output="$(run_audit)"
grep -Fq 'Publication audit passed' <<< "$baseline_output"
grep -Fq 'Configured private taxonomy and reversible variants were not detected.' <<< "$baseline_output"

printf '%s\n' 'not-a-secret' > .env.docker
git add -f .env.docker
set +e
dangerous_output="$(run_audit)"
dangerous_status=$?
set -e
[[ "$dangerous_status" -eq 1 ]]
grep -Fq 'Tracked runtime, credential, database, or private-key file detected' <<< "$dangerous_output"
git reset -q .env.docker
rm -f .env.docker

printf '%s\n' \
  'PRIVATE_TERM_ALPHA' \
  '505249564154455f5445524d5f42455441' \
  'UFJJVkFURV9URVJNX0FMUEhB' \
  '\x50\x52\x49\x56\x41\x54\x45\x5f\x54\x45\x52\x4d\x5f\x42\x45\x54\x41' \
  '%50%52%49%56%41%54%45%5F%54%45%52%4D%5F%41%4C%50%48%41' \
  'PTA' > fixture.txt
git add fixture.txt
set +e
encoded_output="$(run_audit)"
encoded_status=$?
set -e
[[ "$encoded_status" -eq 1 ]]
grep -Fq 'raw/literal' <<< "$encoded_output"
grep -Fq 'raw/hex' <<< "$encoded_output"
grep -Fq 'raw/base64' <<< "$encoded_output"
grep -Fq 'raw/escaped-byte' <<< "$encoded_output"
grep -Fq 'raw/percent-encoded' <<< "$encoded_output"
grep -Fq 'values and paths withheld' <<< "$encoded_output"

printf '%s\n' 'SPTAX' 'VEMPADDING' > fixture.txt
alias_boundary_output="$(run_audit)"
grep -Fq 'Publication audit passed' <<< "$alias_boundary_output"

printf '%s\n' 'Synthetic public fixture.' > fixture.txt
python3 - <<'PY'
import gzip
from pathlib import Path

Path("compressed-fixture.gz").write_bytes(gzip.compress(b"PRIVATE_TERM_BETA"))
PY
git add fixture.txt compressed-fixture.gz
set +e
compressed_output="$(run_audit)"
compressed_status=$?
set -e
[[ "$compressed_status" -eq 1 ]]
grep -Fq 'gzip/literal' <<< "$compressed_output"

git reset -q compressed-fixture.gz
rm -f compressed-fixture.gz
mkdir -p deploy/env
cat > deploy/env/app.env.example <<'EOF'
ALPHAWAVE_DEPLOYMENT_MODE=public
ALPHAWAVE_AUTH_ENABLED=false
ALPHAWAVE_AUTH_COOKIE_SECURE=false
APP_DEV_ENDPOINTS_ENABLED=true
OPENAPI_URL=/openapi.json
EOF
git add deploy/env/app.env.example
set +e
deployment_output="$(run_audit)"
deployment_status=$?
set -e
[[ "$deployment_status" -eq 1 ]]
grep -Fq 'Deployment example security requirements are not satisfied' <<< "$deployment_output"

cat > deploy/env/app.env.example <<'EOF'
ALPHAWAVE_DEPLOYMENT_MODE=public
ALPHAWAVE_AUTH_ENABLED=true
ALPHAWAVE_AUTH_COOKIE_SECURE=true
APP_DEV_ENDPOINTS_ENABLED=false
EOF
secure_output="$(run_audit)"
grep -Fq 'Deployment examples fail closed.' <<< "$secure_output"

printf 'PASS: publication audit regression fixtures\n'
