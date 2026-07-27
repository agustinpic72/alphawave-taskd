import asyncio

from app.models.telegram import TelegramSnapshot
from app.services.reminders import list_reminders
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_processor import process_pending_updates, store_raw_update


def test_create_list_and_cancel_reminder_by_snapshot(db_session):
    messenger = CollectingMessenger()
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "en 30 minutos recordame revisar el horno"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))
    reminder = list_reminders(db_session, status="pending")[0]
    assert reminder.message == "revisar el horno"
    assert "✅ Recordatorio creado" in messenger.messages[-1][1]
    assert "🔔 revisar el horno" in messenger.messages[-1][1]
    assert "🕘 en 30 minutos" in messenger.messages[-1][1]

    store_raw_update(db_session, raw_update(2, "allowed", "chat", "mis recordatorios"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))
    snapshot = db_session.query(TelegramSnapshot).filter_by(snapshot_type="reminders").one()
    assert reminder.id in snapshot.task_ids_json
    assert "🔔 Recordatorios pendientes" in messenger.messages[-1][1]
    assert "🕘" in messenger.messages[-1][1]

    store_raw_update(db_session, raw_update(3, "allowed", "chat", "cancelar recordatorio 1"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))
    db_session.refresh(reminder)
    assert reminder.status == "cancelled"
    assert "🗑️ Recordatorio cancelado" in messenger.messages[-1][1]


def test_allowlist_still_applies_to_reminders(db_session):
    messenger = CollectingMessenger()
    store_raw_update(db_session, raw_update(1, "bad", "chat", "en 30 minutos recordame revisar el horno"))

    result = asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert result.ignored == 1
    assert list_reminders(db_session) == []


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
