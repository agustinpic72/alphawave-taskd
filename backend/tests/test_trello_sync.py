import asyncio
import json

from sqlalchemy import select

from app.core.config import settings
from app.models.tasks import Task
from app.services.tasks import permanent_delete_task, soft_delete_task
from app.services import settings_service
from app.services.trello_sync import run_trello_sync, trello_status


class FakeTrelloClient:
    def __init__(self):
        self.lists = {
            "alpha-board": [
                {"id": "list-tareas", "name": "TAREAS"},
                {"id": "list-proceso", "name": "EN PROCESO"},
                {"id": "list-revision", "name": "EN REVISION"},
                {"id": "list-perpetuas", "name": "Perpetuas"},
                {"id": "list-hechas", "name": "TERMINADAS"},
                {"id": "list-ignored", "name": "CARPETAS/ARCHIVOS"},
            ],
            "beta-board": [
                {"id": "beta-tareas", "name": "TAREAS"},
                {"id": "beta-ignored", "name": "MARKETING Y COMUNICACION"},
            ],
        }
        self.cards = {
            "alpha-board": [
                card("assigned-pending", "Reportes de optimizacion", "list-tareas", members=["member-1"], labels=[{"color": "red"}], desc="Necesita revisar datos de revenue y margen."),
                card("assigned-process", "Endpoint API", "list-proceso", members=["member-1"]),
                card("assigned-review", "Revisar modelo", "list-revision", members=["member-1"]),
                card("perpetual", "Check cuentas", "list-perpetuas", members=["member-1"]),
                card("ignored", "Archivo viejo", "list-ignored", members=["member-1"]),
                card("unassigned", "Otra cosa", "list-tareas"),
                card("mentioned", "member-1 revisar copy", "list-tareas"),
                card("comment-mention", "Card con comentario", "list-tareas"),
            ],
            "beta-board": [
                card("beta-card", "Crear Web Scrapper de Argentina", "beta-tareas", members=["member-1"], labels=[{"color": "orange"}]),
                card("unknown-label", "Label rara", "beta-tareas", members=["member-1"], labels=[{"color": "purple"}]),
            ],
        }
        self.checklists = {
            "assigned-pending": [{"checkItems": [{"state": "complete", "name": "uno"}, {"state": "incomplete", "name": "dos"}]}],
            "comment-mention": [{"checkItems": [{"state": "incomplete", "name": "sin mention"}]}],
        }
        self.actions = {
            "comment-mention": [{"data": {"text": "ping member-1 sin guardar cuerpo completo"}}],
        }

    async def get_lists(self, board_id):
        return self.lists[board_id]

    async def get_cards(self, board_id):
        return self.cards[board_id]

    async def get_checklists(self, card_id):
        return self.checklists.get(card_id, [])

    async def get_comment_actions(self, card_id):
        return self.actions.get(card_id, [])


def test_trello_disabled_no_runs_sync(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", False)

    summary = asyncio.run(run_trello_sync(db_session, FakeTrelloClient()))

    assert summary.status == "disabled"
    assert "no está habilitado" in summary.error


def test_trello_enabled_missing_credentials_returns_clear_error(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "")

    summary = asyncio.run(run_trello_sync(db_session, FakeTrelloClient()))

    assert summary.status == "error"
    assert "faltan credenciales" in summary.error


def test_sync_creates_relevant_tasks_and_attention_without_comment_body(db_session, monkeypatch):
    configure_trello(monkeypatch)
    client = FakeTrelloClient()

    summary = asyncio.run(run_trello_sync(db_session, client))

    assert summary.status == "ok"
    assert summary.cards_seen == 10
    assert summary.tasks_created == 8
    assert summary.attention_items == 2
    assert summary.ignored == 2

    tasks = {task.source_id: task for task in db_session.scalars(select(Task)).all()}
    assert tasks["assigned-pending"].scope == "ALPHA"
    assert tasks["assigned-pending"].trello_state == "pending"
    assert tasks["assigned-pending"].priority_label == "high"
    assert tasks["assigned-pending"].checklist_done == 1
    assert tasks["assigned-pending"].checklist_total == 2
    assigned_metadata = json.loads(tasks["assigned-pending"].metadata_json)
    assert assigned_metadata["trello"]["description"] == "Necesita revisar datos de revenue y margen."
    assert assigned_metadata["trello"]["checklists"][0]["items"][0]["name"] == "uno"
    assert tasks["assigned-process"].trello_state == "in_progress"
    assert tasks["assigned-review"].trello_state == "review"
    assert tasks["perpetual"].task_kind == "perpetual"
    assert tasks["perpetual"].next_checkin_at is not None
    assert tasks["mentioned"].task_kind == "attention"
    assert json.loads(tasks["mentioned"].metadata_json)["trello"]["mention_source"] == "title"
    assert tasks["comment-mention"].task_kind == "attention"
    assert "ping member-1" not in tasks["comment-mention"].metadata_json
    assert tasks["beta-card"].scope == "BETA"
    assert tasks["beta-card"].priority_label == "medium_high"
    assert tasks["unknown-label"].priority_label is None
    assert "ignored" not in tasks
    assert "unassigned" not in tasks


def test_sync_is_idempotent_and_preserves_order(db_session, monkeypatch):
    configure_trello(monkeypatch)
    client = FakeTrelloClient()

    asyncio.run(run_trello_sync(db_session, client))
    task = db_session.scalar(select(Task).where(Task.source_id == "assigned-pending"))
    task.manual_order = 99
    db_session.commit()

    summary = asyncio.run(run_trello_sync(db_session, client))

    assert summary.tasks_created == 0
    assert summary.tasks_updated == 8
    assert db_session.scalar(select(Task).where(Task.source_id == "assigned-pending")).manual_order == 99


def test_sync_preserves_manual_detail_priority_override(db_session, monkeypatch):
    configure_trello(monkeypatch)
    client = FakeTrelloClient()
    asyncio.run(run_trello_sync(db_session, client))
    task = db_session.scalar(select(Task).where(Task.source_id == "assigned-pending"))
    metadata = json.loads(task.metadata_json)
    metadata["manual_detail_overrides"] = ["priority_label"]
    task.metadata_json = json.dumps(metadata)
    task.priority_label = "low"
    db_session.commit()

    asyncio.run(run_trello_sync(db_session, client))

    refreshed = db_session.scalar(select(Task).where(Task.source_id == "assigned-pending"))
    assert refreshed.priority_label == "low"
    assert json.loads(refreshed.metadata_json)["manual_detail_overrides"] == ["priority_label"]


def test_completed_card_marks_existing_local_completed(db_session, monkeypatch):
    configure_trello(monkeypatch)
    client = FakeTrelloClient()
    asyncio.run(run_trello_sync(db_session, client))
    client.cards["alpha-board"] = [card("assigned-pending", "Reportes de optimizacion", "list-hechas", members=["member-1"])]
    client.cards["beta-board"] = []

    summary = asyncio.run(run_trello_sync(db_session, client))

    task = db_session.scalar(select(Task).where(Task.source_id == "assigned-pending"))
    assert summary.tasks_completed == 1
    assert task.status == "completed"
    assert task.completed_at is not None


def test_sync_uses_workflow_state_roles_for_ignored_and_custom_actionable(db_session, monkeypatch):
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
                        "workflow_states": [
                            {"key": "backlog", "label": "Backlog", "role": "custom_actionable", "enabled": True, "list_id": "list-tareas", "list_name": "TAREAS"},
                            {"key": "archive", "label": "Archivo", "role": "ignored", "enabled": True, "list_id": "list-ignored", "list_name": "CARPETAS/ARCHIVOS"},
                            {"key": "completed", "label": "Terminadas", "role": "completed", "enabled": True, "list_id": "list-hechas", "list_name": "TERMINADAS"},
                        ]
                    }
                }
            }
        },
    )
    client = FakeTrelloClient()
    client.cards["alpha-board"] = [
        card("custom-action", "Custom task", "list-tareas", members=["member-1"]),
        card("ignored-action", "Ignored task", "list-ignored", members=["member-1"]),
    ]
    client.cards["beta-board"] = []

    summary = asyncio.run(run_trello_sync(db_session, client))

    tasks = {task.source_id: task for task in db_session.scalars(select(Task)).all()}
    assert summary.tasks_created == 1
    assert summary.ignored == 1
    assert tasks["custom-action"].trello_state == "custom_actionable"
    assert "ignored-action" not in tasks


def test_permanent_delete_tombstone_prevents_trello_reimport(db_session, monkeypatch):
    configure_trello(monkeypatch)
    client = FakeTrelloClient()
    asyncio.run(run_trello_sync(db_session, client))
    task = db_session.scalar(select(Task).where(Task.source_id == "assigned-pending"))
    soft_delete_task(db_session, task)
    permanent_delete_task(db_session, task)

    summary = asyncio.run(run_trello_sync(db_session, client))

    assert summary.status == "ok"
    assert db_session.scalar(select(Task).where(Task.source_id == "assigned-pending")) is None
    assert summary.ignored == 3


def test_trello_status_reads_last_run(db_session, monkeypatch):
    configure_trello(monkeypatch)
    asyncio.run(run_trello_sync(db_session, FakeTrelloClient()))

    status = trello_status(db_session)

    assert status.enabled is True
    assert status.configured is True
    assert status.last_sync_status == "ok"


def configure_trello(monkeypatch):
    monkeypatch.setattr(settings, "trello_enabled", True)
    monkeypatch.setattr(settings, "trello_api_key", "key")
    monkeypatch.setattr(settings, "trello_token", "token")
    monkeypatch.setattr(settings, "trello_member_id", "member-1")
    monkeypatch.setattr(settings, "trello_board_alpha_id", "alpha-board")
    monkeypatch.setattr(settings, "trello_board_beta_id", "beta-board")


def card(card_id, title, list_id, members=None, labels=None, desc=""):
    return {
        "id": card_id,
        "name": title,
        "desc": desc,
        "idList": list_id,
        "idMembers": members or [],
        "shortUrl": f"https://trello.test/c/{card_id}",
        "url": f"https://trello.test/c/{card_id}/full",
        "dateLastActivity": "2026-07-02T10:00:00.000Z",
        "due": None,
        "labels": labels or [],
        "closed": False,
    }
