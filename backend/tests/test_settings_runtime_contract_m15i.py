from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.telegram import TelegramUpdate
from app.services import settings_service


def test_app_settings_overrides_env_defaults_for_runtime_behavior(db_session, monkeypatch):
    monkeypatch.setattr(settings, "reminders_enabled", True)
    monkeypatch.setattr(settings, "daily_briefing_enabled", True)
    monkeypatch.setattr(settings, "backup_enabled", True)
    settings_service.patch_settings(
        db_session,
        {
            "reminders": {"enabled": False},
            "briefing": {"enabled": False},
            "backups": {"enabled": False},
        },
    )

    effective = settings_service.get_settings(db_session)

    assert settings_service.reminder_settings(db_session)["enabled"] is False
    assert settings_service.briefing_settings(db_session)["enabled"] is False
    assert settings_service.backup_settings(db_session)["enabled"] is False
    assert settings_service.llm_enabled(db_session) is False
    assert effective["advanced"]["instance"]["llm_provider"] == "openai"


def test_missing_override_vs_explicit_false_are_distinguishable(db_session, monkeypatch):
    monkeypatch.setattr(settings, "reminders_enabled", True)

    assert settings_service.get_settings(db_session)["reminders"]["enabled"] is True
    assert settings_service.get_sources(db_session)["reminders.enabled"] == "default"

    settings_service.patch_settings(db_session, {"reminders": {"enabled": False}})

    assert settings_service.get_settings(db_session)["reminders"]["enabled"] is False
    assert settings_service.get_sources(db_session)["reminders.enabled"] == "sqlite"

    settings_service.reset_section(db_session, "reminders")

    assert settings_service.get_settings(db_session)["reminders"]["enabled"] is True
    assert settings_service.get_sources(db_session)["reminders.enabled"] == "default"


def test_effective_settings_feed_weekend_runtime(db_session):
    saturday = datetime(2026, 7, 4, 9, 0, tzinfo=timezone.utc)
    settings_service.patch_settings(
        db_session,
        {
            "general": {"timezone": "UTC"},
            "modes": {"weekend": {"enabled": True, "active_days": ["saturday"], "notifications": {"reminders_explicit": False}}},
        },
    )

    weekend = settings_service.get_weekend_mode_state(db_session, now=saturday)

    assert weekend.active_today is True
    assert weekend.notifications["reminders_explicit"] is False
    assert settings_service.should_send_weekend_category(db_session, "manual_telegram", now=saturday) is True
    assert settings_service.llm_enabled(db_session) is False


def test_system_status_payload_redacts_settings_contract_secrets(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "telegram-secret-token")
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "123456789")
    monkeypatch.setattr(settings, "trello_api_key", "trello-secret-key")
    monkeypatch.setattr(settings, "trello_token", "trello-secret-token")
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", "master-secret-key")
    monkeypatch.setattr(settings, "database_url", "postgresql://user:password@localhost/db")
    db_session.add(
        TelegramUpdate(
            update_id=99,
            chat_id="123456789",
            from_user_id="123456789",
            message_id=100,
            text="/todo",
            raw_json='{"token":"telegram-secret-token"}',
            received_at="2026-07-04T10:00:00+00:00",
            status="processed",
            processed_at="2026-07-04T10:00:01+00:00",
        )
    )
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/system/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    for secret in (
        "telegram-secret-token",
        "123456789",
        "trello-secret-key",
        "trello-secret-token",
        "master-secret-key",
        "password@localhost",
    ):
        assert secret not in response.text
