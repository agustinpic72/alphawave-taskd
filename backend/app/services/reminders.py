from datetime import datetime
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.reminders import Reminder
from app.schemas.reminders import ReminderCreate, ReminderUpdate
from app.services import auth as auth_service
from app.services.time import utc_now_iso


logger = logging.getLogger(__name__)


def list_reminders(db: Session, status: str | None = None, limit: int | None = None, *, user_id: str | None = None) -> list[Reminder]:
    statement = select(Reminder).order_by(Reminder.remind_at.asc(), Reminder.created_at.asc())
    if status:
        statement = statement.where(Reminder.status == status)
    if user_id:
        statement = statement.where(Reminder.user_id == user_id)
    if limit:
        statement = statement.limit(limit)
    return list(db.scalars(statement).all())


def get_reminder(db: Session, reminder_id: str, *, user_id: str | None = None) -> Reminder | None:
    reminder = db.get(Reminder, reminder_id)
    if user_id and (not reminder or reminder.user_id != user_id):
        return None
    return reminder


def create_reminder(
    db: Session,
    payload: ReminderCreate,
    *,
    source_update_id: int | None = None,
    user_id: str | None = None,
) -> Reminder:
    user_id = user_id or auth_service.single_owner_user_id(db)
    now = utc_now_iso()
    reminder = Reminder(
        id=str(uuid4()),
        user_id=user_id,
        task_id=payload.task_id,
        message=payload.message.strip(),
        remind_at=payload.remind_at,
        channel=payload.channel or "telegram",
        status="pending",
        created_at=now,
        updated_at=now,
        source=payload.source,
        source_update_id=source_update_id,
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


def update_reminder(db: Session, reminder: Reminder, payload: ReminderUpdate) -> Reminder:
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(reminder, field, value)
    reminder.updated_at = utc_now_iso()
    db.commit()
    db.refresh(reminder)
    return reminder


def cancel_reminder(db: Session, reminder: Reminder) -> Reminder:
    reminder.status = "cancelled"
    reminder.updated_at = utc_now_iso()
    db.commit()
    db.refresh(reminder)
    return reminder


def due_reminders(db: Session, now_iso: str, *, user_id: str | None = None) -> list[Reminder]:
    now = parse_iso(now_iso)
    statement = select(Reminder).where(Reminder.status == "pending")
    if user_id:
        statement = statement.where(Reminder.user_id == user_id)
    pending = list(db.scalars(statement.order_by(Reminder.remind_at.asc(), Reminder.created_at.asc())).all())
    due: list[Reminder] = []
    for reminder in pending:
        try:
            if parse_iso(reminder.remind_at) <= now:
                due.append(reminder)
        except ValueError:
            logger.warning("Skipping corrupt reminder datetime", extra={"reminder_id": reminder.id})
    return due


def mark_sent(db: Session, reminder: Reminder, sent_at: str | None = None) -> Reminder:
    now = sent_at or utc_now_iso()
    reminder.status = "sent"
    reminder.sent_at = now
    reminder.updated_at = now
    db.commit()
    db.refresh(reminder)
    return reminder


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
