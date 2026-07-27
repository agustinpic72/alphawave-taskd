import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.confirmations import PendingConfirmation
from app.models.tasks import Task
from app.schemas.planning import InboxSuggestion, InboxSuggestions, NowPlan, PlanGroup, PlanItem, PlanRecommendation, PriorityExplanation, PriorityFactor, SortProposal, SortProposalItem, TodayPlan
from app.services import confirmations
from app.services import settings_service
from app.services.classification import classify_task_title
from app.services.tasks import list_tasks, reorder_tasks


GROUP_TITLES = {
    "overdue": "Vencidas",
    "today": "Para hoy",
    "blocking": "Bloquean avance",
    "quick_wins": "Quick wins",
    "deep_work": "Trabajo profundo",
    "review": "En revisión / seguimiento",
    "inbox": "Inbox por procesar",
    "attention": "Requiere atención",
    "perpetual": "Perpetuas / seguimiento",
}

SUPPORTED_PRIORITY_CRITERIA = tuple(settings_service.DEFAULT_PRIORITY_CRITERIA.keys())


def today_plan(db: Session, *, user_id: str | None = None) -> TodayPlan:
    priority_config = settings_service.priority_settings(db, user_id=user_id)
    groups: dict[str, list[PlanItem]] = {key: [] for key in GROUP_TITLES}
    for task in _eligible_tasks(db, user_id=user_id):
        key = _group_key(task)
        explanation = score_task(task, priority_config)
        groups[key].append(PlanItem(task=task, reason=explanation.summary or _reason(task, key), priority=explanation))
    plan_groups = [
        PlanGroup(
            key=key,
            title=title,
            items=sorted(items, key=lambda item: _plan_sort_key(item.task, item.priority), reverse=True),
            summary=f"{len(items)} tarea{'' if len(items) == 1 else 's'}",
        )
        for key, title in GROUP_TITLES.items()
        if (items := groups[key])
    ]
    recommendations = [
        PlanRecommendation(
            kind="top",
            task=item.task,
            title=item.task.title,
            reason=item.reason,
            priority=item.priority,
        )
        for item in _top_today_items(plan_groups, limit=3)
    ]
    return TodayPlan(
        groups=plan_groups,
        summary=_today_summary(plan_groups),
        recommendations=recommendations,
        warnings=_planning_warnings(plan_groups),
    )


def now_plan(db: Session, *, user_id: str | None = None) -> NowPlan:
    priority_config = settings_service.priority_settings(db, user_id=user_id)
    eligible = _eligible_tasks(db, user_id=user_id)
    active = [task for task in eligible if task.scope != "Inbox"]
    sorted_tasks = sorted(active, key=lambda task: _now_sort_key(task, priority_config), reverse=True)
    recommended = [_plan_item(task, priority_config, mode="now") for task in sorted_tasks[:1]]
    afterwards = [_plan_item(task, priority_config, mode="now") for task in sorted_tasks[1:3]]
    avoid_tasks = [task for task in list_tasks(db, user_id=user_id) if _is_snoozed(task) or task.scope == "Inbox"][:3]
    avoid = [PlanItem(task=task, reason="está snoozeada o necesita clasificación") for task in avoid_tasks]
    inbox_count = sum(1 for task in eligible if task.scope == "Inbox")
    alternatives = _now_alternatives(active, priority_config, inbox_count)
    return NowPlan(
        recommended=recommended,
        afterwards=afterwards,
        avoid=avoid,
        summary=_now_summary(recommended, alternatives, inbox_count),
        alternatives=alternatives,
        warnings=_now_warnings(active),
    )


def inbox_suggestions(db: Session, *, user_id: str | None = None) -> InboxSuggestions:
    suggestions: list[InboxSuggestion] = []
    for task in list_tasks(db, user_id=user_id):
        if task.scope != "Inbox":
            continue
        classification = classify_task_title(
            task.title,
            db=db,
            user_id=user_id,
            auto_classify=True,
            llm_enabled=settings_service.llm_enabled(db, user_id=user_id),
            **settings_service.classification_context(db, user_id=user_id),
        )
        suggestions.append(
            InboxSuggestion(
                task=task,
                suggested_scope=classification.scope,
                confidence=classification.scope_confidence,
                reason=classification.reason or "clasificación local",
            )
        )
    return InboxSuggestions(suggestions=suggestions)


def propose_sort(db: Session, *, source: str = "ui", chat_id: str | None = None, user_id: str | None = None) -> SortProposal:
    priority_config = settings_service.priority_settings(db, user_id=user_id)
    active_tasks = list_tasks(db, user_id=user_id)
    sortable = _sortable_tasks(active_tasks)
    tasks = sorted(sortable, key=lambda task: _score(task, priority_config), reverse=True)
    sorted_ids = {task.id for task in tasks}
    task_ids = [task.id for task in tasks] + [task.id for task in active_tasks if task.id not in sorted_ids]
    confirmation = confirmations.create_confirmation(
        db,
        source=source,
        chat_id=chat_id,
        action_type="apply_sort_order",
        payload={"task_ids": task_ids},
        user_id=user_id,
    )
    old_positions = {task.id: index for index, task in enumerate(active_tasks, start=1)}
    items = [
        SortProposalItem(
            task_id=task.id,
            old_index=old_positions.get(task.id, index),
            new_index=index,
            title=task.title,
            scope=task.scope,
            score=(explanation := score_task(task, priority_config)).score,
            reason=explanation.summary,
            priority=explanation,
        )
        for index, task in enumerate(tasks, start=1)
    ]
    changed_count = sum(1 for item in items if item.old_index != item.new_index)
    summary = (
        f"Moví {changed_count} tareas según prioridad calculada."
        if changed_count
        else "El orden actual ya coincide con la prioridad calculada."
    )
    return SortProposal(
        confirmation_id=confirmation.id,
        task_ids=task_ids,
        tasks=tasks,
        expires_at=confirmation.expires_at,
        items=items,
        summary=summary,
        requires_confirmation=True,
    )


def apply_sort(db: Session, confirmation_id: str, *, user_id: str | None = None):
    confirmation = db.get(PendingConfirmation, confirmation_id)
    if user_id and (not confirmation or confirmation.user_id != user_id):
        raise ValueError("No hay una confirmación pendiente para aplicar ese orden.")
    if not confirmation or confirmation.status != "pending":
        raise ValueError("No hay una confirmación pendiente para aplicar ese orden.")
    payload = json.loads(confirmation.payload_json)
    tasks = reorder_tasks(db, payload["task_ids"], user_id=user_id)
    confirmations.confirm(db, confirmation)
    return tasks


def _eligible_tasks(db: Session, *, user_id: str | None = None) -> list[Task]:
    return [task for task in list_tasks(db, user_id=user_id) if not _is_snoozed(task) and task.trello_state not in ("completed", "ignored")]


def _sortable_tasks(tasks: list[Task]) -> list[Task]:
    return [
        task
        for task in tasks
        if task.task_kind == "normal"
        and not _is_snoozed(task)
        and task.trello_state not in ("completed", "ignored")
    ]


def _today_summary(groups: list[PlanGroup]) -> str:
    if not groups:
        return "No hay tareas activas para planificar."
    parts = [f"{len(group.items)} {group.title.lower()}" for group in groups if group.key in {"overdue", "today", "quick_wins", "deep_work", "inbox"}]
    return " · ".join(parts) if parts else f"{sum(len(group.items) for group in groups)} tareas activas."


def _now_summary(recommended: list[PlanItem], alternatives: list[PlanRecommendation], inbox_count: int) -> str:
    if recommended:
        return f"Siguiente acción: {recommended[0].task.title}."
    if inbox_count:
        return f"No hay una tarea clara fuera de Inbox. Procesá Inbox: {inbox_count} pendiente{'' if inbox_count == 1 else 's'}."
    if alternatives:
        return alternatives[0].reason
    return "No hay tareas activas para planificar."


def _planning_warnings(groups: list[PlanGroup]) -> list[str]:
    items = [item for group in groups for item in group.items]
    missing = [item for item in items if item.priority and item.priority.warnings]
    return ["Faltan detalles para priorizar mejor."] if missing else []


def _now_warnings(tasks: list[Task]) -> list[str]:
    if not tasks:
        return []
    incomplete = [task for task in tasks if not task.priority_label and not task.impact_score and not task.urgency_score and not task.estimated_minutes]
    return ["Faltan detalles para priorizar mejor."] if len(incomplete) >= max(1, len(tasks) // 2) else []


def _top_today_items(groups: list[PlanGroup], *, limit: int) -> list[PlanItem]:
    ordered: list[PlanItem] = []
    for key in ("overdue", "today", "blocking", "deep_work", "quick_wins"):
        ordered.extend(item for group in groups if group.key == key for item in group.items)
    return sorted(ordered, key=lambda item: _plan_sort_key(item.task, item.priority), reverse=True)[:limit]


def _now_alternatives(tasks: list[Task], priority_config: dict | None, inbox_count: int) -> list[PlanRecommendation]:
    alternatives: list[PlanRecommendation] = []
    quick = _first_matching(tasks, priority_config, lambda task: task.effort_bucket == "quick" or (task.estimated_minutes or 9999) <= 30)
    deep = _first_matching(tasks, priority_config, lambda task: task.effort_bucket == "deep" or (task.estimated_minutes or 0) >= 60)
    low_focus = _first_matching(tasks, priority_config, lambda task: task.context_bucket in {"admin", "review", "errand", "quick_task", "call"})
    for kind, label, task in (
        ("quick_win", "15 min", quick),
        ("deep_work", "foco", deep),
        ("low_focus", "sin foco", low_focus),
    ):
        if task:
            item = _plan_item(task, priority_config, mode="now")
            alternatives.append(
                PlanRecommendation(
                    kind=kind,
                    task=task,
                    title=f"{label}: {task.title}",
                    reason=item.reason,
                    priority=item.priority,
                )
            )
    if inbox_count >= 1:
        alternatives.append(
            PlanRecommendation(
                kind="inbox_cleanup",
                title=f"ordenar: Procesar Inbox ({inbox_count})",
                reason=f"Hay {inbox_count} tarea{'' if inbox_count == 1 else 's'} sin clasificar.",
            )
        )
    return alternatives[:4]


def _first_matching(tasks: list[Task], priority_config: dict | None, predicate) -> Task | None:
    candidates = [task for task in tasks if predicate(task)]
    if not candidates:
        return None
    return sorted(candidates, key=lambda task: _now_sort_key(task, priority_config), reverse=True)[0]


def _plan_sort_key(task: Task, priority: PriorityExplanation | None) -> tuple:
    score = priority.score if priority else 0
    due = _parse_datetime(task.due_at)
    due_rank = -int(due.timestamp()) if due else -9999999999
    return (score, due_rank, -task.manual_order, task.created_at or "")


def _now_sort_key(task: Task, priority_config: dict | None) -> tuple:
    due = _parse_datetime(task.due_at)
    now = datetime.now(ZoneInfo(settings.app_timezone))
    overdue = 1 if due and due.astimezone(ZoneInfo(settings.app_timezone)).date() < now.date() else 0
    today = 1 if due and due.astimezone(ZoneInfo(settings.app_timezone)).date() == now.date() else 0
    blocking = task.blocking_score or 0
    return (overdue, today, _score(task, priority_config, mode="now"), blocking, -task.manual_order, task.created_at or "")


def _is_snoozed(task: Task) -> bool:
    if not task.snoozed_until:
        return False
    from app.services import task_action_parser

    current = task_action_parser.current_app_time()
    return task.snoozed_until > current.isoformat()


def _group_key(task: Task) -> str:
    if task.task_kind == "attention":
        return "attention"
    if task.task_kind == "perpetual":
        return "perpetual"
    if task.scope == "Inbox":
        return "inbox"
    due = _parse_datetime(task.due_at)
    if due:
        today = datetime.now(ZoneInfo(settings.app_timezone)).date()
        due_day = due.astimezone(ZoneInfo(settings.app_timezone)).date()
        if due_day < today:
            return "overdue"
        if due_day == today:
            return "today"
    if (task.blocking_score or 0) >= 4:
        return "blocking"
    if task.trello_state in {"review", "waiting", "blocked"} or task.context_bucket == "review":
        return "review"
    if task.effort_bucket == "quick" or (task.estimated_minutes and task.estimated_minutes <= 30):
        return "quick_wins"
    if task.effort_bucket == "deep":
        return "deep_work"
    return "quick_wins"


def _plan_item(task: Task, priority_config: dict | None, *, mode: str = "today") -> PlanItem:
    explanation = score_task(task, priority_config, mode=mode)
    return PlanItem(task=task, reason=explanation.summary or _reason(task, _group_key(task)), priority=explanation)


def _score(task: Task, priority_config: dict | None = None, *, mode: str = "today") -> float:
    return score_task(task, priority_config, mode=mode).score


def score_task(task: Task, priority_config: dict | None = None, *, mode: str = "today") -> PriorityExplanation:
    criteria = _priority_criteria(priority_config)
    factors: list[PriorityFactor] = []
    warnings: list[str] = []

    for criterion in SUPPORTED_PRIORITY_CRITERIA:
        item = criteria.get(criterion) or {}
        if not item.get("enabled", True):
            continue
        contribution, label, reason, warning = _criterion_signal(task, criterion, mode=mode)
        if warning:
            warnings.append(warning)
        if contribution == 0:
            continue
        weighted = round(contribution * _criterion_multiplier(criteria, criterion), 2)
        direction = "up" if weighted > 0 else "down"
        factors.append(
            PriorityFactor(
                criterion=criterion,
                label=label,
                contribution=weighted,
                direction=direction,
                reason=reason,
            )
        )

    score = round(sum(factor.contribution for factor in factors), 2)
    top_reasons = [factor.label for factor in sorted(factors, key=lambda item: abs(item.contribution), reverse=True)[:3]]
    summary = ", ".join(top_reasons) if top_reasons else "prioridad calculada localmente"
    return PriorityExplanation(
        task_id=task.id,
        score=score,
        priority_band=_priority_band(score),
        factors=factors,
        summary=summary,
        warnings=sorted(set(warnings)),
    )


def _priority_criteria(priority_config: dict | None) -> dict[str, dict]:
    configured = (priority_config or {}).get("criteria") or settings_service.DEFAULT_PRIORITY_CRITERIA
    criteria = {key: dict(value) for key, value in settings_service.DEFAULT_PRIORITY_CRITERIA.items()}
    for key, value in configured.items():
        if key in criteria and isinstance(value, dict):
            criteria[key] = {**criteria[key], **value}
    return criteria


def _criterion_multiplier(criteria: dict[str, dict], criterion: str) -> float:
    enabled = [(key, int(item.get("weight", 0))) for key, item in criteria.items() if key in SUPPORTED_PRIORITY_CRITERIA and item.get("enabled", True)]
    if not enabled:
        return 0
    ordered = sorted(enabled, key=lambda item: item[1], reverse=True)
    index = next((idx for idx, (key, _) in enumerate(ordered) if key == criterion), len(ordered) - 1)
    if len(ordered) == 1:
        return 1.0
    return round(1.0 - (index / (len(ordered) - 1)) * 0.6, 3)


def _criterion_signal(task: Task, criterion: str, *, mode: str) -> tuple[float, str, str, str | None]:
    if criterion == "due_date":
        return _due_date_signal(task)
    if criterion == "manual_priority":
        return _priority_signal(task, source_type="manual")
    if criterion == "source_priority":
        return _priority_signal(task, source_type="source")
    if criterion == "urgency":
        return _score_signal(task.urgency_score, "Urgencia", "Urgencia {value}/5.", "Sin urgencia definida.")
    if criterion == "impact":
        return _score_signal(task.impact_score, "Impacto", "Impacto {value}/5.", "Sin impacto definido.")
    if criterion == "blocking":
        return _score_signal(task.blocking_score, "Bloqueo", "Bloqueo {value}/5.", None)
    if criterion == "effort":
        return _effort_signal(task, mode=mode)
    if criterion == "stale_in_progress":
        return _stale_signal(task)
    if criterion == "age":
        return _age_signal(task)
    return 0, criterion, "Criterio sin implementación.", None


def _score_signal(value: int | None, label: str, reason_template: str, missing_warning: str | None) -> tuple[float, str, str, str | None]:
    if value is None:
        return 0, label, "", missing_warning
    contribution = max(0, min(5, int(value))) * 4
    human = f"{label} alto" if value >= 4 else f"{label} medio" if value >= 2 else f"{label} bajo"
    return contribution, human, reason_template.format(value=value), None


def _priority_signal(task: Task, *, source_type: str) -> tuple[float, str, str, str | None]:
    if not task.priority_label:
        return 0, "Sin prioridad", "", None
    is_source = task.source_type == "trello"
    if source_type == "source" and not is_source:
        return 0, "Sin prioridad Trello", "", None
    if source_type == "manual" and is_source:
        return 0, "Sin prioridad manual", "", None
    values = {"high": 28, "medium_high": 20, "medium": 12, "low": 4}
    labels = {"high": "Prioridad alta", "medium_high": "Prioridad media alta", "medium": "Prioridad media", "low": "Prioridad baja"}
    value = values.get(task.priority_label, 0)
    label = labels.get(task.priority_label, "Prioridad")
    origin = "Trello" if source_type == "source" else "manual"
    return value, label, f"Prioridad {origin}: {label.lower()}.", None


def _due_date_signal(task: Task) -> tuple[float, str, str, str | None]:
    if not task.due_at:
        return 0, "Sin fecha", "", None
    due = _parse_datetime(task.due_at)
    if not due:
        return 0, "Fecha inválida", "", "Deadline inválido."
    now = datetime.now(ZoneInfo(settings.app_timezone))
    days = (due.astimezone(ZoneInfo(settings.app_timezone)).date() - now.date()).days
    if days < 0:
        return 32, "Vencida", "La tarea está vencida.", None
    if days == 0:
        return 28, "Vence hoy", "La tarea vence hoy.", None
    if days == 1:
        return 22, "Vence mañana", "La tarea vence mañana.", None
    if days <= 7:
        return 14, "Vence esta semana", "La tarea vence esta semana.", None
    return 4, "Fecha futura", "Tiene fecha futura visible.", None


def _effort_signal(task: Task, *, mode: str) -> tuple[float, str, str, str | None]:
    if not task.effort_bucket and not task.estimated_minutes:
        return 0, "Sin esfuerzo", "", "Sin estimación de esfuerzo."
    minutes = task.estimated_minutes or 0
    if task.effort_bucket == "quick" or (minutes and minutes <= 30):
        bonus = 14 if mode == "now" else 10
        return bonus, "Quick win", "Parece corta y cerrable.", None
    if task.effort_bucket == "deep" or minutes >= 180:
        if (task.impact_score or 0) >= 4:
            return 4, "Trabajo profundo con impacto", "Requiere foco, pero tiene impacto alto.", None
        return -6, "Esfuerzo profundo", "Requiere un bloque largo y no muestra urgencia alta.", None
    return 4, "Esfuerzo moderado", "Requiere un bloque moderado de trabajo.", None


def _stale_signal(task: Task) -> tuple[float, str, str, str | None]:
    if task.trello_state != "in_progress":
        return 0, "No estancada", "", None
    touched_at = _parse_datetime(task.last_trello_activity_at or task.last_touched_at or task.updated_at or task.first_seen_at)
    if not touched_at:
        return 0, "Sin actividad", "", "Sin fecha de actividad para detectar estancamiento."
    days = max(0, (datetime.now(touched_at.tzinfo or ZoneInfo("UTC")) - touched_at).days)
    if days < 3:
        return 0, "En proceso reciente", "", None
    contribution = min(18, 8 + (days - 3) * 2)
    return contribution, "EN PROCESO estancada", f"Lleva {days} días sin movimiento visible.", None


def _age_signal(task: Task) -> tuple[float, str, str, str | None]:
    created = _parse_datetime(task.first_seen_at or task.created_at)
    if not created:
        return 0, "Sin antigüedad", "", "Sin fecha de creación para calcular antigüedad."
    days = max(0, (datetime.now(created.tzinfo or ZoneInfo("UTC")) - created).days)
    if days < 7:
        return 0, "Reciente", "", None
    contribution = min(12, math.floor(days / 7) * 2)
    return contribution, "Tarea antigua", f"Lleva {days} días sin resolverse.", None


def _priority_band(score: float) -> str:
    if score >= 55:
        return "high"
    if score >= 34:
        return "medium_high"
    if score >= 16:
        return "medium"
    return "low"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed


def _reason(task: Task, key: str) -> str:
    if task.due_at:
        return "tiene deadline visible y conviene atenderla"
    if key == "deep_work":
        return "requiere foco y puede tener impacto alto"
    if key == "quick_task":
        return "parece corta y cerrable"
    if key == "inbox":
        return "necesita clasificación antes de priorizar"
    if key == "errand":
        return "parece personal o depende de salir"
    if key == "attention":
        return "te menciona en Trello pero no está asignada"
    if key == "perpetual":
        return "requiere seguimiento periódico"
    if key == "call":
        return "requiere contacto o llamada"
    return "prioridad calculada localmente"
