import asyncio

from app.core.config import settings
from app.models.telegram import TelegramUpdate
from app.models.tasks import Task
from app.services.planning import today_plan
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_processor import process_pending_updates, send_todo_list, store_raw_update
from app.services.telegram_parser import parse_command
from app.services.time import utc_now_iso


def test_trello_parser_commands():
    assert parse_command("sync trello").action == "trello_sync"
    assert parse_command("sincronizar trello").action == "trello_sync"
    assert parse_command("estado trello").action == "trello_status"


def test_trello_telegram_status_respects_allowlist(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", False)
    store_raw_update(db_session, raw_update(1, "bad", "chat", "estado trello"))
    messenger = CollectingMessenger()

    result = asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert result.ignored == 1
    assert messenger.messages == []
    assert db_session.get(TelegramUpdate, 1).status == "ignored"


def test_trello_task_from_snapshot_is_read_only_for_complete(db_session):
    task = trello_task(db_session, "trello-1", "Reportes")
    messenger = CollectingMessenger()
    asyncio.run(send_todo_list(db_session, "chat", messenger))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "hecho 1"))

    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    db_session.refresh(task)
    assert task.status == "active"
    assert "todavía está en TAREAS" in messenger.messages[-1][1]


def test_planning_includes_attention_and_excludes_completed_trello(db_session):
    trello_task(db_session, "normal", "Normal", task_kind="normal")
    trello_task(db_session, "attention", "Mención", task_kind="attention")
    trello_task(db_session, "done", "Hecha", status="completed", trello_state="completed")

    plan = today_plan(db_session)

    groups = {group.key: [item.task.title for item in group.items] for group in plan.groups}
    assert "Normal" in groups["quick_wins"]
    assert "Mención" in groups["attention"]
    assert all("Hecha" not in titles for titles in groups.values())


def trello_task(db_session, source_id, title, *, task_kind="normal", status="active", trello_state="pending"):
    now = utc_now_iso()
    task = Task(
        id=source_id,
        title=title,
        status=status,
        task_kind=task_kind,
        source_type="trello",
        source_id=source_id,
        source_url="https://trello.test",
        scope="ALPHA",
        origin_label="ALPHA",
        manual_order=1,
        trello_board_id="alpha-board",
        trello_board_name="Project Alpha",
        trello_list_id="list",
        trello_list_name="TAREAS",
        trello_state=trello_state,
        first_seen_at=now,
        created_at=now,
        updated_at=now,
        completed_at=now if status == "completed" else None,
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
