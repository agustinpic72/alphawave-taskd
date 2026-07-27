import importlib.util
import json
from pathlib import Path
import sys
import zipfile

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.services import diagnostics, system


ROOT = Path(__file__).resolve().parents[2]


def load_export_script():
    path = ROOT / "scripts" / "dev" / "export_diagnostics.py"
    spec = importlib.util.spec_from_file_location("export_diagnostics", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules["export_diagnostics"] = module
    spec.loader.exec_module(module)
    return module


def test_redact_sensitive_nested_values_and_known_patterns(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "123:telegram-secret")
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "123456789")
    monkeypatch.setattr(settings, "trello_api_key", "trello-key")
    monkeypatch.setattr(settings, "trello_token", "trello-token")
    payload = {
        "telegram_bot_token": "123:telegram-secret",
        "chat_id": "123456789",
        "nested": [
            "https://api.telegram.org/bot123:telegram-secret/getMe",
            "https://api.trello.test/?key=trello-key&token=trello-token",
            "postgresql://user:pass@localhost/db",
        ],
    }

    redacted = diagnostics.sanitize_diagnostics(payload)
    serialized = json.dumps(redacted)

    assert "telegram-secret" not in serialized
    assert "123456789" not in serialized
    assert "trello-key" not in serialized
    assert "trello-token" not in serialized
    assert "pass@localhost" not in serialized
    assert "<redacted>" in serialized
    assert "123...789" in serialized


def test_system_status_runtime_metadata_is_stable(db_session, monkeypatch):
    monkeypatch.setattr(system, "_git_commit", lambda: "abc1234")
    monkeypatch.setattr(system, "_git_branch", lambda: "main")
    monkeypatch.setattr(system, "_git_dirty", lambda: False)
    monkeypatch.setattr(system, "_git_ahead", lambda: 2)

    status = system.status(db_session)

    assert status.environment["git_commit"] == "abc1234"
    assert status.environment["git_branch"] == "main"
    assert status.environment["git_dirty"] is False
    assert status.environment["git_ahead"] == 2
    assert status.environment["started_at"]
    assert status.environment["uptime_seconds"] >= 0


def test_system_diagnostics_endpoint_is_sanitized(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "telegram-secret-token")
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "123456789")
    monkeypatch.setattr(settings, "trello_api_key", "trello-secret-key")
    monkeypatch.setattr(settings, "trello_token", "trello-secret-token")
    monkeypatch.setattr(settings, "database_url", "postgresql://user:password@localhost/db")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/system/diagnostics")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_at"]
    assert "runtime" in payload
    assert "status" in payload
    assert "settings_summary" in payload
    assert payload["redaction"]["applied"] is True
    for secret in ("telegram-secret-token", "123456789", "trello-secret-key", "trello-secret-token", "password@localhost"):
        assert secret not in response.text


def test_support_bundle_writer_excludes_secrets_and_contains_manifest(tmp_path, monkeypatch):
    export = load_export_script()
    monkeypatch.setattr(export, "ROOT_DIR", ROOT)
    monkeypatch.setenv("ALPHAWAVE_DIAGNOSTICS_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ALPHAWAVE_BASE_URL", "http://127.0.0.1:1")

    exit_code = export.main()

    assert exit_code == 0
    bundles = list(tmp_path.glob("alphawave-diagnostics-*.zip"))
    assert len(bundles) == 1
    with zipfile.ZipFile(bundles[0]) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "diagnostics.json" in names
        assert "system-status.json" in names
        assert ".env" not in names
        content = "\n".join(archive.read(name).decode("utf-8", errors="replace") for name in names)
    assert "telegram-secret-token" not in content
    assert "trello-secret-token" not in content
