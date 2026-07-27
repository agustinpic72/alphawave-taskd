import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.db import get_db
from app.main import app
from app.services import tasks as task_service


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _create_incomplete(client: TestClient, title: str):
    response = client.post(
        "/api/tasks",
        json={
            "title": title,
            "scope": "Personal",
            "priority_label": "medium",
            "impact_score": 3,
            "urgency_score": 2,
            "blocking_score": 1,
            "auto_classify": False,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_incomplete_contract_is_canonical_and_ordered(db_session):
    client = _client(db_session)
    try:
        first = _create_incomplete(client, "Manual queue first")
        second = _create_incomplete(client, "Manual queue second")
        response = client.get("/api/tasks/incomplete-details")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["tasks"][:2]] == [first["id"], second["id"]]
    item = payload["tasks"][0]
    assert item["status"] == "active"
    assert item["source_type"] == "local"
    assert item["source_id"] is None
    assert item["updated_at"] == first["updated_at"]
    assert len(item["version_token"]) == 64
    assert item["missing_count"] == len(item["missing_fields"])
    assert item["candidate"] is True
    assert item["missing_fields"] == ["effort_bucket", "estimated_minutes", "context_bucket", "notes"]


def test_manual_completion_persists_columns_and_metadata_then_recalculates(db_session):
    client = _client(db_session)
    try:
        task = _create_incomplete(client, "Manual queue persist")
        response = client.post(
            f"/api/tasks/{task['id']}/complete-details",
            json={
                "expected_updated_at": task["updated_at"],
                "expected_version_token": client.get("/api/tasks/incomplete-details").json()["tasks"][0]["version_token"],
                "effort_bucket": "deep",
                "estimated_minutes": 90,
                "notes": "Checklist técnico.",
            },
        )
        refreshed = client.get("/api/tasks/incomplete-details")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["effort_bucket"] == "deep"
    assert payload["task"]["estimated_minutes"] == 90
    assert "Checklist técnico." in payload["task"]["metadata_json"]
    assert payload["remaining_missing_fields"] == ["context_bucket"]
    assert payload["is_candidate"] is False
    assert payload["priority_explanation"]["score"] >= 0
    assert task["id"] not in [item["id"] for item in refreshed.json()["tasks"]]


def test_partial_manual_completion_retains_candidate(db_session):
    client = _client(db_session)
    try:
        task = _create_incomplete(client, "Manual queue partial")
        response = client.post(
            f"/api/tasks/{task['id']}/complete-details",
            json={
                "expected_updated_at": task["updated_at"],
                "expected_version_token": client.get("/api/tasks/incomplete-details").json()["tasks"][0]["version_token"],
                "effort_bucket": "medium",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["is_candidate"] is True
    assert response.json()["remaining_missing_fields"] == ["estimated_minutes", "context_bucket", "notes"]


def test_manual_completion_rejects_stale_version_and_preserves_external_change(db_session):
    client = _client(db_session)
    try:
        task = _create_incomplete(client, "Manual queue conflict")
        version_token = client.get("/api/tasks/incomplete-details").json()["tasks"][0]["version_token"]
        external = client.patch(f"/api/tasks/{task['id']}", json={"notes": "Cambio externo."})
        response = client.post(
            f"/api/tasks/{task['id']}/complete-details",
            json={
                "expected_updated_at": task["updated_at"],
                "expected_version_token": version_token,
                "effort_bucket": "deep",
            },
        )
        tasks = client.get("/api/tasks").json()["tasks"]
    finally:
        app.dependency_overrides.clear()

    assert external.status_code == 200
    assert response.status_code == 409
    assert "cambió" in response.json()["detail"]
    persisted = next(item for item in tasks if item["id"] == task["id"])
    assert persisted["effort_bucket"] is None
    assert "Cambio externo." in persisted["metadata_json"]


def test_empty_candidate_response(db_session):
    client = _client(db_session)
    try:
        response = client.get("/api/tasks/incomplete-details")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"tasks": [], "total_candidates": 0, "limit": 25}


def test_manual_completion_rejects_invalid_registry_values_and_empty_patch(db_session):
    client = _client(db_session)
    try:
        task = _create_incomplete(client, "Manual queue validation")
        candidate = client.get("/api/tasks/incomplete-details").json()["tasks"][0]
        base = {
            "expected_updated_at": task["updated_at"],
            "expected_version_token": candidate["version_token"],
        }
        invalid = client.post(
            f"/api/tasks/{task['id']}/complete-details",
            json={**base, "effort_bucket": "invalid"},
        )
        empty = client.post(f"/api/tasks/{task['id']}/complete-details", json=base)
        explicit_null = client.post(
            f"/api/tasks/{task['id']}/complete-details",
            json={**base, "priority_label": None},
        )
    finally:
        app.dependency_overrides.clear()

    assert invalid.status_code == 422
    assert empty.status_code == 422
    assert explicit_null.status_code == 422


def test_task_id_refresh_is_not_limited_to_candidates(db_session):
    client = _client(db_session)
    try:
        task = _create_incomplete(client, "Manual queue refresh")
        candidate = client.get("/api/tasks/incomplete-details").json()["tasks"][0]
        completed = client.post(
            f"/api/tasks/{task['id']}/complete-details",
            json={
                "expected_updated_at": task["updated_at"],
                "expected_version_token": candidate["version_token"],
                "effort_bucket": "deep",
                "estimated_minutes": 60,
                "context_bucket": "deep_work",
            },
        )
        refreshed = client.get(f"/api/tasks/incomplete-details?task_id={task['id']}")
    finally:
        app.dependency_overrides.clear()

    assert completed.status_code == 200
    assert refreshed.status_code == 200
    assert refreshed.json()["tasks"][0]["id"] == task["id"]
    assert refreshed.json()["tasks"][0]["candidate"] is False
    assert refreshed.json()["total_candidates"] == 0


def test_concurrent_manual_writes_use_atomic_version_check(db_session, monkeypatch):
    TestingSession = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False, future=True)

    def override_db():
        with TestingSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    entered_update = threading.Event()
    original_update = task_service.update_task
    first_call = True

    def slow_first_update(*args, **kwargs):
        nonlocal first_call
        if first_call:
            first_call = False
            entered_update.set()
            time.sleep(0.2)
        return original_update(*args, **kwargs)

    monkeypatch.setattr(task_service, "update_task", slow_first_update)
    try:
        task = _create_incomplete(client, "Manual queue concurrent")
        candidate = client.get("/api/tasks/incomplete-details").json()["tasks"][0]
        base = {
            "expected_updated_at": task["updated_at"],
            "expected_version_token": candidate["version_token"],
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                client.post,
                f"/api/tasks/{task['id']}/complete-details",
                json={**base, "effort_bucket": "deep"},
            )
            assert entered_update.wait(timeout=2)
            second = pool.submit(
                client.post,
                f"/api/tasks/{task['id']}/complete-details",
                json={**base, "context_bucket": "admin"},
            )
            responses = [first.result(timeout=5), second.result(timeout=5)]
    finally:
        app.dependency_overrides.clear()

    assert sorted(response.status_code for response in responses) == [200, 409]
