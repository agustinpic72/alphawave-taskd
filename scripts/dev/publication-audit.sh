#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${PUBLICATION_AUDIT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$ROOT_DIR"

failures=0
warnings=0

section() {
  printf '\n== %s ==\n' "$1"
}

fail() {
  failures=$((failures + 1))
  printf 'FAIL: %s\n' "$1"
}

pass() {
  printf 'PASS: %s\n' "$1"
}

warn() {
  warnings=$((warnings + 1))
  printf 'WARN: %s\n' "$1"
}

section "Git status"
git status --short --branch

section "Tracked dangerous files"
dangerous_count="$(
  git ls-files \
    | grep -Ei '(^|/)(\.env($|\.)|.*\.(sqlite|sqlite3|db)(-|$)|id_(rsa|ed25519)$|.*\.(pem|p12|pfx|key)$|credentials?\.json$|secrets?\.json$)' \
    | grep -Eiv '(^|/)\.env(\.[^/]*)?\.example$' \
    | wc -l \
    || true
)"
if [[ "$dangerous_count" -gt 0 ]]; then
  fail "Tracked runtime, credential, database, or private-key file detected ($dangerous_count file(s)); paths withheld."
else
  pass "No tracked runtime, credential, database, or private-key files detected."
fi

section "Secret pattern scan"
secret_pattern='(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|[Bb]ot[[:space:]]+[0-9]{6,}:[A-Za-z0-9_-]{20,})'
secret_count="$(
  git grep -I -E "$secret_pattern" -- . \
    | grep -Ev '(^|/)(backend/tests/|frontend/tests/|scripts/dev/(test-)?publication-audit\.sh:|frontend/src/App\.tsx:|package-lock\.json:)' \
    | wc -l \
    || true
)"
if [[ "$secret_count" -gt 0 ]]; then
  fail "Potential secret material detected ($secret_count match(es)); values and paths withheld."
else
  pass "No high-confidence secret patterns detected."
fi

section "Private taxonomy scan"
private_terms_file="${PUBLICATION_AUDIT_PRIVATE_TERMS_FILE:-}"
if [[ -z "$private_terms_file" ]]; then
  warn "No private taxonomy file configured; CI validates this scanner with synthetic fixtures."
elif [[ ! -f "$private_terms_file" ]]; then
  fail "Configured private taxonomy file is unavailable."
elif PUBLICATION_AUDIT_PRIVATE_TERMS_FILE="$private_terms_file" python3 - <<'PY'
from __future__ import annotations

import base64
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import zlib


def configured_terms() -> list[tuple[bytes, bool]]:
    source = Path(os.environ["PUBLICATION_AUDIT_PRIVATE_TERMS_FILE"])
    terms: list[tuple[bytes, bool]] = []
    for raw_line in source.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        is_alias = line.startswith("alias:")
        value = line.removeprefix("alias:").strip()
        if value:
            terms.append((value.encode("utf-8"), is_alias))
    return terms


def encoded_variants(term: bytes) -> dict[str, bytes]:
    escaped = b"".join(f"\\x{byte:02x}".encode("ascii") for byte in term)
    percent = b"".join(f"%{byte:02X}".encode("ascii") for byte in term)
    encoded = base64.b64encode(term)
    return {
        "literal": term,
        "hex": term.hex().encode("ascii"),
        "base64": encoded,
        "base64-unpadded": encoded.rstrip(b"="),
        "escaped-byte": escaped,
        "percent-encoded": percent,
    }


def decompressed_payloads(data: bytes) -> list[tuple[str, bytes]]:
    payloads: list[tuple[str, bytes]] = []
    for label, decoder in (("gzip", gzip.decompress), ("zlib", zlib.decompress)):
        try:
            payloads.append((label, decoder(data)))
        except (OSError, EOFError, zlib.error):
            continue
    return payloads


def remove_lockfile_integrity_values(path: Path, data: bytes) -> bytes:
    if path.name != "package-lock.json":
        return data
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data

    def sanitize(value: object) -> object:
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items() if key != "integrity"}
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        return value

    return json.dumps(sanitize(document), sort_keys=True).encode("utf-8")


terms = configured_terms()
if not terms:
    print("Private taxonomy configuration is empty.", file=sys.stderr)
    sys.exit(2)

tracked = subprocess.run(
    ["git", "ls-files", "-z"],
    check=True,
    capture_output=True,
).stdout.split(b"\0")
matches: set[str] = set()


def contains_variant(payload: bytes, variant: bytes, *, is_alias: bool, case_sensitive: bool) -> bool:
    haystack = payload if case_sensitive else payload.lower()
    needle = variant if case_sensitive else variant.lower()
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        if not is_alias:
            return True
        before = haystack[index - 1] if index > 0 else None
        after_index = index + len(needle)
        after = haystack[after_index] if after_index < len(haystack) else None
        before_is_token = before is not None and (chr(before).isalnum() or before == ord("_"))
        after_is_token = after is not None and (chr(after).isalnum() or after == ord("_"))
        if not before_is_token and not after_is_token:
            return True
        start = index + 1


for raw_name in tracked:
    if not raw_name:
        continue
    path = Path(os.fsdecode(raw_name))
    if not path.is_file():
        continue
    try:
        data = path.read_bytes()
    except OSError:
        continue
    data = remove_lockfile_integrity_values(path, data)
    payloads = [("raw", data), *decompressed_payloads(data)]
    for container, payload in payloads:
        for term, is_alias in terms:
            if is_alias:
                try:
                    payload.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            for encoding, variant in encoded_variants(term).items():
                if variant and contains_variant(
                    payload,
                    variant,
                    is_alias=is_alias,
                    case_sensitive=encoding.startswith("base64"),
                ):
                    matches.add(f"{container}/{encoding}")

if matches:
    for match_type in sorted(matches):
        print(f"Forbidden taxonomy detected ({match_type}); values and paths withheld.")
    sys.exit(1)
PY
then
  pass "Configured private taxonomy and reversible variants were not detected."
else
  taxonomy_status=$?
  if [[ "$taxonomy_status" -eq 2 ]]; then
    fail "Private taxonomy configuration is empty."
  else
    fail "Private taxonomy or a reversible encoded/compressed variant was detected."
  fi
fi

section "AI runtime boundary"
ai_boundary_count="$(
  git grep -n -I -E '(store[[:space:]]*=[[:space:]]*true|api[_-]?key[[:space:]]*=[[:space:]]*["'\''][^"'\'']+["'\''])' -- \
    backend frontend scripts .env.example 2>/dev/null \
    | grep -Ev '(^|/)(backend/tests/|frontend/tests/|scripts/dev/(test-)?publication-audit\.sh:)' \
    | wc -l \
    || true
)"
if [[ "$ai_boundary_count" -gt 0 ]]; then
  fail "Potential unsafe AI storage or embedded key assignment detected ($ai_boundary_count match(es)); details withheld."
else
  pass "No unsafe AI storage or embedded key assignment detected."
fi

section "Required ignore rules"
required_patterns=(
  '.env'
  '.env.*'
  '*.sqlite'
  '*.db'
  'data/'
  'backups/'
  'logs/'
  'reports/'
  '*.log'
  '__pycache__/'
  '.pytest_cache/'
  '.venv/'
  'node_modules/'
  'dist/'
  'frontend/playwright-report/'
  'frontend/test-results/'
  'audit/'
  '.publication-audit-private-terms'
)
missing=0
for pattern in "${required_patterns[@]}"; do
  if ! grep -Fqx "$pattern" .gitignore; then
    printf 'Missing ignore rule: %s\n' "$pattern"
    missing=$((missing + 1))
  fi
done
if [[ "$missing" -gt 0 ]]; then
  fail "Required ignore rules are incomplete."
else
  pass "Required local and generated artifacts are ignored."
fi

section "Local generated data warning"
local_generated="$(
  find data reports frontend/dist frontend/test-results frontend/playwright-report test-results playwright-report \
    -maxdepth 2 -type f 2>/dev/null | head -50 || true
)"
if [[ -n "$local_generated" ]]; then
  warn "Ignored generated files exist locally; paths withheld. Confirm they remain untracked."
else
  pass "No common generated runtime files found."
fi

section "License"
if [[ -f LICENSE ]]; then
  pass "LICENSE exists."
else
  fail "LICENSE is missing."
fi

section "Deployment security templates"
deploy_templates=()
while IFS= read -r template; do
  [[ -n "$template" ]] && deploy_templates+=("$template")
done < <(git ls-files 'deploy/env/*.example' 2>/dev/null || true)

deployment_failures=0
for template in "${deploy_templates[@]}"; do
  mode="$(grep -E '^ALPHAWAVE_DEPLOYMENT_MODE=' "$template" | tail -1 | cut -d= -f2- || true)"
  auth="$(grep -E '^ALPHAWAVE_AUTH_ENABLED=' "$template" | tail -1 | cut -d= -f2- || true)"
  secure_cookie="$(grep -E '^ALPHAWAVE_AUTH_COOKIE_SECURE=' "$template" | tail -1 | cut -d= -f2- || true)"
  dev_endpoints="$(grep -E '^APP_DEV_ENDPOINTS_ENABLED=' "$template" | tail -1 | cut -d= -f2- || true)"

  if [[ "$mode" != "private" && "$mode" != "public" ]]; then
    deployment_failures=$((deployment_failures + 1))
  fi
  if [[ "$auth" != "true" ]]; then
    deployment_failures=$((deployment_failures + 1))
  fi
  if [[ "$secure_cookie" != "true" ]]; then
    deployment_failures=$((deployment_failures + 1))
  fi
  if [[ -n "$dev_endpoints" && "$dev_endpoints" != "false" ]]; then
    deployment_failures=$((deployment_failures + 1))
  fi
  if grep -Eq '^OPENAPI_URL=/(openapi\.json|docs|redoc)$' "$template"; then
    deployment_failures=$((deployment_failures + 1))
  fi
done

if [[ "$deployment_failures" -gt 0 ]]; then
  fail "Deployment example security requirements are not satisfied ($deployment_failures issue(s)); paths withheld."
elif [[ "${#deploy_templates[@]}" -gt 0 ]]; then
  pass "Deployment examples fail closed."
else
  pass "No deployment environment examples are tracked."
fi

section "Result"
if [[ "$failures" -gt 0 ]]; then
  printf 'Publication audit failed with %s failure(s) and %s warning(s).\n' "$failures" "$warnings"
  exit 1
fi

printf 'Publication audit passed with %s warning(s).\n' "$warnings"
