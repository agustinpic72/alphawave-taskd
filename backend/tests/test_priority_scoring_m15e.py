from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.schemas.tasks import TaskCreate
from app.services import settings_service
from app.services.planning import SUPPORTED_PRIORITY_CRITERIA, score_task
from app.services.tasks import create_task


def _dt(days: int = 0) -> str:
    return (datetime.now(ZoneInfo(settings.app_timezone)) + timedelta(days=days)).isoformat()


def _task(db_session, title: str, **kwargs):
    return create_task(db_session, TaskCreate(title=title, auto_classify=False, **kwargs))


def _criteria(**patches):
    criteria = {key: {**value} for key, value in settings_service.DEFAULT_PRIORITY_CRITERIA.items()}
    for key, patch in patches.items():
        criteria[key] = {**criteria[key], **patch}
    return {"criteria": criteria}


def _factor(explanation, criterion: str):
    return next((factor for factor in explanation.factors if factor.criterion == criterion), None)


def test_due_today_scores_above_no_due(db_session):
    due = _task(db_session, "vence hoy", due_at=_dt(0))
    plain = _task(db_session, "sin fecha")

    assert score_task(due).score > score_task(plain).score
    assert _factor(score_task(due), "due_date").label == "Vence hoy"


def test_overdue_scores_above_future(db_session):
    overdue = _task(db_session, "vencida", due_at=_dt(-2))
    future = _task(db_session, "futura", due_at=_dt(14))

    assert score_task(overdue).score > score_task(future).score
    assert _factor(score_task(overdue), "due_date").label == "Vencida"


def test_scores_use_impact_urgency_and_blocking(db_session):
    rich = _task(db_session, "rica", impact_score=5, urgency_score=4, blocking_score=3)
    plain = _task(db_session, "plain")
    explanation = score_task(rich)

    assert explanation.score > score_task(plain).score
    assert _factor(explanation, "impact")
    assert _factor(explanation, "urgency")
    assert _factor(explanation, "blocking")


def test_stale_in_progress_increases_score_after_threshold(db_session):
    task = _task(db_session, "estancada")
    task.trello_state = "in_progress"
    task.last_trello_activity_at = _dt(-5)
    db_session.commit()

    explanation = score_task(task)

    assert _factor(explanation, "stale_in_progress")
    assert "sin movimiento" in _factor(explanation, "stale_in_progress").reason


def test_age_increases_gradually_but_is_capped(db_session):
    old = _task(db_session, "vieja")
    older = _task(db_session, "muy vieja")
    old.first_seen_at = _dt(-21)
    older.first_seen_at = _dt(-120)
    db_session.commit()

    old_age = _factor(score_task(old), "age")
    older_age = _factor(score_task(older), "age")

    assert old_age.contribution > 0
    assert older_age.contribution <= 12
    assert older_age.contribution >= old_age.contribution


def test_effort_quick_and_deep_contribute_as_designed(db_session):
    quick = _task(db_session, "quick", effort_bucket="quick")
    deep_low = _task(db_session, "deep low", effort_bucket="deep")
    deep_high = _task(db_session, "deep high", effort_bucket="deep", impact_score=5)

    assert _factor(score_task(quick, mode="now"), "effort").direction == "up"
    assert _factor(score_task(deep_low), "effort").direction == "down"
    assert _factor(score_task(deep_high), "effort").direction == "up"


def test_source_and_manual_priority_contribute_separately(db_session):
    manual = _task(db_session, "manual", priority_label="high")
    source = _task(db_session, "source", priority_label="high")
    source.source_type = "trello"
    db_session.commit()

    assert _factor(score_task(manual), "manual_priority")
    assert _factor(score_task(manual), "source_priority") is None
    assert _factor(score_task(source), "source_priority")
    assert _factor(score_task(source), "manual_priority") is None


def test_disabled_criterion_has_no_contribution(db_session):
    due = _task(db_session, "vence hoy", due_at=_dt(0))
    explanation = score_task(due, _criteria(due_date={"enabled": False}))

    assert _factor(explanation, "due_date") is None


def test_reordering_criteria_changes_weight_deterministically(db_session):
    task = _task(db_session, "impacto y deadline", due_at=_dt(0), impact_score=5)
    due_first = score_task(task, _criteria(due_date={"weight": 100}, impact={"weight": 10}))
    impact_first = score_task(task, _criteria(due_date={"weight": 10}, impact={"weight": 100}))

    assert _factor(due_first, "due_date").contribution > _factor(impact_first, "due_date").contribution
    assert _factor(impact_first, "impact").contribution > _factor(due_first, "impact").contribution


def test_explainability_includes_summary_factors_and_warnings(db_session):
    task = _task(db_session, "sin metadata")
    explanation = score_task(task)

    assert explanation.summary
    assert isinstance(explanation.factors, list)
    assert "Sin urgencia definida." in explanation.warnings
    assert "Sin impacto definido." in explanation.warnings
    assert "Sin estimación de esfuerzo." in explanation.warnings


def test_visible_priority_settings_are_implemented():
    assert set(settings_service.DEFAULT_PRIORITY_CRITERIA) == set(SUPPORTED_PRIORITY_CRITERIA)
