import gzip
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.services import auth as auth_service
from app.services import backups
from app.services import system


CONFIRMATION = "RESTAURAR BACKUP"
PASSWORD = "correct horse battery"


def _create_app_db(path, marker):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO app_state VALUES ('marker', ?, '')", (marker,))
        connection.execute("INSERT INTO tasks VALUES ('task-1', ?)", (marker,))


def _configure_temp_database(tmp_path, monkeypatch, marker="original"):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_path = data_dir / "alphawave-taskd.sqlite"
    _create_app_db(database_path, marker)
    monkeypatch.setattr(backups, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database_path}")
    return database_path


def _marker(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT value FROM app_state WHERE key = 'marker'").fetchone()[0]


def _set_marker(path, value):
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE app_state SET value = ? WHERE key = 'marker'", (value,))


def test_online_backup_includes_committed_wal_data_and_atomic_metadata(tmp_path, monkeypatch):
    database_path = _configure_temp_database(tmp_path, monkeypatch)
    with sqlite3.connect(database_path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO tasks VALUES ('wal-task', 'committed in WAL')")
        writer.commit()

        result = backups.create_backup_result(source="security-test")

    assert result.created is True
    assert not list(result.path.parent.glob(".snapshot-*"))
    assert not list(result.path.parent.glob(".backup-*"))
    metadata = json.loads(result.path.with_suffix(".gz.json").read_text(encoding="utf-8"))
    assert metadata["format_version"] == backups.BACKUP_FORMAT_VERSION
    assert metadata["sha256"] == backups._sha256(result.path)
    snapshot_path = tmp_path / "snapshot.sqlite"
    with gzip.open(result.path, "rb") as source:
        snapshot_path.write_bytes(source.read())
    with sqlite3.connect(snapshot_path) as snapshot:
        assert snapshot.execute("SELECT title FROM tasks WHERE id = 'wal-task'").fetchone() == ("committed in WAL",)


def test_validation_rejects_checksum_tampering_bad_gzip_and_missing_schema(tmp_path, monkeypatch):
    database_path = _configure_temp_database(tmp_path, monkeypatch)
    valid = backups.create_backup_result().path
    payload = bytearray(valid.read_bytes())
    payload[4] ^= 1
    valid.write_bytes(payload)

    tampered = backups.validate_backup(valid)
    assert tampered["sqlite_integrity"] == "ok"
    assert tampered["checksum_valid"] is False

    bad_gzip = valid.parent / "alphawave-taskd-20260710-120000.sqlite.gz"
    bad_gzip.write_bytes(b"not-gzip")
    assert backups.validate_backup(bad_gzip)["gzip_valid"] is False

    database_path.unlink()
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    missing_schema = backups.create_backup_result().path
    validation = backups.validate_backup(missing_schema)
    assert validation["sqlite_integrity"] == "ok"
    assert validation["schema_valid"] is False
    assert validation["metadata_valid"] is True


def test_backup_ids_reject_traversal_and_symlinks(tmp_path, monkeypatch):
    _configure_temp_database(tmp_path, monkeypatch)
    backup = backups.create_backup_result().path
    outside = tmp_path / "outside.sqlite.gz"
    outside.write_bytes(backup.read_bytes())
    linked = backup.parent / "alphawave-taskd-20260710-120001.sqlite.gz"
    linked.symlink_to(outside)

    assert backups.get_backup("../outside") is None
    assert backups.get_backup("20260710-120001") is None
    assert backups.get_backup(backups.backup_id_from_path(backup)) == backup


def test_restore_is_atomic_and_creates_verified_pre_restore_backup(tmp_path, monkeypatch):
    database_path = _configure_temp_database(tmp_path, monkeypatch, marker="selected")
    selected = backups.create_backup_result(source="manual").path
    _set_marker(database_path, "current")

    safety = system.restore_database_from_backup(
        selected,
        create_safety_backup=lambda: backups.create_backup(source="pre_restore"),
    )

    assert _marker(database_path) == "selected"
    assert _marker_from_backup(safety, tmp_path) == "current"
    assert backups.backup_metadata(safety)["source"] == "pre_restore"
    assert not list(database_path.parent.glob(".restore-*"))


def test_restore_does_not_touch_database_without_safety_backup(tmp_path, monkeypatch):
    database_path = _configure_temp_database(tmp_path, monkeypatch, marker="selected")
    selected = backups.create_backup_result().path
    _set_marker(database_path, "current")

    with pytest.raises(RuntimeError, match="pre-restore"):
        system.restore_database_from_backup(selected, create_safety_backup=lambda: None)

    assert _marker(database_path) == "current"


def test_restore_rolls_back_after_post_replace_failure(tmp_path, monkeypatch):
    database_path = _configure_temp_database(tmp_path, monkeypatch, marker="selected")
    selected = backups.create_backup_result().path
    _set_marker(database_path, "current")
    real_verify = system._verify_restored_database
    calls = 0

    def fail_first_verification(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.DatabaseError("injected post-replace failure")
        real_verify(path)

    monkeypatch.setattr(system, "_verify_restored_database", fail_first_verification)
    with pytest.raises(sqlite3.DatabaseError, match="injected"):
        system.restore_database_from_backup(
            selected,
            create_safety_backup=lambda: backups.create_backup(source="pre_restore"),
        )

    assert calls == 2
    assert _marker(database_path) == "current"
    assert not list(database_path.parent.glob(".restore-*"))


def test_restore_api_requires_auth_csrf_and_exact_confirmation(tmp_path, db_session, monkeypatch):
    database_path = _configure_temp_database(tmp_path, monkeypatch, marker="selected")
    selected = backups.create_backup_result().path
    backup_id = backups.backup_id_from_path(selected)
    _set_marker(database_path, "current")
    monkeypatch.setattr(settings, "auth_enabled", True)
    auth_service.create_owner(db_session, email="owner@example.com", password=PASSWORD)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        login = client.post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
        csrf = login.json()["csrf_token"]
        no_csrf = client.post(
            f"/api/backups/{backup_id}/restore",
            json={"confirmation_phrase": CONFIRMATION},
        )
        wrong_phrase = client.post(
            f"/api/backups/{backup_id}/restore",
            json={"confirmation_phrase": "restore"},
            headers={"X-CSRF-Token": csrf},
        )
        restored = client.post(
            f"/api/backups/{backup_id}/restore",
            json={"confirmation_phrase": CONFIRMATION},
            headers={"X-CSRF-Token": csrf},
        )
    finally:
        app.dependency_overrides.clear()

    assert no_csrf.status_code == 403
    assert wrong_phrase.status_code == 422
    assert restored.status_code == 200
    assert restored.json()["restored"] is True
    assert restored.json()["safety_backup"]["source"] == "pre_restore"
    assert _marker(database_path) == "selected"


def test_restore_api_is_closed_when_auth_is_disabled(tmp_path, db_session, monkeypatch):
    database_path = _configure_temp_database(tmp_path, monkeypatch, marker="selected")
    selected = backups.create_backup_result().path
    backup_id = backups.backup_id_from_path(selected)
    _set_marker(database_path, "current")
    monkeypatch.setattr(settings, "auth_enabled", False)

    def forbidden_restore(*args, **kwargs):
        raise AssertionError("auth-disabled request reached restore service")

    monkeypatch.setattr(system, "restore_database_from_backup", forbidden_restore)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).post(
            f"/api/backups/{backup_id}/restore",
            json={"confirmation_phrase": CONFIRMATION},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert _marker(database_path) == "current"


def _marker_from_backup(path, tmp_path):
    restored = tmp_path / "read-safety.sqlite"
    with gzip.open(path, "rb") as source:
        restored.write_bytes(source.read())
    return _marker(restored)
