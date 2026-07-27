from datetime import datetime, timedelta, timezone

from app.schemas.reminders import ReminderCreate
from app.services.reminders import cancel_reminder, create_reminder, list_reminders


def test_create_list_and_cancel_reminder(db_session):
    remind_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    reminder = create_reminder(
        db_session,
        ReminderCreate(message="llamar a Juan", remind_at=remind_at, source="dev"),
    )

    reminders = list_reminders(db_session, status="pending")
    assert [item.id for item in reminders] == [reminder.id]

    cancelled = cancel_reminder(db_session, reminder)
    assert cancelled.status == "cancelled"
    assert list_reminders(db_session, status="pending") == []

