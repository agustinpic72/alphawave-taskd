import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import routes as api_routes
from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.tasks import Task, TaskEvent
from app.services import auth as auth_service
from app.services import confirmations, integrations, settings_service, trello_actions
from app.services.time import utc_now_iso


class StrictTrelloClient:
    def __init__(self, *, lists=None):
        self.mutations = []
        self.lists = lists if lists is not None else [{"id": "list-pending", "name": "TAREAS"}]

    async def get_lists(self, board_id):
        assert board_id == "alpha-board"
        return self.lists

    async def create_card(self, list_id, title, description=None, due_at=None):
        self.mutations.append(("create_card", list_id, title, description, due_at))
        return {"id": "card-new", "idList": list_id, "shortUrl": "https://trello.test/c/card-new"}


def test_all_create_entry_points_only_leave_pending_confirmations(db_session, monkeypatch):
    user_id = _configure(db_session, monkeypatch, write_enabled=True, auto_confirm=True)
    task = _local_task(db_session, user_id=user_id)
    executed = []

    async def forbidden_execute(*args, **kwargs):
        executed.append((args, kwargs))
        raise AssertionError("proposal endpoint crossed the confirmation boundary")

    monkeypatch.setattr(trello_actions, "execute_confirmation", forbidden_execute)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        responses = [
            client.post("/api/trello/cards/propose", json={"board_alias": "ALPHA", "title": "proposal"}),
            client.post("/api/trello/cards/create-now", json={"board_alias": "ALPHA", "title": "legacy now"}),
            client.post(f"/api/tasks/{task.id}/create-trello-card", json={"target_state": "pending"}),
        ]
    finally:
        app.dependency_overrides.clear()

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert all(response.json()["status"] == "pending" for response in responses)
    assert [response.json()["action_type"] for response in responses] == [
        "trello_create_card",
        "trello_create_card",
        "trello_link_task_card",
    ]
    assert len(confirmations.list_pending(db_session, user_id=user_id)) == 3
    assert executed == []


@pytest.mark.parametrize("invalidated", ["write_opt_in", "board", "mapping", "remote_list", "task_payload", "stored_payload", "owner"])
def test_execution_revalidates_boundary_and_never_mutates_on_stale_state(db_session, monkeypatch, invalidated):
    user_id = _configure(db_session, monkeypatch, write_enabled=True)
    task = _local_task(db_session, user_id=user_id)
    confirmation = trello_actions.propose_create_card_for_task(db_session, task)
    client = StrictTrelloClient()

    if invalidated == "write_opt_in":
        settings_service.patch_settings(db_session, {"advanced": {"trello_write_enabled": False}}, user_id=user_id)
    elif invalidated == "board":
        _set_board(db_session, user_id, board_id="different-board")
    elif invalidated == "mapping":
        _set_board(db_session, user_id, list_id="list-changed")
    elif invalidated == "remote_list":
        client = StrictTrelloClient(lists=[])
    elif invalidated == "task_payload":
        task.title = "changed after proposal"
        task.updated_at = "2099-01-01T00:00:00+00:00"
        db_session.commit()
    elif invalidated == "stored_payload":
        payload = json.loads(confirmation.payload_json)
        payload["task_title"] = "tampered snapshot"
        confirmation.payload_json = json.dumps(payload)
        db_session.commit()
    else:
        auth_service.create_owner(db_session, email="owner@example.com", password="BoundaryPass123!", allow_existing=True)
        other = auth_service.create_owner(db_session, email="other-owner@example.com", password="BoundaryPass123!", allow_existing=True)
        task.user_id = other.id
        db_session.commit()

    with pytest.raises(ValueError):
        asyncio.run(trello_actions.execute_confirmation(db_session, confirmation, client=client))

    assert client.mutations == []
    assert db_session.get(type(confirmation), confirmation.id).status == "failed"
    assert "trello_mutation_rejected" in _event_types(db_session, confirmation.id)


def test_explicit_execution_and_rejection_are_audited(db_session, monkeypatch):
    user_id = _configure(db_session, monkeypatch, write_enabled=True)
    task = _local_task(db_session, user_id=user_id)
    confirmation = trello_actions.propose_create_card_for_task(db_session, task)
    client = StrictTrelloClient()

    message = asyncio.run(trello_actions.execute_confirmation(db_session, confirmation, client=client))
    db_session.refresh(task)

    assert "Creé y vinculé" in message
    assert client.mutations == [("create_card", "list-pending", "Local task", None, None)]
    assert confirmation.status == "confirmed"
    assert task.source_id == "card-new"
    assert _event_types(db_session, confirmation.id) >= {"trello_mutation_proposed", "trello_mutation_executed"}

    rejected = trello_actions.propose_create_card(db_session, board_alias="ALPHA", title="cancel me", user_id=user_id)
    trello_actions.cancel_confirmation(db_session, rejected)
    assert rejected.status == "cancelled"
    assert "trello_mutation_rejected" in _event_types(db_session, rejected.id)


def test_local_only_confirmation_never_initializes_trello(db_session, monkeypatch):
    user_id = _configure(db_session, monkeypatch, write_enabled=False)
    task = _local_task(db_session, user_id=user_id)
    confirmation = trello_actions.propose_local_only_complete(db_session, task)

    def forbidden_credentials(*args, **kwargs):
        raise AssertionError("local-only action touched Trello integration")

    monkeypatch.setattr(integrations, "get_trello_credentials_for_user", forbidden_credentials)
    message = asyncio.run(trello_actions.execute_confirmation(db_session, confirmation))
    db_session.refresh(task)

    assert "Marqué localmente" in message
    assert task.status == "completed"
    events = db_session.scalars(select(TaskEvent).where(TaskEvent.event_type == "trello_mutation_executed")).all()
    audit = next(json.loads(event.payload_json) for event in events if json.loads(event.payload_json)["confirmation_id"] == confirmation.id)
    assert audit["remote"] is False


def test_other_user_cannot_execute_trello_confirmation_via_api(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "key")
    monkeypatch.setattr(settings, "trello_token", "token")
    monkeypatch.setattr(settings, "trello_member_id", "member")
    user_a = auth_service.create_owner(db_session, email="boundary-a@example.com", password="BoundaryPass123!")
    user_b = auth_service.create_owner(
        db_session,
        email="boundary-b@example.com",
        password="BoundaryPass123!",
        allow_existing=True,
    )
    _set_board(db_session, user_a.id)
    settings_service.patch_settings(
        db_session,
        {"advanced": {"trello_write_enabled": True}},
        user_id=user_a.id,
    )
    confirmation = trello_actions.propose_create_card(
        db_session,
        board_alias="ALPHA",
        title="A private proposal",
        user_id=user_a.id,
    )
    calls = []

    async def forbidden_execute(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("cross-user confirmation reached execution")

    monkeypatch.setattr(api_routes, "_execute_confirmation_action", forbidden_execute)
    monkeypatch.setattr(settings, "auth_enabled", True)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        login = client.post(
            "/api/auth/login",
            json={"email": user_b.email, "password": "BoundaryPass123!"},
        )
        response = client.post(
            f"/api/confirmations/{confirmation.id}/confirm",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert calls == []
    db_session.refresh(confirmation)
    assert confirmation.status == "pending"


def _configure(db_session, monkeypatch, *, write_enabled, auto_confirm=False):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "key")
    monkeypatch.setattr(settings, "trello_token", "token")
    monkeypatch.setattr(settings, "trello_member_id", "member")
    user_id = auth_service.single_owner_user_id(db_session)
    _set_board(db_session, user_id)
    settings_service.patch_settings(
        db_session,
        {"advanced": {"trello_write_enabled": write_enabled, "trello_auto_confirm_writes": auto_confirm}},
        user_id=user_id,
    )
    return user_id


def _set_board(db_session, user_id, *, board_id="alpha-board", list_id="list-pending"):
    settings_service.patch_settings(
        db_session,
        {
            "trello": {
                "enabled": True,
                "boards": {
                    "ALPHA": {
                        "alias": "ALPHA",
                        "name": "Project Alpha",
                        "enabled": True,
                        "board_id": board_id,
                        "states": {"pending": {"list_id": list_id, "list_name": "TAREAS"}},
                    }
                },
            }
        },
        user_id=user_id,
    )


def _local_task(db_session, *, user_id):
    now = utc_now_iso()
    task = Task(
        id=f"local-{len(db_session.scalars(select(Task.id)).all()) + 1}",
        user_id=user_id,
        title="Local task",
        status="active",
        task_kind="normal",
        source_type="local",
        scope="ALPHA",
        manual_order=1,
        first_seen_at=now,
        last_touched_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    db_session.commit()
    return task


def _event_types(db_session, confirmation_id):
    events = db_session.scalars(select(TaskEvent)).all()
    return {
        event.event_type
        for event in events
        if confirmation_id in (event.payload_json or "")
    }
