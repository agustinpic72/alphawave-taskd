import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.core.config import REPO_ROOT
from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.tasks import Task
from app.services import briefing as briefing_service
from app.services import settings_service
from app.services.time import utc_now_iso
from app.services.trello_actions import execute_confirmation, propose_move_task


TZ = ZoneInfo("UTC")


class CollectingMessenger:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id: str, text: str):
        self.messages.append((chat_id, text))
        return len(self.messages)


class RenamedDoneTrelloClient:
    def __init__(self):
        self.calls = []

    async def get_lists(self, board_id):
        return [
            {"id": "list-tareas", "name": "TAREAS"},
            {"id": "list-done", "name": "DONE"},
        ]

    async def get_cards(self, board_id):
        return []

    async def get_checklists(self, card_id):
        return []

    async def get_comment_actions(self, card_id):
        return []

    async def create_card(self, list_id, title, description=None, due_at=None):
        return {"id": "new", "name": title, "idList": list_id}

    async def move_card(self, card_id, target_list_id):
        self.calls.append(("move_card", card_id, target_list_id))
        return {"id": card_id, "idList": target_list_id}

    async def rename_card(self, card_id, new_title):
        return {"id": card_id, "name": new_title}

    async def update_due(self, card_id, due_at):
        return {"id": card_id, "due": due_at}


def test_settings_api_defaults_patch_and_no_secrets(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_token", "secret-token")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.get("/api/settings")
        patched = client.patch("/api/settings", json={"briefing": {"time": "10:30"}})
        invalid = client.patch("/api/settings", json={"briefing": {"time": "99:30"}})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "secret-token" not in response.text
    assert patched.status_code == 200
    assert patched.json()["settings"]["briefing"]["time"] == "10:30"
    assert invalid.status_code == 422


def test_settings_advanced_status_does_not_expose_secrets(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "telegram-secret-token")
    monkeypatch.setattr(settings, "trello_api_key", "trello-secret-key")
    monkeypatch.setattr(settings, "trello_token", "trello-secret-token")
    monkeypatch.setattr(settings, "trello_member_id", "")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.get("/api/settings")
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    advanced = body["settings"]["advanced"]
    assert response.status_code == 200
    assert "telegram-secret-token" not in response.text
    assert "trello-secret-key" not in response.text
    assert "trello-secret-token" not in response.text
    assert advanced["instance"]["telegram_token_configured"] is True
    assert advanced["instance"]["trello_credentials_configured"] is False
    assert "editable" in advanced
    assert "status" in advanced


def test_settings_advanced_defaults_use_env_without_sqlite_override(db_session, monkeypatch):
    configure_trello(monkeypatch)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "telegram-secret-token")
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "user-1")
    monkeypatch.setattr(settings, "reminders_poll_interval_seconds", 45.0)
    monkeypatch.setattr(settings, "trello_sync_interval_minutes", 17)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.get("/api/settings")
    finally:
        app.dependency_overrides.clear()

    advanced = response.json()["settings"]["advanced"]
    assert response.status_code == 200
    assert advanced["status"]["telegram"] == "active"
    assert advanced["status"]["trello"] == "active"
    assert advanced["status"]["trello_write"] == "active"
    assert advanced["editable"]["trello_write_enabled"] is True
    assert advanced["status"]["reminders_interval_seconds"] == 45.0
    assert advanced["status"]["trello_sync_interval_minutes"] == 17
    assert db_session.get(settings_service.AppSetting, "advanced") is None


def test_patch_trello_write_enabled_persists_effective_setting(db_session, monkeypatch):
    configure_trello(monkeypatch)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        disabled = client.patch("/api/settings", json={"advanced": {"trello_write_enabled": False}})
        enabled = client.patch("/api/settings", json={"advanced": {"trello_write_enabled": True}})
    finally:
        app.dependency_overrides.clear()

    assert disabled.status_code == 200
    assert disabled.json()["settings"]["advanced"]["editable"]["trello_write_enabled"] is False
    assert settings_service.trello_write_enabled(db_session) is True
    assert enabled.status_code == 200
    assert enabled.json()["settings"]["advanced"]["editable"]["trello_write_enabled"] is True
    assert settings_service.trello_write_enabled(db_session) is True
    row = settings_service._loads(db_session.get(settings_service.AppSetting, "advanced").value_json, {})
    assert row["editable"]["trello_write_enabled"] is True
    assert set(row["editable"]) <= {"trello_write_enabled", "trello_auto_confirm_writes"}


def test_trello_write_false_override_disables_effective_setting(db_session, monkeypatch):
    configure_trello(monkeypatch)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.patch("/api/settings", json={"advanced": {"trello_write_enabled": False}})
    finally:
        app.dependency_overrides.clear()

    advanced = response.json()["settings"]["advanced"]
    assert response.status_code == 200
    assert advanced["editable"]["trello_write_enabled"] is False
    assert advanced["status"]["trello_write"] == "disabled"
    assert settings_service.trello_write_enabled(db_session) is False


def test_trello_auto_confirm_requires_trello_write_enabled(db_session, monkeypatch):
    configure_trello(monkeypatch)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.patch("/api/settings", json={"advanced": {"trello_write_enabled": False, "trello_auto_confirm_writes": True}})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "Escritura en Trello" in response.json()["detail"]
    assert settings_service.trello_auto_confirm_writes(db_session) is False


def test_auto_confirm_status_uses_clear_states(db_session, monkeypatch):
    configure_trello(monkeypatch)

    disabled = settings_service.get_settings(db_session)["advanced"]["status"]["trello_auto_confirm"]
    settings_service.patch_settings(db_session, {"advanced": {"trello_write_enabled": False}})
    unavailable = settings_service.get_settings(db_session)["advanced"]["status"]["trello_auto_confirm"]
    settings_service.patch_settings(db_session, {"advanced": {"trello_write_enabled": True, "trello_auto_confirm_writes": True}})
    active = settings_service.get_settings(db_session)["advanced"]["status"]["trello_auto_confirm"]

    assert disabled == "disabled"
    assert unavailable == "unavailable"
    assert active == "active"


def test_auto_confirm_status_requires_configuration_when_trello_base_missing(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "")
    monkeypatch.setattr(settings, "trello_token", "")
    monkeypatch.setattr(settings, "trello_member_id", "")

    status = settings_service.get_settings(db_session)["advanced"]["status"]["trello_auto_confirm"]

    assert status == "requires_configuration"


def test_patch_trello_write_enabled_requires_base_config(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "")
    monkeypatch.setattr(settings, "trello_token", "")
    monkeypatch.setattr(settings, "trello_member_id", "")
    monkeypatch.setattr(settings, "trello_board_alpha_id", "")
    monkeypatch.setattr(settings, "trello_board_beta_id", "")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.patch("/api/settings", json={"advanced": {"trello_write_enabled": True}})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "Configurá Trello" in response.json()["detail"]


def test_trello_board_duplicate_ids_are_rejected(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.patch(
            "/api/settings",
            json={
                "trello": {
                    "boards": {
                        "A1": {"alias": "A1", "name": "Uno", "board_id": "same-board", "enabled": True},
                        "A2": {"alias": "A2", "name": "Dos", "board_id": "same-board", "enabled": True},
                    }
                }
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "Board ID Trello duplicado" in response.json()["detail"]


def test_trello_board_duplicate_aliases_are_rejected(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.patch(
            "/api/settings",
            json={
                "trello": {
                    "boards": {
                        "A1": {"alias": "DUP", "name": "Uno", "board_id": "board-1", "enabled": True},
                        "A2": {"alias": "DUP", "name": "Dos", "board_id": "board-2", "enabled": True},
                    }
                }
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "Alias Trello duplicado" in response.json()["detail"]


def test_patch_trello_workflow_state_persists_list_id_and_name(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.patch(
            "/api/settings",
            json={
                "trello": {
                    "boards": {
                        "GAMMA": {
                            "alias": "GAMMA",
                            "name": "Project Gamma",
                            "board_id": "gamma-board",
                            "enabled": True,
                            "workflow_states": [
                                {
                                    "key": "pending",
                                    "label": "TAREAS",
                                    "role": "pending",
                                    "enabled": True,
                                    "required_for": ["create_card"],
                                    "list_id": "gamma-tareas",
                                    "list_name": "TAREAS",
                                }
                            ],
                            "states": {"pending": {"list_id": "gamma-tareas", "list_name": "TAREAS"}},
                        }
                    }
                }
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    board = response.json()["settings"]["trello"]["boards"]["GAMMA"]
    assert board["workflow_states"][0]["list_id"] == "gamma-tareas"
    assert board["workflow_states"][0]["list_name"] == "TAREAS"
    assert board["states"]["pending"]["list_id"] == "gamma-tareas"
    assert board["states"]["pending"]["list_name"] == "TAREAS"


def test_enabled_trello_workflow_state_without_any_list_is_rejected(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.patch(
            "/api/settings",
            json={
                "trello": {
                    "boards": {
                        "A3": {
                            "alias": "A3",
                            "name": "Board nuevo",
                            "board_id": "a3-board",
                            "enabled": True,
                            "workflow_states": [
                                {"key": "pending", "label": "TAREAS", "role": "pending", "enabled": True}
                            ],
                        }
                    }
                }
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "Falta lista" in response.json()["detail"]


def test_disabled_trello_workflow_state_without_list_id_can_be_saved(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.patch(
            "/api/settings",
            json={
                "trello": {
                    "boards": {
                        "A3": {
                            "alias": "A3",
                            "name": "Board nuevo",
                            "board_id": "a3-board",
                            "enabled": True,
                            "workflow_states": [
                                {"key": "perpetual", "label": "Perpetuas", "role": "perpetual", "enabled": False, "list_name": "Perpetuas"}
                            ],
                        }
                    }
                }
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    state = response.json()["settings"]["trello"]["boards"]["A3"]["workflow_states"][0]
    assert state["enabled"] is False
    assert state["list_name"] == "Perpetuas"


def test_patch_settings_does_not_modify_env_file(db_session, monkeypatch):
    configure_trello(monkeypatch)
    env_path = REPO_ROOT / ".env"
    before = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.patch("/api/settings", json={"advanced": {"trello_write_enabled": False}})
    finally:
        app.dependency_overrides.clear()
    after = env_path.read_text(encoding="utf-8") if env_path.exists() else None

    assert response.status_code == 200
    assert after == before


def test_weekend_mode_can_skip_auto_briefing(db_session):
    settings_service.patch_settings(
        db_session,
        {
            "modes": {
                "weekend": {
                    "enabled": True,
                    "active_days": ["saturday"],
                    "active_scopes": ["Personal"],
                    "notifications": {"briefing_auto": False},
                }
            }
        },
    )
    messenger = CollectingMessenger()
    now = datetime(2026, 7, 4, 13, 0, tzinfo=TZ)

    result = asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=now))

    assert result is None
    assert messenger.messages == []


def test_trello_write_uses_configured_list_id_when_name_differs(db_session, monkeypatch):
    configure_trello(monkeypatch)
    settings_service.patch_settings(
        db_session,
        {
            "trello": {
                "boards": {
                    "ALPHA": {
                        "alias": "ALPHA",
                        "name": "Project Alpha",
                        "enabled": True,
                        "board_id": "alpha-board",
                        "states": {"completed": {"list_id": "list-done", "list_name": "DONE"}},
                    }
                }
            }
        },
    )
    task = trello_task(db_session)
    confirmation = propose_move_task(db_session, task, target_state="completed")
    client = RenamedDoneTrelloClient()

    asyncio.run(execute_confirmation(db_session, confirmation, client))

    assert ("move_card", "card-1", "list-done") in client.calls


def test_legacy_trello_states_are_exposed_as_workflow_states(db_session):
    settings_service.patch_settings(
        db_session,
        {"trello": {"boards": {"GAMMA": {"alias": "GAMMA", "name": "Project Gamma", "enabled": True, "board_id": "gamma-board", "states": {"pending": {"list_id": "backlog", "list_name": "BACKLOG"}}}}}},
    )

    board = settings_service.get_settings(db_session)["trello"]["boards"]["GAMMA"]

    assert any(state["role"] == "pending" and state["list_id"] == "backlog" for state in board["workflow_states"])
    assert board["states"]["pending"]["list_id"] == "backlog"


def test_beta_disabled_perpetual_does_not_fail_trello_validation(db_session, monkeypatch):
    configure_trello(monkeypatch)
    lists_by_board = {
        "ALPHA": [
            {"id": "alpha-tareas", "name": "TAREAS"},
            {"id": "alpha-proceso", "name": "EN PROCESO"},
            {"id": "alpha-review", "name": "EN REVISION"},
            {"id": "alpha-done", "name": "TERMINADAS"},
            {"id": "alpha-perpetual", "name": "Perpetuas"},
            {"id": "alpha-ignored", "name": "CARPETAS/ARCHIVOS"},
        ],
        "BETA": [
            {"id": "beta-tareas", "name": "TAREAS"},
            {"id": "beta-proceso", "name": "EN PROCESO"},
            {"id": "beta-review", "name": "EN REVISION"},
            {"id": "beta-done", "name": "TERMINADAS"},
            {"id": "beta-ignored", "name": "MARKETING Y COMUNICACION"},
            {"id": "beta-archive", "name": "CARPETAS - ARCHIVOS"},
        ],
        "GAMMA": [],
    }

    report = settings_service.validate_trello_lists(db_session, lists_by_board)

    beta = next(board for board in report["boards"] if board["alias"] == "BETA")
    assert report["status"] == "ok"
    assert beta["states"]["perpetual"]["status"] == "disabled"


def test_enabled_workflow_state_missing_list_reports_validation_error(db_session, monkeypatch):
    configure_trello(monkeypatch)
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
                        "workflow_states": [
                            {"key": "pending", "label": "Tareas", "role": "pending", "enabled": True, "list_name": "TAREAS"},
                            {"key": "perpetual", "label": "Perpetuas", "role": "perpetual", "enabled": True, "list_name": "Perpetuas"},
                        ]
                    }
                }
            }
        },
    )

    report = settings_service.validate_trello_lists(db_session, {"ALPHA": [], "BETA": [{"id": "beta-tareas", "name": "TAREAS"}], "GAMMA": []})

    beta = next(board for board in report["boards"] if board["alias"] == "BETA")
    assert report["status"] == "error"
    assert beta["states"]["perpetual"]["status"] == "missing"


def configure_trello(monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_write_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "key")
    monkeypatch.setattr(settings, "trello_token", "token")
    monkeypatch.setattr(settings, "trello_member_id", "member-1")
    monkeypatch.setattr(settings, "trello_board_alpha_id", "alpha-board")
    monkeypatch.setattr(settings, "trello_board_beta_id", "beta-board")


def trello_task(db_session):
    now = utc_now_iso()
    task = Task(
        id="task-1",
        title="Reportes Semanal",
        status="active",
        task_kind="normal",
        source_type="trello",
        source_id="card-1",
        source_url="https://trello.test/c/card-1",
        scope="ALPHA",
        origin_label="ALPHA",
        manual_order=1,
        trello_board_id="alpha-board",
        trello_board_name="Project Alpha",
        trello_list_id="list-tareas",
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
