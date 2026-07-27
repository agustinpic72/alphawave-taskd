import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.telegram import TelegramSnapshot, TelegramUpdate
from app.schemas.tasks import TaskCreate
from app.services.tasks import create_task, list_tasks
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_processor import (
    get_last_update_id,
    process_pending_updates,
    send_todo_list,
    store_raw_update,
)

NOW = datetime(2026, 7, 3, 10, 0, tzinfo=ZoneInfo("UTC"))


def test_store_raw_update_persists_update_and_last_update_id(db_session):
    update = store_raw_update(db_session, raw_update(42, "allowed", "chat", "agregá comprar café"))

    assert update is not None
    assert update.update_id == 42
    assert update.from_user_id == "allowed"
    assert update.raw_json is not None
    assert get_last_update_id(db_session) == 42


def test_store_raw_update_can_skip_advancing_real_telegram_offset(db_session):
    update = store_raw_update(db_session, raw_update(-1, "dev-user", "dev-chat", "che, que hago ahora?"), advance_offset=False)

    assert update is not None
    assert update.update_id == -1
    assert get_last_update_id(db_session) is None


def test_allowlist_ignores_other_users(db_session):
    store_raw_update(db_session, raw_update(1, "bad-user", "chat", "agregá comprar café"))
    messenger = CollectingMessenger()

    result = asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert result.processed == 0
    assert result.ignored == 1
    assert messenger.messages == []
    update = db_session.get(TelegramUpdate, 1)
    assert update.status == "ignored"


def test_bulk_add_from_telegram(db_session):
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "agrega:\n- tarea 1\n- tarea 2"))
    messenger = CollectingMessenger()

    result = asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert result.processed == 1
    assert [task.title for task in list_tasks(db_session)] == ["tarea 1", "tarea 2"]
    assert "Agregué:" in messenger.messages[-1][1]


def test_bulk_add_from_telegram_extracts_dates_per_line(db_session, monkeypatch):
    monkeypatch.setattr("app.services.deadlines.current_app_time", lambda: NOW)
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "agrega:\n- comprar cuentas el viernes\n- mañana comprar café"))
    messenger = CollectingMessenger()

    result = asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    tasks = list_tasks(db_session)
    assert result.processed == 1
    assert [task.title for task in tasks] == ["comprar cuentas", "comprar café"]
    assert [task.due_at for task in tasks] == ["2026-07-10T12:00:00+00:00", "2026-07-04T12:00:00+00:00"]


def test_unknown_command_message_is_helpful(db_session):
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "qué onda?"))
    messenger = CollectingMessenger()

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    reply = messenger.messages[-1][1]
    assert reply.startswith("No entendí ese pedido.")
    assert "qué tengo que hacer hoy" in reply


def test_complete_rename_delete_by_snapshot_index(db_session):
    first = create_task(db_session, TaskCreate(title="Primera"))
    second = create_task(db_session, TaskCreate(title="Segunda"))
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))

    store_raw_update(db_session, raw_update(1, "allowed", "chat", "hecho 2"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))
    assert db_session.get(type(second), second.id).status == "completed"

    store_raw_update(db_session, raw_update(2, "allowed", "chat", "renombra 1 a Primera editada"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))
    assert db_session.get(type(first), first.id).title == "Primera editada"

    store_raw_update(db_session, raw_update(3, "allowed", "chat", "borra 1"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))
    assert db_session.get(type(first), first.id).status == "deleted"


def test_move_by_snapshot_index(db_session):
    first = create_task(db_session, TaskCreate(title="Primera"))
    second = create_task(db_session, TaskCreate(title="Segunda"))
    third = create_task(db_session, TaskCreate(title="Tercera"))
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))

    store_raw_update(db_session, raw_update(1, "allowed", "chat", "mueve 3 arriba de 1"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert [task.id for task in list_tasks(db_session)] == [third.id, first.id, second.id]


def test_snapshot_is_saved_when_listing(db_session):
    task = create_task(db_session, TaskCreate(title="Primera"))
    messenger = CollectingMessenger()

    asyncio.run(send_todo_list(db_session, "chat", messenger))

    snapshot = db_session.query(TelegramSnapshot).one()
    assert task.id in snapshot.task_ids_json
    assert messenger.messages[-1][1].startswith("TODO list")


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
