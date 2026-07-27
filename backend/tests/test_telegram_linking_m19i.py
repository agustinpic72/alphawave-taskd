import asyncio
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

from app.core.config import settings
from app.models.integrations import TelegramChatLink, TelegramLinkCode
from app.models.reminders import Reminder
from app.services import auth as auth_service
from app.services import integrations
from app.services import reminders as reminder_service
from app.services.reminder_worker import send_due_reminders
from app.services.tasks import list_tasks
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_processor import process_pending_updates, store_raw_update


PASSWORD = "correct horse battery"


def test_link_code_links_chat_encrypts_destination_and_scopes_inbound_tasks(db_session, monkeypatch):
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode("utf-8"))
    user_a = auth_service.create_owner(db_session, email="tg-link-a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(db_session, email="tg-link-b@example.com", password=PASSWORD, allow_existing=True)
    code, _row = integrations.create_telegram_link_code(db_session, user_id=user_a.id)
    messenger = CollectingMessenger()

    store_raw_update(db_session, _raw_update(1, "from-a", "chat-a", f"/link {code}"))
    result = asyncio.run(process_pending_updates(db_session, messenger))

    assert result.processed == 1
    assert "vinculado" in result.replies[0].casefold()
    link = db_session.query(TelegramChatLink).one()
    assert link.user_id == user_a.id
    assert link.chat_id_hash != "chat-a"
    assert integrations.get_telegram_destination_for_user(db_session, user_a.id) == "chat-a"
    assert integrations.get_telegram_destination_for_user(db_session, user_b.id) is None

    store_raw_update(db_session, _raw_update(2, "from-a", "chat-a", "agregá tarea linkeada"))
    result = asyncio.run(process_pending_updates(db_session, CollectingMessenger()))

    assert result.processed == 1
    assert [task.title for task in list_tasks(db_session, user_id=user_a.id)] == ["tarea linkeada"]
    assert list_tasks(db_session, user_id=user_b.id) == []


def test_link_code_cannot_be_reused_and_expired_or_unknown_codes_are_rejected(db_session, monkeypatch):
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode("utf-8"))
    user = auth_service.create_owner(db_session, email="tg-reuse@example.com", password=PASSWORD)
    code, _row = integrations.create_telegram_link_code(db_session, user_id=user.id)

    store_raw_update(db_session, _raw_update(1, "from-a", "chat-a", f"/vincular {code}"))
    store_raw_update(db_session, _raw_update(2, "from-b", "chat-b", f"/link {code}"))
    store_raw_update(db_session, _raw_update(3, "from-x", "chat-x", "/link NOPE99"))
    result = asyncio.run(process_pending_updates(db_session, CollectingMessenger()))

    assert result.processed == 3
    assert "ya fue usado" in result.replies[1]
    assert "No encontré" in result.replies[2]
    assert db_session.query(TelegramChatLink).count() == 1

    expired_code, expired_row = integrations.create_telegram_link_code(db_session, user_id=user.id)
    expired_row.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db_session.add(expired_row)
    db_session.commit()
    store_raw_update(db_session, _raw_update(4, "from-c", "chat-c", f"/link {expired_code}"))
    result = asyncio.run(process_pending_updates(db_session, CollectingMessenger()))

    assert "venció" in result.replies[-1]


def test_link_without_secret_store_is_inbound_only_and_unlinked_reminder_is_not_marked_sent(db_session, monkeypatch):
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", "")
    user = auth_service.create_owner(db_session, email="tg-inbound-only@example.com", password=PASSWORD)
    code, _row = integrations.create_telegram_link_code(db_session, user_id=user.id)
    store_raw_update(db_session, _raw_update(1, "from-a", "chat-a", f"/link {code}"))

    result = asyncio.run(process_pending_updates(db_session, CollectingMessenger()))

    assert "falta alphawave_secret_encryption_key" in result.replies[0].casefold()
    assert integrations.resolve_telegram_user_id(db_session, "chat-a") == user.id
    assert integrations.get_telegram_destination_for_user(db_session, user.id) is None

    reminder = Reminder(
        id="reminder-a",
        task_id=None,
        message="Ping",
        remind_at="2026-01-01T10:00:00+00:00",
        channel="telegram",
        status="pending",
        created_at="2026-01-01T09:00:00+00:00",
        updated_at="2026-01-01T09:00:00+00:00",
        sent_at=None,
        source=None,
        source_update_id=None,
        user_id=user.id,
    )
    db_session.add(reminder)
    db_session.commit()

    sent = asyncio.run(send_due_reminders(db_session, CollectingMessenger(), now=datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)))

    db_session.refresh(reminder)
    assert sent == 0
    assert reminder.status == "pending"


def test_telegram_legacy_allowlist_still_maps_to_single_owner(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "legacy-user")
    owner = auth_service.create_owner(db_session, email="tg-legacy@example.com", password=PASSWORD)
    store_raw_update(db_session, _raw_update(1, "legacy-user", "legacy-chat", "agregá legacy"))

    result = asyncio.run(process_pending_updates(db_session, CollectingMessenger()))

    assert result.processed == 1
    assert [task.title for task in list_tasks(db_session, user_id=owner.id)] == ["legacy"]


def _raw_update(update_id: int, from_user_id: str, chat_id: str, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": from_user_id},
            "chat": {"id": chat_id},
            "text": text,
        },
    }
