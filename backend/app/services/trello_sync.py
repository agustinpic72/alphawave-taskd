import json
import logging
from datetime import datetime, timedelta
from typing import Protocol, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.tasks import DeletedExternalRef, Task, TaskEvent
from app.models.trello import TrelloSyncRun
from app.schemas.trello import TrelloStatus, TrelloSyncSummary
from app.services.time import utc_now_iso
from app.services import integrations
from app.services import settings_service
from app.services.trello_client import TrelloApiClient
from app.services.trello_config import (
    ACTIONABLE_WORKFLOW_ROLES,
    TrelloBoardConfig,
    board_configs,
    list_state_for_name,
    trello_boards_ready,
    trello_credentials_ready,
)


logger = logging.getLogger(__name__)

PRIORITY_BY_COLOR = {"red": "high", "orange": "medium_high", "yellow": "medium", "green": "low"}


class TrelloClient(Protocol):
    async def get_lists(self, board_id: str) -> list[dict[str, Any]]: ...
    async def get_cards(self, board_id: str) -> list[dict[str, Any]]: ...
    async def get_checklists(self, card_id: str) -> list[dict[str, Any]]: ...
    async def get_comment_actions(self, card_id: str) -> list[dict[str, Any]]: ...


async def run_trello_sync(db: Session, client: TrelloClient | None = None, user_id: str | None = None) -> TrelloSyncSummary:
    return await _run_trello_sync_for_users(db, client=client, user_id=user_id)


async def _run_trello_sync_for_users(db: Session, client: TrelloClient | None = None, user_id: str | None = None) -> TrelloSyncSummary:
    run = _start_run(db)
    summary = TrelloSyncSummary(status="ok")
    try:
        if not settings.trello_enabled:
            summary.status = "disabled"
            summary.error = "Trello no está habilitado. Revisá TRELLO_ENABLED y variables TRELLO_*."
            return _finish_run(db, run, summary)
        user_ids = [user_id] if user_id else integrations.trello_sync_user_ids(db)
        if not any(trello_credentials_ready(db, user_id=current_user_id) and trello_boards_ready(db, user_id=current_user_id) for current_user_id in user_ids):
            summary.status = "error"
            summary.error = "Trello está habilitado pero faltan credenciales o board IDs."
            return _finish_run(db, run, summary)

        for current_user_id in user_ids:
            credentials = integrations.get_trello_credentials_for_user(db, current_user_id)
            if not credentials or not credentials.ready:
                continue
            trello = client or TrelloApiClient(credentials.api_key, credentials.token)
            for board in board_configs(db, user_id=current_user_id):
                if not board.read_ready:
                    continue
                board_summary = await _sync_board(db, trello, board, user_id=current_user_id, member_id=credentials.member_id)
                summary.cards_seen += board_summary.cards_seen
                summary.tasks_created += board_summary.tasks_created
                summary.tasks_updated += board_summary.tasks_updated
                summary.tasks_completed += board_summary.tasks_completed
                summary.attention_items += board_summary.attention_items
                summary.ignored += board_summary.ignored
        return _finish_run(db, run, summary)
    except Exception as exc:  # noqa: BLE001 - Trello must not break the backend.
        logger.exception("trello sync failed")
        summary.status = "error"
        summary.error = str(exc)
        return _finish_run(db, run, summary)


def trello_status(db: Session, user_id: str | None = None) -> TrelloStatus:
    latest = db.scalar(select(TrelloSyncRun).order_by(desc(TrelloSyncRun.started_at)).limit(1))
    return TrelloStatus(
        enabled=settings.trello_enabled,
        configured=trello_credentials_ready(db, user_id=user_id) and trello_boards_ready(db, user_id=user_id),
        last_sync_started_at=latest.started_at if latest else None,
        last_sync_completed_at=latest.completed_at if latest else None,
        last_sync_status=latest.status if latest else None,
        last_sync_error=latest.error if latest else None,
    )


async def _sync_board(db: Session, client: TrelloClient, board: TrelloBoardConfig, *, user_id: str, member_id: str) -> TrelloSyncSummary:
    summary = TrelloSyncSummary(status="ok")
    lists = await client.get_lists(board.board_id)
    list_meta = {
        item["id"]: {
            "name": item["name"],
            "state": settings_service.trello_state_for_list(db, board.alias, item.get("id"), item.get("name"), user_id=user_id)
            or list_state_for_name(board, item["name"]),
        }
        for item in lists
    }
    cards = await client.get_cards(board.board_id)
    for card in cards:
        summary.cards_seen += 1
        if _is_tombstoned(db, "trello", card["id"]):
            summary.ignored += 1
            continue
        list_info = list_meta.get(card.get("idList"), {"name": None, "state": None})
        state = list_info.get("state")
        if state == "completed":
            summary.tasks_completed += _complete_existing(db, card, user_id=user_id)
            continue
        if state == "ignored" or state is None or card.get("closed"):
            summary.ignored += 1
            continue

        checklists = await client.get_checklists(card["id"])
        assigned = member_id in {str(member) for member in card.get("idMembers", [])}
        mention = await _mention_metadata(client, card, checklists, member_id=member_id)
        task_kind = _task_kind(state, assigned, mention)
        if task_kind is None:
            summary.ignored += 1
            continue

        created = _upsert_task(db, board, card, list_info, state, task_kind, checklists, mention, user_id=user_id)
        if created:
            summary.tasks_created += 1
        else:
            summary.tasks_updated += 1
        if task_kind == "attention":
            summary.attention_items += 1
    db.commit()
    return summary


def _task_kind(state: str, assigned: bool, mention: dict[str, Any] | None) -> str | None:
    if assigned and state in ACTIONABLE_WORKFLOW_ROLES:
        return "normal"
    if assigned and state == "perpetual":
        return "perpetual"
    if not assigned and mention and state in ACTIONABLE_WORKFLOW_ROLES:
        return "attention"
    return None


def _upsert_task(
    db: Session,
    board: TrelloBoardConfig,
    card: dict[str, Any],
    list_info: dict[str, Any],
    state: str,
    task_kind: str,
    checklists: list[dict[str, Any]],
    mention: dict[str, Any] | None,
    user_id: str,
) -> bool:
    now = utc_now_iso()
    task = _existing_task(db, card["id"], user_id=user_id)
    created = task is None
    if task is None:
        task = Task(
            id=str(uuid4()),
            user_id=user_id,
            title=card["name"],
            status="active",
            task_kind=task_kind,
            source_type="trello",
            source_id=card["id"],
            scope=board.alias,
            origin_label=board.alias,
            manual_order=_next_order(db, user_id=user_id),
            first_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        _event(db, task.id, "created", {"source": "trello_sync", "card_id": card["id"]})
    elif task.status == "deleted":
        return False
    elif task.status == "completed":
        _event(db, task.id, "restored", {"source": "trello_sync", "card_id": card["id"]})
        task.status = "active"
        task.completed_at = None

    checklist_done, checklist_total = _checklist_progress(checklists)
    metadata = _metadata(task.metadata_json)
    metadata["trello"] = {
        "mentioned": bool(mention),
        "mention_source": mention["source"] if mention else None,
        "mention_detected_at": mention["detected_at"] if mention else None,
        "description": _trim_text(card.get("desc"), 1200),
        "checklists": _checklist_metadata(checklists),
    }

    task.title = card["name"]
    task.task_kind = task_kind
    task.source_type = "trello"
    task.source_id = card["id"]
    task.source_url = card.get("shortUrl") or card.get("url")
    task.scope = board.alias
    task.origin_label = board.alias
    task.trello_board_id = board.board_id
    task.trello_board_name = board.name
    task.trello_list_id = card.get("idList")
    task.trello_list_name = list_info.get("name")
    task.trello_state = state
    task.checklist_done = checklist_done
    task.checklist_total = checklist_total
    manual_overrides = set(metadata.get("manual_detail_overrides") or [])
    if "priority_label" not in manual_overrides:
        task.priority_label = _priority_label(card)
    task.due_at = card.get("due")
    task.last_trello_activity_at = card.get("dateLastActivity")
    task.updated_at = now
    task.metadata_json = json.dumps(metadata, ensure_ascii=False)
    if task_kind == "perpetual" and not task.next_checkin_at:
        task.next_checkin_at = _tomorrow_iso()
    return created


def _complete_existing(db: Session, card: dict[str, Any], *, user_id: str) -> int:
    task = _existing_task(db, card["id"], user_id=user_id)
    if not task or task.status == "deleted":
        return 0
    task.trello_state = "completed"
    task.trello_list_id = card.get("idList")
    task.last_trello_activity_at = card.get("dateLastActivity")
    if task.status == "completed":
        return 0
    now = utc_now_iso()
    task.status = "completed"
    task.completed_at = task.completed_at or now
    task.updated_at = now
    _event(db, task.id, "completed", {"source": "trello_sync", "card_id": card["id"]})
    return 1


async def _mention_metadata(
    client: TrelloClient,
    card: dict[str, Any],
    checklists: list[dict[str, Any]],
    *,
    member_id: str,
) -> dict[str, Any] | None:
    needle = member_id.casefold()
    if _contains_mention(card.get("name"), needle):
        return {"source": "title", "detected_at": utc_now_iso()}
    if _contains_mention(card.get("desc"), needle):
        return {"source": "description", "detected_at": utc_now_iso()}
    for checklist in checklists:
        for item in checklist.get("checkItems", []):
            if _contains_mention(item.get("name"), needle):
                return {"source": "checklist", "detected_at": utc_now_iso()}
    for action in await client.get_comment_actions(card["id"]):
        text = ((action.get("data") or {}).get("text") or "")
        if _contains_mention(text, needle):
            return {"source": "comment", "detected_at": utc_now_iso()}
    return None


def _contains_mention(value: str | None, needle: str) -> bool:
    return bool(value and needle and needle in value.casefold())


def _checklist_progress(checklists: list[dict[str, Any]]) -> tuple[int, int]:
    total = 0
    done = 0
    for checklist in checklists:
        for item in checklist.get("checkItems", []):
            total += 1
            if item.get("state") == "complete":
                done += 1
    return done, total


def _checklist_metadata(checklists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for checklist in checklists[:5]:
        items = []
        for item in checklist.get("checkItems", [])[:25]:
            name = _trim_text(item.get("name"), 240)
            if not name:
                continue
            items.append({"name": name, "state": item.get("state") or "unknown"})
        if items:
            result.append({"name": _trim_text(checklist.get("name"), 120), "items": items})
    return result


def _trim_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text[:limit]


def _priority_label(card: dict[str, Any]) -> str | None:
    for label in card.get("labels") or []:
        mapped = PRIORITY_BY_COLOR.get(label.get("color"))
        if mapped:
            return mapped
    return None


def _existing_task(db: Session, card_id: str, *, user_id: str | None = None) -> Task | None:
    statement = select(Task).where(Task.source_type == "trello", Task.source_id == card_id)
    if user_id:
        statement = statement.where(Task.user_id == user_id)
    return db.scalar(statement)


def _is_tombstoned(db: Session, source_type: str, source_id: str) -> bool:
    return bool(
        db.scalar(
            select(DeletedExternalRef.id).where(
                DeletedExternalRef.source_type == source_type,
                DeletedExternalRef.source_id == source_id,
            )
        )
    )


def _next_order(db: Session, *, user_id: str | None = None) -> int:
    statement = select(func.max(Task.manual_order))
    if user_id:
        statement = statement.where(Task.user_id == user_id)
    current = db.scalar(statement)
    return int(current or 0) + 1


def _event(db: Session, task_id: str | None, event_type: str, payload: dict | None = None) -> None:
    user_id = None
    if task_id:
        task = db.get(Task, task_id)
        user_id = task.user_id if task else None
    db.add(
        TaskEvent(
            id=str(uuid4()),
            user_id=user_id,
            task_id=task_id,
            event_type=event_type,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
            created_at=utc_now_iso(),
        )
    )


def _metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _tomorrow_iso() -> str:
    tz = ZoneInfo(settings.app_timezone)
    tomorrow = datetime.now(tz) + timedelta(days=1)
    return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()


def _start_run(db: Session) -> TrelloSyncRun:
    run = TrelloSyncRun(id=str(uuid4()), started_at=utc_now_iso(), status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _finish_run(db: Session, run: TrelloSyncRun, summary: TrelloSyncSummary) -> TrelloSyncSummary:
    run.completed_at = utc_now_iso()
    run.status = summary.status
    run.cards_seen = summary.cards_seen
    run.tasks_created = summary.tasks_created
    run.tasks_updated = summary.tasks_updated
    run.tasks_completed = summary.tasks_completed
    run.attention_items = summary.attention_items
    run.ignored = summary.ignored
    run.error = summary.error
    db.commit()
    return summary
