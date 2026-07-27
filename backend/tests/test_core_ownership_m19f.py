from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.confirmations import PendingConfirmation
from app.models.reminders import Reminder
from app.models.tasks import Task
from app.schemas.reminders import ReminderCreate
from app.schemas.tasks import TaskCreate
from app.services import auth as auth_service
from app.services import ownership
from app.services.reminders import create_reminder, due_reminders
from app.services.tasks import create_task, list_tasks
from app.services.trello_sync import _upsert_task


PASSWORD = "correct horse battery"


def _auth_clients(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    user_a = auth_service.create_owner(db_session, email="a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(db_session, email="b@example.com", password=PASSWORD, allow_existing=True)
    app.dependency_overrides[get_db] = lambda: db_session
    client_a = TestClient(app)
    client_b = TestClient(app)
    login_a = client_a.post("/api/auth/login", json={"email": "a@example.com", "password": PASSWORD})
    login_b = client_b.post("/api/auth/login", json={"email": "b@example.com", "password": PASSWORD})
    assert login_a.status_code == 200
    assert login_b.status_code == 200
    return user_a, user_b, client_a, {"X-CSRF-Token": login_a.json()["csrf_token"]}, client_b, {"X-CSRF-Token": login_b.json()["csrf_token"]}


def test_tasks_api_isolates_user_a_from_user_b(db_session, monkeypatch):
    _user_a, _user_b, client_a, headers_a, client_b, headers_b = _auth_clients(db_session, monkeypatch)
    try:
        created = client_a.post("/api/tasks", json={"title": "A private task", "auto_classify": False}, headers=headers_a)
        task_id = created.json()["id"]
        list_b = client_b.get("/api/tasks")
        update_b = client_b.patch(f"/api/tasks/{task_id}", json={"title": "B takeover"}, headers=headers_b)
        complete_b = client_b.post(f"/api/tasks/{task_id}/complete", headers=headers_b)
        update_a = client_a.patch(f"/api/tasks/{task_id}", json={"title": "A updated"}, headers=headers_a)
        complete_a = client_a.post(f"/api/tasks/{task_id}/complete", headers=headers_a)
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert list_b.status_code == 200
    assert "A private task" not in str(list_b.json())
    assert update_b.status_code == 404
    assert complete_b.status_code == 404
    assert update_a.status_code == 200
    assert complete_a.status_code == 200


def test_reminders_api_and_worker_keep_user_scope(db_session, monkeypatch):
    user_a, user_b, client_a, headers_a, client_b, headers_b = _auth_clients(db_session, monkeypatch)
    now = datetime.now()
    try:
        created = client_a.post(
            "/api/reminders",
            json={"message": "A reminder", "remind_at": (now - timedelta(minutes=1)).isoformat(), "source": "ui"},
            headers=headers_a,
        )
        reminder_id = created.json()["id"]
        list_b = client_b.get("/api/reminders")
        cancel_b = client_b.post(f"/api/reminders/{reminder_id}/cancel", headers=headers_b)
        due_a = due_reminders(db_session, now.isoformat(), user_id=user_a.id)
        due_b = due_reminders(db_session, now.isoformat(), user_id=user_b.id)
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert list_b.json()["reminders"] == []
    assert cancel_b.status_code == 404
    assert [reminder.message for reminder in due_a] == ["A reminder"]
    assert due_b == []


def test_confirmations_api_isolates_list_confirm_and_cancel(db_session, monkeypatch):
    _user_a, _user_b, client_a, headers_a, client_b, headers_b = _auth_clients(db_session, monkeypatch)
    try:
        client_a.post("/api/tasks", json={"title": "A low", "priority_label": "low", "auto_classify": False}, headers=headers_a)
        client_a.post("/api/tasks", json={"title": "A high", "priority_label": "high", "auto_classify": False}, headers=headers_a)
        proposal = client_a.post("/api/tasks/sort/propose", headers=headers_a)
        confirmation_id = proposal.json()["confirmation_id"]
        list_b = client_b.get("/api/confirmations")
        confirm_b = client_b.post(f"/api/confirmations/{confirmation_id}/confirm", headers=headers_b)
        cancel_b = client_b.post(f"/api/confirmations/{confirmation_id}/cancel", headers=headers_b)
        confirm_a = client_a.post(f"/api/confirmations/{confirmation_id}/confirm", headers=headers_a)
    finally:
        app.dependency_overrides.clear()

    assert proposal.status_code == 200
    assert list_b.json()["confirmations"] == []
    assert confirm_b.status_code == 404
    assert cancel_b.status_code == 404
    assert confirm_a.status_code == 200


def test_planning_suggestions_and_inbox_are_user_scoped(db_session, monkeypatch):
    _user_a, _user_b, client_a, headers_a, client_b, headers_b = _auth_clients(db_session, monkeypatch)
    due_at = (datetime.now() + timedelta(hours=2)).isoformat()
    try:
        task = client_a.post(
            "/api/tasks",
            json={"title": "A planning task", "scope": "Inbox", "due_at": due_at, "auto_classify": False},
            headers=headers_a,
        ).json()
        today_b = client_b.get("/api/planning/today")
        inbox_b = client_b.get("/api/planning/inbox-suggestions")
        incomplete_b = client_b.get("/api/tasks/incomplete-details")
        suggest_b = client_b.post(f"/api/tasks/{task['id']}/suggest-details", headers=headers_b)
        apply_b = client_b.post(
            f"/api/tasks/{task['id']}/apply-suggestions",
            json={"suggestions": {"priority_label": "high"}, "fields": ["priority_label"], "source": "heuristic"},
            headers=headers_b,
        )
        manual_b = client_b.post(
            f"/api/tasks/{task['id']}/complete-details",
            json={
                "expected_updated_at": task["updated_at"],
                "expected_version_token": "not-visible-to-user-b",
                "effort_bucket": "deep",
            },
            headers=headers_b,
        )
        apply_a = client_a.post(
            f"/api/tasks/{task['id']}/apply-suggestions",
            json={"suggestions": {"priority_label": "high"}, "fields": ["priority_label"], "source": "heuristic"},
            headers=headers_a,
        )
    finally:
        app.dependency_overrides.clear()

    assert "A planning task" not in str(today_b.json())
    assert inbox_b.json()["suggestions"] == []
    assert "A planning task" not in str(incomplete_b.json())
    assert suggest_b.status_code == 404
    assert apply_b.status_code == 404
    assert manual_b.status_code == 404
    assert apply_a.status_code == 200
    assert apply_a.json()["task"]["priority_label"] == "high"
    assert apply_a.json()["applied_fields"] == ["priority_label"]


def test_legacy_backfill_and_default_writes_get_single_owner(db_session):
    task = create_task(db_session, TaskCreate(title="owned by default", auto_classify=False))
    reminder = create_reminder(db_session, ReminderCreate(message="owned reminder", remind_at=datetime.now().isoformat(), source="ui"))
    legacy = Task(
        id="legacy-task",
        user_id=None,
        title="legacy",
        status="active",
        task_kind="normal",
        source_type="local",
        scope="Inbox",
        manual_order=10,
        first_seen_at=datetime.now().isoformat(),
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    confirmation = PendingConfirmation(
        id="legacy-confirmation",
        user_id=None,
        source="ui",
        action_type="noop",
        payload_json="{}",
        status="pending",
        created_at=datetime.now().isoformat(),
        expires_at=(datetime.now() + timedelta(hours=1)).isoformat(),
    )
    db_session.add_all([legacy, confirmation])
    db_session.commit()

    owner_id = ownership.backfill_core_user_ids(db_session)

    assert task.user_id == owner_id
    assert reminder.user_id == owner_id
    assert db_session.get(Task, "legacy-task").user_id == owner_id
    assert db_session.get(PendingConfirmation, "legacy-confirmation").user_id == owner_id
    assert [item.id for item in list_tasks(db_session, user_id=owner_id)]


def test_trello_sync_created_task_gets_single_owner(db_session):
    board = type("Board", (), {"alias": "ALPHA", "board_id": "board-1", "name": "Project Alpha"})()
    card = {
        "id": "card-1",
        "name": "Trello owned",
        "idList": "list-1",
        "shortUrl": "https://trello.test/c/card-1",
        "idMembers": [],
        "desc": "",
    }

    created = _upsert_task(
        db_session,
        board,
        card,
        {"name": "Tasks", "state": "pending"},
        "pending",
        "normal",
        [],
        None,
        user_id=ownership.integration_owner_user_id(db_session, "trello"),
    )
    db_session.flush()

    task = db_session.query(Task).filter(Task.source_id == "card-1").one()
    assert created is True
    assert task.user_id == ownership.integration_owner_user_id(db_session, "trello")
