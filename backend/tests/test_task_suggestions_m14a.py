import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.models.tasks import Task, TaskEvent
from app.main import app
from app.schemas.llm import OpenAITaskSuggestion, TaskClassification
from app.schemas.tasks import BulkTaskSuggestionApplyItem
from app.services import task_suggestions
from app.services.time import utc_now_iso


def ai_status(*, safe_to_use: bool, status: str, reason: str):
    return SimpleNamespace(
        safe_to_use=safe_to_use,
        status=status,
        reason=reason,
        user_enabled=safe_to_use,
        api_key_configured=safe_to_use,
        model="gpt-5.6-sol",
    )


def test_suggest_details_for_empty_task_returns_structured_suggestions(db_session, monkeypatch):
    task = make_task(db_session, "t1", "implementar endpoint de Project Delta", scope="DELTA")

    response = task_suggestions.suggest_details(db_session, task)

    assert response.task_id == "t1"
    assert response.source == "heuristic"
    assert response.llm_status == "requires_encryption_key"
    assert "due_at" not in response.missing_fields
    assert response.suggestions["priority_label"].value in {"medium_high", "high", "medium"}
    assert response.suggestions["effort_bucket"].value == "deep"
    assert response.suggestions["context_bucket"].value == "deep_work"
    assert "Fallback heurístico" in response.warnings[0]


def test_detail_gaps_candidate_ignores_due_at_requirement(db_session):
    task = make_task(db_session, "t1", "programar endpoint", scope="DELTA")

    assert "due_at" not in task_suggestions.missing_detail_fields(task)
    assert task_suggestions.is_candidate(task) is True


def test_provider_error_uses_heuristic_fallback(db_session, monkeypatch):
    captured = {}

    class Provider:
        def suggest_task_details(self, request):
            raise task_suggestions.llm.LLMError("OpenAI no respondió dentro del tiempo permitido.")

    def fake_classify(title, **kwargs):
        captured["llm_enabled"] = kwargs.get("llm_enabled")
        return TaskClassification(
            normalized_title=title,
            scope="Inbox",
            scope_confidence=0,
            effort_bucket="deep",
            estimated_minutes=120,
            context_bucket="deep_work",
            reason="fallback",
        )

    calls = {"count": 0}

    def fake_status(db, user_id):
        calls["count"] += 1
        if calls["count"] == 1:
            return ai_status(safe_to_use=True, status="ready", reason="Lista.")
        return ai_status(
            safe_to_use=False,
            status="error",
            reason="OpenAI no respondió dentro del tiempo permitido.",
        )

    monkeypatch.setattr("app.services.task_suggestions.llm.llm_status", fake_status)
    monkeypatch.setattr("app.services.task_suggestions.llm.get_llm_provider", lambda db, user_id: Provider())
    monkeypatch.setattr("app.services.task_suggestions.llm.record_llm_error", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.task_suggestions.classify_task_title", fake_classify)
    task = make_task(db_session, "t1", "implementar endpoint")

    response = task_suggestions.suggest_details(db_session, task)

    assert captured["llm_enabled"] is False
    assert response.source == "heuristic"
    assert response.llm_status == "error"
    assert "tiempo permitido" in response.warnings[0]


def test_structured_openai_suggestion_reports_openai_source(db_session, monkeypatch):
    class Provider:
        def suggest_task_details(self, request):
            return OpenAITaskSuggestion(
                priority_label="high",
                impact_score=5,
                effort_bucket="deep",
                estimated_minutes=180,
                context_bucket="deep_work",
                reasoning_summary="La tarea requiere concentración.",
            )

    monkeypatch.setattr(
        "app.services.task_suggestions.llm.llm_status",
        lambda db, user_id: ai_status(safe_to_use=True, status="ready", reason="Lista."),
    )
    monkeypatch.setattr("app.services.task_suggestions.llm.get_llm_provider", lambda db, user_id: Provider())
    monkeypatch.setattr("app.services.task_suggestions.llm.record_llm_success", lambda *args, **kwargs: None)
    task = make_task(db_session, "t1", "implementar endpoint")

    response = task_suggestions.suggest_details(db_session, task)

    assert response.source == "openai"
    assert response.llm_status == "ready"
    assert response.suggestions["impact_score"].value == 5


def test_structured_openai_unknown_values_are_not_exposed(db_session, monkeypatch):
    class Provider:
        def suggest_task_details(self, request):
            return OpenAITaskSuggestion(
                effort_bucket="unknown",
                context_bucket="unknown",
                reasoning_summary="No hay señal suficiente.",
            )

    monkeypatch.setattr(
        "app.services.task_suggestions.llm.llm_status",
        lambda db, user_id: ai_status(safe_to_use=True, status="ready", reason="Lista."),
    )
    monkeypatch.setattr("app.services.task_suggestions.llm.get_llm_provider", lambda db, user_id: Provider())
    monkeypatch.setattr("app.services.task_suggestions.llm.record_llm_success", lambda *args, **kwargs: None)
    task = make_task(db_session, "t1", "tarea ambigua", scope="Personal")

    response = task_suggestions.suggest_details(db_session, task)

    assert "effort_bucket" not in response.suggestions
    assert "context_bucket" not in response.suggestions
    assert any("no aplicables" in warning for warning in response.warnings)


def test_apply_suggestions_updates_only_selected_fields(db_session):
    task = make_task(db_session, "t1", "hacer reporte")

    result = task_suggestions.apply_suggestions(
        db_session,
        task,
        {
            "priority_label": "high",
            "effort_bucket": "deep",
            "estimated_minutes": 120,
            "context_bucket": "deep_work",
        },
        ["priority_label", "estimated_minutes"],
    )

    assert result.applied_fields == ["priority_label", "estimated_minutes"]
    assert result.task.priority_label == "high"
    assert result.task.estimated_minutes == 120
    assert result.task.effort_bucket is None
    assert result.task.context_bucket is None
    assert {"effort_bucket", "context_bucket"}.issubset(result.remaining_missing_fields)


def test_apply_suggestions_validates_invalid_values(db_session):
    task = make_task(db_session, "t1", "hacer reporte")

    result = task_suggestions.apply_suggestions(
        db_session,
        task,
        {"priority_label": "critical", "estimated_minutes": 30},
        ["priority_label", "estimated_minutes", "not_a_field"],
    )

    assert result.applied_fields == ["estimated_minutes"]
    assert [(item.field, item.reason) for item in result.skipped_fields] == [
        ("priority_label", "invalid_value"),
        ("not_a_field", "invalid_field"),
    ]


def test_apply_suggestions_enforces_task_string_limits(db_session):
    task = make_task(db_session, "t1", "hacer reporte")

    result = task_suggestions.apply_suggestions(
        db_session,
        task,
        {"normalized_title": "x" * 501, "notes": "n" * 10001, "effort_bucket": "quick"},
        ["normalized_title", "notes", "effort_bucket"],
        allow_existing_field_updates=True,
    )

    assert result.applied_fields == ["effort_bucket"]
    assert [(item.field, item.reason) for item in result.skipped_fields] == [
        ("normalized_title", "invalid_value"),
        ("notes", "invalid_value"),
    ]
    assert result.task.title == "hacer reporte"
    assert "notes" not in json.loads(result.task.metadata_json or "{}")


def test_apply_suggestions_does_not_overwrite_existing_fields_by_default(db_session):
    task = make_task(db_session, "t1", "hacer reporte", priority_label="low")

    skipped = task_suggestions.apply_suggestions(db_session, task, {"priority_label": "high"}, ["priority_label"])
    assert skipped.applied_fields == []
    assert skipped.skipped_fields[0].reason == "no_overwrite"

    updated = task_suggestions.apply_suggestions(
        db_session,
        task,
        {"priority_label": "high"},
        ["priority_label"],
        allow_existing_field_updates=True,
    )

    assert updated.task.priority_label == "high"


def test_apply_suggestions_rejects_invalid_effort_context_and_minutes(db_session):
    task = make_task(db_session, "t1", "hacer reporte")

    result = task_suggestions.apply_suggestions(
        db_session,
        task,
        {"effort_bucket": "huge", "context_bucket": "focus", "estimated_minutes": 2},
        ["effort_bucket", "context_bucket", "estimated_minutes"],
    )

    assert result.applied_fields == []
    assert [(item.field, item.reason) for item in result.skipped_fields] == [
        ("effort_bucket", "invalid_value"),
        ("context_bucket", "invalid_value"),
        ("estimated_minutes", "invalid_value"),
    ]


def test_due_at_not_suggested_without_date_signal(db_session):
    task = make_task(db_session, "t1", "programar endpoint", scope="DELTA")

    response = task_suggestions.suggest_details(db_session, task)

    assert "due_at" not in response.suggestions


def test_due_at_suggested_with_manana_signal(db_session):
    task = make_task(db_session, "t1", "comprar cafe mañana", scope="Personal")

    response = task_suggestions.suggest_details(db_session, task)

    assert response.suggestions["due_at"].value is not None


def test_apply_notes_persists_in_metadata_without_trello_write(db_session):
    task = make_task(db_session, "t1", "hacer reporte", metadata_json='{"classification":{"x":1}}')

    result = task_suggestions.apply_suggestions(db_session, task, {"notes": "Nota breve"}, ["notes"], source="openai")
    metadata = json.loads(result.task.metadata_json)

    assert metadata["classification"] == {"x": 1}
    assert metadata["notes"] == "Nota breve"
    assert result.task.source_type == "local"
    assert result.task.source_id is None
    event = db_session.query(TaskEvent).filter(TaskEvent.task_id == task.id, TaskEvent.event_type == "task_suggestions_applied").one()
    assert json.loads(event.payload_json) == {"fields": ["notes"], "source": "openai"}
    assert "Nota breve" not in event.payload_json


def test_apply_accepts_serialized_suggestion_items(db_session):
    task = make_task(db_session, "t1", "hacer reporte")

    updated = task_suggestions.apply_suggestions(
        db_session,
        task,
        {
            "effort_bucket": {
                "value": "deep",
                "confidence": 0.8,
                "reason": "Respuesta estructurada.",
                "applies_to_empty_field": True,
            }
        },
        ["effort_bucket"],
    )

    assert updated.task.effort_bucket == "deep"


def test_apply_regression_persists_recalculates_and_excludes_applied_fields(db_session, monkeypatch):
    class Provider:
        def suggest_task_details(self, request):
            return OpenAITaskSuggestion(
                effort_bucket="deep",
                estimated_minutes=90,
                context_bucket="admin",
                notes="Checklist breve.",
                reasoning_summary="Fixture determinística.",
            )

    monkeypatch.setattr(
        "app.services.task_suggestions.llm.llm_status",
        lambda db, user_id: ai_status(safe_to_use=True, status="ready", reason="Lista."),
    )
    monkeypatch.setattr("app.services.task_suggestions.llm.get_llm_provider", lambda db, user_id: Provider())
    monkeypatch.setattr("app.services.task_suggestions.llm.record_llm_success", lambda *args, **kwargs: None)
    task = make_task(
        db_session,
        "t1",
        "[AI APPLY REGRESSION] Documentar onboarding",
        scope="Personal",
        priority_label="medium",
        impact_score=3,
        urgency_score=2,
        blocking_score=1,
    )

    first = task_suggestions.suggest_details(db_session, task)
    before = task_suggestions.missing_detail_fields(task)
    result = task_suggestions.apply_suggestions(
        db_session,
        task,
        first.suggestions,
        ["effort_bucket", "estimated_minutes"],
        source=first.source,
    )
    fresh = db_session.get(Task, task.id)
    db_session.refresh(fresh)
    after = task_suggestions.missing_detail_fields(fresh)
    second = task_suggestions.suggest_details(db_session, fresh)

    assert {"effort_bucket", "estimated_minutes", "context_bucket", "notes"}.issubset(before)
    assert result.applied_fields == ["effort_bucket", "estimated_minutes"]
    assert result.task.effort_bucket == "deep"
    assert result.task.estimated_minutes == 90
    assert "effort_bucket" not in after
    assert "estimated_minutes" not in after
    assert {"context_bucket", "notes"}.issubset(after)
    assert "effort_bucket" not in second.suggestions
    assert "estimated_minutes" not in second.suggestions
    assert {"context_bucket", "notes"}.issubset(second.suggestions)


def test_apply_same_selection_is_idempotent_and_emits_one_event(db_session):
    task = make_task(db_session, "t1", "hacer reporte")
    suggestions = {"effort_bucket": {"value": "deep", "confidence": 0.8, "reason": "Fixture."}}

    first = task_suggestions.apply_suggestions(db_session, task, suggestions, ["effort_bucket"], source="heuristic")
    first_updated_at = first.task.updated_at
    second = task_suggestions.apply_suggestions(db_session, task, suggestions, ["effort_bucket"], source="heuristic")
    events = db_session.query(TaskEvent).filter(TaskEvent.task_id == task.id, TaskEvent.event_type == "task_suggestions_applied").all()

    assert first.applied_fields == ["effort_bucket"]
    assert second.applied_fields == []
    assert [(item.field, item.reason) for item in second.skipped_fields] == [("effort_bucket", "already_set")]
    assert second.task.updated_at == first_updated_at
    assert len(events) == 1


def test_unconfigured_scope_does_not_recommend_a_trello_card(db_session):
    task = make_task(db_session, "t1", "implementar sync", scope="BETA", source_type="local", source_id=None)

    response = task_suggestions.suggest_details(db_session, task)

    assert "trello_card_recommendation" not in response.suggestions
    assert task.source_id is None


def test_suggest_details_passes_trello_description_and_checklist_context(db_session, monkeypatch):
    captured = {}

    class Provider:
        def suggest_task_details(self, request):
            captured["context"] = request.context
            return OpenAITaskSuggestion(
                impact_score=5,
                urgency_score=4,
                blocking_score=2,
                reasoning_summary="El contexto indica trabajo relevante.",
            )

    monkeypatch.setattr(
        "app.services.task_suggestions.llm.llm_status",
        lambda db, user_id: SimpleNamespace(safe_to_use=True, status="ready", reason="Lista."),
    )
    monkeypatch.setattr("app.services.task_suggestions.llm.get_llm_provider", lambda db, user_id: Provider())
    monkeypatch.setattr("app.services.task_suggestions.llm.record_llm_success", lambda *args, **kwargs: None)
    task = make_task(
        db_session,
        "t1",
        "Inventory Overview",
        source_type="trello",
        source_id="card-1",
        scope="DELTA",
        metadata_json=json.dumps(
            {
                "trello": {
                    "description": "Actualizar lógica de producción.",
                    "checklists": [{"items": [{"name": "Validar edge cases", "state": "incomplete"}]}],
                }
            }
        ),
    )

    response = task_suggestions.suggest_details(db_session, task)

    assert "Actualizar lógica de producción" in captured["context"]
    assert "Validar edge cases" in captured["context"]
    assert response.suggestions["impact_score"].value == 5


def test_create_and_apply_suggestion_confirmation(db_session, monkeypatch):
    monkeypatch.setattr("app.services.settings_service.llm_enabled", lambda db: False)
    task = make_task(db_session, "t1", "implementar endpoint de Project Delta", scope="DELTA")

    confirmations, skipped = task_suggestions.create_suggestion_confirmations(db_session, [task])

    assert skipped == 0
    assert len(confirmations) == 1
    confirmation = confirmations[0]
    assert confirmation.action_type == task_suggestions.APPLY_SUGGESTIONS_ACTION
    assert "implementar endpoint de Project Delta" in (confirmation.summary or "")

    message = task_suggestions.apply_suggestion_confirmation(db_session, confirmation)
    db_session.refresh(task)

    assert "Sugerencias aplicadas" in message
    assert task.effort_bucket == "deep"
    assert confirmation.status == "confirmed"
    event = db_session.query(TaskEvent).filter(TaskEvent.task_id == task.id, TaskEvent.event_type == "task_suggestions_applied").one()
    assert json.loads(event.payload_json)["source"] == "heuristic"


def test_incomplete_tasks_returns_missing_metadata(db_session):
    make_task(db_session, "t1", "vacía")
    make_task(db_session, "t2", "completa", priority_label="high", effort_bucket="quick", estimated_minutes=15, context_bucket="quick_task", impact_score=3, urgency_score=2, blocking_score=1, metadata_json='{"notes":"ok"}')

    result, total = task_suggestions.incomplete_tasks(db_session.query(Task).all())

    assert [item["id"] for item in result] == ["t1"]
    assert total == 1
    assert "priority_label" in result[0]["missing_fields"]
    assert "notes" in result[0]["missing_fields"]


def test_incomplete_tasks_excludes_completed_deleted_and_due_at_alone(db_session):
    make_task(db_session, "active", "activa", priority_label=None, impact_score=3)
    make_task(db_session, "completed", "completa vieja", status="completed")
    make_task(db_session, "deleted", "borrada", status="deleted")
    make_task(
        db_session,
        "due_only",
        "solo deadline",
        priority_label="high",
        effort_bucket="quick",
        estimated_minutes=15,
        context_bucket="quick_task",
        impact_score=3,
        urgency_score=3,
        blocking_score=1,
        due_at="2026-07-10T12:00:00+00:00",
        metadata_json='{"notes":"ok"}',
    )

    result, total = task_suggestions.incomplete_tasks(db_session.query(Task).all())

    assert [item["id"] for item in result] == ["active"]
    assert total == 1


def test_bulk_suggest_returns_results_and_source_summary(db_session):
    tasks = [make_task(db_session, "t1", "programar endpoint", scope="DELTA"), make_task(db_session, "t2", "comprar cafe mañana", scope="Personal")]

    response = task_suggestions.suggest_details_bulk(db_session, tasks)

    assert response.mode == "sync"
    assert response.source_summary["heuristic"] == 2
    assert [item.ok for item in response.results] == [True, True]
    assert response.results[0].suggestion.task_id == "t1"


def test_bulk_suggest_per_task_error_does_not_fail_batch(db_session, monkeypatch):
    tasks = [make_task(db_session, "t1", "programar endpoint"), make_task(db_session, "t2", "comprar cafe")]
    original = task_suggestions.suggest_details

    def fake_suggest(db, task, **kwargs):
        if task.id == "t2":
            raise RuntimeError("boom token=secret")
        return original(db, task, **kwargs)

    monkeypatch.setattr(task_suggestions, "suggest_details", fake_suggest)

    response = task_suggestions.suggest_details_bulk(db_session, tasks)

    assert response.source_summary["error"] == 1
    assert response.results[0].ok is True
    assert response.results[1].ok is False
    assert "secret" not in response.results[1].error


def test_bulk_apply_updates_multiple_tasks_and_reports_partial_failure(db_session):
    t1 = make_task(db_session, "t1", "programar endpoint")
    t2 = make_task(db_session, "t2", "hacer reporte", priority_label="low")

    response = task_suggestions.apply_suggestions_bulk(
        db_session,
        {"t1": t1, "t2": t2},
        [
            BulkTaskSuggestionApplyItem(task_id="t1", suggestions={"priority_label": "high"}, fields=["priority_label"]),
            BulkTaskSuggestionApplyItem(task_id="t2", suggestions={"priority_label": "high"}, fields=["priority_label"]),
        ],
    )

    db_session.refresh(t1)
    db_session.refresh(t2)
    assert response.applied == 1
    assert response.errors == []
    assert response.results[1].applied_fields == []
    assert response.results[1].skipped_fields[0].reason == "no_overwrite"
    assert t1.priority_label == "high"
    assert t2.priority_label == "low"


def test_bulk_apply_ignores_trello_recommendation_and_never_creates_card(db_session):
    task = make_task(db_session, "t1", "programar sync", scope="BETA", source_type="local", source_id=None)

    response = task_suggestions.apply_suggestions_bulk(
        db_session,
        {"t1": task},
        [
            BulkTaskSuggestionApplyItem(
                task_id="t1",
                suggestions={
                    "priority_label": "medium_high",
                    "trello_card_recommendation": {"recommended": True, "board_alias": "BETA"},
                },
                fields=["priority_label", "trello_card_recommendation"],
            )
        ],
    )

    db_session.refresh(task)
    assert response.applied == 1
    assert task.priority_label == "medium_high"
    assert task.source_id is None


def test_bulk_apply_recalculates_candidate_threshold_per_task(db_session):
    complete_defaults = {
        "priority_label": "medium",
        "impact_score": 3,
        "urgency_score": 2,
        "blocking_score": 1,
        "metadata_json": '{"notes":"lista"}',
    }
    removed = make_task(db_session, "removed", "documentar", context_bucket="admin", **complete_defaults)
    retained = make_task(db_session, "retained", "revisar", **complete_defaults)

    response = task_suggestions.apply_suggestions_bulk(
        db_session,
        {"removed": removed, "retained": retained},
        [
            BulkTaskSuggestionApplyItem(
                task_id="removed",
                suggestions={"effort_bucket": "quick", "estimated_minutes": 20},
                fields=["effort_bucket", "estimated_minutes"],
            ),
            BulkTaskSuggestionApplyItem(
                task_id="retained",
                suggestions={"effort_bucket": "deep"},
                fields=["effort_bucket"],
            ),
        ],
    )
    results = {result.task.id: result for result in response.results}

    assert results["removed"].is_candidate is False
    assert results["removed"].remaining_missing_fields == []
    assert results["retained"].is_candidate is True
    assert {"estimated_minutes", "context_bucket"}.issubset(results["retained"].remaining_missing_fields)
    assert "effort_bucket" not in results["retained"].remaining_missing_fields


def test_bulk_suggest_endpoint_rejects_more_than_25_ids(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/tasks/suggest-details-bulk",
                json={"task_ids": [f"t{i}" for i in range(26)], "limit": 25},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 400


def make_task(db_session, task_id, title, **kwargs):
    now = utc_now_iso()
    task = Task(
        id=task_id,
        title=title,
        status=kwargs.pop("status", "active"),
        task_kind="normal",
        source_type=kwargs.pop("source_type", "local"),
        source_id=kwargs.pop("source_id", None),
        scope=kwargs.pop("scope", "Inbox"),
        manual_order=kwargs.pop("manual_order", 1),
        first_seen_at=now,
        created_at=now,
        updated_at=now,
        **kwargs,
    )
    db_session.add(task)
    db_session.commit()
    return task
