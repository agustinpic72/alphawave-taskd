import json
import logging
from datetime import date, datetime, time, timedelta
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.briefing import BriefingRun
from app.models.auth import User
from app.models.tasks import Task, TaskEvent
from app.models.telegram import TelegramSnapshot
from app.schemas.briefing import BriefingPayload, BriefingStatus
from app.services import confirmations
from app.services import auth as auth_service
from app.services import integrations
from app.services import settings_service
from app.services.classification import classify_task_title
from app.services.tasks import list_tasks
from app.services.time import utc_now_iso


class BriefingMessenger(Protocol):
    async def send_message(self, chat_id: str, text: str) -> int | None: ...


logger = logging.getLogger(__name__)

GROUP_ORDER = [
    ("deep_work", "Trabajo profundo"),
    ("quick_task", "Tareas rápidas"),
    ("review_followup", "Revisión / seguimiento"),
    ("calls_contact", "Llamadas / contacto"),
    ("errands_personal", "Errands / personal"),
    ("requires_attention", "Requiere atención"),
    ("inbox_classification", "Inbox para clasificar"),
    ("stale_in_progress", "EN PROCESO estancadas"),
    ("perpetual_due", "Perpetuas vencidas"),
    ("pending_confirmations", "Confirmaciones pendientes"),
]

SECTION_LABELS = {
    "deep_work": "🧠 Trabajo profundo",
    "quick_task": "⚡ Tareas rápidas",
    "review_followup": "🔎 Revisión / seguimiento",
    "calls_contact": "📞 Llamadas / contacto",
    "errands_personal": "🏃 Errands / personal",
    "requires_attention": "👀 Requiere atención",
    "inbox_classification": "📥 Inbox para clasificar",
    "stale_in_progress": "⏳ EN PROCESO estancadas",
    "perpetual_due": "♾️ Perpetuas vencidas",
    "pending_confirmations": "✅ Confirmaciones pendientes",
}

MONTHS_SHORT = {
    1: "ene",
    2: "feb",
    3: "mar",
    4: "abr",
    5: "may",
    6: "jun",
    7: "jul",
    8: "ago",
    9: "sep",
    10: "oct",
    11: "nov",
    12: "dic",
}

TRELLO_STATE_LABELS = {
    "pending": "pendiente",
    "in_progress": "en proceso",
    "review": "en revisión",
    "perpetual": "perpetua",
    "completed": "completada",
    "ignored": "ignorada",
}

PRIORITY_LABELS = {
    "high": "alta",
    "medium_high": "media-alta",
    "medium": "media",
    "low": "baja",
}


def status(db: Session, *, now: datetime | None = None, user_id: str | None = None) -> BriefingStatus:
    user_id, _legacy_scope = _resolve_user_scope(db, user_id)
    briefing_config = settings_service.briefing_settings(db, user_id=user_id)
    local = _configured_local_now(now, briefing_config.get("timezone"))
    today = local.date().isoformat()
    return BriefingStatus(
        enabled=briefing_config["enabled"],
        timezone=briefing_config["timezone"],
        time=briefing_config["time"],
        late_cutoff=briefing_config["late_cutoff"],
        today_sent=_sent_today(db, today, user_id=user_id),
        last_run=_last_run(db, user_id=user_id),
    )


def generate_payload(
    db: Session,
    *,
    briefing_date: str | None = None,
    now: datetime | None = None,
    apply_weekend: bool = False,
    user_id: str | None = None,
) -> BriefingPayload:
    user_id, legacy_scope = _resolve_user_scope(db, user_id)
    briefing_config = settings_service.briefing_settings(db, user_id=user_id)
    local = _configured_local_now(now, briefing_config.get("timezone"))
    target_date = briefing_date or local.date().isoformat()
    groups: dict[str, list[dict]] = {key: [] for key, _ in GROUP_ORDER}
    visible_ids: list[str] = []
    perpetual_ids: list[str] = []

    for task in _eligible_tasks(db, local, user_id=user_id, include_legacy_unowned=legacy_scope):
        if apply_weekend and not settings_service.weekend_scope_allowed(db, task.scope, now=local, user_id=user_id):
            continue
        item = _task_item(task, local)
        if task.task_kind == "attention":
            if briefing_config.get("include_attention", True):
                _append_limited(groups, "requires_attention", item, briefing_config["max_attention"])
        elif task.scope == "Inbox":
            if not briefing_config.get("include_inbox", True):
                continue
            suggestion = classify_task_title(
                task.title,
                db=db,
                user_id=user_id,
                auto_classify=True,
                llm_enabled=settings_service.llm_enabled(db, user_id=user_id),
                **settings_service.classification_context(db, user_id=user_id),
            )
            item["suggested_scope"] = suggestion.scope
            item["reason"] = suggestion.reason or "clasificación local"
            _append_limited(groups, "inbox_classification", item, briefing_config["max_inbox"])
        elif task.trello_state == "in_progress" and _is_stale_in_progress(task, local):
            if not briefing_config.get("include_stale_in_progress", True):
                continue
            if apply_weekend and not settings_service.weekend_notification_enabled(db, "stale_in_progress", now=local, user_id=user_id):
                continue
            item["reason"] = _stale_reason(task, local)
            _append_limited(groups, "stale_in_progress", item, briefing_config["max_reviews"])
        elif task.trello_state == "in_progress":
            continue
        elif task.task_kind == "perpetual":
            if not briefing_config.get("include_perpetuals", True):
                continue
            if apply_weekend and not settings_service.weekend_notification_enabled(db, "perpetual_checkins", now=local, user_id=user_id):
                continue
            if _perpetual_due(task, local) and len(groups["perpetual_due"]) < min(briefing_config["max_perpetuals"], settings.perpetual_max_per_day):
                item["reason"] = _perpetual_reason(task, local)
                groups["perpetual_due"].append(item)
                perpetual_ids.append(task.id)
        elif task.context_bucket == "deep_work" or task.effort_bucket == "deep":
            _append_limited(groups, "deep_work", item, briefing_config["max_deep_work"])
        elif task.context_bucket == "call":
            _append_limited(groups, "calls_contact", item, briefing_config["max_reviews"])
        elif task.context_bucket == "review" or task.trello_state in ("review", "in_progress"):
            _append_limited(groups, "review_followup", item, briefing_config["max_reviews"])
        elif task.scope == "Personal" or task.context_bucket == "errand":
            _append_limited(groups, "errands_personal", item, briefing_config["max_reviews"])
        else:
            _append_limited(groups, "quick_task", item, briefing_config["max_quick_tasks"])

    if briefing_config.get("include_confirmations", True):
        pending = confirmations.list_pending(db, user_id=user_id)
        for confirmation in pending:
            groups["pending_confirmations"].append(
                {
                    "id": confirmation.id,
                    "title": confirmation.summary or confirmation.action_type,
                    "scope": "confirmación",
                    "expires_at": confirmation.expires_at,
                }
            )

    text, visible_ids = _format_payload(target_date, groups, briefing_time=briefing_config["time"])
    return BriefingPayload(briefing_date=target_date, text=text, groups={key: value for key, value in groups.items() if value})


async def send_briefing(
    db: Session,
    messenger: BriefingMessenger,
    *,
    chat_id: str | None = None,
    reason: str = "auto",
    now: datetime | None = None,
    force: bool = False,
    user_id: str | None = None,
) -> BriefingRun:
    user_id, legacy_scope = _resolve_user_scope(db, user_id)
    briefing_config = settings_service.briefing_settings(db, user_id=user_id)
    local = _configured_local_now(now, briefing_config.get("timezone"))
    briefing_date = local.date().isoformat()
    if reason == "auto" and not force and _sent_today(db, briefing_date, user_id=user_id):
        return _record_run(
            db,
            briefing_date,
            "skipped",
            reason="already_sent",
            payload=None,
            scheduled_for=_scheduled_for(local.date(), briefing_config),
            user_id=user_id,
        )

    payload = generate_payload(
        db,
        briefing_date=briefing_date,
        now=local,
        apply_weekend=reason == "auto",
        user_id=None if legacy_scope else user_id,
    )
    run = _record_run(
        db,
        briefing_date,
        "pending",
        reason=reason,
        payload=payload,
        scheduled_for=_scheduled_for(local.date(), briefing_config),
        user_id=user_id,
    )
    try:
        target_chat = chat_id or _telegram_destination(db, user_id)
        if not target_chat:
            raise ValueError("No hay chat Telegram configurado para enviar briefing.")
        message_id = await messenger.send_message(target_chat, payload.text)
        run.status = "sent"
        run.sent_at = utc_now_iso()
        run.updated_at = utc_now_iso()
        db.add(run)
        _save_snapshots(db, target_chat, message_id, payload)
        _event(db, None, "briefing_sent", {"briefing_date": briefing_date, "reason": reason}, user_id=user_id)
        db.commit()
        return run
    except Exception as exc:  # noqa: BLE001 - persist failures.
        run.status = "failed"
        run.reason = reason
        run.updated_at = utc_now_iso()
        db.add(run)
        _event(db, None, "briefing_failed", {"briefing_date": briefing_date, "error": str(exc)}, user_id=user_id)
        db.commit()
        raise


async def maybe_send_due_briefing(
    db: Session,
    messenger: BriefingMessenger,
    *,
    now: datetime | None = None,
    startup: bool = False,
    user_id: str | None = None,
) -> BriefingRun | None:
    user_id, legacy_scope = _resolve_user_scope(db, user_id)
    briefing_config = settings_service.briefing_settings(db, user_id=user_id)
    local = _configured_local_now(now, briefing_config.get("timezone"))
    if not briefing_config["enabled"]:
        return None
    if startup and not briefing_config["send_on_startup"]:
        return None
    weekend_key = "briefing_late_startup" if startup else "briefing_auto"
    if not settings_service.should_send_weekend_category(db, weekend_key, now=local, user_id=user_id):
        logger.info("Briefing skipped: weekend_mode_active")
        return None
    if _sent_today(db, local.date().isoformat(), user_id=user_id):
        return None
    if local.time() < _parse_time(briefing_config["time"]):
        return None
    if local.time() >= _parse_time(briefing_config["late_cutoff"]):
        if _skipped_today(db, local.date().isoformat(), "late_cutoff", user_id=user_id):
            return None
        return _record_run(
            db,
            local.date().isoformat(),
            "skipped",
            reason="late_cutoff",
            payload=None,
            scheduled_for=_scheduled_for(local.date(), briefing_config),
            user_id=user_id,
        )
    return await send_briefing(
        db,
        messenger,
        reason="auto",
        now=local,
        user_id=None if legacy_scope else user_id,
    )


def list_runs(db: Session, limit: int = 20, *, user_id: str | None = None) -> list[BriefingRun]:
    user_id, legacy_scope = _resolve_user_scope(db, user_id)
    owner_filter = BriefingRun.user_id == user_id
    if legacy_scope:
        owner_filter = or_(owner_filter, BriefingRun.user_id.is_(None), BriefingRun.user_id == "")
    statement = select(BriefingRun).where(owner_filter).order_by(desc(BriefingRun.created_at)).limit(limit)
    return list(db.scalars(statement).all())


def update_perpetual_checkin(db: Session, chat_id: str, *, days: int, note: str, user_id: str | None = None) -> Task:
    user_id, legacy_scope = _resolve_user_scope(db, user_id)
    snapshot = db.scalar(
        select(TelegramSnapshot)
        .where(TelegramSnapshot.chat_id == chat_id, TelegramSnapshot.snapshot_type == "perpetuals")
        .order_by(desc(TelegramSnapshot.created_at))
        .limit(1)
    )
    if not snapshot:
        raise ValueError("No tengo un check-in perpetuo reciente. Pedime briefing primero.")
    task_ids = json.loads(snapshot.task_ids_json)
    if len(task_ids) != 1:
        raise ValueError("Hay más de una perpetua reciente. Decime cuál querés actualizar.")
    owner_filter = Task.user_id == user_id
    if legacy_scope or _single_user_matches(db, user_id):
        owner_filter = or_(owner_filter, Task.user_id.is_(None), Task.user_id == "")
    task = db.scalar(select(Task).where(Task.id == task_ids[0], owner_filter))
    if not task:
        raise ValueError("Esa tarea ya no existe.")
    local = _local_now(None)
    task.last_checkin_at = local.isoformat()
    task.next_checkin_at = (local + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    metadata = _metadata(task.metadata_json)
    metadata["last_user_update"] = note
    task.metadata_json = json.dumps(metadata, ensure_ascii=False)
    task.updated_at = utc_now_iso()
    _event(db, task.id, "perpetual_checkin_updated", {"days": days, "note": note})
    db.commit()
    db.refresh(task)
    return task


def _eligible_tasks(
    db: Session,
    local: datetime,
    *,
    user_id: str,
    include_legacy_unowned: bool = False,
) -> list[Task]:
    tasks = []
    if include_legacy_unowned:
        statement = (
            select(Task)
            .where(
                Task.status == "active",
                or_(Task.user_id == user_id, Task.user_id.is_(None), Task.user_id == ""),
            )
            .order_by(Task.manual_order.asc(), Task.created_at.asc())
        )
        candidates = list(db.scalars(statement).all())
    else:
        candidates = list_tasks(db, user_id=user_id)
    for task in candidates:
        if task.status != "active":
            continue
        if task.trello_state in ("completed", "ignored"):
            continue
        if task.snoozed_until and task.snoozed_until > local.isoformat():
            continue
        tasks.append(task)
    return tasks


def _task_item(task: Task, local: datetime) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "scope": task.scope,
        "due_at": task.due_at,
        "priority_label": task.priority_label,
        "trello_state": task.trello_state,
        "reason": _default_reason(task),
    }


def _format_payload(briefing_date: str, groups: dict[str, list[dict]], *, briefing_time: str) -> tuple[str, list[str]]:
    reference_date = date.fromisoformat(briefing_date)
    lines = [f"🌅 Briefing diario · {briefing_time}", ""]
    counter = 1
    visible_ids: list[str] = []
    for key, title in GROUP_ORDER:
        items = groups.get(key) or []
        if not items:
            continue
        lines.append(SECTION_LABELS.get(key, title))
        for item in items:
            if "id" in item and key != "pending_confirmations":
                visible_ids.append(item["id"])
            prefix = f"{counter}. " if key != "pending_confirmations" else "- "
            lines.append(f"{prefix}{item['title']}")
            if key == "inbox_classification":
                lines.append(f"   Sugerencia: {item['suggested_scope']}")
                lines.append(f"   Confirmá: \"la {counter} es {item['suggested_scope']}\"")
            elif key == "pending_confirmations":
                lines.append(f"  vence {_format_datetime_short(item['expires_at'], reference_date)}")
            else:
                metadata = _format_item_metadata(item, reference_date)
                if metadata:
                    lines.append(f"   {metadata}")
                if item.get("reason"):
                    lines.append(f"   Motivo: {item['reason']}")
            if key != "pending_confirmations":
                counter += 1
        lines.append("")
    if counter == 1 and not groups.get("pending_confirmations"):
        lines.append("No hay tareas activas para destacar.")
    return "\n".join(lines).strip(), visible_ids


def _format_item_metadata(item: dict, reference_date: date) -> str:
    parts = [value for value in (item.get("scope"), _format_trello_state(item.get("trello_state"))) if value]
    if item.get("due_at"):
        parts.append(_format_due_short(item["due_at"], reference_date))
    priority = _format_priority_label(item.get("priority_label"))
    if priority:
        parts.append(priority)
    return " · ".join(parts)


def _format_due_short(value: str, reference_date: date) -> str:
    due = datetime.fromisoformat(value)
    due_date = due.date()
    if due_date == reference_date:
        return "vence hoy"
    if due_date == reference_date + timedelta(days=1):
        return "vence mañana"
    if due_date < reference_date:
        return f"vencida desde {_format_day_month(due_date)}"
    return f"vence {_format_day_month(due_date)}"


def _format_datetime_short(value: str, reference_date: date) -> str:
    moment = datetime.fromisoformat(value)
    day = moment.date()
    if day == reference_date:
        label = "hoy"
    elif day == reference_date + timedelta(days=1):
        label = "mañana"
    else:
        label = _format_day_month(day)
    return f"{label} {moment:%H:%M}"


def _format_day_month(value: date) -> str:
    return f"{value.day:02d} {MONTHS_SHORT[value.month]}"


def _format_trello_state(value: str | None) -> str | None:
    if not value:
        return None
    return TRELLO_STATE_LABELS.get(value, value.replace("_", " "))


def _format_priority_label(value: str | None) -> str | None:
    if not value:
        return None
    return PRIORITY_LABELS.get(value)


def _save_snapshots(db: Session, chat_id: str, message_id: int | None, payload: BriefingPayload) -> None:
    visible_ids = []
    perpetual_ids = []
    for key, items in payload.groups.items():
        for item in items:
            task_id = item.get("id")
            if task_id and key != "pending_confirmations":
                visible_ids.append(task_id)
            if task_id and key == "perpetual_due":
                perpetual_ids.append(task_id)
    for snapshot_type, ids in (("tasks", visible_ids), ("perpetuals", perpetual_ids)):
        db.add(
            TelegramSnapshot(
                id=str(uuid4()),
                chat_id=chat_id,
                message_id=message_id,
                snapshot_type=snapshot_type,
                task_ids_json=json.dumps(ids),
                created_at=utc_now_iso(),
            )
        )


def _sent_today(db: Session, briefing_date: str, *, user_id: str) -> bool:
    return bool(
        db.scalar(
            select(BriefingRun)
            .where(
                BriefingRun.user_id == user_id,
                BriefingRun.briefing_date == briefing_date,
                BriefingRun.status == "sent",
                BriefingRun.reason == "auto",
            )
            .limit(1)
        )
    )


def _skipped_today(db: Session, briefing_date: str, reason: str, *, user_id: str) -> bool:
    return bool(
        db.scalar(
            select(BriefingRun)
            .where(
                BriefingRun.user_id == user_id,
                BriefingRun.briefing_date == briefing_date,
                BriefingRun.status == "skipped",
                BriefingRun.reason == reason,
            )
            .limit(1)
        )
    )


def _last_run(db: Session, *, user_id: str) -> BriefingRun | None:
    return db.scalar(
        select(BriefingRun)
        .where(BriefingRun.user_id == user_id)
        .order_by(desc(BriefingRun.created_at))
        .limit(1)
    )


def _record_run(
    db: Session,
    briefing_date: str,
    status: str,
    *,
    reason: str,
    payload: BriefingPayload | None,
    scheduled_for: str,
    user_id: str,
) -> BriefingRun:
    now = utc_now_iso()
    run = BriefingRun(
        id=str(uuid4()),
        user_id=user_id,
        briefing_date=briefing_date,
        scheduled_for=scheduled_for,
        sent_at=None,
        status=status,
        reason=reason,
        channel="telegram",
        payload_json=payload.model_dump_json() if payload else None,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    event_name = {
        "pending": "briefing_generated",
        "sent": "briefing_sent",
        "skipped": "briefing_skipped",
        "failed": "briefing_failed",
    }.get(status, f"briefing_{status}")
    _event(db, None, event_name, {"briefing_date": briefing_date, "reason": reason}, user_id=user_id)
    db.commit()
    db.refresh(run)
    return run


def _is_stale_in_progress(task: Task, local: datetime) -> bool:
    if not settings.checkins_enabled or task.trello_state != "in_progress":
        return False
    moved = _reference_time(task)
    return bool(moved and local - moved.astimezone(local.tzinfo) > timedelta(days=settings.in_progress_stale_days))


def _perpetual_due(task: Task, local: datetime) -> bool:
    if task.task_kind != "perpetual" or not task.next_checkin_at:
        return False
    return datetime.fromisoformat(task.next_checkin_at).astimezone(local.tzinfo) <= local


def _reference_time(task: Task) -> datetime | None:
    for value in (task.last_trello_activity_at, task.last_touched_at, task.updated_at, task.first_seen_at):
        if value:
            return datetime.fromisoformat(value)
    return None


def _stale_reason(task: Task, local: datetime) -> str:
    reference = _reference_time(task)
    days = (local - reference.astimezone(local.tzinfo)).days if reference else settings.in_progress_stale_days
    return f"está en EN PROCESO hace {days} días sin movimiento"


def _perpetual_reason(task: Task, local: datetime) -> str:
    if not task.last_checkin_at:
        return "requiere primer check-in"
    reference = datetime.fromisoformat(task.last_checkin_at).astimezone(local.tzinfo)
    return f"último update hace {(local - reference).days} días"


def _default_reason(task: Task) -> str:
    if task.due_at:
        return "tiene deadline visible"
    if task.task_kind == "attention":
        return "te mencionaron, pero no estás asignado"
    if task.effort_bucket == "deep":
        return "requiere foco"
    if task.effort_bucket == "quick":
        return "parece corta y cerrable"
    return "prioridad local"


def _append_limited(groups: dict[str, list[dict]], key: str, item: dict, limit: int) -> None:
    if len(groups[key]) < limit:
        groups[key].append(item)


def _local_now(now: datetime | None, timezone_name: str | None = None) -> datetime:
    timezone = ZoneInfo(timezone_name or settings.daily_briefing_timezone)
    if now is None:
        return datetime.now(timezone)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone)
    return now.astimezone(timezone)


def _configured_local_now(now: datetime | None, timezone_name: str | None = None) -> datetime:
    try:
        return _local_now(now, timezone_name)
    except TypeError:
        return _local_now(now)


def _parse_time(value: str) -> time:
    hour, minute = value.split(":", maxsplit=1)
    return time(hour=int(hour), minute=int(minute))


def _scheduled_for(day: date, briefing_config: dict | None = None) -> str:
    briefing_config = briefing_config or {"timezone": settings.daily_briefing_timezone, "time": settings.daily_briefing_time}
    timezone = ZoneInfo(briefing_config["timezone"])
    scheduled = datetime.combine(day, _parse_time(briefing_config["time"]), tzinfo=timezone)
    return scheduled.isoformat()


def _metadata(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _event(
    db: Session,
    task_id: str | None,
    event_type: str,
    payload: dict | None = None,
    *,
    user_id: str | None = None,
) -> None:
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


def _resolve_user_scope(db: Session, user_id: str | None) -> tuple[str, bool]:
    if user_id:
        return user_id, False
    user_ids = list(db.scalars(select(User.id).order_by(User.created_at.asc()).limit(2)).all())
    if len(user_ids) > 1:
        raise ValueError("Briefing requiere user_id cuando hay más de un usuario.")
    if user_ids:
        return user_ids[0], True
    return auth_service.single_owner_user_id(db), True


def _telegram_destination(db: Session, user_id: str) -> str | None:
    destination = integrations.get_telegram_destination_for_user(db, user_id)
    if not destination:
        return None
    integration_status = integrations.telegram_status_for_user(db, user_id)
    if integration_status.get("legacy_fallback"):
        user_count = db.scalar(select(func.count(User.id))) or 0
        if user_count != 1:
            return None
    return destination


def _single_user_matches(db: Session, user_id: str) -> bool:
    user_ids = list(db.scalars(select(User.id).limit(2)).all())
    return user_ids == [user_id]
