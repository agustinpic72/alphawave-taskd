import asyncio

from fastapi.testclient import TestClient

from app.api import routes as api_routes
from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.confirmations import PendingConfirmation
from app.models.tasks import Task
from app.services import settings_service, trello_actions, trello_sync
from app.services.tasks import complete_task, is_trello_linked_task
from app.services.time import utc_now_iso


class DynamicTrelloClient:
    def __init__(self, api_key="key", token="token"):
        self.calls = []

    async def get_lists(self, board_id):
        return [
            {"id": "gamma-tasks", "name": "TAREAS", "closed": False},
            {"id": "gamma-progress", "name": "EN PROCESO", "closed": False},
            {"id": "gamma-review", "name": "EN REVISION", "closed": False},
            {"id": "gamma-done", "name": "TERMINADAS", "closed": False},
        ]

    async def get_cards(self, board_id):
        if board_id != "gamma-board":
            return []
        return [
            {
                "id": "gamma-card-1",
                "name": "desplegar Project Gamma",
                "desc": "deploy checklist",
                "idList": "gamma-tasks",
                "idMembers": ["member-1"],
                "labels": [],
                "shortUrl": "https://trello.test/c/gamma-card-1",
                "url": "https://trello.test/c/gamma-card-1/full",
                "dateLastActivity": "2026-07-08T10:00:00.000Z",
                "due": None,
                "closed": False,
            }
        ]

    async def get_checklists(self, card_id):
        return []

    async def get_comment_actions(self, card_id):
        return []

    async def move_card(self, card_id, target_list_id):
        self.calls.append(("move_card", card_id, target_list_id))
        return {"id": card_id, "idList": target_list_id}


def test_default_settings_do_not_hardcode_tc_board(db_session):
    settings_payload = settings_service.get_settings(db_session)

    assert "GAMMA" not in settings_payload["trello"]["boards"]
    assert "GAMMA" not in settings_service.schema()["scopes"]


def test_create_trello_board_requires_board_id_and_rejects_duplicates(db_session, monkeypatch):
    configure_trello(monkeypatch)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        missing = client.post("/api/settings/trello/boards", json={"alias": "GAMMA", "name": "Project Gamma", "board_id": ""})
        created = client.post("/api/settings/trello/boards", json={"alias": "GAMMA", "name": "Project Gamma", "board_id": "gamma-board"})
        duplicate = client.post("/api/settings/trello/boards", json={"alias": "TRC", "name": "Duplicate", "board_id": "gamma-board"})
    finally:
        app.dependency_overrides.clear()

    assert missing.status_code == 422
    assert "board_id" in missing.json()["detail"]
    assert created.status_code == 201
    assert created.json()["settings"]["trello"]["boards"]["GAMMA"]["board_id"] == "gamma-board"
    assert duplicate.status_code == 422
    assert "duplicado" in duplicate.json()["detail"]


def test_patch_board_rejects_alias_rename_and_disable_preserves_config(db_session, monkeypatch):
    configure_trello(monkeypatch)
    settings_service.create_trello_board(db_session, {"alias": "GAMMA", "name": "Project Gamma", "board_id": "gamma-board"})
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        renamed = client.patch("/api/settings/trello/boards/GAMMA", json={"alias": "TRC"})
        disabled = client.post("/api/settings/trello/boards/GAMMA/disable")
    finally:
        app.dependency_overrides.clear()

    assert renamed.status_code == 422
    board = disabled.json()["settings"]["trello"]["boards"]["GAMMA"]
    assert disabled.status_code == 200
    assert board["enabled"] is False
    assert board["board_id"] == "gamma-board"
    assert "GAMMA" in settings_service.get_settings(db_session)["trello"]["boards"]


def test_validate_board_persists_list_ids_and_validation_status(db_session, monkeypatch):
    configure_trello(monkeypatch)
    settings_service.create_trello_board(db_session, {"alias": "GAMMA", "name": "Project Gamma", "board_id": "gamma-board"})
    monkeypatch.setattr(api_routes, "TrelloApiClient", DynamicTrelloClient)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/settings/trello/boards/GAMMA/validate")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    board = settings_service.get_settings(db_session)["trello"]["boards"]["GAMMA"]
    assert board["validation"]["status"] == "ok"
    assert board["states"]["pending"]["list_id"] == "gamma-tasks"
    assert board["states"]["completed"]["list_id"] == "gamma-done"


def test_dynamic_board_sync_uses_settings_board_and_list_id(db_session, monkeypatch):
    configure_trello(monkeypatch)
    settings_service.create_trello_board(db_session, {"alias": "GAMMA", "name": "Project Gamma", "board_id": "gamma-board"})
    settings_service.validate_trello_lists(db_session, {"GAMMA": asyncio.run(DynamicTrelloClient().get_lists("gamma-board"))})

    summary = asyncio.run(trello_sync.run_trello_sync(db_session, DynamicTrelloClient()))
    task = db_session.query(Task).filter_by(source_id="gamma-card-1").one()

    assert summary.status == "ok"
    assert summary.tasks_created == 1
    assert task.scope == "GAMMA"
    assert task.trello_list_id == "gamma-tasks"
    assert task.trello_state == "pending"


def test_board_auto_confirm_overrides_global_default(db_session, monkeypatch):
    configure_trello(monkeypatch)
    settings_service.create_trello_board(db_session, {"alias": "GAMMA", "name": "Project Gamma", "board_id": "gamma-board"})
    settings_service.patch_trello_board(
        db_session,
        "GAMMA",
        {
            "auto_confirm_writes": False,
            "workflow_states": [
                {"key": "pending", "label": "Tareas", "role": "pending", "enabled": True, "list_id": "gamma-tasks", "list_name": "TAREAS"},
                {"key": "completed", "label": "Terminadas", "role": "completed", "enabled": True, "list_id": "gamma-done", "list_name": "TERMINADAS"},
            ],
        },
    )
    settings_service.patch_settings(db_session, {"advanced": {"trello_write_enabled": True, "trello_auto_confirm_writes": True}})
    confirmation = trello_actions.propose_move_task(db_session, trello_task(db_session), target_state="completed")

    message = asyncio.run(trello_actions.maybe_auto_confirm(db_session, confirmation, DynamicTrelloClient()))

    assert message is None
    assert db_session.get(PendingConfirmation, confirmation.id).status == "pending"


def test_local_task_in_trello_backed_scope_completes_without_trello_flow(db_session, monkeypatch):
    configure_trello(monkeypatch)
    settings_service.create_trello_board(db_session, {"alias": "GAMMA", "name": "Project Gamma", "board_id": "gamma-board"})
    task = local_task(db_session, scope="GAMMA")

    assert is_trello_linked_task(task) is False
    completed = complete_task(db_session, task)

    assert completed.status == "completed"


def configure_trello(monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_write_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "key")
    monkeypatch.setattr(settings, "trello_token", "token")
    monkeypatch.setattr(settings, "trello_member_id", "member-1")
    monkeypatch.setattr(settings, "trello_board_alpha_id", "alpha-board")
    monkeypatch.setattr(settings, "trello_board_beta_id", "beta-board")


def local_task(db_session, *, scope="GAMMA"):
    now = utc_now_iso()
    task = Task(
        id="local-task",
        title="tarea local",
        status="active",
        task_kind="normal",
        source_type="local",
        scope=scope,
        manual_order=1,
        first_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


def trello_task(db_session):
    now = utc_now_iso()
    task = Task(
        id="trello-task",
        title="card gamma",
        status="active",
        task_kind="normal",
        source_type="trello",
        source_id="gamma-card-1",
        source_url="https://trello.test/c/gamma-card-1",
        scope="GAMMA",
        origin_label="GAMMA",
        manual_order=1,
        trello_board_id="gamma-board",
        trello_board_name="Project Gamma",
        trello_list_id="gamma-tasks",
        trello_list_name="TAREAS",
        trello_state="pending",
        first_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task
