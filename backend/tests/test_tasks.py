import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.main import app
from app.models.confirmations import PendingConfirmation
from app.models.tasks import TaskEvent
from app.schemas.tasks import BulkTaskCreate, TaskCreate, TaskUpdate
from app.services.tasks import (
    bulk_create_tasks,
    complete_task,
    create_task,
    is_trello_linked_task,
    list_tasks,
    reorder_tasks,
    restore_task,
    soft_delete_task,
    update_task,
)


def test_create_task_infers_scope_and_deadline(db_session):
    task = create_task(db_session, TaskCreate(title="domingo ver peli con mi novia"))

    assert task.title == "ver peli con mi novia"
    assert task.scope == "Personal"
    assert task.due_at is not None
    assert task.status == "active"


def test_bulk_create_tasks(db_session):
    tasks = bulk_create_tasks(db_session, BulkTaskCreate(text="- revisar endpoint DELTA\n- comprar café"))

    assert [task.scope for task in tasks] == ["Inbox", "Personal"]
    assert [task.manual_order for task in tasks] == [1, 2]


def test_complete_soft_delete_restore_and_events(db_session):
    task = create_task(db_session, TaskCreate(title="Change AFN MFN to bool"))

    completed = complete_task(db_session, task)
    assert completed.status == "completed"
    assert completed.completed_at is not None

    deleted = soft_delete_task(db_session, completed)
    assert deleted.status == "deleted"
    assert deleted.deleted_at is not None

    restored = restore_task(db_session, deleted)
    assert restored.status == "active"
    assert restored.deleted_at is None

    events = db_session.scalars(select(TaskEvent.event_type).order_by(TaskEvent.created_at)).all()
    assert {"created", "completed", "deleted", "restored"}.issubset(set(events))


def test_trello_linked_helper_requires_card_id(db_session):
    task = create_task(db_session, TaskCreate(title="Comprar café", scope="Personal"))
    assert is_trello_linked_task(task) is False

    task.scope = "BETA"
    task.source_type = "local"
    task.source_id = None
    assert is_trello_linked_task(task) is False

    task.source_type = "trello"
    task.source_id = None
    assert is_trello_linked_task(task) is False

    task.source_id = "card-1"
    assert is_trello_linked_task(task) is True


def test_local_trello_backed_scope_completes_locally(db_session):
    task = create_task(db_session, TaskCreate(title="Preparar landing", scope="BETA"))

    completed = complete_task(db_session, task)

    assert completed.status == "completed"
    assert completed.source_type == "local"
    assert completed.source_id is None


def test_update_rename_and_reorder(db_session):
    first = create_task(db_session, TaskCreate(title="Primera"))
    second = create_task(db_session, TaskCreate(title="Segunda"))

    renamed = update_task(db_session, first, TaskUpdate(title="Primera editada"))
    assert renamed.title == "Primera editada"

    reordered = reorder_tasks(db_session, [second.id, first.id])
    assert [task.id for task in reordered] == [second.id, first.id]
    assert [task.manual_order for task in reordered] == [1, 2]


def test_update_task_notes_persists_in_metadata(db_session):
    task = create_task(db_session, TaskCreate(title="Anotar detalle"))

    updated = update_task(db_session, task, TaskUpdate(notes="Línea uno\nLínea dos"))

    metadata = json.loads(updated.metadata_json)
    assert metadata["notes"] == "Línea uno\nLínea dos"
    events = db_session.scalars(select(TaskEvent.event_type).order_by(TaskEvent.created_at)).all()
    assert "corrected" in events


def test_patch_task_endpoint_updates_notes(db_session):
    task = create_task(db_session, TaskCreate(title="Endpoint notas"))
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).patch(f"/api/tasks/{task.id}", json={"notes": "Nota endpoint"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert json.loads(response.json()["metadata_json"])["notes"] == "Nota endpoint"


def test_list_tasks_endpoint_filters_active_inbox(db_session):
    inbox = create_task(db_session, TaskCreate(title="Inbox filtrada", scope="Inbox", auto_classify=False))
    create_task(db_session, TaskCreate(title="Personal filtrada", scope="Personal", auto_classify=False))
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/tasks?status=active&scope=Inbox")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert [task["id"] for task in payload["tasks"]] == [inbox.id]


def test_update_task_metadata_fields_validate_and_persist(db_session):
    task = create_task(db_session, TaskCreate(title="Completar metadata"))

    updated = update_task(
        db_session,
        task,
        TaskUpdate(
            priority_label="medium_high",
            impact_score=4,
            urgency_score=3,
            blocking_score=2,
            effort_bucket="deep",
            estimated_minutes=120,
            context_bucket="deep_work",
        ),
    )

    assert updated.priority_label == "medium_high"
    assert updated.impact_score == 4
    assert updated.urgency_score == 3
    assert updated.blocking_score == 2
    assert updated.effort_bucket == "deep"
    assert updated.estimated_minutes == 120
    assert updated.context_bucket == "deep_work"


def test_update_task_from_inbox_creates_processed_event(db_session):
    task = create_task(db_session, TaskCreate(title="Procesar inbox", scope="Inbox", auto_classify=False))

    updated = update_task(db_session, task, TaskUpdate(scope="Personal", notes="Listo para hacer"))

    assert updated.scope == "Personal"
    events = db_session.scalars(select(TaskEvent.event_type).order_by(TaskEvent.created_at)).all()
    assert "inbox_processed" in events


def test_update_inbox_task_to_trello_scope_does_not_create_trello_confirmation(db_session):
    task = create_task(db_session, TaskCreate(title="Procesar a ALPHA", scope="Inbox", auto_classify=False))

    updated = update_task(db_session, task, TaskUpdate(scope="ALPHA"))

    assert updated.scope == "ALPHA"
    assert updated.source_type == "local"
    assert updated.source_id is None
    assert db_session.scalars(select(PendingConfirmation)).all() == []


def test_update_task_rejects_invalid_scores_and_minutes():
    with pytest.raises(ValidationError):
        TaskUpdate(impact_score=6)
    with pytest.raises(ValidationError):
        TaskUpdate(estimated_minutes=0)


def test_search_active_tasks(db_session):
    create_task(db_session, TaskCreate(title="Transactions API endpoint DELTA"))
    create_task(db_session, TaskCreate(title="Comprar café"))

    results = list_tasks(db_session, q="transactions")

    assert len(results) == 1
    assert results[0].scope == "Inbox"
