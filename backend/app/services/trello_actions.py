import json
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.confirmations import PendingConfirmation
from app.models.tasks import Task, TaskEvent
from app.services import confirmations
from app.services import integrations
from app.services import settings_service
from app.services import tasks as task_service
from app.services.time import utc_now_iso
from app.services.trello_client import TrelloApiClient
from app.services.trello_config import (
    TrelloBoardConfig,
    board_by_alias,
    default_list_name,
    list_state_for_name,
    trello_boards_ready,
    trello_credentials_ready,
    workflow_state_for_role_or_key,
)
from app.services.trello_sync import run_trello_sync


TRELLO_ACTION_TYPES = {
    "trello_create_card",
    "trello_link_task_card",
    "trello_move_card",
    "trello_rename_card",
    "trello_update_due",
    "trello_complete_transition",
    "trello_local_only_complete",
}
STATE_LABELS = {
    "pending": "TAREAS",
    "in_progress": "EN PROCESO",
    "review": "EN REVISION",
    "completed": "TERMINADAS",
}


class TrelloWriteClient(Protocol):
    async def get_lists(self, board_id: str) -> list[dict[str, Any]]: ...
    async def get_cards(self, board_id: str) -> list[dict[str, Any]]: ...
    async def get_checklists(self, card_id: str) -> list[dict[str, Any]]: ...
    async def get_comment_actions(self, card_id: str) -> list[dict[str, Any]]: ...
    async def create_card(self, list_id: str, title: str, description: str | None = None, due_at: str | None = None) -> dict[str, Any]: ...
    async def move_card(self, card_id: str, target_list_id: str) -> dict[str, Any]: ...
    async def rename_card(self, card_id: str, new_title: str) -> dict[str, Any]: ...
    async def update_due(self, card_id: str, due_at: str | None) -> dict[str, Any]: ...


def propose_create_card(
    db: Session,
    *,
    board_alias: str,
    title: str,
    list_name: str | None = None,
    description: str | None = None,
    due_at: str | None = None,
    source: str = "ui",
    chat_id: str | None = None,
    user_message_id: int | None = None,
    user_id: str | None = None,
) -> PendingConfirmation:
    board, state, display_list = _resolve_board_and_state(db, board_alias, list_name or "pending", user_id=user_id)
    payload = {
        "board_alias": board.alias,
        "board_id": board.board_id,
        "list_state": state,
        "list_name": display_list,
        "list_id": _configured_list_id(db, board, state, user_id=user_id),
        "title": title.strip(),
        "description": description,
        "due_at": due_at,
    }
    summary = f"Crear card en {board.alias}: {title.strip()}"
    return _create(db, source=source, chat_id=chat_id, user_message_id=user_message_id, action_type="trello_create_card", payload=payload, summary=summary, user_id=user_id)


def propose_move_task(
    db: Session,
    task: Task,
    *,
    target_state: str,
    action_type: str = "trello_move_card",
    source: str = "ui",
    chat_id: str | None = None,
    user_message_id: int | None = None,
) -> PendingConfirmation:
    _ensure_trello_task(task)
    _ensure_task_owner(db, task)
    board = _board_for_task(db, task)
    display = _state_display(board, target_state)
    payload = {
        **_task_snapshot(task, board),
        "target_state": target_state,
        "target_list_name": display,
        "target_list_id": _configured_list_id(db, board, target_state, user_id=task.user_id),
    }
    summary = f'Mover "{task.title}" a {display}'
    return _create(db, source=source, chat_id=chat_id, user_message_id=user_message_id, action_type=action_type, payload=payload, summary=summary, user_id=task.user_id)


def propose_rename_task(db: Session, task: Task, *, title: str, source: str = "ui", chat_id: str | None = None) -> PendingConfirmation:
    _ensure_trello_task(task)
    _ensure_task_owner(db, task)
    board = _board_for_task(db, task)
    payload = {**_task_snapshot(task, board), "title": title.strip(), "old_title": task.title}
    summary = f'Renombrar "{task.title}" a "{title.strip()}"'
    return _create(db, source=source, chat_id=chat_id, action_type="trello_rename_card", payload=payload, summary=summary, user_id=task.user_id)


def propose_update_due(db: Session, task: Task, *, due_at: str | None, source: str = "ui", chat_id: str | None = None) -> PendingConfirmation:
    _ensure_trello_task(task)
    _ensure_task_owner(db, task)
    board = _board_for_task(db, task)
    payload = {**_task_snapshot(task, board), "due_at": due_at, "old_due_at": task.due_at}
    summary = f'Actualizar deadline de "{task.title}"'
    return _create(db, source=source, chat_id=chat_id, action_type="trello_update_due", payload=payload, summary=summary, user_id=task.user_id)


def propose_local_only_complete(db: Session, task: Task, *, source: str = "ui", chat_id: str | None = None) -> PendingConfirmation:
    _ensure_task_owner(db, task)
    payload = {
        "task_id": task.id,
        "title": task.title,
        "owner_id": task.user_id,
        "task_updated_at": task.updated_at,
        "task_status": task.status,
    }
    summary = f'Completar sólo local: "{task.title}"'
    return _create(db, source=source, chat_id=chat_id, action_type="trello_local_only_complete", payload=payload, summary=summary, user_id=task.user_id)


def propose_create_card_for_task(
    db: Session,
    task: Task,
    *,
    target_state: str = "pending",
) -> PendingConfirmation:
    _ensure_task_owner(db, task)
    _ensure_linkable_local_task(db, task, target_state)
    board = _board_for_linkable_scope(db, task.scope, user_id=task.user_id)
    list_name = _list_name_for_state(db, board, target_state, user_id=task.user_id)
    payload = {
        "task_id": task.id,
        "owner_id": task.user_id,
        "task_updated_at": task.updated_at,
        "task_status": task.status,
        "task_source_type": task.source_type,
        "task_scope": task.scope,
        "task_title": task.title,
        "task_due_at": task.due_at,
        "board_alias": board.alias,
        "board_id": board.board_id,
        "target_state": target_state,
        "target_list_name": list_name,
        "target_list_id": _configured_list_id(db, board, target_state, user_id=task.user_id),
    }
    return _create(
        db,
        source="ui",
        chat_id=None,
        action_type="trello_link_task_card",
        payload=payload,
        summary=f'Crear card Trello para "{task.title}"',
        user_id=task.user_id,
    )


async def create_card_for_task(
    db: Session,
    task: Task,
    *,
    target_state: str = "pending",
    client: TrelloWriteClient | None = None,
) -> PendingConfirmation:
    """Compatibility entry point: creating a card now only creates a proposal."""
    del client
    return propose_create_card_for_task(db, task, target_state=target_state)


async def execute_confirmation(db: Session, confirmation: PendingConfirmation, client: TrelloWriteClient | None = None) -> str:
    confirmation = confirmations.get_pending(db, confirmation.id, user_id=confirmation.user_id) if confirmation.user_id else confirmations.get_pending(db, confirmation.id)
    if not confirmation:
        raise ValueError("La confirmación no está pendiente o ya venció.")
    payload: dict[str, Any] = {}
    try:
        if not confirmation.user_id:
            raise ValueError("La confirmación no tiene propietario.")
        parsed_payload = json.loads(confirmation.payload_json)
        if not isinstance(parsed_payload, dict):
            raise ValueError("El payload de confirmación no es válido.")
        payload = parsed_payload
        if confirmation.action_type == "trello_local_only_complete":
            task = _task(db, payload["task_id"], user_id=confirmation.user_id)
            _validate_local_snapshot(task, payload, confirmation.user_id)
            task_service.complete_task(db, task)
            _event(db, task.id, "local_only_completed", {"confirmation_id": confirmation.id})
            confirmations.confirm(db, confirmation)
            _audit(db, confirmation, "executed", task_id=task.id, remote=False)
            return f"Listo. Marqué localmente: {task.title}"

        _validate_writes_enabled(db, user_id=confirmation.user_id)
        credentials = integrations.get_trello_credentials_for_user(db, confirmation.user_id)
        if not credentials:
            raise ValueError("Faltan credenciales o board IDs de Trello.")
        trello = client or TrelloApiClient(credentials.api_key, credentials.token)
        if confirmation.action_type == "trello_create_card":
            board = _board(db, payload["board_alias"], user_id=confirmation.user_id)
            _validate_board_snapshot(db, board, payload, "list_state", "list_id", confirmation.user_id)
            list_id = await _resolve_list_id(db, trello, board, payload["list_state"], user_id=confirmation.user_id)
            card = await trello.create_card(list_id, payload["title"], payload.get("description"), payload.get("due_at"))
            _event(db, None, "trello_card_created", {"confirmation_id": confirmation.id, "card_id": card.get("id"), "board": board.alias})
            await run_trello_sync(db, trello, user_id=confirmation.user_id)
            confirmations.confirm(db, confirmation)
            _audit(db, confirmation, "executed", remote=True)
            return f'Listo. Creé la card en Trello: {payload["title"]}'

        if confirmation.action_type == "trello_link_task_card":
            task = _task(db, payload["task_id"], user_id=confirmation.user_id)
            _validate_link_snapshot(db, task, payload, confirmation.user_id)
            board = _board_for_linkable_scope(db, task.scope, user_id=confirmation.user_id)
            _validate_board_snapshot(db, board, payload, "target_state", "target_list_id", confirmation.user_id)
            list_id = await _resolve_list_id(db, trello, board, payload["target_state"], user_id=confirmation.user_id)
            card = await trello.create_card(list_id, task.title, None, task.due_at)
            _link_created_card(db, task, board, payload, list_id, card)
            confirmations.confirm(db, confirmation)
            _audit(db, confirmation, "executed", task_id=task.id, remote=True)
            return f'Listo. Creé y vinculé la card en Trello: {task.title}'

        if confirmation.action_type in ("trello_move_card", "trello_complete_transition"):
            task = _task(db, payload["task_id"], user_id=confirmation.user_id)
            _validate_task_snapshot(task, payload, confirmation.user_id)
            board = _board_for_task(db, task)
            _validate_board_snapshot(db, board, payload, "target_state", "target_list_id", confirmation.user_id)
            list_id = await _resolve_list_id(db, trello, board, payload["target_state"], user_id=task.user_id)
            await trello.move_card(payload["card_id"], list_id)
            _event(db, task.id, "trello_card_moved", {"confirmation_id": confirmation.id, "target_state": payload["target_state"]})
            await run_trello_sync(db, trello, user_id=task.user_id)
            confirmations.confirm(db, confirmation)
            _audit(db, confirmation, "executed", task_id=task.id, remote=True)
            return f'Listo. Moví "{task.title}" a {payload["target_list_name"]}.'

        if confirmation.action_type == "trello_rename_card":
            task = _task(db, payload["task_id"], user_id=confirmation.user_id)
            _validate_task_snapshot(task, payload, confirmation.user_id)
            board = _board_for_task(db, task)
            _validate_current_list_mapping(db, board, task, payload, confirmation.user_id)
            await trello.rename_card(payload["card_id"], payload["title"])
            _event(db, task.id, "trello_card_renamed", {"confirmation_id": confirmation.id, "title": payload["title"]})
            await run_trello_sync(db, trello, user_id=task.user_id)
            confirmations.confirm(db, confirmation)
            _audit(db, confirmation, "executed", task_id=task.id, remote=True)
            return f'Listo. Renombré la card a "{payload["title"]}".'

        if confirmation.action_type == "trello_update_due":
            task = _task(db, payload["task_id"], user_id=confirmation.user_id)
            _validate_task_snapshot(task, payload, confirmation.user_id)
            board = _board_for_task(db, task)
            _validate_current_list_mapping(db, board, task, payload, confirmation.user_id)
            await trello.update_due(payload["card_id"], payload.get("due_at"))
            _event(db, task.id, "trello_due_updated", {"confirmation_id": confirmation.id, "due_at": payload.get("due_at")})
            await run_trello_sync(db, trello, user_id=task.user_id)
            confirmations.confirm(db, confirmation)
            _audit(db, confirmation, "executed", task_id=task.id, remote=True)
            return "Listo. Actualicé el deadline en Trello."
        raise ValueError("Tipo de confirmación no soportado.")
    except Exception as exc:  # noqa: BLE001 - persist failure with the confirmation.
        confirmations.fail(db, confirmation, str(exc))
        task_id = payload.get("task_id")
        _event(db, task_id, "trello_write_failed", {"confirmation_id": confirmation.id, "error": str(exc)})
        _audit(db, confirmation, "rejected", task_id=task_id, reason=str(exc))
        raise


async def maybe_auto_confirm(db: Session, confirmation: PendingConfirmation, client: TrelloWriteClient | None = None) -> str | None:
    """Auto-confirm is intentionally neutralized by the M20G confirmation boundary."""
    del db, confirmation, client
    return None


def cancel_confirmation(db: Session, confirmation: PendingConfirmation) -> None:
    confirmations.cancel(db, confirmation)
    _event(db, None, "confirmation_cancelled", {"confirmation_id": confirmation.id, "action_type": confirmation.action_type})
    if confirmation.action_type in TRELLO_ACTION_TYPES:
        _audit(db, confirmation, "rejected", reason="cancelled")


def _create(
    db: Session,
    *,
    source: str,
    chat_id: str | None,
    user_message_id: int | None = None,
    action_type: str,
    payload: dict,
    summary: str,
    user_id: str | None = None,
) -> PendingConfirmation:
    confirmation = confirmations.create_confirmation(
        db,
        source=source,
        chat_id=chat_id,
        user_message_id=user_message_id,
        action_type=action_type,
        payload=payload,
        ttl_hours=settings.trello_confirmation_ttl_hours,
        summary=summary,
        user_id=user_id,
    )
    _event(db, payload.get("task_id"), "confirmation_created", {"confirmation_id": confirmation.id, "action_type": action_type, "summary": summary}, user_id=user_id)
    _audit(db, confirmation, "proposed", task_id=payload.get("task_id"))
    return confirmation


def _validate_writes_enabled(db: Session, user_id: str | None = None) -> None:
    if not settings.trello_enabled:
        raise ValueError("Trello no está habilitado. Revisá TRELLO_ENABLED.")
    advanced = settings_service.get_settings(db, user_id=user_id).get("advanced") or {}
    trello_write_preference = bool((advanced.get("editable") or {}).get("trello_write_enabled"))
    if not trello_write_preference:
        raise ValueError("Trello write está deshabilitado. Revisá Configuración > Avanzado.")
    if not trello_credentials_ready(db, user_id=user_id) or not trello_boards_ready(db, user_id=user_id):
        raise ValueError("Trello write está habilitado pero faltan credenciales o board IDs.")


def _ensure_linkable_local_task(db: Session, task: Task, target_state: str) -> None:
    if task_service.is_trello_linked_task(task) or task.source_id or task.source_url:
        raise ValueError("Esta tarea ya tiene tarjeta Trello.")
    if task.status != "active":
        raise ValueError("Sólo se puede crear tarjeta Trello para tareas activas.")
    board = _board_for_linkable_scope(db, task.scope, user_id=task.user_id)
    workflow_state = workflow_state_for_role_or_key(board, target_state)
    if not workflow_state or workflow_state.role not in ("pending", "custom_actionable"):
        raise ValueError(f"Falta configurar la lista Tareas para {board.alias} en Configuración → Trello.")
    if not workflow_state.list_id:
        raise ValueError(f"Falta configurar la lista Tareas para {board.alias} en Configuración → Trello.")


def _board_for_linkable_scope(db: Session, scope: str | None, user_id: str | None = None) -> TrelloBoardConfig:
    clean_scope = (scope or "").strip()
    board = board_by_alias(clean_scope, db, user_id=user_id)
    if not board or not board.enabled:
        raise ValueError(f"El scope {clean_scope or 'sin scope'} no tiene board Trello conectado.")
    if not board.board_id:
        raise ValueError(f"Falta configurar el board Trello para {board.alias}.")
    return board


def _list_name_for_state(db: Session, board: TrelloBoardConfig, target_state: str, user_id: str | None = None) -> str | None:
    mapping = settings_service.trello_mapping_for_state(db, board.alias, target_state, user_id=user_id)
    if mapping and mapping.get("list_name"):
        return mapping["list_name"]
    return _state_display(board, target_state)


def _resolve_board_and_state(db: Session, board_alias: str, list_name_or_state: str, user_id: str | None = None) -> tuple[TrelloBoardConfig, str, str]:
    board = _board(db, board_alias, user_id=user_id)
    state = list_state_for_name(board, list_name_or_state) or list_name_or_state
    display = _state_display(board, state)
    workflow_state = workflow_state_for_role_or_key(board, state)
    if not workflow_state or workflow_state.role not in ("pending", "custom_actionable"):
        raise ValueError("La lista Trello no existe o no está habilitada para crear cards en este board.")
    return board, state, display


def _configured_list_id(db: Session, board: TrelloBoardConfig, state: str, *, user_id: str | None) -> str | None:
    mapping = settings_service.trello_mapping_for_state(db, board.alias, state, user_id=user_id)
    return str(mapping.get("list_id")) if mapping and mapping.get("list_id") else None


def _task_snapshot(task: Task, board: TrelloBoardConfig) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "owner_id": task.user_id,
        "task_updated_at": task.updated_at,
        "task_status": task.status,
        "card_id": task.source_id,
        "board_alias": board.alias,
        "board_id": board.board_id,
        "current_state": task.trello_state,
        "current_list_id": task.trello_list_id,
    }


def _validate_local_snapshot(task: Task, payload: dict[str, Any], user_id: str) -> None:
    if task.user_id != user_id or payload.get("owner_id") != user_id:
        raise ValueError("Cambió el propietario de la tarea; prepará una nueva confirmación.")
    if task.updated_at != payload.get("task_updated_at") or task.status != payload.get("task_status"):
        raise ValueError("La tarea cambió desde la propuesta; prepará una nueva confirmación.")


def _validate_task_snapshot(task: Task, payload: dict[str, Any], user_id: str) -> None:
    _validate_local_snapshot(task, payload, user_id)
    _ensure_trello_task(task)
    if task.source_id != payload.get("card_id"):
        raise ValueError("Cambió la card vinculada; prepará una nueva confirmación.")
    if task.trello_state != payload.get("current_state") or task.trello_list_id != payload.get("current_list_id"):
        raise ValueError("La ubicación Trello cambió desde la propuesta; prepará una nueva confirmación.")


def _validate_link_snapshot(db: Session, task: Task, payload: dict[str, Any], user_id: str) -> None:
    _validate_local_snapshot(task, payload, user_id)
    if (
        task.source_type != payload.get("task_source_type")
        or task.scope != payload.get("task_scope")
        or task.title != payload.get("task_title")
        or task.due_at != payload.get("task_due_at")
    ):
        raise ValueError("La tarea cambió desde la propuesta; prepará una nueva confirmación.")
    _ensure_linkable_local_task(db, task, payload["target_state"])


def _validate_board_snapshot(
    db: Session,
    board: TrelloBoardConfig,
    payload: dict[str, Any],
    state_key: str,
    list_id_key: str,
    user_id: str,
) -> None:
    if board.alias != payload.get("board_alias") or board.board_id != payload.get("board_id"):
        raise ValueError("Cambió la configuración del board; prepará una nueva confirmación.")
    expected_list_id = payload.get(list_id_key)
    current_list_id = _configured_list_id(db, board, payload[state_key], user_id=user_id)
    if not expected_list_id or current_list_id != expected_list_id:
        raise ValueError("Cambió o falta el mapeo de lista; prepará una nueva confirmación.")


def _validate_current_list_mapping(
    db: Session,
    board: TrelloBoardConfig,
    task: Task,
    payload: dict[str, Any],
    user_id: str,
) -> None:
    if board.alias != payload.get("board_alias") or board.board_id != payload.get("board_id"):
        raise ValueError("Cambió la configuración del board; prepará una nueva confirmación.")
    current_mapping_id = _configured_list_id(db, board, task.trello_state or "", user_id=user_id)
    if not current_mapping_id or current_mapping_id != payload.get("current_list_id"):
        raise ValueError("Cambió o falta el mapeo de la lista actual; prepará una nueva confirmación.")


def _link_created_card(
    db: Session,
    task: Task,
    board: TrelloBoardConfig,
    payload: dict[str, Any],
    list_id: str,
    card: dict[str, Any],
) -> None:
    card_id = card.get("id")
    if not card_id:
        raise ValueError("Trello creó una respuesta sin card id.")
    now = utc_now_iso()
    task.source_type = "trello"
    task.source_id = str(card_id)
    task.source_url = card.get("shortUrl") or card.get("url")
    task.origin_label = board.alias
    task.scope = board.alias
    task.trello_board_id = board.board_id
    task.trello_board_name = board.name
    task.trello_list_id = card.get("idList") or list_id
    task.trello_list_name = payload.get("target_list_name")
    task.trello_state = payload["target_state"]
    task.last_trello_activity_at = card.get("dateLastActivity")
    task.updated_at = now
    task.last_touched_at = now
    _event(db, task.id, "trello_card_linked", {"card_id": task.source_id, "board": board.alias, "target_state": task.trello_state})
    db.commit()
    db.refresh(task)


async def _resolve_list_id(db: Session, client: TrelloWriteClient, board: TrelloBoardConfig, target_state: str, user_id: str | None = None) -> str:
    display = _state_display(board, target_state)
    lists = await client.get_lists(board.board_id)
    by_id = {item.get("id"): item for item in lists if item.get("id")}
    mapping = settings_service.trello_mapping_for_state(db, board.alias, target_state, user_id=user_id)
    if mapping and mapping.get("list_id"):
        item = by_id.get(mapping["list_id"])
        if item:
            if mapping.get("list_name") != item.get("name"):
                settings_service.update_trello_mapping(db, board.alias, target_state, list_id=item.get("id"), list_name=item.get("name"), user_id=user_id)
            return item["id"]
        raise ValueError(
            f"No encontré la lista configurada para {board.alias}/{target_state}. "
            "Abrí Configuraciones > Trello, validá listas y guardá el mapeo correcto."
        )
    raise ValueError(
        f"Falta configurar list_id para {board.alias}/{target_state} ({display}). "
        "Abrí Configuración > Trello, elegí la lista desde el dropdown y guardá."
    )


def _board(db: Session, alias: str, user_id: str | None = None) -> TrelloBoardConfig:
    board = board_by_alias(alias, db, user_id=user_id)
    if not board or not board.enabled:
        raise ValueError("Board Trello inválido o no configurado.")
    if not board.board_id_configured:
        raise ValueError(f"Falta configurar el board Trello para {board.alias}.")
    return board


def _board_for_task(db: Session, task: Task) -> TrelloBoardConfig:
    board = _board(db, task.scope or task.origin_label or "", user_id=task.user_id)
    if task.trello_board_id and task.trello_board_id != board.board_id:
        raise ValueError(f"El board configurado para {board.alias} no coincide con el origen de esta tarea.")
    return board


def _state_display(board: TrelloBoardConfig, state: str) -> str:
    workflow_state = workflow_state_for_role_or_key(board, state)
    display = workflow_state.list_name if workflow_state else default_list_name(board, state)
    if not display:
        raise ValueError(f"Ese estado Trello no está configurado para {board.alias}.")
    return display


def _task(db: Session, task_id: str, *, user_id: str | None = None) -> Task:
    task = task_service.get_task(db, task_id, user_id=user_id)
    if not task:
        raise ValueError("La tarea ya no existe.")
    return task


def _ensure_task_owner(db: Session, task: Task) -> None:
    if not task.user_id:
        from app.services import ownership

        task.user_id = ownership.integration_owner_user_id(db, "trello")
        db.commit()
        db.refresh(task)


def _ensure_trello_task(task: Task) -> None:
    if not task_service.is_trello_linked_task(task):
        raise ValueError("La tarea no viene de Trello.")


def _audit(
    db: Session,
    confirmation: PendingConfirmation,
    outcome: str,
    *,
    task_id: str | None = None,
    remote: bool | None = None,
    reason: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "confirmation_id": confirmation.id,
        "action_type": confirmation.action_type,
        "outcome": outcome,
    }
    if remote is not None:
        payload["remote"] = remote
    if reason:
        payload["reason"] = reason
    _event(db, task_id, f"trello_mutation_{outcome}", payload, user_id=confirmation.user_id)


def _event(
    db: Session,
    task_id: str | None,
    event_type: str,
    payload: dict | None = None,
    *,
    user_id: str | None = None,
) -> None:
    if task_id and not user_id:
        task = task_service.get_task(db, task_id)
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
    db.commit()
