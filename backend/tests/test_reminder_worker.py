import asyncio
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.schemas.reminders import ReminderCreate
from app.services.reminder_worker import send_due_reminders
from app.services.reminders import create_reminder
from app.services.telegram_client import CollectingMessenger


def test_scheduler_sends_due_reminder_and_does_not_duplicate(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    now = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    reminder = create_reminder(
        db_session,
        ReminderCreate(
            message="llamar a Juan",
            remind_at=(now - timedelta(minutes=1)).isoformat(),
            source="dev",
        ),
    )
    messenger = CollectingMessenger()

    sent = asyncio.run(send_due_reminders(db_session, messenger, now=now))
    sent_again = asyncio.run(send_due_reminders(db_session, messenger, now=now))

    assert sent == 1
    assert sent_again == 0
    assert messenger.messages == [("chat", "🔔 Recordatorio\n\nllamar a Juan")]
    db_session.refresh(reminder)
    assert reminder.status == "sent"
    assert reminder.sent_at is not None


def test_scheduler_groups_more_than_three_overdue_reminders(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    now = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    for index in range(5):
        create_reminder(
            db_session,
            ReminderCreate(
                message=f"recordatorio {index + 1}",
                remind_at=(now - timedelta(minutes=index + 1)).isoformat(),
                source="dev",
            ),
        )
    messenger = CollectingMessenger()

    sent = asyncio.run(send_due_reminders(db_session, messenger, now=now))

    assert sent == 5
    assert len(messenger.messages) == 1
    assert "🔔 Tenías 5 recordatorios pendientes" in messenger.messages[0][1]
    assert "1. recordatorio" in messenger.messages[0][1]
    assert "vencía:" in messenger.messages[0][1]
    assert "recordatorio 5" in messenger.messages[0][1]


def test_scheduler_keeps_due_reminder_pending_when_telegram_fails(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")
    now = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    reminder = create_reminder(
        db_session,
        ReminderCreate(
            message="fallo telegram",
            remind_at=(now - timedelta(minutes=1)).isoformat(),
            source="dev",
        ),
    )
    messenger = FailingMessenger()

    sent = asyncio.run(send_due_reminders(db_session, messenger, now=now))

    assert sent == 0
    db_session.refresh(reminder)
    assert reminder.status == "pending"
    assert reminder.sent_at is None


class FailingMessenger:
    async def send_message(self, chat_id: str, text: str) -> int | None:
        raise RuntimeError("send failed")
