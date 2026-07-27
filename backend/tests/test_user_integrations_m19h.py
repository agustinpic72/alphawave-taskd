import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.integrations import TelegramChatLink, UserIntegration
from app.models.tasks import Task
from app.schemas.tasks import TaskCreate
from app.services import auth as auth_service
from app.services import integrations
from app.services import settings_service
from app.services.tasks import create_task, list_tasks
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_processor import process_pending_updates, store_raw_update
from app.services.trello_actions import create_card_for_task
from app.services.trello_sync import run_trello_sync


PASSWORD = "correct horse battery"


class SingleBoardTrelloClient:
    async def get_lists(self, board_id):
        return [{"id": "list-a", "name": "TAREAS"}]

    async def get_cards(self, board_id):
        return [
            {
                "id": "card-a",
                "name": "Trello task A",
                "desc": "",
                "idList": "list-a",
                "idMembers": ["member-1"],
                "shortUrl": "https://trello.test/c/card-a",
                "url": "https://trello.test/c/card-a/full",
                "dateLastActivity": "2026-07-02T10:00:00.000Z",
                "due": None,
                "labels": [],
                "closed": False,
            }
        ]

    async def get_checklists(self, card_id):
        return []

    async def get_comment_actions(self, card_id):
        return []


def test_schema_models_exist_and_telegram_hash_is_redacted(db_session):
    user = auth_service.create_owner(db_session, email="schema@example.com", password=PASSWORD)
    link = integrations.link_telegram_chat(db_session, user_id=user.id, chat_id="-100123456789")

    assert db_session.query(UserIntegration).count() == 0
    assert db_session.query(TelegramChatLink).count() == 1
    assert link.chat_id_hash != "-100123456789"
    assert link.chat_id_redacted == "-1...89"


def test_trello_settings_are_scoped_and_legacy_only_for_single_owner(db_session):
    user_a = auth_service.create_owner(db_session, email="trello-a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(db_session, email="trello-b@example.com", password=PASSWORD, allow_existing=True)

    settings_service.patch_settings(db_session, {"trello": {"enabled": True, "boards": {"GAMMA": _board("GAMMA", "board-a")}}}, user_id=user_a.id)

    settings_a = settings_service.get_settings(db_session, user_id=user_a.id)
    settings_b = settings_service.get_settings(db_session, user_id=user_b.id)

    assert "GAMMA" in settings_a["trello"]["boards"]
    assert settings_b["trello"]["boards"] == {}
    assert settings_b["trello"]["status"] == "requires_config"

    with pytest.raises(Exception, match="Trello no está configurado"):
        settings_service.patch_settings(db_session, {"trello": {"enabled": True, "boards": {"TB": _board("TB", "board-b")}}}, user_id=user_b.id)


def test_user_integration_creation_recovers_when_concurrent_insert_wins(db_session, monkeypatch):
    original_commit = db_session.commit
    injected = False

    def commit_with_concurrent_winner():
        nonlocal injected
        if injected:
            return original_commit()
        injected = True
        OtherSession = sessionmaker(bind=db_session.get_bind(), future=True)
        with OtherSession() as other:
            other.add(
                UserIntegration(
                    id="concurrent-winner",
                    user_id="local-owner",
                    provider="trello",
                    status="legacy_instance",
                    config_json="{}",
                    credentials_source="instance_env",
                    created_at="2026-07-10T00:00:00+00:00",
                    updated_at="2026-07-10T00:00:00+00:00",
                )
            )
            other.commit()
        raise IntegrityError("concurrent insert", {}, Exception("unique constraint"))

    monkeypatch.setattr(db_session, "commit", commit_with_concurrent_winner)

    row = integrations.set_user_integration(
        db_session,
        user_id="local-owner",
        provider="trello",
        config={"enabled": False, "boards": {}},
        status="legacy_instance",
    )

    assert row.id == "concurrent-winner"
    assert integrations.get_user_integration(db_session, "local-owner", "trello") is row
    assert row.config_json == '{"boards": {}, "enabled": false}'


def test_trello_boards_endpoint_does_not_show_other_user_config(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    user_a = auth_service.create_owner(db_session, email="api-a@example.com", password=PASSWORD)
    _user_b = auth_service.create_owner(db_session, email="api-b@example.com", password=PASSWORD, allow_existing=True)
    settings_service.patch_settings(db_session, {"trello": {"enabled": True, "boards": {"GAMMA": _board("GAMMA", "board-a")}}}, user_id=user_a.id)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client_a = TestClient(app)
        client_b = TestClient(app)
        login_a = client_a.post("/api/auth/login", json={"email": "api-a@example.com", "password": PASSWORD})
        login_b = client_b.post("/api/auth/login", json={"email": "api-b@example.com", "password": PASSWORD})
        assert login_a.status_code == 200
        assert login_b.status_code == 200
        boards_a = client_a.get("/api/settings/trello/boards").json()["boards"]
        boards_b = client_b.get("/api/settings/trello/boards").json()["boards"]
    finally:
        app.dependency_overrides.clear()

    assert "GAMMA" in [board["alias"] for board in boards_a]
    assert boards_b == []


def test_trello_sync_assigns_tasks_to_integration_owner(db_session, monkeypatch):
    _configure_trello_env(monkeypatch)
    user = auth_service.create_owner(db_session, email="sync@example.com", password=PASSWORD)
    integrations.set_user_integration(
        db_session,
        user_id=user.id,
        provider="trello",
        config={"enabled": True, "boards": {"A": _board("A", "board-a")}},
        status="active",
        credentials_source="instance_env",
    )

    summary = asyncio.run(run_trello_sync(db_session, SingleBoardTrelloClient(), user_id=user.id))

    task = db_session.scalar(select(Task).where(Task.source_id == "card-a"))
    assert summary.status == "ok"
    assert task is not None
    assert task.user_id == user.id
    assert task.scope == "A"


def test_trello_write_for_user_without_config_cannot_use_owner_mapping(db_session, monkeypatch):
    _configure_trello_env(monkeypatch, write_enabled=True)
    user_a = auth_service.create_owner(db_session, email="write-a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(db_session, email="write-b@example.com", password=PASSWORD, allow_existing=True)
    settings_service.patch_settings(db_session, {"advanced": {"trello_write_enabled": True}})
    settings_service.patch_settings(db_session, {"trello": {"enabled": True, "boards": {"ALPHA": _board("ALPHA", "alpha-board")}}}, user_id=user_a.id)
    task = create_task(db_session, TaskCreate(title="B local", scope="ALPHA", auto_classify=False), user_id=user_b.id)

    with pytest.raises(ValueError, match="Faltan credenciales|no tiene board Trello conectado"):
        asyncio.run(create_card_for_task(db_session, task, client=SingleBoardTrelloClient()))


def test_telegram_chat_links_scope_inbound_tasks_and_unknown_chat_is_ignored(db_session):
    user_a = auth_service.create_owner(db_session, email="tg-a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(db_session, email="tg-b@example.com", password=PASSWORD, allow_existing=True)
    integrations.link_telegram_chat(db_session, user_id=user_a.id, chat_id="chat-a")
    integrations.link_telegram_chat(db_session, user_id=user_b.id, chat_id="chat-b")
    store_raw_update(db_session, _raw_update(1, "from-a", "chat-a", "agregá tarea A"))
    store_raw_update(db_session, _raw_update(2, "from-b", "chat-b", "agregá tarea B"))
    store_raw_update(db_session, _raw_update(3, "from-x", "chat-x", "agregá tarea X"))
    messenger = CollectingMessenger()

    result = asyncio.run(process_pending_updates(db_session, messenger))

    assert result.processed == 2
    assert result.ignored == 1
    assert [task.title for task in list_tasks(db_session, user_id=user_a.id)] == ["tarea A"]
    assert [task.title for task in list_tasks(db_session, user_id=user_b.id)] == ["tarea B"]


def test_telegram_legacy_allowed_maps_to_single_owner(db_session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "legacy-user")
    owner = auth_service.create_owner(db_session, email="legacy@example.com", password=PASSWORD)
    store_raw_update(db_session, _raw_update(1, "legacy-user", "legacy-chat", "agregá legacy"))
    messenger = CollectingMessenger()

    result = asyncio.run(process_pending_updates(db_session, messenger))

    assert result.processed == 1
    assert [task.title for task in list_tasks(db_session, user_id=owner.id)] == ["legacy"]


def _board(alias: str, board_id: str) -> dict:
    return {
        "alias": alias,
        "name": alias,
        "board_id": board_id,
        "enabled": True,
        "workflow_states": [
            {"key": "pending", "label": "Tareas", "role": "pending", "enabled": True, "list_id": "list-a", "list_name": "TAREAS", "required_for": ["create_card"]},
            {"key": "completed", "label": "Done", "role": "completed", "enabled": True, "list_id": "list-done", "list_name": "DONE"},
        ],
        "states": {"pending": {"list_id": "list-a", "list_name": "TAREAS"}},
    }


def _configure_trello_env(monkeypatch, *, write_enabled: bool = False) -> None:
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_write_enabled", write_enabled)
    monkeypatch.setattr(settings, "trello_api_key", "key")
    monkeypatch.setattr(settings, "trello_token", "token")
    monkeypatch.setattr(settings, "trello_member_id", "member-1")
    monkeypatch.setattr(settings, "trello_board_alpha_id", "alpha-board")
    monkeypatch.setattr(settings, "trello_board_beta_id", "")


def _raw_update(update_id: int, user_id: str, chat_id: str, text: str):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 100,
            "from": {"id": user_id},
            "chat": {"id": chat_id},
            "text": text,
        },
    }
