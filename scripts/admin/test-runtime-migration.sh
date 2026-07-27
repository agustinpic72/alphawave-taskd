#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
DB="$TMP/source.sqlite"

python3 - "$DB" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("create table marker (value text)")
    db.execute("insert into marker values ('baseline')")
PY

plan="$($ROOT/scripts/admin/migrate-systemd-runtime-to-docker.sh --plan --database "$DB" --data-volume custom-data --backup-volume custom-backups)"
grep -Fq "Plan complete; runtime state was not changed." <<<"$plan"
grep -Fq "data=custom-data backups=custom-backups" <<<"$plan"

baseline="$TMP/baseline"
"$ROOT/scripts/admin/capture-docker-baseline.sh" --execute --database "$DB" --output-dir "$baseline" --data-volume custom-data --backup-volume custom-backups >/dev/null
python3 - "$baseline" <<'PY'
import json, sqlite3, sys
from pathlib import Path
root = Path(sys.argv[1])
manifest = json.loads((root / "manifest.json").read_text())
assert manifest["format_version"] == 1
assert manifest["data_volume"] == "custom-data"
assert manifest["backup_volume"] == "custom-backups"
assert manifest["database"]["sqlite_integrity"] == "ok"
assert len(manifest["database"]["sha256"]) == 64
with sqlite3.connect(root / "alphawave-taskd.sqlite") as db:
    assert db.execute("select value from marker").fetchone() == ("baseline",)
PY

mkdir -p "$TMP/export/backups"
cp "$DB" "$TMP/export/alphawave-taskd.sqlite"
sleep 0.01
python3 - "$TMP/export/backups/newer.sqlite.gz" <<'PY'
import gzip, sqlite3, sys, tempfile
from pathlib import Path
target = Path(sys.argv[1])
with tempfile.NamedTemporaryFile() as raw:
    with sqlite3.connect(raw.name) as db:
        db.execute("create table marker (value text)")
        db.execute("insert into marker values ('newest-backup')")
    raw.seek(0)
    with gzip.open(target, "wb") as out:
        out.write(raw.read())
PY
kind="$(python3 "$ROOT/scripts/admin/runtime_migration.py" newest --directory "$TMP/export" --output "$TMP/latest.sqlite")"
[[ "$kind" == database ]]
python3 - "$TMP/latest.sqlite" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    assert db.execute("select value from marker").fetchone() == ("baseline",)
PY

secret="never-print-this-token"
output="$(ALPHAWAVE_SECRET_ENCRYPTION_KEY="$secret" "$ROOT/scripts/admin/rollback-docker-to-systemd.sh" --plan --database "$DB")"
! grep -Fq "$secret" <<<"$output"

python3 - "$ROOT/scripts/admin/migrate-systemd-runtime-to-docker.sh" "$ROOT/scripts/admin/rollback-docker-to-systemd.sh" <<'PY'
import sys
from pathlib import Path
migration = Path(sys.argv[1]).read_text()
rollback = Path(sys.argv[2]).read_text()
assert migration.index("trap cleanup_on_error ERR") < migration.index("systemctl --user stop alphawave-taskd")
assert migration.index("systemctl --user stop alphawave-taskd") < migration.index('--output-dir "$BASELINE_DIR"', migration.index("systemctl --user stop alphawave-taskd"))
assert "chown 10001:10001 /target/.alphawave-taskd.sqlite.migrating" in migration
assert 'chown 10001:"$HOST_GID" /target /target-backups' in migration
assert "chmod 770 /target /target-backups" in migration
assert "systemctl --user mask alphawave-taskd.service" in migration
assert "systemctl --user unmask alphawave-taskd.service" in migration
assert "systemctl --user disable alphawave-taskd.service" in migration
assert "wait_for_api_health" in migration
assert "wait_for_web_health" in migration
assert 'docker compose --env-file "$ENV_FILE"' in migration
assert 'config --format json' in migration
assert 'systemd did not stop" >&2; exit 1' not in migration
assert 'Source/destination row counts differ." >&2; exit 1' not in migration
assert "alphawave_data_soak_v1" in migration and "alphawave_data_soak_v1" in rollback
assert "alphawave_backups_soak_v1" in migration and "alphawave_backups_soak_v1" in rollback
assert "recover_previous_systemd_database" in rollback
assert "systemctl --user mask alphawave-taskd.service" in rollback
assert "systemctl --user unmask alphawave-taskd.service" in rollback
assert "transaction_state=replace_started" in rollback
PY

set +e
cancelled="$(printf 'NO\n' | "$ROOT/scripts/admin/migrate-systemd-runtime-to-docker.sh" --execute --database "$DB" 2>&1)"
status=$?
set -e
[[ $status -ne 0 ]]
! grep -Fq "Stopping" <<<"$cancelled"

"$ROOT/scripts/admin/test-external-migration-shell-m20o.sh"
"$ROOT/scripts/admin/test-transactional-rollback-m20o.sh"

echo "runtime migration script drills: PASS"
