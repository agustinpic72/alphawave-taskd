from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.integrations import UserIntegration
from app.models.settings import AppSetting, UserSetting
from app.schemas.reminders import ReminderCreate
from app.schemas.tasks import TaskCreate
from app.services import auth as auth_service
from app.services import briefing as briefing_service
from app.services import planning as planning_service
from app.services import reminder_worker
from app.services import reminders as reminder_service
from app.services import settings_service
from app.services.tasks import create_task


PASSWORD = "correct horse battery"


class CollectingMessenger:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str) -> int:
        self.messages.append((chat_id, text))
        return len(self.messages)


def _auth_clients(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    user_a = auth_service.create_owner(db_session, email="settings-a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(db_session, email="settings-b@example.com", password=PASSWORD, allow_existing=True)
    app.dependency_overrides[get_db] = lambda: db_session
    client_a = TestClient(app)
    client_b = TestClient(app)
    login_a = client_a.post("/api/auth/login", json={"email": "settings-a@example.com", "password": PASSWORD})
    login_b = client_b.post("/api/auth/login", json={"email": "settings-b@example.com", "password": PASSWORD})
    assert login_a.status_code == 200
    assert login_b.status_code == 200
    return user_a, user_b, client_a, {"X-CSRF-Token": login_a.json()["csrf_token"]}, client_b, {"X-CSRF-Token": login_b.json()["csrf_token"]}


def test_settings_api_isolates_user_preferences_and_explicit_false(db_session, monkeypatch):
    _user_a, _user_b, client_a, headers_a, client_b, _headers_b = _auth_clients(db_session, monkeypatch)
    try:
        response_a = client_a.patch(
            "/api/settings",
            json={"reminders": {"enabled": False}, "general": {"timezone": "America/Argentina/Buenos_Aires"}},
            headers=headers_a,
        )
        settings_a = client_a.get("/api/settings")
        settings_b = client_b.get("/api/settings")
    finally:
        app.dependency_overrides.clear()

    assert response_a.status_code == 200
    assert settings_a.json()["settings"]["reminders"]["enabled"] is False
    assert settings_a.json()["settings"]["general"]["timezone"] == "America/Argentina/Buenos_Aires"
    assert settings_a.json()["sources"]["reminders.enabled"] == "user"
    assert settings_b.json()["settings"]["reminders"]["enabled"] is True
    assert settings_b.json()["settings"]["general"]["timezone"] != "America/Argentina/Buenos_Aires"


def test_user_briefing_weekend_priority_and_timezone_are_isolated(db_session):
    user_a = auth_service.create_owner(db_session, email="a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(db_session, email="b@example.com", password=PASSWORD, allow_existing=True)

    settings_service.patch_settings(
        db_session,
        {
            "briefing": {"time": "10:00"},
            "modes": {"weekend": {"enabled": True, "active_days": ["monday"], "active_scopes": ["Personal"]}},
            "priority": {"preset": "quick_wins"},
            "general": {"timezone": "UTC"},
        },
        user_id=user_a.id,
    )

    monday = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    assert settings_service.briefing_settings(db_session, user_id=user_a.id)["time"] == "10:00"
    assert settings_service.briefing_settings(db_session, user_id=user_b.id)["time"] != "10:00"
    assert settings_service.get_weekend_mode_state(db_session, now=monday, user_id=user_a.id).active_today is True
    assert settings_service.get_weekend_mode_state(db_session, now=monday, user_id=user_b.id).active_today is False
    assert settings_service.priority_settings(db_session, user_id=user_a.id)["preset"] == "quick_wins"
    assert settings_service.priority_settings(db_session, user_id=user_b.id)["preset"] == "balanced"
    assert settings_service.get_settings(db_session, user_id=user_a.id)["general"]["timezone"] == "UTC"


def test_full_draft_patch_routes_user_and_instance_sections(db_session):
    user = auth_service.create_owner(db_session, email="full@example.com", password=PASSWORD)
    settings_service.patch_settings(
        db_session,
        {
            "reminders": {"enabled": False},
            "trello": {"enabled": True, "boards": {}},
            "backups": {"enabled": False, "retention_days": 5},
        },
        user_id=user.id,
        is_admin=True,
    )

    user_sections = {(row.section, row.key) for row in db_session.query(UserSetting).all()}

    assert ("reminders", settings_service.USER_SETTING_SECTION_KEY) in user_sections
    assert ("trello", settings_service.USER_SETTING_SECTION_KEY) not in user_sections
    assert db_session.get(AppSetting, "trello") is not None
    assert db_session.query(UserIntegration).filter_by(user_id=user.id, provider="trello").one()
    assert db_session.get(AppSetting, "backups") is not None
    assert settings_service.get_settings(db_session, user_id=user.id)["reminders"]["enabled"] is False


def test_reminder_worker_uses_reminder_owner_settings(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    user_a = auth_service.create_owner(db_session, email="rem-a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(db_session, email="rem-b@example.com", password=PASSWORD, allow_existing=True)
    settings_service.patch_settings(db_session, {"reminders": {"enabled": False}}, user_id=user_a.id)
    now = datetime.now(timezone.utc)
    reminder_service.create_reminder(
        db_session,
        ReminderCreate(message="A muted", remind_at=(now - timedelta(minutes=1)).isoformat(), source="ui"),
        user_id=user_a.id,
    )
    reminder_service.create_reminder(
        db_session,
        ReminderCreate(message="B sent", remind_at=(now - timedelta(minutes=1)).isoformat(), source="ui"),
        user_id=user_b.id,
    )
    messenger = CollectingMessenger()

    sent = __import__("asyncio").run(reminder_worker.send_due_reminders(db_session, messenger, now=now))

    assert sent == 0
    assert messenger.messages == []


def test_briefing_and_planning_use_user_settings(db_session):
    user_a = auth_service.create_owner(db_session, email="plan-a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(db_session, email="plan-b@example.com", password=PASSWORD, allow_existing=True)
    create_task(db_session, TaskCreate(title="A task", scope="Personal", auto_classify=False), user_id=user_a.id)
    create_task(db_session, TaskCreate(title="B task", scope="Personal", auto_classify=False), user_id=user_b.id)
    settings_service.patch_settings(db_session, {"briefing": {"include_inbox": False, "time": "10:00"}, "priority": {"preset": "deep_work"}}, user_id=user_a.id)

    payload_a = briefing_service.generate_payload(db_session, user_id=user_a.id)
    payload_b = briefing_service.generate_payload(db_session, user_id=user_b.id)
    plan_a = planning_service.today_plan(db_session, user_id=user_a.id)

    assert "A task" in payload_a.text
    assert "B task" not in payload_a.text
    assert "B task" in payload_b.text
    assert settings_service.briefing_settings(db_session, user_id=user_a.id)["time"] == "10:00"
    assert plan_a.recommendations or plan_a.groups


def test_auth_disabled_settings_use_single_owner_fallback(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        patch = client.patch("/api/settings", json={"reminders": {"enabled": False}})
        get = client.get("/api/settings")
    finally:
        app.dependency_overrides.clear()

    assert patch.status_code == 200
    assert get.json()["settings"]["reminders"]["enabled"] is False
    assert db_session.query(UserSetting).count() == 1
