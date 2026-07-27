import asyncio
from datetime import datetime, timedelta, timezone
import gzip
import json

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.reminders import Reminder
from app.models.trello import TrelloSyncRun
from app.schemas.reminders import ReminderCreate
from app.schemas.tasks import TaskCreate
from app.services import backups, briefing as briefing_service, settings_service
from app.services.reminder_worker import send_due_reminders
from app.services.reminders import create_reminder
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_processor import process_pending_updates, store_raw_update
from app.services.tasks import create_task


def test_reminders_enabled_false_skips_due_reminders(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    now = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    settings_service.patch_settings(db_session, {"reminders": {"enabled": False}})
    create_reminder(db_session, ReminderCreate(message="no enviar", remind_at=(now - timedelta(minutes=1)).isoformat(), source="ui"))

    sent = asyncio.run(send_due_reminders(db_session, CollectingMessenger(), now=now))

    assert sent == 0


def test_reminders_enabled_true_sends_due_reminders(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    now = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    settings_service.patch_settings(db_session, {"reminders": {"enabled": True}})
    create_reminder(db_session, ReminderCreate(message="enviar", remind_at=(now - timedelta(minutes=1)).isoformat(), source="ui"))
    messenger = CollectingMessenger()

    sent = asyncio.run(send_due_reminders(db_session, messenger, now=now))

    assert sent == 1
    assert messenger.messages[0][1].endswith("enviar")


def test_telegram_task_action_defaults_come_from_app_settings(db_session, monkeypatch):
    fixed = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.task_action_parser.current_app_time", lambda: fixed)
    settings_service.patch_settings(
        db_session,
        {"reminders": {"default_time": "10:30", "snooze_default_time": "11:15", "later_delay_hours": 4}},
    )
    create_task(db_session, TaskCreate(title="comprar cuentas", auto_classify=False))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "recordame llamar mañana"))
    store_raw_update(db_session, raw_update(2, "allowed", "chat", "no quiero hacer comprar cuentas ahora, recordamelo más tarde"))
    messenger = CollectingMessenger()

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    pending = {reminder.message: reminder.remind_at for reminder in db_session.query(Reminder).all()}
    assert pending["llamar"] == "2026-07-04T10:30:00+00:00"
    assert pending["comprar cuentas"] == "2026-07-03T16:00:00+00:00"
    assert "más tarde (16:00)" in messenger.messages[-1][1]


def test_invalid_remind_at_rejected_and_corrupt_reminder_does_not_break_batch(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).post("/api/reminders", json={"message": "bad", "remind_at": "not-a-date"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422

    now = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    db_session.add(
        Reminder(
            id="corrupt",
            message="corrupt",
            remind_at="not-a-date",
            channel="telegram",
            status="pending",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            source="ui",
        )
    )
    create_reminder(db_session, ReminderCreate(message="good", remind_at=(now - timedelta(minutes=1)).isoformat(), source="ui"))
    messenger = CollectingMessenger()

    sent = asyncio.run(send_due_reminders(db_session, messenger, now=now))

    assert sent == 1
    assert messenger.messages[0][1].endswith("good")


def test_briefing_enabled_setting_controls_next_tick_and_cutoff_validation(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    now = datetime(2026, 7, 2, 13, 0, tzinfo=timezone.utc)
    messenger = CollectingMessenger()
    settings_service.patch_settings(db_session, {"briefing": {"enabled": False}})

    disabled = asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=now))

    assert disabled is None
    assert messenger.messages == []
    settings_service.patch_settings(db_session, {"briefing": {"enabled": True, "time": "12:00", "late_cutoff": "19:00"}})
    enabled = asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=now))
    assert enabled is not None

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).patch("/api/settings", json={"briefing": {"time": "13:00", "late_cutoff": "12:00"}})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "posterior a la hora del briefing" in response.text


def test_backup_runtime_uses_app_settings_for_automatic_and_retention(tmp_path, db_session, monkeypatch, minimal_app_db):
    data_dir = tmp_path / "data"
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True)
    sqlite_path = data_dir / "alphawave-taskd.sqlite"
    minimal_app_db(sqlite_path)
    old_backup = backup_dir / "alphawave-taskd-old.sqlite.gz"
    with gzip.open(old_backup, "wb") as handle:
        handle.write(b"old")
    old_backup.with_suffix(".gz.json").write_text(json.dumps({"source": "automatic"}), encoding="utf-8")
    old_time = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()
    old_backup.touch()
    import os

    os.utime(old_backup, (old_time, old_time))
    monkeypatch.setattr(backups, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{sqlite_path}")

    settings_service.patch_settings(db_session, {"backups": {"enabled": False, "retention_days": 1}})
    automatic = backups.create_backup_result(db=db_session, manual=False)
    assert automatic.created is False
    assert "desactivados" in automatic.reason

    manual = backups.create_backup_result(db=db_session, manual=True)
    assert manual.created is True
    assert not old_backup.exists()


def test_weekend_reminders_explicit_controls_telegram_noise(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    saturday = datetime(2026, 7, 4, 10, 0, tzinfo=timezone.utc)
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
    create_reminder(db_session, ReminderCreate(message="silenciado", remind_at=(saturday - timedelta(minutes=1)).isoformat(), source="ui"))
    assert asyncio.run(send_due_reminders(db_session, CollectingMessenger(), now=saturday)) == 0

    settings_service.patch_settings(db_session, {"modes": {"weekend": {"notifications": {"reminders_explicit": True}}}})
    messenger = CollectingMessenger()
    assert asyncio.run(send_due_reminders(db_session, messenger, now=saturday)) == 1


def test_system_status_read_only_no_secrets_and_openai_not_configured(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "super-secret-token")
    monkeypatch.setattr(settings, "trello_api_key", "trello-secret-key")
    monkeypatch.setattr(settings, "trello_token", "trello-secret-token")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/system/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "super-secret-token" not in response.text
    assert "trello-secret-key" not in response.text
    assert "trello-secret-token" not in response.text
    payload = response.json()
    assert payload["services"]["llm"]["safe_to_use"] is False
    assert payload["reminders"]["interval_seconds"] > 0
    assert payload["services"]["reminders"]["interval_seconds"] > 0


def test_system_status_reflects_disabled_runtime_settings(db_session):
    settings_service.patch_settings(db_session, {"reminders": {"enabled": False}, "briefing": {"enabled": False}})
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/system/status")
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["services"]["reminders"]["status"] == "disabled"
    assert payload["services"]["briefing"]["status"] == "disabled"


def test_system_status_trello_dynamic_board_warnings(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "key")
    monkeypatch.setattr(settings, "trello_token", "token")
    monkeypatch.setattr(settings, "trello_member_id", "member")
    settings_service.patch_settings(
        db_session,
        {
            "trello": {
                "boards": {
                    "OFF": {"alias": "OFF", "name": "Off board", "enabled": False, "board_id": ""},
                    "MISS": {
                        "alias": "MISS",
                        "name": "Missing mappings",
                        "enabled": True,
                        "board_id": "missing-board",
                        "workflow_states": [
                            {"key": "pending", "label": "Tareas", "role": "pending", "enabled": True, "list_name": "TAREAS"},
                            {"key": "completed", "label": "Terminadas", "role": "completed", "enabled": True, "list_name": "DONE"},
                            {"key": "perpetual", "label": "Perpetuas", "role": "perpetual", "enabled": False},
                        ],
                    },
                }
            }
        },
    )
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/system/status")
    finally:
        app.dependency_overrides.clear()

    boards = {board["alias"]: board for board in response.json()["services"]["trello"]["boards"]}
    assert response.status_code == 200
    assert boards["OFF"]["status"] == "disabled"
    assert boards["OFF"]["missing_required_roles"] == []
    assert boards["MISS"]["status"] == "warning"
    assert boards["MISS"]["missing_required_roles"] == ["pending", "completed"]


def test_system_status_endpoint_is_read_only(db_session):
    before_sync_runs = db_session.query(TrelloSyncRun).count()
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/system/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert db_session.query(TrelloSyncRun).count() == before_sync_runs


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
