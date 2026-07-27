from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services import integrations


WORKFLOW_ROLES = {
    "pending",
    "in_progress",
    "review",
    "completed",
    "perpetual",
    "ignored",
    "blocked",
    "backlog",
    "custom_actionable",
    "custom_passive",
}
ACTIONABLE_WORKFLOW_ROLES = {"pending", "in_progress", "review", "custom_actionable"}
CORE_WORKFLOW_ROLES = {"pending", "in_progress", "review", "completed"}


@dataclass(frozen=True)
class TrelloWorkflowState:
    key: str
    label: str
    role: str
    enabled: bool = True
    required_for: tuple[str, ...] = ()
    list_id: str | None = None
    list_name: str | None = None


@dataclass(frozen=True)
class TrelloBoardConfig:
    alias: str
    name: str
    lists: dict[str, list[str]]
    env_board_id: str = ""
    board_id_value: str = ""
    enabled: bool = True
    workflow_states: tuple[TrelloWorkflowState, ...] = field(default_factory=tuple)

    @property
    def board_id_configured(self) -> bool:
        return bool(self.board_id.strip())

    @property
    def read_ready(self) -> bool:
        return self.enabled and self.board_id_configured

    @property
    def write_ready(self) -> bool:
        return self.read_ready

    @property
    def board_id(self) -> str:
        if self.board_id_value:
            return self.board_id_value
        if self.env_board_id == "TRELLO_BOARD_ALPHA_ID":
            return settings.resolved_trello_alpha_board_id
        if self.env_board_id == "TRELLO_BOARD_BETA_ID":
            return settings.resolved_trello_beta_board_id
        return ""


# Legacy environment adapters remain available for existing single-owner
# installs. Settings only exposes one when its board ID is actually configured.
TRELLO_BOARDS = [
    TrelloBoardConfig(
        alias="ALPHA",
        name="Project Alpha",
        env_board_id="TRELLO_BOARD_ALPHA_ID",
        lists={
            "pending": ["TAREAS"],
            "in_progress": ["EN PROCESO"],
            "review": ["EN REVISION"],
            "perpetual": ["Perpetuas"],
            "completed": ["TERMINADAS"],
            "ignored": ["CARPETAS/ARCHIVOS"],
        },
    ),
    TrelloBoardConfig(
        alias="BETA",
        name="Project Beta",
        env_board_id="TRELLO_BOARD_BETA_ID",
        lists={
            "pending": ["TAREAS"],
            "in_progress": ["EN PROCESO"],
            "review": ["EN REVISION"],
            "completed": ["TERMINADAS"],
            "ignored": ["CARPETAS - ARCHIVOS", "MARKETING Y COMUNICACION"],
        },
    ),
]


def board_configs(db=None, *, include_disabled: bool = False, user_id: str | None = None) -> list[TrelloBoardConfig]:
    if db is None:
        boards = TRELLO_BOARDS
    else:
        boards = _settings_board_configs(db, user_id=user_id)
    if include_disabled:
        return boards
    return [board for board in boards if board.enabled]


def trello_credentials_ready(db=None, *, user_id: str | None = None) -> bool:
    if db is not None:
        credentials = integrations.get_trello_credentials_for_user(db, user_id)
        return bool(credentials and credentials.ready)
    return bool(settings.trello_api_key and settings.trello_token and settings.trello_member_id)


def trello_boards_ready(db=None, *, user_id: str | None = None) -> bool:
    return any(board.read_ready for board in board_configs(db, user_id=user_id))


def trello_writes_ready(db=None, *, user_id: str | None = None) -> bool:
    return bool(settings.trello_enabled and settings.trello_write_enabled and trello_credentials_ready(db, user_id=user_id) and trello_boards_ready(db, user_id=user_id))


def board_by_alias(alias: str, db=None, *, user_id: str | None = None) -> TrelloBoardConfig | None:
    for board in board_configs(db, include_disabled=True, user_id=user_id):
        if board.alias.casefold() == alias.casefold():
            return board
    return None


def list_state_for_name(board: TrelloBoardConfig, list_name: str) -> str | None:
    normalized = list_name.casefold()
    for state, names in board.lists.items():
        if any(normalized == name.casefold() for name in names):
            return state
    return None


def default_list_name(board: TrelloBoardConfig, state: str) -> str | None:
    values = board.lists.get(state)
    return values[0] if values else None


def workflow_state_for_role(board: TrelloBoardConfig, role: str) -> TrelloWorkflowState | None:
    for state in board.workflow_states:
        if state.enabled and state.role == role:
            return state
    return None


def workflow_state_for_list(board: TrelloBoardConfig, list_id: str | None, list_name: str | None) -> TrelloWorkflowState | None:
    for state in board.workflow_states:
        if not state.enabled:
            continue
        if list_id and state.list_id == list_id:
            return state
        if list_name and state.list_name and state.list_name.casefold() == list_name.casefold():
            return state
    role = list_state_for_name(board, list_name) if list_name else None
    return workflow_state_for_role(board, role) if role else None


def workflow_state_for_role_or_key(board: TrelloBoardConfig, value: str) -> TrelloWorkflowState | None:
    for state in board.workflow_states:
        if state.enabled and (state.role == value or state.key == value):
            return state
    return None


def workflow_role_enabled(board: TrelloBoardConfig, role: str) -> bool:
    return workflow_state_for_role(board, role) is not None


def _settings_board_configs(db, *, user_id: str | None = None) -> list[TrelloBoardConfig]:
    from app.services import settings_service

    effective = settings_service.get_settings(db, user_id=user_id)
    raw_boards = ((effective.get("trello") or {}).get("boards") or {})
    result: list[TrelloBoardConfig] = []
    for alias, raw_board in raw_boards.items():
        if not isinstance(raw_board, dict):
            continue
        clean_alias = str(raw_board.get("alias") or alias).strip()
        if not clean_alias:
            continue
        legacy = _legacy_board(clean_alias)
        workflow_states = tuple(_workflow_states_from_raw_board(clean_alias, raw_board, legacy))
        lists: dict[str, list[str]] = {}
        for state in workflow_states:
            if state.enabled and state.list_name:
                lists.setdefault(state.role, []).append(state.list_name)
        result.append(
            TrelloBoardConfig(
                alias=clean_alias,
                name=str(raw_board.get("name") or clean_alias),
                env_board_id=legacy.env_board_id if legacy else "",
                lists=lists,
                board_id_value=str(raw_board.get("board_id") or ""),
                enabled=bool(raw_board.get("enabled", True)),
                workflow_states=workflow_states,
            )
        )
    return result


def _legacy_board(alias: str) -> TrelloBoardConfig | None:
    for board in TRELLO_BOARDS:
        if board.alias.casefold() == alias.casefold():
            return board
    return None


def _workflow_states_from_raw_board(alias: str, raw_board: dict[str, Any], legacy: TrelloBoardConfig | None) -> list[TrelloWorkflowState]:
    raw_workflow = raw_board.get("workflow_states")
    if isinstance(raw_workflow, list):
        states = [_workflow_state_from_dict(item, index) for index, item in enumerate(raw_workflow) if isinstance(item, dict)]
    else:
        raw_states = raw_board.get("states") if isinstance(raw_board.get("states"), dict) else {}
        states = _workflow_states_from_legacy_states(alias, raw_states, legacy)
    return _dedupe_workflow_states(states)


def _workflow_state_from_dict(item: dict[str, Any], index: int) -> TrelloWorkflowState:
    role = str(item.get("role") or item.get("key") or "custom_actionable").strip()
    if role not in WORKFLOW_ROLES:
        role = "custom_actionable"
    key = str(item.get("key") or role or f"state_{index + 1}").strip()
    return TrelloWorkflowState(
        key=key,
        label=str(item.get("label") or _workflow_label(role, key)).strip(),
        role=role,
        enabled=bool(item.get("enabled", True)),
        required_for=tuple(str(value) for value in item.get("required_for") or ()),
        list_id=str(item.get("list_id") or "") or None,
        list_name=str(item.get("list_name") or "") or None,
    )


def _workflow_states_from_legacy_states(alias: str, raw_states: dict[str, Any], legacy: TrelloBoardConfig | None) -> list[TrelloWorkflowState]:
    states: list[TrelloWorkflowState] = []
    roles = ["pending", "in_progress", "review", "completed", "perpetual"]
    for role in roles:
        mapping = raw_states.get(role) if isinstance(raw_states.get(role), dict) else {}
        fallback_names = (legacy.lists.get(role) if legacy else None) or _dynamic_default_names(role)
        list_name = mapping.get("list_name") or (fallback_names[0] if fallback_names else None)
        list_id = mapping.get("list_id")
        enabled = bool(mapping.get("enabled", True)) if mapping else _legacy_role_enabled(alias, role, legacy)
        states.append(
            TrelloWorkflowState(
                key=role,
                label=_workflow_label(role, role),
                role=role,
                enabled=enabled,
                required_for=_required_for(role),
                list_id=str(list_id or "") or None,
                list_name=str(list_name or "") or None,
            )
        )
    ignored_names = (legacy.lists.get("ignored") if legacy else None) or []
    ignored_mappings = raw_states.get("ignored")
    if isinstance(ignored_mappings, dict):
        ignored_names = [str(ignored_mappings.get("list_name") or "Ignorar")]
    for index, list_name in enumerate(ignored_names):
        states.append(
            TrelloWorkflowState(
                key=f"ignored_{index + 1}",
                label=str(list_name),
                role="ignored",
                enabled=True,
                list_name=str(list_name),
            )
        )
    return states


def _legacy_role_enabled(alias: str, role: str, legacy: TrelloBoardConfig | None) -> bool:
    if role == "perpetual":
        return bool(legacy and role in legacy.lists)
    if role in CORE_WORKFLOW_ROLES:
        return bool(legacy is None or role in legacy.lists)
    return False


def _dynamic_default_names(role: str) -> list[str]:
    return {
        "pending": ["TAREAS"],
        "in_progress": ["EN PROCESO"],
        "review": ["EN REVISION"],
        "completed": ["TERMINADAS"],
        "perpetual": ["Perpetuas"],
    }.get(role, [])


def _required_for(role: str) -> tuple[str, ...]:
    return {
        "pending": ("create_card", "start_transition"),
        "in_progress": ("start_transition", "stale_checkins"),
        "review": ("finish_from_in_progress",),
        "completed": ("complete",),
    }.get(role, ())


def _workflow_label(role: str, key: str) -> str:
    return {
        "pending": "Tareas",
        "in_progress": "En proceso",
        "review": "En revisión",
        "completed": "Terminadas",
        "perpetual": "Perpetuas",
        "ignored": "Ignorar",
        "blocked": "Bloqueadas",
        "backlog": "Backlog",
        "custom_actionable": "Accionable",
        "custom_passive": "Pasiva",
    }.get(role, key)


def _dedupe_workflow_states(states: list[TrelloWorkflowState]) -> list[TrelloWorkflowState]:
    result: list[TrelloWorkflowState] = []
    seen: set[str] = set()
    for state in states:
        key = state.key or state.role
        if key in seen:
            key = f"{key}_{len(seen) + 1}"
        seen.add(key)
        result.append(
            TrelloWorkflowState(
                key=key,
                label=state.label,
                role=state.role,
                enabled=state.enabled,
                required_for=state.required_for,
                list_id=state.list_id,
                list_name=state.list_name,
            )
        )
    return result
