import gzip
import sqlite3
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.services import backups
from app.services import system as system_service


def test_preflight_ok_with_minimal_local_setup(tmp_path, monkeypatch):
    prepare_repo(tmp_path)
    monkeypatch.setattr(system_service, "REPO_ROOT", tmp_path)
    configure_minimal(monkeypatch, tmp_path)

    report = system_service.preflight()

    assert report.status == "ok"
    assert {check.name for check in report.checks} >= {"repo_root", "python_backend", "sqlite_path"}


def test_preflight_telegram_enabled_missing_credentials_is_error(tmp_path, monkeypatch):
    prepare_repo(tmp_path)
    monkeypatch.setattr(system_service, "REPO_ROOT", tmp_path)
    configure_minimal(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "")

    report = system_service.preflight()

    assert report.status == "error"
    assert any(check.name == "telegram_bot_token" for check in report.checks)


def test_preflight_trello_write_requires_trello_enabled(tmp_path, monkeypatch):
    prepare_repo(tmp_path)
    monkeypatch.setattr(system_service, "REPO_ROOT", tmp_path)
    configure_minimal(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "trello_enabled", False)
    monkeypatch.setattr(settings, "trello_write_enabled", True)

    report = system_service.preflight()

    assert report.status == "error"
    assert any("TRELLO_WRITE_ENABLED" in check.message for check in report.checks)


def test_preflight_trello_accepts_one_configured_board(tmp_path, monkeypatch):
    prepare_repo(tmp_path)
    monkeypatch.setattr(system_service, "REPO_ROOT", tmp_path)
    configure_minimal(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "key")
    monkeypatch.setattr(settings, "trello_token", "token")
    monkeypatch.setattr(settings, "trello_member_id", "member")
    monkeypatch.setattr(settings, "trello_board_alpha_id", "board-alpha")
    monkeypatch.setattr(settings, "trello_board_beta_id", "")

    report = system_service.preflight()
    trello = next(check for check in report.checks if check.name == "trello_config")

    assert trello.status == "ok"
    assert trello.message == "configured"


def test_system_health_info_and_preflight_endpoints_do_not_expose_tokens(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "super-secret-token")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        health = client.get("/api/system/health")
        info = client.get("/api/system/info")
        preflight = client.get("/api/system/preflight")
    finally:
        app.dependency_overrides.clear()

    assert health.status_code == 200
    assert health.json()["database"] == "ok"
    assert info.status_code == 200
    assert "super-secret-token" not in info.text
    assert preflight.status_code == 200
    assert "checks" in preflight.json()
    assert "super-secret-token" not in preflight.text


def test_backup_restore_creates_safety_backup_and_restores_database(tmp_path, monkeypatch, minimal_app_db):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sqlite_path = data_dir / "alphawave-taskd.sqlite"
    minimal_app_db(sqlite_path)
    monkeypatch.setattr(backups, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{sqlite_path}")
    monkeypatch.setattr(settings, "backup_enabled", True)

    backup_path = backups.create_backup()
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute("INSERT INTO app_state (key, value, updated_at) VALUES ('restore-marker', 'changed', '')")

    safety = system_service.restore_database_from_backup(backup_path, create_safety_backup=backups.create_backup)

    assert safety is not None
    assert safety.exists()
    with sqlite3.connect(sqlite_path) as connection:
        assert connection.execute("SELECT value FROM app_state WHERE key = 'restore-marker'").fetchone() is None
    with gzip.open(safety, "rb") as handle:
        safety_copy = tmp_path / "safety.sqlite"
        safety_copy.write_bytes(handle.read())
    with sqlite3.connect(safety_copy) as connection:
        assert connection.execute("SELECT value FROM app_state WHERE key = 'restore-marker'").fetchone() == ("changed",)


def test_backup_status_endpoint_returns_latest_backup(tmp_path, monkeypatch, db_session, minimal_app_db):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sqlite_path = data_dir / "alphawave-taskd.sqlite"
    minimal_app_db(sqlite_path)
    monkeypatch.setattr(backups, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{sqlite_path}")
    monkeypatch.setattr(settings, "backup_enabled", True)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        backups.create_backup()
        client = TestClient(app)
        response = client.get("/api/system/backup-status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["latest_path"].endswith(".sqlite.gz")
    assert payload["latest_created_at"]


def test_systemd_template_and_install_script_are_safe():
    root = Path(__file__).resolve().parents[2]
    service = (root / "systemd" / "alphawave-taskd.service").read_text(encoding="utf-8")
    install = (root / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert "WorkingDirectory=__REPO_PATH__" in service
    assert "EnvironmentFile=__REPO_PATH__/.env" in service
    assert "Environment=PATH=__SERVICE_PATH__" in service
    assert "__SERVICE_PATH__" in install
    assert "TELEGRAM_BOT_TOKEN" not in service
    assert "systemctl --user enable alphawave-taskd" in install
    assert "systemctl --user start alphawave-taskd" not in install


def test_smoke_script_dry_run():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([str(root / "scripts" / "smoke.sh"), "--dry-run"], check=False, capture_output=True, text=True)

    assert result.returncode == 0
    assert "Smoke dry-run" in result.stdout


def prepare_repo(path: Path) -> None:
    (path / "backend" / "app").mkdir(parents=True)
    (path / "backend" / ".venv" / "bin").mkdir(parents=True)
    (path / "backend" / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    (path / "frontend" / "node_modules").mkdir(parents=True)
    (path / "frontend" / "dist").mkdir(parents=True)
    (path / "frontend" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    (path / ".env.example").write_text("APP_HOST=127.0.0.1\n", encoding="utf-8")
    (path / ".env").write_text("APP_HOST=127.0.0.1\n", encoding="utf-8")
    (path / "data" / "backups").mkdir(parents=True)
    (path / "logs").mkdir()


def configure_minimal(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{root / 'data' / 'alphawave-taskd.sqlite'}")
    monkeypatch.setattr(settings, "app_host", "127.0.0.1")
    monkeypatch.setattr(settings, "app_port", 8711)
    monkeypatch.setattr(settings, "app_timezone", "UTC")
    monkeypatch.setattr(settings, "daily_briefing_time", "13:00")
    monkeypatch.setattr(settings, "daily_briefing_late_cutoff", "19:00")
    monkeypatch.setattr(settings, "backup_retention_days", 30)
    monkeypatch.setattr(settings, "telegram_enabled", False)
    monkeypatch.setattr(settings, "trello_enabled", False)
    monkeypatch.setattr(settings, "trello_write_enabled", False)
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode())
