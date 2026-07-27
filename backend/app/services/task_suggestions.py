import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.confirmations import PendingConfirmation
from app.models.tasks import Task, TaskEvent
from app.schemas.llm import OpenAITaskSuggestion, OpenAITaskSuggestionInput
from app.schemas.tasks import (
    BulkTaskSuggestionApplyError,
    BulkTaskSuggestionApplyItem,
    BulkTaskSuggestionApplyResponse,
    BulkTaskSuggestionResult,
    BulkTaskSuggestionResponse,
    SuggestionItem,
    TaskSuggestionApplyResponse,
    TaskSuggestionApplySkip,
    TaskSuggestionResponse,
)
from app.services import auth as auth_service
from app.services import confirmations, llm
from app.services import planning
from app.services import tasks as task_service
from app.services import settings_service
from app.services.classification import classify_task_title
from app.services.deadlines import parse_clear_deadline
from app.services.time import utc_now_iso


APPLY_SUGGESTIONS_ACTION = "apply_task_suggestions"
PRIORITIES = {"high", "medium_high", "medium", "low"}
EFFORTS = {"quick", "medium", "deep"}
CONTEXTS = {"deep_work", "quick_task", "call", "errand", "admin", "review"}
MAX_BULK_TASKS = 25


@dataclass(frozen=True)
class SuggestionFieldSpec:
    task_field: str | None
    metadata_key: str | None
    validator: str
    max_length: int | None = None
    tracks_gap: bool = False
    unknown_is_missing: bool = False
    inbox_is_missing: bool = False


TASK_SUGGESTION_FIELD_REGISTRY: dict[str, SuggestionFieldSpec] = {
    "normalized_title": SuggestionFieldSpec("title", None, "string", max_length=500),
    "scope": SuggestionFieldSpec("scope", None, "string", inbox_is_missing=True),
    "priority_label": SuggestionFieldSpec("priority_label", None, "priority", tracks_gap=True),
    "impact_score": SuggestionFieldSpec("impact_score", None, "score", tracks_gap=True),
    "urgency_score": SuggestionFieldSpec("urgency_score", None, "score", tracks_gap=True),
    "blocking_score": SuggestionFieldSpec("blocking_score", None, "score", tracks_gap=True),
    "effort_bucket": SuggestionFieldSpec("effort_bucket", None, "effort", tracks_gap=True, unknown_is_missing=True),
    "estimated_minutes": SuggestionFieldSpec("estimated_minutes", None, "minutes", tracks_gap=True),
    "context_bucket": SuggestionFieldSpec("context_bucket", None, "context", tracks_gap=True, unknown_is_missing=True),
    "due_at": SuggestionFieldSpec("due_at", None, "datetime"),
    "notes": SuggestionFieldSpec(None, "notes", "string", max_length=10000, tracks_gap=True),
}
APPLICABLE_FIELDS = set(TASK_SUGGESTION_FIELD_REGISTRY)
IMPORTANT_FIELDS = {field for field, spec in TASK_SUGGESTION_FIELD_REGISTRY.items() if spec.tracks_gap}


def missing_detail_fields(task: Task) -> list[str]:
    metadata = _metadata(task.metadata_json)
    return [
        field
        for field, spec in TASK_SUGGESTION_FIELD_REGISTRY.items()
        if spec.tracks_gap and not _field_has_value(task, metadata, field)
    ]


def is_candidate(task: Task) -> bool:
    return task.status == "active" and len(candidate_missing_fields(task)) >= 2


def candidate_missing_fields(task: Task) -> list[str]:
    return [field for field in missing_detail_fields(task) if field in IMPORTANT_FIELDS]


def incomplete_tasks(tasks: list[Task], *, limit: int = 25, scope: str | None = None, include_completed: bool = False) -> tuple[list[dict[str, Any]], int]:
    result: list[dict[str, Any]] = []
    total = 0
    for task in tasks:
        if not include_completed and task.status != "active":
            continue
        if task.status == "deleted":
            continue
        if scope and task.scope.casefold() != scope.casefold():
            continue
        missing = candidate_missing_fields(task)
        if len(missing) >= 2:
            total += 1
            if len(result) < min(limit, MAX_BULK_TASKS):
                result.append(incomplete_task_payload(task))
    return result, total


def incomplete_task_payload(task: Task) -> dict[str, Any]:
    missing = candidate_missing_fields(task)
    return {
        "id": task.id,
        "title": task.title,
        "scope": task.scope,
        "status": task.status,
        "source_type": task.source_type,
        "source_id": task.source_id,
        "updated_at": task.updated_at,
        "version_token": task_service.task_version_token(task),
        "missing_fields": missing,
        "missing_count": len(missing),
        "candidate": is_candidate(task),
        "current": _current_values(task),
    }


def suggest_details_bulk(db: Session, tasks: list[Task], *, include_notes: bool = True, include_checklist: bool = False, allow_existing_field_updates: bool = False) -> BulkTaskSuggestionResponse:
    selected = tasks[:MAX_BULK_TASKS]
    results: list[BulkTaskSuggestionResult] = []
    source_summary = {"openai": 0, "heuristic": 0, "error": 0}
    warnings: list[str] = []
    if len(tasks) > MAX_BULK_TASKS:
        warnings.append(f"Se procesaron sólo {MAX_BULK_TASKS} tareas por seguridad.")
    for task in selected:
        try:
            suggestion = suggest_details(
                db,
                task,
                include_notes=include_notes,
                include_checklist=include_checklist,
                allow_existing_field_updates=allow_existing_field_updates,
            )
            source_summary[suggestion.source if suggestion.source in source_summary else "heuristic"] += 1
            results.append(BulkTaskSuggestionResult(task_id=task.id, ok=True, suggestion=suggestion))
        except Exception as exc:  # noqa: BLE001 - per-task isolation for bulk review.
            source_summary["error"] += 1
            results.append(BulkTaskSuggestionResult(task_id=task.id, ok=False, error=llm.sanitize_llm_text(str(exc))))
    return BulkTaskSuggestionResponse(mode="sync", source_summary=source_summary, results=results, warnings=warnings)


def apply_suggestions_bulk(db: Session, tasks_by_id: dict[str, Task], items: list[BulkTaskSuggestionApplyItem]) -> BulkTaskSuggestionApplyResponse:
    applied = 0
    errors: list[BulkTaskSuggestionApplyError] = []
    updated_tasks: list[Any] = []
    results: list[TaskSuggestionApplyResponse] = []
    for item in items[:MAX_BULK_TASKS]:
        task = tasks_by_id.get(item.task_id)
        if not task:
            errors.append(BulkTaskSuggestionApplyError(task_id=item.task_id, error="La tarea ya no existe."))
            continue
        try:
            result = apply_suggestions(
                db,
                task,
                item.suggestions,
                item.fields,
                allow_existing_field_updates=item.allow_existing_field_updates,
                source=item.source,
            )
            if result.applied_fields:
                applied += 1
            results.append(result)
            updated_tasks.append(result.task)
        except Exception as exc:  # noqa: BLE001 - one task failure should not rollback the batch.
            db.rollback()
            errors.append(BulkTaskSuggestionApplyError(task_id=item.task_id, error=llm.sanitize_llm_text(str(exc))))
    return BulkTaskSuggestionApplyResponse(applied=applied, errors=errors, tasks=updated_tasks, results=results)


def suggest_details(
    db: Session,
    task: Task,
    *,
    include_notes: bool = True,
    include_checklist: bool = False,
    allow_existing_field_updates: bool = False,
) -> TaskSuggestionResponse:
    task = _fresh_task(db, task.id, task.user_id)
    missing = set(missing_detail_fields(task))
    llm_status = llm.llm_status(db, task.user_id)
    use_openai = bool(llm_status.safe_to_use)
    if use_openai:
        try:
            provider = llm.get_llm_provider(db, task.user_id)
            if provider is None:
                raise llm.LLMError("OpenAI no está lista para esta tarea.")
            structured = provider.suggest_task_details(
                OpenAITaskSuggestionInput(
                    title=task.title,
                    scope=task.scope,
                    current=_current_values(task),
                    missing_fields=sorted(missing),
                    context=_suggestion_context(task),
                    allowed_scopes=settings_service.available_scopes(db, task.user_id),
                    include_notes=include_notes,
                )
            )
            suggestions, filtered_fields = _structured_suggestions(
                db,
                task,
                structured,
                missing=missing,
                allow_existing_field_updates=allow_existing_field_updates,
            )
            if _should_recommend_trello_card(db, task):
                suggestions["trello_card_recommendation"] = _trello_recommendation(task)
            llm.record_llm_success(db, user_id=task.user_id)
            return TaskSuggestionResponse(
                task_id=task.id,
                suggestions=suggestions,
                source="openai",
                llm_status="ready",
                missing_fields=sorted(missing),
                created_at=utc_now_iso(),
                warnings=[
                    *list(structured.warnings),
                    *([f"Se ignoraron campos no aplicables o ya completos: {', '.join(sorted(filtered_fields))}."] if filtered_fields else []),
                ],
            )
        except llm.LLMError as exc:
            llm.record_llm_error(db, str(exc), user_id=task.user_id)
            llm_status = llm.llm_status(db, task.user_id)

    classification = classify_task_title(
        task.title,
        db=db,
        user_id=task.user_id,
        auto_classify=True,
        llm_enabled=False,
        user_text=_suggestion_context(task),
        **settings_service.classification_context(db, user_id=task.user_id),
    )
    normalized_title, parsed_due = parse_clear_deadline(task.title)
    suggestions: dict[str, SuggestionItem] = {}
    source = "heuristic"
    warnings = [f"Fallback heurístico: {llm_status.reason}"]

    if allow_existing_field_updates and normalized_title.strip() and normalized_title.strip() != task.title.strip():
        suggestions["normalized_title"] = _item(normalized_title.strip(), 0.82, "Quité una señal de fecha del título.", False)
    if task.scope == "Inbox" and classification.scope != "Inbox":
        suggestions["scope"] = _item(classification.scope, classification.scope_confidence, classification.reason or "clasificación por contexto", True)
    if "priority_label" in missing:
        suggestions["priority_label"] = _item(_priority(task, parsed_due), 0.64, "Priorización local según deadline, estado y palabras clave.")
    if "impact_score" in missing:
        suggestions["impact_score"] = _item(classification.impact_score or _score_for_impact(task), 0.7 if classification.impact_score else 0.62, classification.reason if classification.impact_score else "Estimación local por scope y tipo de trabajo.")
    if "urgency_score" in missing:
        suggestions["urgency_score"] = _item(classification.urgency_score or _score_for_urgency(task, parsed_due), 0.7 if classification.urgency_score else 0.62, classification.reason if classification.urgency_score else "Estimación local por deadline o señales de urgencia.")
    if "blocking_score" in missing:
        suggestions["blocking_score"] = _item(classification.blocking_score or _score_for_blocking(task), 0.7 if classification.blocking_score else 0.58, classification.reason if classification.blocking_score else "Estimación local por señales de bloqueo o dependencia.")
    if "effort_bucket" in missing and classification.effort_bucket != "unknown":
        suggestions["effort_bucket"] = _item(classification.effort_bucket, 0.7, classification.reason or "heurística de esfuerzo")
    if "estimated_minutes" in missing and classification.estimated_minutes:
        suggestions["estimated_minutes"] = _item(classification.estimated_minutes, 0.68, "Duración estimada por esfuerzo sugerido.")
    if "context_bucket" in missing and classification.context_bucket != "unknown":
        suggestions["context_bucket"] = _item(classification.context_bucket, 0.7, "Contexto sugerido por palabras clave y scope.")
    if task.due_at is None:
        suggested_due = parsed_due or classification.due_at
        if suggested_due or _has_deadline_signal(task):
            suggestions["due_at"] = _item(suggested_due, 0.72 if suggested_due else 0.35, "No hay señal suficiente para deadline." if not suggested_due else "Detecté una fecha clara en el título o contexto.")
    if include_notes and "notes" in missing:
        suggestions["notes"] = _item(_notes(task, classification.effort_bucket), 0.55, "Resumen local para arrancar sin escribir en Trello.")
    if _should_recommend_trello_card(db, task):
        suggestions["trello_card_recommendation"] = _trello_recommendation(task)

    return TaskSuggestionResponse(
        task_id=task.id,
        suggestions=suggestions,
        source=source,
        llm_status=llm_status.status,
        missing_fields=sorted(missing),
        created_at=utc_now_iso(),
        warnings=warnings,
    )


def _structured_suggestions(
    db: Session,
    task: Task,
    result: OpenAITaskSuggestion,
    *,
    missing: set[str],
    allow_existing_field_updates: bool,
) -> tuple[dict[str, SuggestionItem], list[str]]:
    reason = result.reasoning_summary
    values: dict[str, Any] = {
        "normalized_title": result.normalized_title,
        "scope": result.scope,
        "priority_label": result.priority_label,
        "impact_score": result.impact_score,
        "urgency_score": result.urgency_score,
        "blocking_score": result.blocking_score,
        "effort_bucket": result.effort_bucket,
        "estimated_minutes": result.estimated_minutes,
        "context_bucket": result.context_bucket,
        "due_at": result.due_at,
        "notes": result.notes,
    }
    if values["scope"] is not None:
        allowed = settings_service.available_scopes(db, task.user_id)
        values["scope"] = next(
            (scope for scope in allowed if scope.casefold() == str(values["scope"]).casefold()),
            None,
        )
    if values["due_at"] is not None:
        try:
            values["due_at"] = _datetime_or_none(values["due_at"], "due_at")
        except ValueError:
            values["due_at"] = None

    suggestions: dict[str, SuggestionItem] = {}
    filtered_fields: list[str] = []
    for field, value in values.items():
        if value is None:
            continue
        if field == "normalized_title" and not allow_existing_field_updates:
            filtered_fields.append(field)
            continue
        if not allow_existing_field_updates and _field_has_value(task, _metadata(task.metadata_json), field):
            filtered_fields.append(field)
            continue
        try:
            validated_value = _validate_suggestion_value(field, value, TASK_SUGGESTION_FIELD_REGISTRY[field])
        except (TypeError, ValueError):
            filtered_fields.append(field)
            continue
        suggestions[field] = _item(validated_value, 0.8, reason, field in missing)
    return suggestions, filtered_fields


def _trello_recommendation(task: Task) -> SuggestionItem:
    return _item(
        {
            "recommended": True,
            "reason": f"Scope {task.scope} suele trackearse en Trello, pero esta tarea no tiene card.",
            "board_alias": task.scope,
        },
        0.66,
        "Recomendación calculada localmente; no crea ni modifica Trello.",
        True,
    )


def create_suggestion_confirmations(
    db: Session,
    tasks: list[Task],
    *,
    include_notes: bool = True,
    user_id: str | None = None,
) -> tuple[list[PendingConfirmation], int]:
    created: list[PendingConfirmation] = []
    skipped = 0
    for task in tasks:
        if not task.user_id:
            task.user_id = user_id or auth_service.single_owner_user_id(db)
            db.flush()
        response = suggest_details(db, task, include_notes=include_notes)
        fields = [field for field, item in response.suggestions.items() if field in APPLICABLE_FIELDS and item.value is not None]
        if not fields:
            skipped += 1
            continue
        suggestions = {field: item.value for field, item in response.suggestions.items()}
        confirmation = confirmations.create_confirmation(
            db,
            source="ui",
            action_type=APPLY_SUGGESTIONS_ACTION,
            payload={
                "task_id": task.id,
                "suggestions": suggestions,
                "fields": fields,
                "source": response.source,
                "created_at": response.created_at,
            },
            summary=f'Aplicar sugerencias a "{task.title}"',
            user_id=user_id or task.user_id,
        )
        created.append(confirmation)
    return created, skipped


def apply_suggestion_confirmation(db: Session, confirmation: PendingConfirmation) -> str:
    payload = json.loads(confirmation.payload_json)
    task = db.get(Task, payload.get("task_id"))
    if not task:
        raise ValueError("La tarea de esta confirmación ya no existe.")
    if confirmation.user_id and task.user_id != confirmation.user_id:
        raise ValueError("La tarea de esta confirmación ya no existe.")
    fields = payload.get("fields")
    suggestions = payload.get("suggestions")
    if not isinstance(fields, list) or not isinstance(suggestions, dict):
        raise ValueError("La confirmación de sugerencias tiene un payload inválido.")
    apply_suggestions(
        db,
        task,
        suggestions,
        [str(field) for field in fields],
        source=str(payload.get("source") or "unknown"),
    )
    confirmations.confirm(db, confirmation)
    return f'Sugerencias aplicadas: {task.title}'


def _suggestion_context(task: Task) -> str | None:
    metadata = _metadata(task.metadata_json)
    parts: list[str] = []
    notes = str(metadata.get("notes") or "").strip()
    if notes:
        parts.append(f"Notas locales: {notes}")
    if task.source_type == "trello":
        trello = metadata.get("trello") if isinstance(metadata.get("trello"), dict) else {}
        description = str(trello.get("description") or "").strip()
        if description:
            parts.append(f"Descripción Trello: {description}")
        checklists = trello.get("checklists")
        if isinstance(checklists, list):
            checklist_lines = []
            for checklist in checklists:
                if not isinstance(checklist, dict):
                    continue
                for item in checklist.get("items") or []:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    if name:
                        checklist_lines.append(f"- [{item.get('state') or 'unknown'}] {name}")
            if checklist_lines:
                parts.append("Checklist Trello:\n" + "\n".join(checklist_lines[:40]))
        if task.checklist_total is not None:
            parts.append(f"Progreso checklist Trello: {task.checklist_done or 0}/{task.checklist_total}")
    return "\n\n".join(parts) or None


def apply_suggestions(
    db: Session,
    task: Task,
    suggestions: dict[str, Any],
    fields: list[str],
    *,
    allow_existing_field_updates: bool = False,
    source: str = "unknown",
) -> TaskSuggestionApplyResponse:
    task = _fresh_task(db, task.id, task.user_id)
    metadata = _metadata(task.metadata_json)
    applied_fields: list[str] = []
    skipped_fields: list[TaskSuggestionApplySkip] = []
    for field in fields:
        spec = TASK_SUGGESTION_FIELD_REGISTRY.get(field)
        if spec is None:
            skipped_fields.append(TaskSuggestionApplySkip(field=field, reason="invalid_field"))
            continue
        if field not in suggestions:
            skipped_fields.append(TaskSuggestionApplySkip(field=field, reason="missing_suggestion"))
            continue
        try:
            value = _validate_suggestion_value(field, _suggestion_value(suggestions[field]), spec)
        except (TypeError, ValueError):
            skipped_fields.append(TaskSuggestionApplySkip(field=field, reason="invalid_value"))
            continue
        current_value = _stored_field_value(task, metadata, spec)
        if _values_equal(current_value, value):
            skipped_fields.append(TaskSuggestionApplySkip(field=field, reason="already_set"))
            continue
        if not allow_existing_field_updates and _field_has_value(task, metadata, field):
            skipped_fields.append(TaskSuggestionApplySkip(field=field, reason="no_overwrite"))
            continue
        if spec.metadata_key:
            metadata[spec.metadata_key] = value
            task.metadata_json = json.dumps(metadata, ensure_ascii=False)
        elif spec.task_field:
            setattr(task, spec.task_field, value)
        applied_fields.append(field)

    if applied_fields:
        now = utc_now_iso()
        task.updated_at = now
        task.last_touched_at = now
        db.add(
            TaskEvent(
                id=str(uuid4()),
                user_id=task.user_id,
                task_id=task.id,
                event_type="task_suggestions_applied",
                payload_json=json.dumps(
                    {"fields": applied_fields, "source": _safe_suggestion_source(source)},
                    ensure_ascii=False,
                ),
                created_at=now,
            )
        )
        db.commit()
    task = _fresh_task(db, task.id, task.user_id)
    remaining = missing_detail_fields(task)
    priority = planning.score_task(
        task,
        settings_service.priority_settings(db, user_id=task.user_id),
    )
    return TaskSuggestionApplyResponse(
        task=task,
        applied_fields=applied_fields,
        skipped_fields=skipped_fields,
        remaining_missing_fields=remaining,
        is_candidate=is_candidate(task),
        priority_explanation=priority.model_dump(),
    )


def _item(value: Any, confidence: float, reason: str, applies_to_empty_field: bool = True) -> SuggestionItem:
    return SuggestionItem(value=value, confidence=max(0.0, min(confidence, 1.0)), reason=reason, applies_to_empty_field=applies_to_empty_field)


def _priority(task: Task, parsed_due: str | None) -> str:
    title = task.title.casefold()
    if parsed_due or task.due_at or any(word in title for word in ("urgente", "deadline", "bloquea", "bloqueado")):
        return "high"
    if task.source_type == "trello" and task.trello_state in {"in_progress", "review"}:
        return "medium_high"
    if any(word in title for word in ("revisar", "fix", "bug", "endpoint")):
        return "medium_high"
    return "medium"


def _score_for_impact(task: Task) -> int:
    title = task.title.casefold()
    if task.source_type == "trello" or any(word in title for word in ("cliente", "produccion", "endpoint", "bug")):
        return 4
    return 3


def _score_for_urgency(task: Task, parsed_due: str | None) -> int:
    title = task.title.casefold()
    if parsed_due or task.due_at or "urgente" in title:
        return 4
    if "hoy" in title or "ahora" in title:
        return 5
    return 2


def _score_for_blocking(task: Task) -> int:
    title = task.title.casefold()
    if any(word in title for word in ("bloquea", "bloqueado", "sin output", "endpoint")):
        return 4
    return 2


def _notes(task: Task, effort: str) -> str:
    effort_text = {"quick": "rápida", "medium": "media", "deep": "profunda"}.get(effort, "por definir")
    return f"Objetivo: avanzar con {task.title}. Scope: {task.scope}. Complejidad estimada: {effort_text}."


def _metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _current_values(task: Task) -> dict[str, Any]:
    metadata = _metadata(task.metadata_json)
    return {
        field: _stored_field_value(task, metadata, spec)
        for field, spec in TASK_SUGGESTION_FIELD_REGISTRY.items()
        if spec.tracks_gap
    }


def _field_has_value(task: Task, metadata: dict[str, Any], field: str) -> bool:
    spec = TASK_SUGGESTION_FIELD_REGISTRY[field]
    value = _stored_field_value(task, metadata, spec)
    if isinstance(value, str) and not value.strip():
        return False
    if spec.unknown_is_missing and value == "unknown":
        return False
    if spec.inbox_is_missing and value == "Inbox":
        return False
    return value is not None


def _stored_field_value(task: Task, metadata: dict[str, Any], spec: SuggestionFieldSpec) -> Any:
    if spec.metadata_key:
        return metadata.get(spec.metadata_key)
    return getattr(task, spec.task_field, None) if spec.task_field else None


def _fresh_task(db: Session, task_id: str, user_id: str | None) -> Task:
    statement = select(Task).where(Task.id == task_id)
    if user_id is not None:
        statement = statement.where(Task.user_id == user_id)
    task = db.scalar(statement.execution_options(populate_existing=True))
    if task is None:
        raise ValueError("La tarea ya no existe.")
    db.refresh(task)
    return task


def _validate_suggestion_value(field: str, value: Any, spec: SuggestionFieldSpec) -> Any:
    if spec.validator == "string":
        return _string_value(value, field, max_length=spec.max_length)
    if spec.validator == "priority":
        return _choice(value, PRIORITIES, field)
    if spec.validator == "score":
        return _score(value, field)
    if spec.validator == "effort":
        return _choice(value, EFFORTS, field)
    if spec.validator == "minutes":
        return _positive_int(value, field)
    if spec.validator == "context":
        return _choice(value, CONTEXTS, field)
    if spec.validator == "datetime":
        return _datetime_or_none(value, field)
    raise ValueError(f"{field} inválido.")


def _values_equal(current: Any, suggested: Any) -> bool:
    return current == suggested


def _safe_suggestion_source(source: str) -> str:
    return source if source in {"openai", "heuristic"} else "unknown"


def _has_deadline_signal(task: Task) -> bool:
    metadata = _metadata(task.metadata_json)
    text = " ".join([task.title, str(metadata.get("notes") or "")]).casefold()
    return any(
        signal in text
        for signal in (
            "hoy",
            "mañana",
            "maniana",
            "lunes",
            "martes",
            "miércoles",
            "miercoles",
            "jueves",
            "viernes",
            "sábado",
            "sabado",
            "domingo",
            "deadline",
            "vence",
            "vencimiento",
        )
    )


def _should_recommend_trello_card(db: Session, task: Task) -> bool:
    if task.source_id is not None:
        return False
    trello = settings_service.get_settings(db, user_id=task.user_id).get("trello") or {}
    boards = trello.get("boards") if isinstance(trello.get("boards"), dict) else {}
    return any(
        isinstance(board, dict)
        and board.get("enabled", True)
        and bool(str(board.get("board_id") or "").strip())
        and str(board.get("alias") or alias) == task.scope
        for alias, board in boards.items()
    )


def _string_value(value: Any, field: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} inválido.")
    normalized = value.strip()
    if max_length is not None and len(normalized) > max_length:
        raise ValueError(f"{field} excede el largo máximo permitido.")
    return normalized


def _suggestion_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    if isinstance(value, SuggestionItem):
        return value.value
    return value


def _choice(value: Any, allowed: set[Any], field: str):
    if value not in allowed:
        raise ValueError(f"{field} inválido.")
    return value


def _score(value: Any, field: str) -> int:
    number = _strict_int(value, field)
    if number < 1 or number > 5:
        raise ValueError(f"{field} debe estar entre 1 y 5.")
    return number


def _positive_int(value: Any, field: str) -> int:
    number = _strict_int(value, field)
    if number < 5 or number > 1440:
        raise ValueError(f"{field} debe estar entre 5 y 1440.")
    return number


def _strict_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} inválido.")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field} inválido.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} inválido.") from exc


def _datetime_or_none(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} inválido.")
    candidate = value.strip()
    if not candidate:
        raise ValueError(f"{field} inválido.")
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} inválido.") from exc
    return candidate
