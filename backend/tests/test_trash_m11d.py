from fastapi.testclient import TestClient

from app.core.db import get_db
from app.main import app
from app.models.tasks import Task
from app.schemas.tasks import TaskCreate
from app.services.tasks import complete_task, create_task, list_tasks, soft_delete_task


def test_deleted_task_can_be_permanently_deleted(db_session):
    task = soft_delete_task(db_session, create_task(db_session, TaskCreate(title="M11D delete me")))

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.delete(f"/api/tasks/{task.id}/permanent")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"deleted_count": 1}
    assert db_session.get(Task, task.id) is None


def test_permanent_delete_active_task_returns_error(db_session):
    task = create_task(db_session, TaskCreate(title="M11D keep active"))

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.delete(f"/api/tasks/{task.id}/permanent")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert db_session.get(Task, task.id) is not None


def test_permanent_delete_completed_task_returns_error(db_session):
    task = complete_task(db_session, create_task(db_session, TaskCreate(title="M11D keep completed")))

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.delete(f"/api/tasks/{task.id}/permanent")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert db_session.get(Task, task.id).status == "completed"


def test_bulk_permanent_delete_rejects_non_deleted_safely(db_session):
    deleted = soft_delete_task(db_session, create_task(db_session, TaskCreate(title="M11D deleted")))
    active = create_task(db_session, TaskCreate(title="M11D active"))

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/tasks/trash/delete-bulk", json={"task_ids": [deleted.id, active.id]})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert db_session.get(Task, deleted.id) is not None
    assert db_session.get(Task, active.id) is not None


def test_bulk_permanent_delete_deletes_deleted_tasks(db_session):
    first = soft_delete_task(db_session, create_task(db_session, TaskCreate(title="M11D bulk 1")))
    second = soft_delete_task(db_session, create_task(db_session, TaskCreate(title="M11D bulk 2")))

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/tasks/trash/delete-bulk", json={"task_ids": [first.id, second.id]})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"deleted_count": 2}
    assert db_session.get(Task, first.id) is None
    assert db_session.get(Task, second.id) is None


def test_empty_trash_deletes_only_deleted_tasks(db_session):
    active = create_task(db_session, TaskCreate(title="M11D active"))
    completed = complete_task(db_session, create_task(db_session, TaskCreate(title="M11D completed")))
    first = soft_delete_task(db_session, create_task(db_session, TaskCreate(title="M11D trash 1")))
    second = soft_delete_task(db_session, create_task(db_session, TaskCreate(title="M11D trash 2")))

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/tasks/trash/empty")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"deleted_count": 2}
    assert db_session.get(Task, first.id) is None
    assert db_session.get(Task, second.id) is None
    assert db_session.get(Task, active.id).status == "active"
    assert db_session.get(Task, completed.id).status == "completed"


def test_restore_bulk_restores_deleted_tasks(db_session):
    first = soft_delete_task(db_session, create_task(db_session, TaskCreate(title="M11D restore 1")))
    second = soft_delete_task(db_session, create_task(db_session, TaskCreate(title="M11D restore 2")))

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/tasks/trash/restore-bulk", json={"task_ids": [first.id, second.id]})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"restored_count": 2}
    assert [task.title for task in list_tasks(db_session)] == ["M11D restore 1", "M11D restore 2"]
