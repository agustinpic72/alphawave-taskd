import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.reminders import Reminder
from app.models.tasks import Task
from app.models.trello import TrelloSyncRun
from app.schemas.reminders import ReminderCreate
from app.schemas.tasks import TaskCreate
from app.services import briefing as briefing_service
from app.services import settings_service
from app.services.reminder_worker import send_due_reminders
from app.services.reminders import create_reminder
from app.services.tasks import create_task
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_processor import process_pending_updates, store_raw_update


def test_weekend_mode_state_uses_effective_timezone_and_days(db_session):
    friday_utc = datetime(2026, 7, 3, 22, 30, tzinfo=timezone.utc)
    settings_service.patch_settings(
        db_session,
        {
            "general": {"timezone": "Etc/GMT-2"},
            "modes": {"weekend": {"enabled": True, "active_days": ["saturday"], "active_scopes": ["Personal"]}},
        },
    )

    state = settings_service.get_weekend_mode_state(db_session, now=friday_utc)

    assert state.enabled is True
    assert state.active_today is True
    assert state.timezone == "Etc/GMT-2"
    assert state.active_days == ["saturday"]
    assert state.active_scopes == ["Personal"]

    settings_service.patch_settings(db_session, {"general": {"timezone": "UTC"}})
    utc_state = settings_service.get_weekend_mode_state(db_session, now=friday_utc)
    assert utc_state.active_today is False

    settings_service.patch_settings(db_session, {"modes": {"weekend": {"enabled": False}}})
    disabled_state = settings_service.get_weekend_mode_state(db_session, now=friday_utc)
    assert disabled_state.active_today is False


def test_weekend_mode_empty_days_and_missing_notifications_are_safe(db_session):
    saturday = datetime(2026, 7, 4, 10, 0, tzinfo=timezone.utc)
    settings_service.patch_settings(
        db_session,
        {"modes": {"weekend": {"enabled": True, "active_days": [], "active_scopes": [], "notifications": {}}}},
    )

    state = settings_service.get_weekend_mode_state(db_session, now=saturday)

    assert state.active_today is False
    assert state.notifications["reminders_explicit"] is True
    assert settings_service.should_send_weekend_category(db_session, "manual_telegram", now=saturday) is True
    assert settings_service.should_send_weekend_category(db_session, "trello_sync", now=saturday) is True
    assert settings_service.should_send_weekend_category(db_session, "backup", now=saturday) is True


def test_weekend_explicit_reminders_can_be_muted_without_marking_sent(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    saturday = datetime(2026, 7, 4, 10, 0, tzinfo=timezone.utc)
    monday = datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc)
    settings_service.patch_settings(
        db_session,
        {
            "modes": {
                "weekend": {
                    "enabled": True,
                    "active_days": ["saturday"],
                    "notifications": {"reminders_explicit": False, "reminders_overdue": False},
                }
            }
        },
    )
    reminder = create_reminder(
        db_session,
        ReminderCreate(message="queda pendiente", remind_at=(saturday - timedelta(minutes=1)).isoformat(), source="ui"),
    )

    sent = asyncio.run(send_due_reminders(db_session, CollectingMessenger(), now=saturday))

    assert sent == 0
    db_session.refresh(reminder)
    assert reminder.status == "pending"
    assert reminder.sent_at is None

    messenger = CollectingMessenger()
    sent_after_weekend = asyncio.run(send_due_reminders(db_session, messenger, now=monday))

    assert sent_after_weekend == 1
    db_session.refresh(reminder)
    assert reminder.status == "sent"
    assert messenger.messages[-1][1].endswith("queda pendiente")


def test_weekend_explicit_reminders_enabled_still_send(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    saturday = datetime(2026, 7, 4, 10, 0, tzinfo=timezone.utc)
    settings_service.patch_settings(
        db_session,
        {"modes": {"weekend": {"enabled": True, "active_days": ["saturday"], "notifications": {"reminders_explicit": True}}}},
    )
    create_reminder(db_session, ReminderCreate(message="enviar igual", remind_at=(saturday - timedelta(minutes=1)).isoformat(), source="telegram"))
    messenger = CollectingMessenger()

    sent = asyncio.run(send_due_reminders(db_session, messenger, now=saturday))

    assert sent == 1
    assert messenger.messages[-1][1].endswith("enviar igual")


def test_weekend_auto_briefing_can_be_muted_but_manual_bypasses(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    saturday = datetime(2026, 7, 4, 13, 0, tzinfo=timezone.utc)
    create_task(db_session, TaskCreate(title="tarea visible", scope="Personal"))
    settings_service.patch_settings(
        db_session,
        {
            "briefing": {"enabled": True, "time": "12:00", "late_cutoff": "19:00", "send_on_startup": True},
            "modes": {
                "weekend": {
                    "enabled": True,
                    "active_days": ["saturday"],
                    "notifications": {"briefing_auto": False, "briefing_late_startup": False},
                }
            },
        },
    )
    messenger = CollectingMessenger()

    skipped = asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=saturday))
    manual = asyncio.run(briefing_service.send_briefing(db_session, messenger, chat_id="chat", reason="manual", now=saturday, force=True))

    assert skipped is None
    assert manual.status == "sent"
    assert any("Briefing" in message for _, message in messenger.messages)


def test_weekend_auto_briefing_enabled_sends(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    saturday = datetime(2026, 7, 4, 13, 0, tzinfo=timezone.utc)
    create_task(db_session, TaskCreate(title="tarea weekend", scope="Personal"))
    settings_service.patch_settings(
        db_session,
        {
            "briefing": {"enabled": True, "time": "12:00", "late_cutoff": "19:00"},
            "modes": {"weekend": {"enabled": True, "active_days": ["saturday"], "notifications": {"briefing_auto": True}}},
        },
    )
    messenger = CollectingMessenger()

    run = asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=saturday))

    assert run is not None
    assert run.status == "sent"


def test_weekend_telegram_manual_commands_still_work(db_session):
    first = create_task(db_session, TaskCreate(title="primera", scope="DELTA"))
    create_task(db_session, TaskCreate(title="segunda", scope="Personal"))
    settings_service.patch_settings(
        db_session,
        {
            "modes": {
                "weekend": {
                    "enabled": True,
                    "active_days": ["saturday"],
                    "active_scopes": ["Personal"],
                    "notifications": {"briefing_auto": False, "reminders_explicit": False},
                }
            }
        },
    )
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "/todo"))
    store_raw_update(db_session, raw_update(2, "allowed", "chat", "qué hago ahora"))
    store_raw_update(db_session, raw_update(3, "allowed", "chat", "hecho 1"))
    messenger = CollectingMessenger()

    result = asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    db_session.refresh(first)
    assert result.processed == 3
    assert any("TODO" in message for _, message in messenger.messages)
    assert any("Ahora haría" in message for _, message in messenger.messages)
    assert first.status == "completed"


def test_weekend_status_is_read_only_and_exposes_runtime_state(db_session):
    before_sync_runs = db_session.query(TrelloSyncRun).count()
    before_reminders = db_session.query(Reminder).count()
    before_tasks = db_session.query(Task).count()
    today = settings_service.WEEKDAY_NAMES[datetime.now(timezone.utc).weekday()]
    settings_service.patch_settings(
        db_session,
        {
            "general": {"timezone": "UTC"},
            "modes": {"weekend": {"enabled": True, "active_days": [today], "notifications": {"reminders_explicit": False}}},
        },
    )
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/system/status")
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["weekend_mode"]["enabled"] is True
    assert payload["weekend_mode"]["active_today"] is True
    assert payload["services"]["weekend_mode"]["muted"]
    assert db_session.query(TrelloSyncRun).count() == before_sync_runs
    assert db_session.query(Reminder).count() == before_reminders
    assert db_session.query(Task).count() == before_tasks


def raw_update(update_id: int, user_id: str, chat_id: str, text: str):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 100,
            "from": {"id": user_id},
            "chat": {"id": chat_id},
            "text": text,
        },
    }
