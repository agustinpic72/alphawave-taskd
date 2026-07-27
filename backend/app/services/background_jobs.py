import asyncio
import json
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.confirmations import PendingConfirmation
from app.models.jobs import BackgroundJob
from app.schemas.jobs import BackgroundJobRead, BackgroundJobResultItem
from app.services import confirmations as confirmation_service
from app.services import planning as planning_service
from app.services import task_suggestions
from app.services import trello_actions
from app.services.time import utc_now_iso


BULK_CONFIRMATIONS_KIND = "bulk_confirmations"
TERMINAL_STATUSES = {"completed", "partial", "failed"}


def create_bulk_confirmation_job(db: Session, confirmation_ids: list[str], *, user_id: str) -> BackgroundJob:
    now = utc_now_iso()
    job = BackgroundJob(
        id=str(uuid4()),
        kind=BULK_CONFIRMATIONS_KIND,
        status="queued",
        total=len(confirmation_ids),
        processed=0,
        succeeded=0,
        failed=0,
        payload_json=json.dumps({"confirmation_ids": confirmation_ids, "user_id": user_id}, ensure_ascii=False),
        result_json=json.dumps([], ensure_ascii=False),
        created_at=now,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def schedule_bulk_confirmation_job(job_id: str) -> None:
    asyncio.create_task(process_bulk_confirmation_job(job_id))


async def process_bulk_confirmation_job(job_id: str) -> None:
    with SessionLocal() as db:
        await process_bulk_confirmation_job_in_session(db, job_id)


async def process_bulk_confirmation_job_in_session(db: Session, job_id: str) -> None:
    job = db.get(BackgroundJob, job_id)
    if not job:
        return
    payload = _loads(job.payload_json, {})
    confirmation_ids = [str(item) for item in payload.get("confirmation_ids", [])]
    user_id = str(payload.get("user_id") or "")
    _mark_running(db, job)
    results: list[dict[str, str | None]] = []

    for confirmation_id in confirmation_ids:
        result = await _process_confirmation(db, confirmation_id, user_id=user_id)
        results.append(result)
        job = db.get(BackgroundJob, job_id)
        if not job:
            return
        job.processed = len(results)
        job.succeeded = sum(1 for item in results if item.get("status") == "confirmed")
        job.failed = len(results) - job.succeeded
        job.result_json = json.dumps(results, ensure_ascii=False)
        db.add(job)
        db.commit()

    job = db.get(BackgroundJob, job_id)
    if not job:
        return
    job.completed_at = utc_now_iso()
    if job.succeeded == job.total:
        job.status = "completed"
    elif job.succeeded == 0:
        job.status = "failed"
    else:
        job.status = "partial"
    db.add(job)
    db.commit()


def get_job(db: Session, job_id: str) -> BackgroundJob | None:
    return db.get(BackgroundJob, job_id)


def list_jobs(db: Session, *, kind: str | None = None, status: str | None = None, limit: int = 20) -> list[BackgroundJob]:
    statement = select(BackgroundJob)
    if kind:
        statement = statement.where(BackgroundJob.kind == kind)
    if status:
        statement = statement.where(BackgroundJob.status == status)
    statement = statement.order_by(desc(BackgroundJob.created_at)).limit(limit)
    return list(db.scalars(statement).all())


def serialize_job(job: BackgroundJob) -> BackgroundJobRead:
    return BackgroundJobRead(
        id=job.id,
        kind=job.kind,
        status=job.status,
        total=job.total,
        processed=job.processed,
        succeeded=job.succeeded,
        failed=job.failed,
        results=[
            BackgroundJobResultItem(
                id=str(item.get("id")),
                status=str(item.get("status")),
                message=item.get("message"),
                error=item.get("error"),
            )
            for item in _loads(job.result_json, [])
            if isinstance(item, dict)
        ],
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error=job.error,
    )


def _mark_running(db: Session, job: BackgroundJob) -> None:
    now = utc_now_iso()
    job.status = "running"
    job.started_at = now
    db.add(job)
    db.commit()


async def _process_confirmation(db: Session, confirmation_id: str, *, user_id: str) -> dict[str, str | None]:
    if not user_id:
        return {"id": confirmation_id, "status": "failed", "error": "El job no tiene propietario.", "message": None}
    confirmation = confirmation_service.get_pending(db, confirmation_id, user_id=user_id)
    if not confirmation:
        return {"id": confirmation_id, "status": "failed", "error": "La confirmación no está pendiente o ya venció.", "message": None}
    try:
        message = await _execute_confirmation_action(db, confirmation)
        return {"id": confirmation_id, "status": "confirmed", "message": message, "error": None}
    except Exception as exc:  # noqa: BLE001 - bulk jobs must continue with remaining confirmations.
        db.rollback()
        current = db.get(PendingConfirmation, confirmation_id)
        if current and current.status == "pending":
            confirmation_service.fail(db, current, _sanitize_error(str(exc)))
        return {"id": confirmation_id, "status": "failed", "error": _sanitize_error(str(exc)), "message": None}


async def _execute_confirmation_action(db: Session, confirmation: PendingConfirmation) -> str:
    if confirmation.action_type == "apply_sort_order":
        planning_service.apply_sort(db, confirmation.id)
        return "Orden aplicado."
    if confirmation.action_type == task_suggestions.APPLY_SUGGESTIONS_ACTION:
        return task_suggestions.apply_suggestion_confirmation(db, confirmation)
    if confirmation.action_type in trello_actions.TRELLO_ACTION_TYPES:
        return await trello_actions.execute_confirmation(db, confirmation)
    raise ValueError("Confirmation action is not executable from API")


def _sanitize_error(message: str) -> str:
    sanitized = message
    for secret in (settings.trello_api_key, settings.trello_token, settings.telegram_bot_token):
        if secret:
            sanitized = sanitized.replace(secret, "[secret]")
    return sanitized


def _loads(raw: str | None, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback
