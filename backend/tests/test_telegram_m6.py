import asyncio

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.confirmations import PendingConfirmation
from app.models.tasks import Task
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_parser import parse_command
from app.services.telegram_processor import process_pending_updates, send_todo_list, store_raw_update
from app.services import settings_service
from app.services.time import utc_now_iso


@pytest.fixture(autouse=True)
def configure_alpha_board(monkeypatch):
    monkeypatch.setattr(settings, "trello_board_alpha_id", "alpha-board")


def test_m6_parser_commands():
    assert parse_command("crea card en ALPHA: revisar métricas").action == "trello_create_card"
    assert parse_command("crea card en BETA / TAREAS: hacer landing").payload["board_alias"] == "BETA"
    assert parse_command("voy a empezar la 2").action == "trello_start_index"
    assert parse_command("ya terminé la 2").action == "trello_finish_index"
    assert parse_command("poné deadline a la 2 para el viernes").action == "set_deadline_index"
    assert parse_command("confirmar").action == "confirm_pending"
    assert parse_command("confirmar 2").payload["index"] == 2
    assert parse_command("cancelar").action == "cancel_pending"
    assert parse_command("confirmaciones").action == "list_confirmations"


def test_start_pending_trello_task_creates_move_confirmation(db_session):
    trello_task(db_session, "card-1", "Reportes", trello_state="pending")
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "voy a empezar la 1"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    confirmation = db_session.scalar(select(PendingConfirmation).where(PendingConfirmation.action_type == "trello_move_card"))
    assert confirmation is not None
    assert "EN PROCESO" in messenger.messages[-1][1]


def test_finish_in_progress_trello_task_proposes_review(db_session):
    trello_task(db_session, "card-1", "Reportes", trello_state="in_progress")
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "ya terminé la 1"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    confirmation = db_session.scalar(select(PendingConfirmation).where(PendingConfirmation.action_type == "trello_complete_transition"))
    assert confirmation is not None
    assert "EN REVISION" in messenger.messages[-1][1]


def test_finish_in_progress_without_review_proposes_completed(db_session):
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
                        "workflow_states": [
                            {"key": "pending", "label": "Tareas", "role": "pending", "enabled": True, "list_name": "TAREAS"},
                            {"key": "in_progress", "label": "En proceso", "role": "in_progress", "enabled": True, "list_name": "EN PROCESO"},
                            {"key": "review", "label": "En revisión", "role": "review", "enabled": False},
                            {"key": "completed", "label": "Terminadas", "role": "completed", "enabled": True, "list_name": "TERMINADAS"},
                        ]
                    }
                }
            }
        },
    )
    trello_task(db_session, "card-1", "Reportes", trello_state="in_progress")
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "ya terminé la 1"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    confirmation = db_session.scalar(select(PendingConfirmation).where(PendingConfirmation.action_type == "trello_complete_transition"))
    assert confirmation is not None
    assert "TERMINADAS" in confirmation.summary


def test_finish_review_trello_task_proposes_completed(db_session):
    trello_task(db_session, "card-1", "Reportes", trello_state="review")
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "ya terminé la 1"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    confirmation = db_session.scalar(select(PendingConfirmation).where(PendingConfirmation.action_type == "trello_complete_transition"))
    assert "TERMINADAS" in confirmation.summary


def test_finish_pending_trello_task_asks_options(db_session):
    trello_task(db_session, "card-1", "Reportes", trello_state="pending")
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "ya terminé la 1"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert "¿Qué querés hacer?" in messenger.messages[-1][1]
    assert db_session.scalar(select(PendingConfirmation).where(PendingConfirmation.action_type == "trello_finish_choice")) is not None


def test_finish_perpetual_blocks(db_session):
    trello_task(db_session, "card-1", "Check", trello_state="perpetual", task_kind="perpetual")
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "ya terminé la 1"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert "perpetua" in messenger.messages[-1][1]


def test_local_done_still_completes(db_session):
    local = local_task(db_session, "Comprar café")
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "done 1"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    db_session.refresh(local)
    assert local.status == "completed"


def test_local_trello_backed_scope_done_completes_without_confirmation(db_session):
    local = local_task(db_session, "Preparar landing", scope="BETA")
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "done 1"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    db_session.refresh(local)
    assert local.status == "completed"
    assert db_session.scalar(select(PendingConfirmation)) is None
    assert "Completé: Preparar landing" in messenger.messages[-1][1]


def test_trello_source_without_card_id_done_completes_locally(db_session):
    local = local_task(db_session, "Card rota", scope="BETA", source_type="trello", source_id=None)
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "done 1"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    db_session.refresh(local)
    assert local.status == "completed"
    assert db_session.scalar(select(PendingConfirmation)) is None
    assert "¿Qué querés hacer?" not in messenger.messages[-1][1]


def test_rename_trello_creates_confirmation(db_session):
    trello_task(db_session, "card-1", "Reportes")
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "renombra 1 a Nuevo"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert db_session.scalar(select(PendingConfirmation).where(PendingConfirmation.action_type == "trello_rename_card")) is not None


def test_multiple_confirmations_requires_index(db_session):
    trello_task(db_session, "card-1", "Reportes")
    trello_task(db_session, "card-2", "Otra")
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "voy a empezar la 1"))
    store_raw_update(db_session, raw_update(2, "allowed", "chat", "voy a empezar la 2"))
    store_raw_update(db_session, raw_update(3, "allowed", "chat", "confirmar"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert "varias confirmaciones" in messenger.messages[-1][1]


def local_task(db_session, title, *, scope="Inbox", source_type="local", source_id=None):
    now = utc_now_iso()
    task = Task(
        id=title,
        title=title,
        status="active",
        task_kind="normal",
        source_type=source_type,
        source_id=source_id,
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


def trello_task(db_session, source_id, title, *, trello_state="pending", task_kind="normal"):
    now = utc_now_iso()
    task = Task(
        id=source_id,
        title=title,
        status="active",
        task_kind=task_kind,
        source_type="trello",
        source_id=source_id,
        source_url="https://trello.test",
        scope="ALPHA",
        origin_label="ALPHA",
        manual_order=1 if source_id == "card-1" else 2,
        trello_board_id="alpha-board",
        trello_board_name="Project Alpha",
        trello_list_id="list",
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


def raw_update(update_id: int, user_id: str, chat_id: str, text: str):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 100,
            "from": {"id": user_id},
            "chat": {"id": chat_id},
            "text": text,
        },
    }
