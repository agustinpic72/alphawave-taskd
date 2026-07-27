import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.main import app
from app.models.reminders import Reminder
from app.models.tasks import TaskEvent
from app.schemas.tasks import TaskCreate
from app.services.planning import now_plan
from app.services.reminders import list_reminders
from app.services.tasks import create_task, list_tasks
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_parser import parse_command
from app.services.telegram_processor import process_pending_updates, store_raw_update


NOW = datetime(2026, 7, 3, 10, 0, tzinfo=ZoneInfo("UTC"))


def test_parse_task_targeted_actions(monkeypatch):
    monkeypatch.setattr("app.services.task_action_parser.current_app_time", lambda: NOW)

    cases = {
        "ver peli con mi novia recordamelo el sábado": ("task_reminder_snooze", "ver peli con mi novia", "2026-07-04T09:00:00+00:00"),
        "recordame ver peli con mi novia el sábado": ("create_reminder", "ver peli con mi novia", "2026-07-04T09:00:00+00:00"),
        "comprar cuentas recordamelo mañana": ("task_reminder_snooze", "comprar cuentas", "2026-07-04T09:00:00+00:00"),
        "comprar cuentas recordamelo maniana": ("task_reminder_snooze", "comprar cuentas", "2026-07-04T09:00:00+00:00"),
        "recordame comprar cuentas mañana a las 10": ("create_reminder", "comprar cuentas", "2026-07-04T10:00:00+00:00"),
        "recordame comprar cuentas maniana a las 10": ("create_reminder", "comprar cuentas", "2026-07-04T10:00:00+00:00"),
        "posponé comprar cuentas hasta el sábado": ("task_snooze", "comprar cuentas", "2026-07-04T09:00:00+00:00"),
        "pospone comprar cuentas hasta el sabado": ("task_snooze", "comprar cuentas", "2026-07-04T09:00:00+00:00"),
        "snooze comprar cuentas hasta mañana": ("task_snooze", "comprar cuentas", "2026-07-04T09:00:00+00:00"),
        "comprar cuentas deadline viernes": ("task_deadline", "comprar cuentas", "2026-07-10T12:00:00+00:00"),
        "cambiá comprar cuentas al viernes": ("task_snooze", "comprar cuentas", "2026-07-10T09:00:00+00:00"),
    }
    for text, expected in cases.items():
        command = parse_command(text)
        assert (command.action, command.payload["target"], command.payload["when_at"]) == expected

    later = parse_command("no quiero hacer comprar cuentas ahora, recordamelo más tarde")
    assert later.action == "task_reminder_snooze"
    assert later.payload["target"] == "comprar cuentas"
    assert later.payload["when_at"] == "2026-07-03T12:00:00+00:00"

    later_ascii = parse_command("no quiero hacer comprar cuentas ahora, recordamelo mas tarde")
    assert later_ascii.action == "task_reminder_snooze"
    assert later_ascii.payload["target"] == "comprar cuentas"
    assert later_ascii.payload["when_at"] == "2026-07-03T12:00:00+00:00"

    add = parse_command("agregá comprar cuentas el viernes")
    assert add.action == "add"
    assert add.payload["title"] == "comprar cuentas el viernes"


def test_existing_task_recordamelo_snoozes_and_creates_associated_reminder(db_session, monkeypatch):
    monkeypatch.setattr("app.services.task_action_parser.current_app_time", lambda: NOW)
    task = create_task(db_session, TaskCreate(title="ver peli con mi novia"))
    messenger = CollectingMessenger()
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "ver peli con mi novia recordamelo el sábado"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    db_session.refresh(task)
    reminders = list_reminders(db_session, status="pending")
    assert len(list_tasks(db_session)) == 1
    assert task.snoozed_until == "2026-07-04T09:00:00+00:00"
    assert reminders[0].task_id == task.id
    assert reminders[0].remind_at == task.snoozed_until
    assert "✅ Recordatorio creado" in messenger.messages[-1][1]
    assert "🔔 ver peli con mi novia" in messenger.messages[-1][1]
    assert "🕘 el sábado a las 09:00" in messenger.messages[-1][1]


def test_no_existing_task_recordamelo_asks_without_creating(db_session, monkeypatch):
    monkeypatch.setattr("app.services.task_action_parser.current_app_time", lambda: NOW)
    messenger = CollectingMessenger()
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "pagar seguro recordamelo el lunes"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert list_tasks(db_session) == []
    assert list_reminders(db_session) == []
    assert "No encontré una tarea clara" in messenger.messages[-1][1]
    assert "Crear tarea + recordatorio" in messenger.messages[-1][1]


def test_prefix_recordame_creates_standalone_or_associated_reminder(db_session, monkeypatch):
    monkeypatch.setattr("app.services.task_action_parser.current_app_time", lambda: NOW)
    task = create_task(db_session, TaskCreate(title="comprar cuentas"))
    messenger = CollectingMessenger()
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "recordame comprar cuentas mañana a las 10"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    db_session.refresh(task)
    associated = db_session.scalars(select(Reminder)).one()
    assert associated.task_id == task.id
    assert associated.remind_at == "2026-07-04T10:00:00+00:00"
    assert task.snoozed_until == associated.remind_at

    store_raw_update(db_session, raw_update(2, "allowed", "chat", "recordame llamar a Juan mañana a las 10"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    reminders = list_reminders(db_session, status="pending")
    assert len(reminders) == 2
    assert reminders[1].task_id is None
    assert reminders[1].message == "llamar a Juan"


def test_snooze_only_and_deadline_only(db_session, monkeypatch):
    monkeypatch.setattr("app.services.task_action_parser.current_app_time", lambda: NOW)
    task = create_task(db_session, TaskCreate(title="comprar cuentas"))
    messenger = CollectingMessenger()

    store_raw_update(db_session, raw_update(1, "allowed", "chat", "posponé comprar cuentas hasta el sábado"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))
    db_session.refresh(task)
    assert task.snoozed_until == "2026-07-04T09:00:00+00:00"
    assert list_reminders(db_session) == []

    store_raw_update(db_session, raw_update(2, "allowed", "chat", "comprar cuentas deadline viernes"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))
    db_session.refresh(task)
    assert task.due_at == "2026-07-10T12:00:00+00:00"
    assert task.snoozed_until == "2026-07-04T09:00:00+00:00"
    assert list_reminders(db_session) == []


def test_multiple_matches_can_be_resolved_by_number(db_session, monkeypatch):
    monkeypatch.setattr("app.services.task_action_parser.current_app_time", lambda: NOW)
    first = create_task(db_session, TaskCreate(title="comprar cuentas"))
    second = create_task(db_session, TaskCreate(title="comprar cuentas proveedor"))
    messenger = CollectingMessenger()

    store_raw_update(db_session, raw_update(1, "allowed", "chat", "comprar cuentas recordamelo mañana"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))
    assert "Encontré varias tareas parecidas" in messenger.messages[-1][1]

    store_raw_update(db_session, raw_update(2, "allowed", "chat", "2"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    db_session.refresh(first)
    db_session.refresh(second)
    reminder = list_reminders(db_session, status="pending")[0]
    assert first.snoozed_until is None
    assert second.snoozed_until == "2026-07-04T09:00:00+00:00"
    assert reminder.task_id == second.id


def test_snoozed_task_is_hidden_from_now_plan(db_session, monkeypatch):
    monkeypatch.setattr("app.services.task_action_parser.current_app_time", lambda: NOW)
    task = create_task(db_session, TaskCreate(title="comprar café"))
    messenger = CollectingMessenger()
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "snooze comprar café hasta mañana"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    db_session.refresh(task)
    assert task.snoozed_until == "2026-07-04T09:00:00+00:00"
    assert all(item.task.id != task.id for item in now_plan(db_session).recommended)


def test_add_task_with_date_still_creates_new_task(db_session, monkeypatch):
    monkeypatch.setattr("app.services.deadlines.current_app_time", lambda: NOW)
    messenger = CollectingMessenger()
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "agregá comprar cuentas el viernes"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    task = list_tasks(db_session)[0]
    assert task.title == "comprar cuentas"
    assert task.due_at == "2026-07-10T12:00:00+00:00"
    assert task.snoozed_until is None
    assert list_reminders(db_session) == []


def test_snooze_endpoint_updates_task_and_event(db_session):
    task = create_task(db_session, TaskCreate(title="comprar café"))
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/tasks/{task.id}/snooze",
            json={"snoozed_until": "2026-07-04T09:00:00+00:00", "reason": "test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    db_session.refresh(task)
    assert task.snoozed_until == "2026-07-04T09:00:00+00:00"
    events = db_session.scalars(select(TaskEvent.event_type).order_by(TaskEvent.created_at)).all()
    assert "snoozed" in events


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
