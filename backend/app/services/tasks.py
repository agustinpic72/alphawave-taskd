import json
import hashlib
from uuid import uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.tasks import DeletedExternalRef, Task, TaskEvent
from app.schemas.tasks import BulkTaskCreate, TaskCreate, TaskUpdate
from app.services import settings_service
from app.services import auth as auth_service
from app.services.classification import classify_task_title
from app.services.deadlines import parse_clear_deadline
from app.services.time import utc_now_iso


ACTIVE_STATUSES = ("active",)


def task_version_token(task: Task) -> str:
    state = {
        field: getattr(task, field)
        for field in (
            "title",
            "status",
            "scope",
            "priority_label",
            "due_at",
            "impact_score",
            "urgency_score",
            "blocking_score",
            "effort_bucket",
            "estimated_minutes",
            "context_bucket",
            "metadata_json",
            "updated_at",
        )
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _next_order(db: Session, user_id: str | None = None) -> int:
    statement = select(func.max(Task.manual_order))
    if user_id:
        statement = statement.where(Task.user_id == user_id)
    current = db.scalar(statement)
    return int(current or 0) + 1


def _event(db: Session, task_id: str | None, event_type: str, payload: dict | None = None, *, user_id: str | None = None) -> None:
    if user_id is None and task_id:
        task = db.get(Task, task_id)
        user_id = task.user_id if task else None
    db.add(
        TaskEvent(
            id=str(uuid4()),
            user_id=user_id,
            task_id=task_id,
            event_type=event_type,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
            created_at=utc_now_iso(),
        )
    )


def _base_query(status: str = "active", user_id: str | None = None) -> Select[tuple[Task]]:
    statement = select(Task).where(Task.status == status)
    if user_id:
        statement = statement.where(Task.user_id == user_id)
    return statement.order_by(Task.manual_order.asc(), Task.created_at.asc())


def list_tasks(db: Session, status: str = "active", q: str | None = None, scope: str | None = None, *, user_id: str | None = None) -> list[Task]:
    statement = _base_query(status, user_id=user_id)
    if q:
        statement = statement.where(Task.title.ilike(f"%{q}%"))
    if scope:
        statement = statement.where(Task.scope == scope)
    return list(db.scalars(statement).all())


def get_task(db: Session, task_id: str, *, user_id: str | None = None) -> Task | None:
    task = db.get(Task, task_id)
    if user_id and (not task or task.user_id != user_id):
        return None
    return task


def create_task(db: Session, payload: TaskCreate, *, user_id: str | None = None) -> Task:
    user_id = user_id or auth_service.single_owner_user_id(db)
    title = payload.title.strip()
    title, parsed_due = parse_clear_deadline(title)
    now = utc_now_iso()
    classification = classify_task_title(
        title,
        db=db,
        user_id=user_id,
        auto_classify=payload.auto_classify,
        llm_enabled=settings_service.llm_enabled(db, user_id=user_id),
        **settings_service.classification_context(db, user_id=user_id),
    )
    scope = payload.scope
    if payload.auto_classify and scope == "Inbox":
        title = classification.normalized_title
        scope = classification.scope

    task = Task(
        id=str(uuid4()),
        user_id=user_id,
        title=title,
        status="active",
        task_kind="normal",
        source_type="local",
        scope=scope,
        manual_order=_next_order(db, user_id=user_id),
        priority_label=payload.priority_label,
        impact_score=payload.impact_score,
        urgency_score=payload.urgency_score,
        blocking_score=payload.blocking_score,
        effort_bucket=payload.effort_bucket or _none_if_unknown(classification.effort_bucket),
        estimated_minutes=payload.estimated_minutes or classification.estimated_minutes,
        context_bucket=payload.context_bucket or _none_if_unknown(classification.context_bucket),
        due_at=payload.due_at or parsed_due,
        first_seen_at=now,
        last_touched_at=now,
        created_at=now,
        updated_at=now,
        metadata_json=json.dumps({"classification": classification.model_dump()}, ensure_ascii=False),
    )
    db.add(task)
    _event(db, task.id, "created", {"title": task.title, "scope": task.scope}, user_id=user_id)
    db.commit()
    db.refresh(task)
    return task


def bulk_create_tasks(db: Session, payload: BulkTaskCreate, *, user_id: str | None = None) -> list[Task]:
    lines = [line.strip().removeprefix("-").strip() for line in payload.text.splitlines()]
    titles = [line for line in lines if line]
    created: list[Task] = []
    for title in titles:
        created.append(create_task(db, TaskCreate(title=title, auto_classify=payload.auto_classify), user_id=user_id))
    return created


def update_task(db: Session, task: Task, payload: TaskUpdate, *, preserve_trello_manual_fields: bool = False) -> Task:
    changes = payload.model_dump(exclude_unset=True)
    old_title = task.title
    old_scope = task.scope
    notes_changed = "notes" in changes
    if notes_changed:
        metadata = _metadata(task.metadata_json)
        metadata["notes"] = changes.pop("notes") or ""
        task.metadata_json = json.dumps(metadata, ensure_ascii=False)
    if (
        preserve_trello_manual_fields
        and task.source_type == "trello"
        and changes.get("priority_label") is not None
    ):
        metadata = _metadata(task.metadata_json)
        overrides = set(metadata.get("manual_detail_overrides") or [])
        overrides.add("priority_label")
        metadata["manual_detail_overrides"] = sorted(overrides)
        task.metadata_json = json.dumps(metadata, ensure_ascii=False)
    for field, value in changes.items():
        setattr(task, field, value)
    task.updated_at = utc_now_iso()
    task.last_touched_at = task.updated_at
    if "title" in changes and changes["title"] != old_title:
        _event(db, task.id, "renamed", {"from": old_title, "to": changes["title"]}, user_id=task.user_id)
    for field in (
        "scope",
        "effort_bucket",
        "estimated_minutes",
        "context_bucket",
        "impact_score",
        "urgency_score",
        "blocking_score",
    ):
        if field in changes:
            _event(db, task.id, "corrected", {"field": field, "value": changes[field]}, user_id=task.user_id)
    if old_scope == "Inbox" and changes.get("scope") and changes["scope"] != "Inbox":
        _event(db, task.id, "inbox_processed", {"from": old_scope, "to": changes["scope"]}, user_id=task.user_id)
    if "due_at" in changes:
        _event(db, task.id, "deadline_updated", {"due_at": changes["due_at"]}, user_id=task.user_id)
    if "snoozed_until" in changes:
        _event(db, task.id, "snoozed", {"snoozed_until": changes["snoozed_until"]}, user_id=task.user_id)
    if notes_changed:
        _event(db, task.id, "corrected", {"field": "notes", "value": _metadata(task.metadata_json).get("notes", "")}, user_id=task.user_id)
    db.commit()
    db.refresh(task)
    return task


def _metadata(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def snooze_task(db: Session, task: Task, snoozed_until: str, *, reason: str | None = None) -> Task:
    now = utc_now_iso()
    task.snoozed_until = snoozed_until
    task.updated_at = now
    task.last_touched_at = now
    payload = {"snoozed_until": snoozed_until}
    if reason:
        payload["reason"] = reason
    _event(db, task.id, "snoozed", payload, user_id=task.user_id)
    db.commit()
    db.refresh(task)
    return task


def complete_task(db: Session, task: Task) -> Task:
    now = utc_now_iso()
    task.status = "completed"
    task.completed_at = now
    task.updated_at = now
    task.last_touched_at = now
    _event(db, task.id, "completed", user_id=task.user_id)
    db.commit()
    db.refresh(task)
    return task


def is_trello_linked_task(task: Task) -> bool:
    return task.source_type == "trello" and bool(str(task.source_id or "").strip())


def soft_delete_task(db: Session, task: Task) -> Task:
    now = utc_now_iso()
    task.status = "deleted"
    task.deleted_at = now
    task.updated_at = now
    task.last_touched_at = now
    _event(db, task.id, "deleted", user_id=task.user_id)
    db.commit()
    db.refresh(task)
    return task


def restore_task(db: Session, task: Task) -> Task:
    now = utc_now_iso()
    task.status = "active"
    task.deleted_at = None
    task.completed_at = None
    task.updated_at = now
    task.last_touched_at = now
    task.manual_order = _next_order(db, user_id=task.user_id)
    _event(db, task.id, "restored", user_id=task.user_id)
    db.commit()
    db.refresh(task)
    return task


def restore_tasks(db: Session, task_ids: list[str], *, user_id: str | None = None) -> int:
    tasks = _tasks_for_ids(db, task_ids, user_id=user_id)
    _ensure_all_found(task_ids, tasks)
    _ensure_status(tasks, "deleted", "Sólo se pueden restaurar tareas que están en Papelera.")
    now = utc_now_iso()
    next_order = _next_order(db, user_id=user_id)
    for index, task in enumerate(tasks):
        task.status = "active"
        task.deleted_at = None
        task.completed_at = None
        task.updated_at = now
        task.last_touched_at = now
        task.manual_order = next_order + index
        _event(db, task.id, "restored", user_id=task.user_id)
    db.commit()
    return len(tasks)


def permanent_delete_task(db: Session, task: Task) -> int:
    if task.status != "deleted":
        raise ValueError("Sólo se pueden eliminar definitivamente tareas que están en Papelera.")
    _permanent_delete(db, task)
    db.commit()
    return 1


def permanent_delete_tasks(db: Session, task_ids: list[str], *, user_id: str | None = None) -> int:
    tasks = _tasks_for_ids(db, task_ids, user_id=user_id)
    _ensure_all_found(task_ids, tasks)
    _ensure_status(tasks, "deleted", "Sólo se pueden eliminar definitivamente tareas que están en Papelera.")
    for task in tasks:
        _permanent_delete(db, task)
    db.commit()
    return len(tasks)


def empty_trash(db: Session, *, user_id: str | None = None) -> int:
    tasks = list_tasks(db, status="deleted", user_id=user_id)
    for task in tasks:
        _permanent_delete(db, task)
    db.commit()
    return len(tasks)


def reorder_tasks(db: Session, task_ids: list[str], *, user_id: str | None = None) -> list[Task]:
    tasks_by_id = {task.id: task for task in list_tasks(db, user_id=user_id)}
    for index, task_id in enumerate(task_ids, start=1):
        task = tasks_by_id.get(task_id)
        if task:
            task.manual_order = index
            task.updated_at = utc_now_iso()
    _event(db, None, "reordered", {"task_ids": task_ids}, user_id=user_id)
    db.commit()
    return list_tasks(db, user_id=user_id)


def _tasks_for_ids(db: Session, task_ids: list[str], *, user_id: str | None = None) -> list[Task]:
    unique_ids = list(dict.fromkeys(task_ids))
    if not unique_ids:
        return []
    statement = select(Task).where(Task.id.in_(unique_ids))
    if user_id:
        statement = statement.where(Task.user_id == user_id)
    tasks = list(db.scalars(statement).all())
    by_id = {task.id: task for task in tasks}
    return [by_id[task_id] for task_id in unique_ids if task_id in by_id]


def _ensure_all_found(task_ids: list[str], tasks: list[Task]) -> None:
    found = {task.id for task in tasks}
    missing = [task_id for task_id in task_ids if task_id not in found]
    if missing:
        raise ValueError("Una o más tareas no existen.")


def _ensure_status(tasks: list[Task], status: str, message: str) -> None:
    if any(task.status != status for task in tasks):
        raise ValueError(message)


def _permanent_delete(db: Session, task: Task) -> None:
    if task.source_type == "trello" and task.source_id:
        _tombstone_external_ref(db, task.source_type, task.source_id)
    _event(
        db,
        task.id,
        "permanently_deleted",
        {"title": task.title, "source_type": task.source_type, "source_id": task.source_id},
        user_id=task.user_id,
    )
    db.delete(task)


def _tombstone_external_ref(db: Session, source_type: str, source_id: str) -> None:
    existing = db.scalar(
        select(DeletedExternalRef).where(
            DeletedExternalRef.source_type == source_type,
            DeletedExternalRef.source_id == source_id,
        )
    )
    if existing:
        existing.deleted_at = utc_now_iso()
        existing.reason = "permanent_delete_local"
        return
    db.add(
        DeletedExternalRef(
            id=str(uuid4()),
            source_type=source_type,
            source_id=source_id,
            reason="permanent_delete_local",
            deleted_at=utc_now_iso(),
        )
    )


def _none_if_unknown(value: str | None) -> str | None:
    return None if value in (None, "unknown") else value
