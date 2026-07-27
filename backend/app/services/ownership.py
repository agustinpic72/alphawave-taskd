from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.confirmations import PendingConfirmation
from app.models.reminders import Reminder
from app.models.tasks import Task, TaskEvent
from app.services import auth as auth_service


def integration_owner_user_id(db: Session, integration: str | None = None) -> str:
    return auth_service.single_owner_user_id(db)


def telegram_owner_user_id(db: Session, chat_id: str | None = None) -> str:
    return auth_service.single_owner_user_id(db)


def backfill_core_user_ids(db: Session) -> str:
    owner_id = auth_service.single_owner_user_id(db)
    for model in (Task, Reminder, PendingConfirmation):
        rows = db.scalars(select(model).where((model.user_id.is_(None)) | (model.user_id == ""))).all()
        for row in rows:
            row.user_id = owner_id
    events = db.scalars(select(TaskEvent).where((TaskEvent.user_id.is_(None)) | (TaskEvent.user_id == ""))).all()
    for event in events:
        task = db.get(Task, event.task_id) if event.task_id else None
        event.user_id = task.user_id if task and task.user_id else owner_id
    db.commit()
    return owner_id
