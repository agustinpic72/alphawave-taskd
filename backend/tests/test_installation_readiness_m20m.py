from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.services import auth as auth_service
from app.services import integrations, secret_store


def test_trello_user_credentials_require_member_id(db_session, monkeypatch):
    user = auth_service.create_owner(db_session, email="ready@example.com", password="correct horse battery")
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_member_id", "")
    monkeypatch.setattr(secret_store, "is_secret_store_available", lambda: True)
    monkeypatch.setattr(secret_store, "get_integration_secret_hint", lambda *args, secret_type, **kwargs: "key...hint" if secret_type == "api_key" else "token...hint")
    monkeypatch.setattr(secret_store, "has_integration_secret", lambda *args, **kwargs: True)
    monkeypatch.setattr(secret_store, "get_integration_secret", lambda *args, secret_type, **kwargs: "key" if secret_type == "api_key" else "token")

    result = integrations.trello_status_for_user(db_session, user.id)

    assert result["credentials_present"] is True
    assert result["member_id_configured"] is False
    assert result["configured"] is False
    assert result["status"] == "requires_config"


def test_instance_trello_credentials_do_not_count_as_configured_without_member_id(db_session, monkeypatch):
    user = auth_service.create_owner(db_session, email="owner-ready@example.com", password="correct horse battery")
    monkeypatch.setattr(settings, "trello_api_key", "instance-key")
    monkeypatch.setattr(settings, "trello_token", "instance-token")
    monkeypatch.setattr(settings, "trello_member_id", "")

    result = integrations.trello_status_for_user(db_session, user.id)

    assert result["credentials_present"] is True
    assert result["configured"] is False
    assert result["credentials_source"] == "instance_env"
    assert result["status"] == "requires_config"


def test_installation_readiness_separates_runtime_and_user_and_keeps_fallback(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_member_id", "")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/system/installation-readiness")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"generated_at", "instance", "user"}
    assert payload["instance"]["trello"]["status"] == "requires_configuration"
    assert payload["user"]["trello"]["ready"] is False
    assert payload["instance"]["llm"]["status"] == "unavailable"
    assert "heurísticas determinísticas" in payload["instance"]["llm"]["fallback"]
    assert payload["user"]["llm"]["ready"] is False
