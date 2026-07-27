import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.confirmations import PendingConfirmation
from app.models.tasks import Task
from app.services import confirmations
from app.services import settings_service
from app.services.time import utc_now_iso
from app.services.trello_actions import execute_confirmation, propose_create_card, propose_move_task, propose_rename_task, propose_update_due


class FakeWriteClient:
    def __init__(self):
        self.calls = []
        self.cards = {
            "alpha-board": [],
            "beta-board": [],
        }

    async def get_lists(self, board_id):
        return [
            {"id": "list-tareas", "name": "TAREAS"},
            {"id": "list-proceso", "name": "EN PROCESO"},
            {"id": "list-revision", "name": "EN REVISION"},
            {"id": "list-hechas", "name": "TERMINADAS"},
        ]

    async def get_cards(self, board_id):
        return self.cards.get(board_id, [])

    async def get_checklists(self, card_id):
        return []

    async def get_comment_actions(self, card_id):
        return []

    async def create_card(self, list_id, title, description=None, due_at=None):
        self.calls.append(("create_card", list_id, title, description, due_at))
        card = card_payload("new-card", title, list_id)
        self.cards["alpha-board"].append(card)
        return card

    async def move_card(self, card_id, target_list_id):
        self.calls.append(("move_card", card_id, target_list_id))
        return {"id": card_id, "idList": target_list_id}

    async def rename_card(self, card_id, new_title):
        self.calls.append(("rename_card", card_id, new_title))
        return {"id": card_id, "name": new_title}

    async def update_due(self, card_id, due_at):
        self.calls.append(("update_due", card_id, due_at))
        return {"id": card_id, "due": due_at}


def test_trello_write_disabled_blocks_writes(db_session, monkeypatch):
    configure(monkeypatch, write_enabled=False)
    confirmation = propose_create_card(db_session, board_alias="ALPHA", title="revisar métricas")

    with pytest.raises(ValueError, match="write está deshabilitado"):
        asyncio.run(execute_confirmation(db_session, confirmation, FakeWriteClient()))

    assert db_session.get(PendingConfirmation, confirmation.id).status == "failed"


def test_trello_enabled_false_blocks_writes(db_session, monkeypatch):
    configure(monkeypatch, enabled=False, write_enabled=True)
    confirmation = propose_create_card(db_session, board_alias="ALPHA", title="revisar métricas")

    with pytest.raises(ValueError, match="no está habilitado"):
        asyncio.run(execute_confirmation(db_session, confirmation, FakeWriteClient()))


def test_missing_credentials_blocks_writes(db_session, monkeypatch):
    configure(monkeypatch, write_enabled=True)
    monkeypatch.setattr(settings, "trello_api_key", "")
    confirmation = propose_create_card(db_session, board_alias="ALPHA", title="revisar métricas")

    with pytest.raises(ValueError, match="faltan credenciales"):
        asyncio.run(execute_confirmation(db_session, confirmation, FakeWriteClient()))


def test_confirmation_ttl_default_24h(db_session, monkeypatch):
    configure(monkeypatch)
    confirmation = propose_create_card(db_session, board_alias="ALPHA", title="revisar métricas")

    created = datetime.fromisoformat(confirmation.created_at)
    expires = datetime.fromisoformat(confirmation.expires_at)
    assert expires - created == timedelta(hours=24)


def test_dynamic_trello_board_from_settings_creates_confirmation(db_session, monkeypatch):
    configure(monkeypatch)
    settings_service.patch_settings(
        db_session,
        {
            "trello": {
                "boards": {
                    "GAMMA": {
                        "alias": "GAMMA",
                        "name": "Project Gamma",
                        "board_id": "gamma-board",
                        "enabled": True,
                        "states": {
                            "pending": {"list_id": "list-gamma-backlog", "list_name": "BACKLOG"},
                            "completed": {"list_id": "list-gamma-done", "list_name": "DONE"},
                        },
                    }
                }
            }
        },
    )

    confirmation = propose_create_card(db_session, board_alias="GAMMA", list_name="BACKLOG", title="sync trades")
    payload = json.loads(confirmation.payload_json)

    assert payload["board_alias"] == "GAMMA"
    assert payload["board_id"] == "gamma-board"
    assert payload["list_state"] == "pending"
    assert payload["list_name"] == "BACKLOG"


def test_create_card_confirmation_executes_and_syncs(db_session, monkeypatch):
    configure(monkeypatch, write_enabled=True)
    configure_alpha_mappings(db_session)
    client = FakeWriteClient()
    confirmation = propose_create_card(db_session, board_alias="ALPHA", title="revisar métricas")

    result = asyncio.run(execute_confirmation(db_session, confirmation, client))

    assert "Creé la card" in result
    assert client.calls[0] == ("create_card", "list-tareas", "revisar métricas", None, None)
    assert db_session.get(PendingConfirmation, confirmation.id).status == "confirmed"


def test_move_rename_due_confirmations_call_client(db_session, monkeypatch):
    configure(monkeypatch, write_enabled=True)
    configure_alpha_mappings(db_session)
    task = trello_task(db_session, "card-1", "Reportes")
    client = FakeWriteClient()

    move = propose_move_task(db_session, task, target_state="in_progress")
    rename = propose_rename_task(db_session, task, title="Nuevo título")
    due = propose_update_due(db_session, task, due_at="2026-07-03T09:00:00+02:00")

    asyncio.run(execute_confirmation(db_session, move, client))
    asyncio.run(execute_confirmation(db_session, rename, client))
    asyncio.run(execute_confirmation(db_session, due, client))

    assert ("move_card", "card-1", "list-proceso") in client.calls
    assert ("rename_card", "card-1", "Nuevo título") in client.calls
    assert ("update_due", "card-1", "2026-07-03T09:00:00+02:00") in client.calls


def test_rename_and_due_reject_task_whose_board_has_no_id(db_session, monkeypatch):
    configure(monkeypatch, write_enabled=True)
    monkeypatch.setattr(settings, "trello_board_alpha_id", "")
    settings_service.patch_settings(
        db_session,
        {
            "trello": {
                "enabled": True,
                "boards": {
                    "ALPHA": {"alias": "ALPHA", "name": "Project Alpha", "enabled": True, "board_id": "", "states": {}},
                    "BETA": {"alias": "BETA", "name": "Project Beta", "enabled": True, "board_id": "beta-board", "states": {}},
                },
            }
        },
    )
    task = trello_task(db_session, "card-idless", "Disconnected task")

    with pytest.raises(ValueError, match="Falta configurar el board Trello para ALPHA"):
        propose_rename_task(db_session, task, title="Nuevo título")
    with pytest.raises(ValueError, match="Falta configurar el board Trello para ALPHA"):
        propose_update_due(db_session, task, due_at=None)


def test_stale_rename_confirmation_rechecks_task_board_before_remote_call(db_session, monkeypatch):
    configure(monkeypatch, write_enabled=True)
    configure_alpha_mappings(db_session)
    task = trello_task(db_session, "card-stale", "Stale mapping")
    confirmation = propose_rename_task(db_session, task, title="Nuevo título")
    settings_service.patch_settings(
        db_session,
        {
            "trello": {
                "boards": {
                    "BETA": {"alias": "BETA", "name": "Project Beta", "enabled": True, "board_id": "beta-board", "states": {}}
                }
            }
        },
        user_id=task.user_id,
    )
    client = FakeWriteClient()

    with pytest.raises(ValueError, match="Board Trello inválido o no configurado"):
        asyncio.run(execute_confirmation(db_session, confirmation, client))

    assert not any(call[0] == "rename_card" for call in client.calls)


def test_expired_confirmation_cannot_execute(db_session, monkeypatch):
    configure(monkeypatch, write_enabled=True)
    confirmation = propose_create_card(db_session, board_alias="ALPHA", title="revisar métricas")
    confirmation.expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat()
    db_session.commit()

    with pytest.raises(ValueError, match="ya venció"):
        asyncio.run(execute_confirmation(db_session, confirmation, FakeWriteClient()))


def test_cancel_confirmation(db_session):
    confirmation = confirmations.create_confirmation(
        db_session,
        source="telegram",
        chat_id="chat",
        action_type="trello_create_card",
        payload={"title": "x"},
    )

    confirmations.cancel(db_session, confirmation)

    assert db_session.get(PendingConfirmation, confirmation.id).status == "cancelled"


def configure(monkeypatch, *, enabled=True, write_enabled=False):
    monkeypatch.setattr(settings, "trello_enabled", enabled)
    monkeypatch.setattr(settings, "trello_write_enabled", write_enabled)
    monkeypatch.setattr(settings, "trello_api_key", "key")
    monkeypatch.setattr(settings, "trello_token", "token")
    monkeypatch.setattr(settings, "trello_member_id", "member-1")
    monkeypatch.setattr(settings, "trello_board_alpha_id", "alpha-board")
    monkeypatch.setattr(settings, "trello_board_beta_id", "beta-board")
    monkeypatch.setattr(settings, "trello_confirmation_ttl_hours", 24)


def configure_alpha_mappings(db_session):
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
                        "states": {
                            "pending": {"list_id": "list-tareas", "list_name": "TAREAS"},
                            "in_progress": {"list_id": "list-proceso", "list_name": "EN PROCESO"},
                            "review": {"list_id": "list-revision", "list_name": "EN REVISION"},
                            "completed": {"list_id": "list-hechas", "list_name": "TERMINADAS"},
                        }
                    }
                }
            }
        },
    )


def trello_task(db_session, source_id, title, *, trello_state="pending"):
    now = utc_now_iso()
    task = Task(
        id=source_id,
        title=title,
        status="active",
        task_kind="normal",
        source_type="trello",
        source_id=source_id,
        source_url="https://trello.test",
        scope="ALPHA",
        origin_label="ALPHA",
        manual_order=1,
        trello_board_id="alpha-board",
        trello_board_name="Project Alpha",
        trello_list_id="list-tareas",
        trello_list_name="TAREAS",
        trello_state=trello_state,
        first_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


def card_payload(card_id, title, list_id):
    return {
        "id": card_id,
        "name": title,
        "desc": "",
        "idList": list_id,
        "idMembers": ["member-1"],
        "shortUrl": f"https://trello.test/c/{card_id}",
        "url": f"https://trello.test/c/{card_id}/full",
        "dateLastActivity": "2026-07-02T10:00:00.000Z",
        "due": None,
        "labels": [],
        "closed": False,
    }
