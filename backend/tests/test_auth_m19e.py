from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.auth import AuthSession
from app.models.auth import User
from app.schemas.tasks import TaskCreate
from app.services import auth as auth_service
from app.services.time import utc_now_iso
from app.services.tasks import create_task


OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "correct horse battery"


def test_auth_disabled_keeps_existing_endpoints_open(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/tasks")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_login_sets_session_cookie_and_session_endpoint_returns_user(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    auth_service.create_owner(db_session, email=OWNER_EMAIL, password=OWNER_PASSWORD)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        login = client.post("/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
        session = client.get("/api/auth/session")
    finally:
        app.dependency_overrides.clear()

    assert login.status_code == 200
    assert settings.auth_cookie_name in login.cookies
    assert login.json()["csrf_token"]
    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    assert session.json()["user"]["email"] == OWNER_EMAIL
    assert session.json()["csrf_token"]


def test_login_invalid_credentials_fails_without_session(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    auth_service.create_owner(db_session, email=OWNER_EMAIL, password=OWNER_PASSWORD)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).post("/api/auth/login", json={"email": OWNER_EMAIL, "password": "wrong"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert settings.auth_cookie_name not in response.cookies


def test_logout_revokes_session_and_clears_cookie(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    auth_service.create_owner(db_session, email=OWNER_EMAIL, password=OWNER_PASSWORD)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        login = client.post("/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
        csrf = login.json()["csrf_token"]
        logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
        tasks = client.get("/api/tasks")
    finally:
        app.dependency_overrides.clear()

    assert logout.status_code == 200
    assert settings.auth_cookie_name in logout.headers.get("set-cookie", "")
    assert tasks.status_code == 401


def test_expired_session_is_rejected(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    user = auth_service.create_owner(db_session, email=OWNER_EMAIL, password=OWNER_PASSWORD)
    tokens = auth_service.create_session(db_session, user)
    session = db_session.scalar(select(AuthSession).where(AuthSession.id == tokens.session.id))
    session.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        client.cookies.set(settings.auth_cookie_name, tokens.session_token)
        response = client.get("/api/tasks")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_auth_enabled_requires_login_for_protected_endpoints(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    auth_service.create_owner(db_session, email=OWNER_EMAIL, password=OWNER_PASSWORD)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        health = client.get("/api/health")
        tasks = client.get("/api/tasks")
        session = client.get("/api/auth/session")
    finally:
        app.dependency_overrides.clear()

    assert health.status_code == 200
    assert session.status_code == 200
    assert session.json()["authenticated"] is False
    assert tasks.status_code == 401


def test_instance_endpoints_require_admin_when_auth_enabled(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    auth_service.create_owner(db_session, email=OWNER_EMAIL, password=OWNER_PASSWORD)
    now = utc_now_iso()
    member = User(
        id=str(uuid4()),
        email="member@example.com",
        password_hash=auth_service.hash_password(OWNER_PASSWORD),
        display_name="Member",
        role="member",
        status="active",
        created_at=now,
        updated_at=now,
    )
    db_session.add(member)
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        login = client.post("/api/auth/login", json={"email": "member@example.com", "password": OWNER_PASSWORD})
        csrf = login.json()["csrf_token"]
        diagnostics = client.get("/api/system/diagnostics")
        backup_status = client.get("/api/backups/status")
        jobs = client.get("/api/jobs")
        create_backup = client.post("/api/backups", headers={"X-CSRF-Token": csrf})
    finally:
        app.dependency_overrides.clear()

    assert diagnostics.status_code == 403
    assert backup_status.status_code == 403
    assert jobs.status_code == 403
    assert create_backup.status_code == 403


def test_dev_telegram_endpoints_disabled_by_default(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "app_dev_endpoints_enabled", False)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        simulate = client.post("/api/dev/simulate-message", json={"text": "/todo", "chat_id": "smoke", "user_id": "smoke"})
        process = client.post("/api/telegram/process-pending")
    finally:
        app.dependency_overrides.clear()

    assert simulate.status_code == 404
    assert process.status_code == 404


def test_csrf_required_for_unsafe_methods_when_auth_enabled(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    auth_service.create_owner(db_session, email=OWNER_EMAIL, password=OWNER_PASSWORD)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        login = client.post("/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
        without_csrf = client.post("/api/tasks", json={"title": "No csrf"})
        with_csrf = client.post(
            "/api/tasks",
            json={"title": "With csrf"},
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        )
    finally:
        app.dependency_overrides.clear()

    assert without_csrf.status_code == 403
    assert with_csrf.status_code == 201
    assert with_csrf.json()["title"] == "With csrf"


def test_get_does_not_require_csrf_when_auth_enabled(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    auth_service.create_owner(db_session, email=OWNER_EMAIL, password=OWNER_PASSWORD)
    create_task(db_session, TaskCreate(title="Visible with cookie"))
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        client.post("/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
        response = client.get("/api/tasks")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["tasks"][0]["title"] == "Visible with cookie"


def test_password_is_hashed_and_duplicate_owner_refused(db_session):
    user = auth_service.create_owner(db_session, email=OWNER_EMAIL, password=OWNER_PASSWORD)

    assert user.password_hash != OWNER_PASSWORD
    assert auth_service.verify_password(OWNER_PASSWORD, user.password_hash) is True
    try:
        auth_service.create_owner(db_session, email="other@example.com", password=OWNER_PASSWORD)
    except ValueError as exc:
        assert "Ya existe un owner" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("duplicate owner was allowed")
