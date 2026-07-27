import gzip
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.services import backups, settings_service


def _sqlite(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO tasks VALUES ('task-1', 'Snapshot test')")


def _setup(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sqlite_path = data_dir / "alphawave-taskd.sqlite"
    _sqlite(sqlite_path)
    monkeypatch.setattr(backups, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{sqlite_path}")
    return sqlite_path


def test_manual_backup_allowed_when_automatic_disabled_and_returns_metadata(tmp_path, db_session, monkeypatch):
    _setup(tmp_path, monkeypatch)
    settings_service.patch_settings(db_session, {"backups": {"enabled": False, "retention_days": 7}})

    result = backups.create_backup_result(db=db_session, manual=True)

    assert result.created is True
    assert result.backup["source"] == "manual"
    assert result.backup["path_redacted"].startswith("data/backups/")
    assert result.backup["size_bytes"] > 0


def test_backup_status_reads_effective_settings_and_lists_newest_first(tmp_path, db_session, monkeypatch):
    _setup(tmp_path, monkeypatch)
    settings_service.patch_settings(db_session, {"backups": {"enabled": False, "retention_days": 3}})
    first = backups.create_backup_result(db=db_session, manual=True).path
    second = backups.create_backup_result(db=db_session, manual=True).path

    payload = backups.backup_status_payload(db=db_session)
    listed = backups.list_backups()

    assert payload["automatic_enabled"] is False
    assert payload["retention_days"] == 3
    assert payload["count"] == 2
    assert listed[0]["id"] == backups.backup_id_from_path(second)
    assert listed[1]["id"] == backups.backup_id_from_path(first)
    assert str(tmp_path) not in listed[0]["path_redacted"]


def test_retention_preserves_manual_unknown_and_newest_backup(tmp_path, db_session, monkeypatch):
    _setup(tmp_path, monkeypatch)
    backup_dir = tmp_path / "data" / "backups"
    backup_dir.mkdir(exist_ok=True)
    old_manual = backup_dir / "alphawave-taskd-old-manual.sqlite.gz"
    old_auto = backup_dir / "alphawave-taskd-old-auto.sqlite.gz"
    for path in (old_manual, old_auto):
        with gzip.open(path, "wb") as handle:
            handle.write(b"not sqlite")
        old = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
        os.utime(path, (old, old))
    old_auto.with_suffix(".gz.json").write_text(json.dumps({"source": "automatic"}), encoding="utf-8")
    settings_service.patch_settings(db_session, {"backups": {"enabled": True, "retention_days": 1}})

    newest = backups.create_backup_result(db=db_session, manual=False).path

    assert newest.exists()
    assert old_manual.exists()
    assert not old_auto.exists()


def test_validate_backup_valid_missing_and_invalid(tmp_path, db_session, monkeypatch):
    _setup(tmp_path, monkeypatch)
    valid = backups.create_backup_result(db=db_session, manual=True).path
    missing = tmp_path / "data" / "backups" / "missing.sqlite.gz"
    invalid = tmp_path / "data" / "backups" / "alphawave-taskd-invalid.sqlite.gz"
    invalid.write_bytes(b"")

    assert backups.validate_backup(valid)["sqlite_integrity"] == "ok"
    assert backups.validate_backup(missing)["readable"] is False
    assert backups.validate_backup(invalid)["sqlite_integrity"] == "failed"


def test_restore_plan_is_read_only_and_returns_confirmation_phrase(tmp_path, db_session, monkeypatch):
    sqlite_path = _setup(tmp_path, monkeypatch)
    backup = backups.create_backup_result(db=db_session, manual=True).path
    before = sqlite_path.read_bytes()

    plan = backups.restore_plan(backup)

    assert sqlite_path.read_bytes() == before
    assert plan["confirmation_phrase"] == "RESTAURAR BACKUP"
    assert plan["automatic_restore_available"] is True
    assert plan["manual_commands"]


def test_backup_api_endpoints(tmp_path, db_session, monkeypatch):
    _setup(tmp_path, monkeypatch)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        created = client.post("/api/backups")
        backup_id = created.json()["backup"]["id"]
        status = client.get("/api/backups/status")
        listing = client.get("/api/backups")
        validated = client.post(f"/api/backups/{backup_id}/validate")
        plan = client.post(f"/api/backups/{backup_id}/restore-plan")
        restore = client.post(f"/api/backups/{backup_id}/restore", json={"confirmation_phrase": "RESTAURAR BACKUP"})
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 200
    assert status.json()["count"] == 1
    assert listing.json()["backups"][0]["id"] == backup_id
    assert validated.json()["backup"]["valid"] is True
    assert plan.json()["confirmation_phrase"] == "RESTAURAR BACKUP"
    assert restore.status_code == 403
