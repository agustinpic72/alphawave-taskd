import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.core.config import settings
from app.main import app
from app.services import auth as auth_service
from app.services import settings_service
from app.services.trello_actions import propose_create_card
from app.services.trello_config import board_configs, trello_boards_ready
from app.services.trello_sync import run_trello_sync


PASSWORD = "correct horse battery staple"


class RecordingTrelloClient:
    def __init__(self) -> None:
        self.board_ids: list[str] = []

    async def get_lists(self, board_id):
        self.board_ids.append(board_id)
        return []

    async def get_cards(self, board_id):
        return []

    async def get_checklists(self, card_id):
        return []

    async def get_comment_actions(self, card_id):
        return []


def test_template_flags_and_unrelated_patch_do_not_require_board_id(db_session, monkeypatch):
    _configure_trello(monkeypatch)
    result = settings_service.patch_settings(
        db_session,
        {
            "trello": {"enabled": True, "boards": {"ALPHA": _board("ALPHA", "")}},
            "backups": {"retention_days": 14},
        },
    )

    board = result["trello"]["boards"]["ALPHA"]
    assert board["enabled"] is True
    assert board["configured"] is False
    assert board["board_id_configured"] is False
    assert board["read_ready"] is False
    assert board["write_ready"] is False
    assert result["trello"]["configured"] is False
    assert result["backups"]["retention_days"] == 14


def test_enabled_template_without_board_id_is_reported_as_warning(db_session, monkeypatch):
    _configure_trello(monkeypatch)
    settings_service.patch_settings(
        db_session,
        {"trello": {"enabled": True, "boards": {"ALPHA": _board("ALPHA", "")}}},
    )
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/system/status")
    finally:
        app.dependency_overrides.clear()

    board = response.json()["services"]["trello"]["boards"][0]
    assert response.status_code == 200
    assert board["alias"] == "ALPHA"
    assert board["status"] == "warning"
    assert board["board_id_present"] is False


def test_explicit_empty_boards_replaces_collection_while_omission_preserves_it(db_session):
    settings_service.patch_settings(
        db_session,
        {"trello": {"boards": {"GAMMA": _board("GAMMA", "board-gamma")}}},
    )
    preserved = settings_service.patch_settings(db_session, {"trello": {"enabled": False}})
    cleared = settings_service.patch_settings(db_session, {"trello": {"boards": {}}})

    assert set(preserved["trello"]["boards"]) == {"GAMMA"}
    assert cleared["trello"]["boards"] == {}
    assert settings_service.get_settings(db_session)["trello"]["boards"] == {}


def test_read_ready_uses_any_configured_board_and_sync_skips_templates(db_session, monkeypatch):
    _configure_trello(monkeypatch)
    owner = auth_service.create_owner(db_session, email="alpha@example.com", password=PASSWORD)
    settings_service.patch_settings(
        db_session,
        {
            "trello": {
                "enabled": True,
                "boards": {
                    "ALPHA": _board("ALPHA", ""),
                    "BETA": _board("BETA", "board-beta"),
                },
            }
        },
        user_id=owner.id,
    )
    client = RecordingTrelloClient()

    assert trello_boards_ready(db_session, user_id=owner.id) is True
    summary = asyncio.run(run_trello_sync(db_session, client, user_id=owner.id))

    assert summary.status == "ok"
    assert client.board_ids == ["board-beta"]


def test_write_target_without_board_id_is_rejected_before_remote_call(db_session, monkeypatch):
    _configure_trello(monkeypatch)
    settings_service.patch_settings(
        db_session,
        {"trello": {"enabled": True, "boards": {"DELTA": _board("DELTA", "")}}},
    )

    with pytest.raises(ValueError, match="Falta configurar el board Trello para DELTA"):
        propose_create_card(db_session, board_alias="DELTA", title="Local template task")


def test_write_preference_is_not_write_ready_without_remote_board(db_session, monkeypatch):
    _configure_trello(monkeypatch)
    settings_service.patch_settings(
        db_session,
        {"trello": {"enabled": True, "boards": {"ALPHA": _board("ALPHA", "")}}},
    )
    effective = settings_service.patch_settings(
        db_session,
        {"advanced": {"trello_write_enabled": True}},
    )

    assert effective["advanced"]["editable"]["trello_write_enabled"] is True
    assert effective["advanced"]["prerequisites"]["trello_write_enabled"] is False
    assert settings_service.trello_write_enabled(db_session) is False


def _board(alias: str, board_id: str) -> dict:
    return {
        "alias": alias,
        "name": f"Project {alias.title()}",
        "enabled": True,
        "board_id": board_id,
        "workflow_states": [
            {
                "key": "pending",
                "label": "Pending",
                "role": "pending",
                "enabled": True,
                "list_name": "PENDING",
            }
        ],
    }


def _configure_trello(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_write_enabled", False)
    monkeypatch.setattr(settings, "trello_api_key", "key")
    monkeypatch.setattr(settings, "trello_token", "token")
    monkeypatch.setattr(settings, "trello_member_id", "member-alpha")
    monkeypatch.setattr(settings, "trello_board_alpha_id", "")
    monkeypatch.setattr(settings, "trello_alpha_board_id", "")
    monkeypatch.setattr(settings, "trello_board_beta_id", "")
    monkeypatch.setattr(settings, "trello_beta_board_id", "")
