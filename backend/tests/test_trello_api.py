import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import routes as api_routes
from app.core.config import settings
from app.core.db import get_db
from app.models.confirmations import PendingConfirmation
from app.models.tasks import Task
from app.services import background_jobs
from app.services import confirmations
from app.services import settings_service
from app.services import trello_actions
from app.services import trello_sync
from app.services.time import utc_now_iso
from app.main import app


class FakeBoardDiscoveryClient:
    def __init__(self, api_key, token):
        self.api_key = api_key
        self.token = token

    async def get_member_boards(self, member_id):
        return [
            {"id": "board-1", "name": "Project Alpha", "url": "https://trello.test/b/alpha", "shortLink": "alpha", "closed": False},
            {"id": "board-2", "name": "Project Gamma", "url": "https://trello.test/b/gamma", "shortLink": "gamma", "closed": False},
            {"id": "closed", "name": "Closed", "url": "https://trello.test/b/x", "shortLink": "x", "closed": True},
        ]

    async def get_lists(self, board_id):
        return [
            {"id": "gamma-tareas", "name": "TAREAS", "closed": False},
            {"id": "gamma-progress", "name": "EN PROCESO", "closed": False},
            {"id": "gamma-closed", "name": "CERRADA", "closed": True},
        ]


class FakeLinkCardClient:
    def __init__(self, api_key="key", token="token"):
        self.calls = []

    async def get_lists(self, board_id):
        return [
            {"id": "list-tareas", "name": "TAREAS"},
            {"id": "list-progress", "name": "EN PROCESO"},
        ]

    async def get_cards(self, board_id):
        return [
            {
                "id": "card-linked",
                "name": "Pagina web nueva empresa referidos",
                "idList": "list-tareas",
                "shortUrl": "https://trello.test/c/card-linked",
                "url": "https://trello.test/c/card-linked-long",
                "due": "2026-07-08T10:00:00+00:00",
                "dateLastActivity": "2026-07-06T10:00:00+00:00",
                "idMembers": ["member-1"],
                "labels": [],
                "closed": False,
            }
        ]

    async def get_checklists(self, card_id):
        return []

    async def get_comment_actions(self, card_id):
        return []

    async def create_card(self, list_id, title, description=None, due_at=None):
        self.calls.append(("create_card", list_id, title, description, due_at))
        return {
            "id": "card-linked",
            "name": title,
            "idList": list_id,
            "shortUrl": "https://trello.test/c/card-linked",
            "due": due_at,
            "dateLastActivity": "2026-07-06T10:00:00+00:00",
        }


def test_trello_sync_endpoint_returns_disabled(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", False)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/trello/sync")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"


def test_trello_status_endpoint_returns_configuration_state(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.get("/api/trello/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["configured"] is False


def test_discover_available_trello_boards_returns_boards_without_secrets(db_session, monkeypatch):
    configure_trello(monkeypatch)
    monkeypatch.setattr(api_routes, "TrelloApiClient", FakeBoardDiscoveryClient)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.get("/api/settings/trello/discover-boards")
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert [board["name"] for board in body["boards"]] == ["Project Alpha", "Project Gamma"]
    assert "token" not in response.text
    assert "key" not in response.text


def test_discover_available_trello_boards_missing_credentials_returns_human_error(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "")
    monkeypatch.setattr(settings, "trello_token", "")
    monkeypatch.setattr(settings, "trello_member_id", "")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.get("/api/settings/trello/discover-boards")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "No hay credenciales Trello configuradas" in response.json()["detail"]


def test_discover_trello_board_lists_returns_lists_without_secrets(db_session, monkeypatch):
    configure_trello(monkeypatch)
    monkeypatch.setattr(api_routes, "TrelloApiClient", FakeBoardDiscoveryClient)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.get("/api/settings/trello/boards/board-2/lists")
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert body["board_id"] == "board-2"
    assert [item["name"] for item in body["lists"]] == ["TAREAS", "EN PROCESO"]
    assert "token" not in response.text
    assert "key" not in response.text


def test_propose_create_card_endpoint_creates_confirmation(db_session, monkeypatch):
    configure_trello(monkeypatch)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/trello/cards/propose", json={"board_alias": "ALPHA", "title": "revisar métricas"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["action_type"] == "trello_create_card"
    assert response.json()["status"] == "pending"


def test_propose_create_card_ignores_legacy_auto_confirm_and_stays_pending(db_session, monkeypatch):
    configure_trello(monkeypatch)
    settings_service.patch_settings(
        db_session,
        {"advanced": {"trello_write_enabled": True, "trello_auto_confirm_writes": True}},
    )

    executed = []

    async def fake_execute(db, confirmation, client=None):
        executed.append(confirmation.id)
        return "ok"

    monkeypatch.setattr(trello_actions, "execute_confirmation", fake_execute)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/trello/cards/propose", json={"board_alias": "ALPHA", "title": "revisar métricas"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert len(confirmations.list_pending(db_session)) == 1
    assert executed == []


def test_create_trello_card_now_is_neutralized_to_pending_confirmation(db_session, monkeypatch):
    configure_trello(monkeypatch)

    executed = []

    async def fake_execute(db, confirmation, client=None):
        executed.append(confirmation.id)
        return "ok"

    monkeypatch.setattr(trello_actions, "execute_confirmation", fake_execute)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post(
            "/api/trello/cards/create-now",
            json={"board_alias": "ALPHA", "title": "revisar métricas", "due_at": "2026-07-08T10:00:00+00:00", "priority": "high"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["action_type"] == "trello_create_card"
    assert len(confirmations.list_pending(db_session)) == 1
    assert executed == []


def test_create_trello_card_now_only_proposes_when_trello_write_disabled(db_session, monkeypatch):
    configure_trello(monkeypatch)
    settings_service.patch_settings(db_session, {"advanced": {"trello_write_enabled": False}})
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/trello/cards/create-now", json={"board_alias": "ALPHA", "title": "revisar métricas"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert len(confirmations.list_pending(db_session)) == 1


def test_link_local_task_to_trello_card_requires_explicit_confirmation(db_session, monkeypatch):
    configure_trello(monkeypatch)
    configure_beta_pending_mapping(db_session)
    fake_client = FakeLinkCardClient()
    task = local_task(
        db_session,
        "task-local",
        "Pagina web nueva empresa referidos",
        scope="BETA",
        manual_order=42,
        due_at="2026-07-08T10:00:00+00:00",
        metadata_json='{"impact_score_note":"keep"}',
    )

    confirmation = asyncio.run(trello_actions.create_card_for_task(db_session, task, client=fake_client))
    assert confirmation.status == "pending"
    assert confirmation.action_type == "trello_link_task_card"
    assert fake_client.calls == []

    asyncio.run(trello_actions.execute_confirmation(db_session, confirmation, client=fake_client))
    db_session.refresh(task)
    tasks = db_session.scalars(select(Task)).all()
    assert fake_client.calls == [("create_card", "list-tareas", "Pagina web nueva empresa referidos", None, "2026-07-08T10:00:00+00:00")]
    assert len(tasks) == 1
    assert task.source_type == "trello"
    assert task.source_id == "card-linked"
    assert task.source_url == "https://trello.test/c/card-linked"
    assert task.scope == "BETA"
    assert task.origin_label == "BETA"
    assert task.trello_board_id == "beta-board"
    assert task.trello_board_name == "Project Beta"
    assert task.trello_list_id == "list-tareas"
    assert task.trello_list_name == "TAREAS"
    assert task.trello_state == "pending"
    assert task.manual_order == 42
    assert task.metadata_json == '{"impact_score_note":"keep"}'


def test_link_local_task_to_trello_card_rejects_personal_scope(db_session, monkeypatch):
    configure_trello(monkeypatch)
    task = local_task(db_session, "task-local", "Personal task", scope="Personal")

    with pytest.raises(ValueError, match="El scope Personal no tiene board Trello conectado"):
        asyncio.run(trello_actions.create_card_for_task(db_session, task, client=FakeLinkCardClient()))


def test_link_local_task_to_trello_card_rejects_already_linked(db_session, monkeypatch):
    configure_trello(monkeypatch)
    task = local_task(db_session, "task-local", "Already linked", scope="BETA")
    task.source_type = "trello"
    task.source_id = "card-existing"
    task.source_url = "https://trello.test/c/card-existing"
    db_session.commit()

    with pytest.raises(ValueError, match="Esta tarea ya tiene tarjeta Trello"):
        asyncio.run(trello_actions.create_card_for_task(db_session, task, client=FakeLinkCardClient()))


def test_link_local_task_to_trello_card_rejects_missing_pending_mapping(db_session, monkeypatch):
    configure_trello(monkeypatch)
    task = local_task(db_session, "task-local", "Missing mapping", scope="BETA")
    monkeypatch.setattr(trello_actions, "workflow_state_for_role_or_key", lambda board, value: None)

    with pytest.raises(ValueError, match="Falta configurar la lista Tareas para BETA"):
        asyncio.run(trello_actions.create_card_for_task(db_session, task, client=FakeLinkCardClient()))


def test_link_local_task_to_trello_card_rejects_disabled_trello_write(db_session, monkeypatch):
    configure_trello(monkeypatch)
    configure_beta_pending_mapping(db_session)
    settings_service.patch_settings(db_session, {"advanced": {"trello_write_enabled": False}})
    task = local_task(db_session, "task-local", "Write disabled", scope="BETA")
    confirmation = asyncio.run(trello_actions.create_card_for_task(db_session, task, client=FakeLinkCardClient()))

    with pytest.raises(ValueError, match="Trello write está deshabilitado"):
        asyncio.run(trello_actions.execute_confirmation(db_session, confirmation, client=FakeLinkCardClient()))


def test_linked_task_is_not_duplicated_by_later_sync(db_session, monkeypatch):
    configure_trello(monkeypatch)
    configure_beta_pending_mapping(db_session)
    fake_client = FakeLinkCardClient()
    task = local_task(db_session, "task-local", "Pagina web nueva empresa referidos", scope="BETA", manual_order=42)

    confirmation = asyncio.run(trello_actions.create_card_for_task(db_session, task, client=fake_client))
    asyncio.run(trello_actions.execute_confirmation(db_session, confirmation, client=fake_client))
    summary = asyncio.run(trello_sync.run_trello_sync(db_session, fake_client))
    tasks = db_session.scalars(select(Task)).all()
    db_session.refresh(task)
    assert summary.status == "ok"
    assert len(tasks) == 1
    assert task.id == "task-local"
    assert task.source_id == "card-linked"
    assert task.manual_order == 42


def test_confirm_endpoint_executes_confirmation(db_session, monkeypatch):
    configure_trello(monkeypatch)
    confirmation = PendingConfirmation(
        id="c1",
        source="ui",
        action_type="trello_create_card",
        payload_json='{"title":"x"}',
        status="pending",
        created_at="2026-07-02T10:00:00+00:00",
        expires_at="2099-07-02T10:00:00+00:00",
    )
    db_session.add(confirmation)
    db_session.commit()

    async def fake_execute(db, confirmation):
        return "ok"

    monkeypatch.setattr(trello_actions, "execute_confirmation", fake_execute)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/confirmations/c1/confirm")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["message"] == "ok"


def test_cancel_endpoint_cancels_confirmation(db_session):
    confirmation = PendingConfirmation(
        id="c1",
        source="ui",
        action_type="trello_create_card",
        payload_json='{"title":"x"}',
        status="pending",
        created_at="2026-07-02T10:00:00+00:00",
        expires_at="2099-07-02T10:00:00+00:00",
    )
    db_session.add(confirmation)
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/confirmations/c1/cancel")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert db_session.get(PendingConfirmation, "c1").status == "cancelled"


def test_bulk_confirm_confirms_multiple_pending_confirmations(db_session, monkeypatch):
    add_confirmation(db_session, "c1")
    add_confirmation(db_session, "c2")

    async def fake_execute(db, confirmation):
        confirmations.confirm(db, confirmation)
        return f"ok {confirmation.id}"

    monkeypatch.setattr(trello_actions, "execute_confirmation", fake_execute)
    queued, body = run_bulk_confirm_job(db_session, monkeypatch, ["c1", "c2"])

    assert queued["status"] == "queued"
    assert queued["total"] == 2
    assert body["status"] == "completed"
    assert body["succeeded"] == 2
    assert body["failed"] == 0
    assert [result["status"] for result in body["results"]] == ["confirmed", "confirmed"]
    assert db_session.get(PendingConfirmation, "c1").status == "confirmed"
    assert db_session.get(PendingConfirmation, "c2").status == "confirmed"


def test_bulk_confirm_handles_failure_and_continues(db_session, monkeypatch):
    add_confirmation(db_session, "c1")
    add_confirmation(db_session, "c2")
    add_confirmation(db_session, "c3")

    async def fake_execute(db, confirmation):
        if confirmation.id == "c2":
            raise ValueError("falló c2")
        confirmations.confirm(db, confirmation)
        return f"ok {confirmation.id}"

    monkeypatch.setattr(trello_actions, "execute_confirmation", fake_execute)
    queued, body = run_bulk_confirm_job(db_session, monkeypatch, ["c1", "c2", "c3"])

    assert queued["status"] == "queued"
    assert body["status"] == "partial"
    assert body["succeeded"] == 2
    assert body["failed"] == 1
    assert body["results"][1]["status"] == "failed"
    assert "falló c2" in body["results"][1]["error"]
    assert db_session.get(PendingConfirmation, "c1").status == "confirmed"
    assert db_session.get(PendingConfirmation, "c2").status == "failed"
    assert db_session.get(PendingConfirmation, "c3").status == "confirmed"


def test_bulk_confirm_marks_expired_confirmation_failed(db_session, monkeypatch):
    add_confirmation(db_session, "expired", expires_at="2020-07-02T10:00:00+00:00")
    add_confirmation(db_session, "fresh")

    async def fake_execute(db, confirmation):
        confirmations.confirm(db, confirmation)
        return "ok"

    monkeypatch.setattr(trello_actions, "execute_confirmation", fake_execute)
    queued, body = run_bulk_confirm_job(db_session, monkeypatch, ["expired", "fresh"])

    assert queued["status"] == "queued"
    assert body["status"] == "partial"
    assert body["succeeded"] == 1
    assert body["failed"] == 1
    assert body["results"][0]["status"] == "failed"
    assert db_session.get(PendingConfirmation, "expired").status == "expired"
    assert db_session.get(PendingConfirmation, "fresh").status == "confirmed"


def test_bulk_cancel_cancels_pending_and_skips_non_pending(db_session):
    add_confirmation(db_session, "c1")
    add_confirmation(db_session, "c2", status="confirmed", resolved_at="2026-07-02T11:00:00+00:00")
    add_confirmation(db_session, "c3")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/confirmations/bulk-cancel", json={"confirmation_ids": ["c1", "c2", "c3"]})
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "partial"
    assert body["cancelled"] == 2
    assert body["failed"] == 1
    assert db_session.get(PendingConfirmation, "c1").status == "cancelled"
    assert db_session.get(PendingConfirmation, "c2").status == "confirmed"
    assert db_session.get(PendingConfirmation, "c3").status == "cancelled"


def test_bulk_confirm_trello_write_disabled_fails_safely(db_session, monkeypatch):
    configure_trello(monkeypatch)
    monkeypatch.setattr(settings, "trello_enabled", False)
    add_confirmation(db_session, "c1")
    add_confirmation(db_session, "c2")
    queued, body = run_bulk_confirm_job(db_session, monkeypatch, ["c1", "c2"])

    assert queued["status"] == "queued"
    assert body["status"] == "failed"
    assert body["succeeded"] == 0
    assert body["failed"] == 2
    assert all("Trello no está habilitado" in result["error"] for result in body["results"])
    assert db_session.get(PendingConfirmation, "c1").status == "failed"
    assert db_session.get(PendingConfirmation, "c2").status == "failed"


def run_bulk_confirm_job(db_session, monkeypatch, confirmation_ids):
    monkeypatch.setattr(background_jobs, "schedule_bulk_confirmation_job", lambda job_id: None)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/confirmations/bulk-confirm", json={"confirmation_ids": confirmation_ids})
        assert response.status_code == 200
        queued = response.json()
        asyncio.run(background_jobs.process_bulk_confirmation_job_in_session(db_session, queued["job_id"]))
        job_response = client.get(f"/api/jobs/{queued['job_id']}")
        assert job_response.status_code == 200
        return queued, job_response.json()
    finally:
        app.dependency_overrides.clear()


def add_confirmation(
    db_session,
    confirmation_id,
    *,
    action_type="trello_create_card",
    status="pending",
    resolved_at=None,
    expires_at="2099-07-02T10:00:00+00:00",
):
    confirmation = PendingConfirmation(
        id=confirmation_id,
        source="ui",
        action_type=action_type,
        payload_json='{"title":"x"}',
        status=status,
        created_at="2026-07-02T10:00:00+00:00",
        expires_at=expires_at,
        resolved_at=resolved_at,
    )
    db_session.add(confirmation)
    db_session.commit()
    return confirmation


def local_task(
    db_session,
    task_id,
    title,
    *,
    scope="BETA",
    manual_order=1,
    due_at=None,
    metadata_json=None,
    status="active",
):
    now = utc_now_iso()
    task = Task(
        id=task_id,
        title=title,
        status=status,
        task_kind="normal",
        source_type="local",
        scope=scope,
        manual_order=manual_order,
        due_at=due_at,
        first_seen_at=now,
        last_touched_at=now,
        created_at=now,
        updated_at=now,
        metadata_json=metadata_json,
    )
    db_session.add(task)
    db_session.commit()
    return task


def configure_trello(monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_write_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "key")
    monkeypatch.setattr(settings, "trello_token", "token")
    monkeypatch.setattr(settings, "trello_member_id", "member-1")
    monkeypatch.setattr(settings, "trello_board_alpha_id", "alpha-board")
    monkeypatch.setattr(settings, "trello_board_beta_id", "beta-board")


def configure_beta_pending_mapping(db_session):
    settings_service.patch_settings(
        db_session,
        {
            "trello": {
                "boards": {
                    "BETA": {
                        "alias": "BETA",
                        "name": "Project Beta",
                        "enabled": True,
                        "board_id": "beta-board",
                        "states": {
                            "pending": {"list_id": "list-tareas", "list_name": "TAREAS"},
                        }
                    }
                }
            }
        },
    )
