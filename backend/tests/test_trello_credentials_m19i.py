from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.integrations import IntegrationSecret
from app.services import auth as auth_service
from app.services import integrations


PASSWORD = "correct horse battery"


class FakeTrelloApiClient:
    def __init__(self, api_key: str, token: str, *, timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.token = token
        self.timeout = timeout

    async def get_member_boards(self, member_id: str):
        assert self.api_key == "user-api-key"
        assert self.token == "user-token"
        assert member_id == "member-1"
        return [{"id": "board-1", "name": "Board", "closed": False}]


def test_trello_credentials_are_encrypted_and_user_scoped(db_session, monkeypatch):
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setattr(settings, "trello_member_id", "member-1")
    user_a = auth_service.create_owner(db_session, email="trello-secret-a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(db_session, email="trello-secret-b@example.com", password=PASSWORD, allow_existing=True)

    integrations.store_trello_credentials(db_session, user_id=user_a.id, api_key="user-api-key", token="user-token")

    credentials_a = integrations.get_trello_credentials_for_user(db_session, user_a.id)
    credentials_b = integrations.get_trello_credentials_for_user(db_session, user_b.id)
    rows = db_session.query(IntegrationSecret).all()

    assert credentials_a is not None
    assert credentials_a.source == "user_encrypted"
    assert credentials_a.api_key == "user-api-key"
    assert credentials_a.token == "user-token"
    assert credentials_b is None
    assert all("user-api-key" not in row.ciphertext and "user-token" not in row.ciphertext for row in rows)


def test_trello_owner_legacy_env_fallback_and_user_credentials_precedence(db_session, monkeypatch):
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setattr(settings, "trello_api_key", "env-key")
    monkeypatch.setattr(settings, "trello_token", "env-token")
    monkeypatch.setattr(settings, "trello_member_id", "member-1")
    owner = auth_service.create_owner(db_session, email="trello-owner@example.com", password=PASSWORD)

    legacy = integrations.get_trello_credentials_for_user(db_session, owner.id)
    assert legacy is not None
    assert legacy.source == "instance_env"

    integrations.store_trello_credentials(db_session, user_id=owner.id, api_key="user-api-key", token="user-token")
    user_credentials = integrations.get_trello_credentials_for_user(db_session, owner.id)

    assert user_credentials is not None
    assert user_credentials.source == "user_encrypted"
    assert user_credentials.api_key == "user-api-key"


def test_trello_revoke_removes_active_credentials(db_session, monkeypatch):
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setattr(settings, "trello_member_id", "member-1")
    user = auth_service.create_owner(db_session, email="trello-revoke@example.com", password=PASSWORD)
    integrations.store_trello_credentials(db_session, user_id=user.id, api_key="user-api-key", token="user-token")

    integrations.revoke_trello_credentials(db_session, user_id=user.id)

    assert integrations.get_trello_credentials_for_user(db_session, user.id) is None
    assert {row.status for row in db_session.query(IntegrationSecret).all()} == {"revoked"}


def test_trello_integration_endpoints_are_auth_csrf_and_redacted(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setattr(settings, "trello_member_id", "member-1")
    monkeypatch.setattr("app.api.routes.TrelloApiClient", FakeTrelloApiClient)
    auth_service.create_owner(db_session, email="trello-api@example.com", password=PASSWORD)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        assert client.get("/api/integrations/trello/status").status_code == 401
        login = client.post("/api/auth/login", json={"email": "trello-api@example.com", "password": PASSWORD})
        csrf = login.json()["csrf_token"]
        no_csrf = client.post("/api/integrations/trello/manual-credentials", json={"api_key": "user-api-key", "token": "user-token"})
        saved = client.post(
            "/api/integrations/trello/manual-credentials",
            json={"api_key": "user-api-key", "token": "user-token"},
            headers={"X-CSRF-Token": csrf},
        )
        status = client.get("/api/integrations/trello/status")
        validated = client.post("/api/integrations/trello/validate", headers={"X-CSRF-Token": csrf})
    finally:
        app.dependency_overrides.clear()

    assert no_csrf.status_code == 403
    assert saved.status_code == 200
    assert status.json()["credentials_source"] == "user_encrypted"
    assert status.json()["api_key_hint"] == "use...key"
    assert status.json()["token_hint"] == "use...ken"
    assert "user-api-key" not in str(status.json())
    assert "user-token" not in str(status.json())
    assert validated.json()["ok"] is True
    assert validated.json()["boards_seen"] == 1


def test_trello_manual_credentials_require_encryption_key(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", "")
    auth_service.create_owner(db_session, email="trello-no-key@example.com", password=PASSWORD)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        login = client.post("/api/auth/login", json={"email": "trello-no-key@example.com", "password": PASSWORD})
        response = client.post(
            "/api/integrations/trello/manual-credentials",
            json={"api_key": "user-api-key", "token": "user-token"},
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "ALPHAWAVE_SECRET_ENCRYPTION_KEY" in response.json()["detail"]
