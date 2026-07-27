import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.confirmations import PendingConfirmation
from app.services import auth as auth_service
from app.services.time import utc_now_iso


def create_confirmation(
    db: Session,
    *,
    source: str,
    action_type: str,
    payload: dict,
    chat_id: str | None = None,
    user_message_id: int | None = None,
    ttl_hours: int = 24,
    summary: str | None = None,
    user_id: str | None = None,
) -> PendingConfirmation:
    user_id = user_id or auth_service.single_owner_user_id(db)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    confirmation = PendingConfirmation(
        id=str(uuid4()),
        user_id=user_id,
        source=source,
        chat_id=chat_id,
        user_message_id=user_message_id,
        action_type=action_type,
        payload_json=json.dumps(payload, ensure_ascii=False),
        status="pending",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=ttl_hours)).isoformat(),
        summary=summary,
    )
    db.add(confirmation)
    db.commit()
    db.refresh(confirmation)
    return confirmation


def list_pending(db: Session, *, chat_id: str | None = None, source: str | None = None, user_id: str | None = None) -> list[PendingConfirmation]:
    _expire_due(db)
    statement = select(PendingConfirmation).where(PendingConfirmation.status == "pending")
    if chat_id is not None:
        statement = statement.where(PendingConfirmation.chat_id == chat_id)
    if source is not None:
        statement = statement.where(PendingConfirmation.source == source)
    if user_id is not None:
        statement = statement.where(PendingConfirmation.user_id == user_id)
    statement = statement.order_by(desc(PendingConfirmation.created_at))
    return list(db.scalars(statement).all())


def latest_pending(db: Session, *, chat_id: str | None = None, action_type: str | None = None, user_id: str | None = None) -> PendingConfirmation | None:
    statement = select(PendingConfirmation).where(PendingConfirmation.status == "pending")
    if chat_id is not None:
        statement = statement.where(PendingConfirmation.chat_id == chat_id)
    if action_type is not None:
        statement = statement.where(PendingConfirmation.action_type == action_type)
    if user_id is not None:
        statement = statement.where(PendingConfirmation.user_id == user_id)
    statement = statement.order_by(PendingConfirmation.created_at.desc()).limit(1)
    confirmation = db.scalar(statement)
    if confirmation and confirmation.expires_at <= utc_now_iso():
        confirmation.status = "expired"
        confirmation.resolved_at = utc_now_iso()
        db.commit()
        return None
    return confirmation


def get_pending(db: Session, confirmation_id: str, *, user_id: str | None = None) -> PendingConfirmation | None:
    confirmation = db.get(PendingConfirmation, confirmation_id)
    if user_id and (not confirmation or confirmation.user_id != user_id):
        return None
    if not confirmation or confirmation.status != "pending":
        return None
    if confirmation.expires_at <= utc_now_iso():
        confirmation.status = "expired"
        confirmation.resolved_at = utc_now_iso()
        db.commit()
        return None
    return confirmation


def confirm(db: Session, confirmation: PendingConfirmation) -> None:
    confirmation.status = "confirmed"
    confirmation.resolved_at = utc_now_iso()
    db.commit()


def cancel(db: Session, confirmation: PendingConfirmation) -> None:
    confirmation.status = "cancelled"
    confirmation.resolved_at = utc_now_iso()
    db.commit()


def fail(db: Session, confirmation: PendingConfirmation, error: str) -> None:
    confirmation.status = "failed"
    confirmation.error = error
    confirmation.resolved_at = utc_now_iso()
    db.commit()


def _expire_due(db: Session) -> None:
    now = utc_now_iso()
    due = list(
        db.scalars(
            select(PendingConfirmation).where(
                PendingConfirmation.status == "pending",
                PendingConfirmation.expires_at <= now,
            )
        )
    )
    for confirmation in due:
        confirmation.status = "expired"
        confirmation.resolved_at = now
    if due:
        db.commit()
