import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app.schemas.tasks import TaskCreate
from app.services.tasks import create_task, list_tasks
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_processor import process_pending_updates, send_todo_list, store_raw_update

NOW = datetime(2026, 7, 3, 10, 0, tzinfo=ZoneInfo("UTC"))


def test_telegram_add_does_not_invent_unconfigured_scope(db_session):
    messenger = CollectingMessenger()
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "agregá revisar endpoint de Project Delta"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert list_tasks(db_session)[0].scope == "Inbox"


def test_telegram_llm_fallback_receives_update_chat_id(db_session, monkeypatch):
    messenger = CollectingMessenger()
    captured = {}

    def fallback(db, text, command, *, chat_id=None):
        captured["chat_id"] = chat_id
        return command

    monkeypatch.setattr("app.services.telegram_processor._llm_fallback_command", fallback)
    store_raw_update(db_session, raw_update(1, "allowed", "linked-chat", "comando desconocido"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert captured["chat_id"] == "linked-chat"


def test_telegram_add_extracts_natural_deadline(db_session, monkeypatch):
    monkeypatch.setattr("app.services.deadlines.current_app_time", lambda: NOW)
    messenger = CollectingMessenger()
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "Agregá comprar cuentas el viernes"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    task = list_tasks(db_session)[0]
    assert task.title == "comprar cuentas"
    assert task.due_at == "2026-07-10T12:00:00+00:00"
    assert any("Agregué: comprar cuentas" in message for _, message in messenger.messages)


def test_telegram_natural_today_phrase_runs_planning(db_session):
    create_task(db_session, TaskCreate(title="comprar café"))
    messenger = CollectingMessenger()
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "buenas, que tengo que hacer hoy?"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert any("Hoy tenés" in message for _, message in messenger.messages)


def test_telegram_scope_and_effort_corrections(db_session):
    create_task(db_session, TaskCreate(title="Primera", scope="Client Work", auto_classify=False))
    create_task(db_session, TaskCreate(title="Segunda"))
    create_task(db_session, TaskCreate(title="Tercera"))
    create_task(db_session, TaskCreate(title="Cuarta"))
    create_task(db_session, TaskCreate(title="Quinta"))
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))

    store_raw_update(db_session, raw_update(1, "allowed", "chat", "la 2 es Client Work"))
    store_raw_update(db_session, raw_update(2, "allowed", "chat", "la 3 es Personal"))
    store_raw_update(db_session, raw_update(3, "allowed", "chat", "la 4 toma 2 horas"))
    store_raw_update(db_session, raw_update(4, "allowed", "chat", "la 5 es tarea rápida"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    tasks = list_tasks(db_session)
    assert tasks[1].scope == "Client Work"
    assert tasks[2].scope == "Personal"
    assert tasks[3].effort_bucket == "deep"
    assert tasks[3].estimated_minutes == 120
    assert tasks[4].effort_bucket == "quick"
    assert tasks[4].context_bucket == "quick_task"


def test_telegram_planning_and_sort_confirmation(db_session):
    first = create_task(db_session, TaskCreate(title="comprar café", priority_label="low"))
    second = create_task(db_session, TaskCreate(title="implementar endpoint Project Delta", priority_label="high"))
    messenger = CollectingMessenger()

    store_raw_update(db_session, raw_update(1, "allowed", "chat", "qué tengo que hacer hoy"))
    store_raw_update(db_session, raw_update(2, "allowed", "chat", "qué hago ahora"))
    store_raw_update(db_session, raw_update(3, "allowed", "chat", "ordená por prioridad"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert any("Hoy tenés" in message for _, message in messenger.messages)
    assert any("Ahora haría" in message for _, message in messenger.messages)
    assert [task.id for task in list_tasks(db_session)] == [first.id, second.id]

    store_raw_update(db_session, raw_update(4, "allowed", "chat", "confirmar"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert [task.id for task in list_tasks(db_session)] == [second.id, first.id]


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
