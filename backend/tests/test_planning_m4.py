from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models.confirmations import PendingConfirmation
from app.schemas.tasks import TaskCreate
from app.services.planning import apply_sort, now_plan, propose_sort, today_plan
from app.services.tasks import create_task, list_tasks


def test_today_groups_tasks(db_session):
    create_task(db_session, TaskCreate(title="implementar endpoint", scope="Client Work", auto_classify=False))
    create_task(db_session, TaskCreate(title="comprar café"))
    create_task(db_session, TaskCreate(title="sin contexto raro"))

    plan = today_plan(db_session)
    keys = {group.key for group in plan.groups}

    assert "deep_work" in keys
    assert "quick_wins" in keys
    assert "inbox" in keys


def test_now_returns_recommendation(db_session):
    create_task(db_session, TaskCreate(title="comprar café"))

    plan = now_plan(db_session)

    assert plan.recommended
    assert plan.recommended[0].task.title == "comprar café"
    assert plan.summary


def test_today_plan_groups_overdue_today_quick_deep_and_inbox(db_session):
    now = datetime.now(ZoneInfo(settings.app_timezone))
    create_task(db_session, TaskCreate(title="M18E vencida", scope="Personal", due_at=(now - timedelta(days=1)).isoformat(), priority_label="high"))
    create_task(db_session, TaskCreate(title="M18E hoy", scope="Personal", due_at=now.replace(hour=18, minute=0).isoformat()))
    create_task(db_session, TaskCreate(title="M18E quick", scope="Personal", effort_bucket="quick", estimated_minutes=15))
    create_task(db_session, TaskCreate(title="M18E deep", scope="DELTA", effort_bucket="deep", estimated_minutes=90, impact_score=4))
    create_task(db_session, TaskCreate(title="M18E inbox", scope="Inbox", auto_classify=False))

    plan = today_plan(db_session)
    keys = {group.key for group in plan.groups}

    assert {"overdue", "today", "quick_wins", "deep_work", "inbox"}.issubset(keys)
    assert "undefined" not in plan.model_dump_json()
    assert plan.summary


def test_now_plan_prefers_overdue_and_offers_alternatives_without_writes(db_session):
    now = datetime.now(ZoneInfo(settings.app_timezone))
    overdue = create_task(
        db_session,
        TaskCreate(title="M18E urgente", scope="DELTA", due_at=(now - timedelta(days=1)).isoformat(), priority_label="high", impact_score=5, urgency_score=5),
    )
    create_task(db_session, TaskCreate(title="M18E quick", scope="Personal", effort_bucket="quick", estimated_minutes=15, context_bucket="admin"))
    create_task(db_session, TaskCreate(title="M18E deep", scope="DELTA", effort_bucket="deep", estimated_minutes=90, impact_score=4))
    create_task(db_session, TaskCreate(title="M18E inbox", scope="Inbox", auto_classify=False))

    before_confirmations = db_session.query(PendingConfirmation).count()
    plan = now_plan(db_session)

    assert plan.recommended[0].task.id == overdue.id
    assert {item.kind for item in plan.alternatives} >= {"quick_win", "deep_work", "inbox_cleanup"}
    assert db_session.query(PendingConfirmation).count() == before_confirmations


def test_sort_propose_does_not_apply_until_confirmation(db_session):
    low = create_task(db_session, TaskCreate(title="comprar café", priority_label="low"))
    high = create_task(db_session, TaskCreate(title="implementar endpoint Project Delta", priority_label="high"))

    proposal = propose_sort(db_session)
    assert proposal.tasks[0].id == high.id
    assert [task.id for task in list_tasks(db_session)] == [low.id, high.id]

    apply_sort(db_session, proposal.confirmation_id)
    assert [task.id for task in list_tasks(db_session)] == [high.id, low.id]


def test_sort_propose_includes_reasons_and_excludes_non_sortable(db_session):
    low = create_task(db_session, TaskCreate(title="comprar café", priority_label="low"))
    high = create_task(db_session, TaskCreate(title="implementar endpoint Project Delta", priority_label="high"))
    attention = create_task(db_session, TaskCreate(title="attention", priority_label="high"))
    attention.task_kind = "attention"
    db_session.commit()

    proposal = propose_sort(db_session)

    assert [item.task_id for item in proposal.items] == [high.id, low.id]
    assert all(item.reason for item in proposal.items)
    assert attention.id in proposal.task_ids
