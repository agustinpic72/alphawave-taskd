from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Awaitable, Callable

import httpx


RequestCallable = Callable[[str, str, dict[str, Any]], Awaitable[Any]]

REQUIRED_ROLES = {"pending", "completed"}
RECOMMENDED_ROLES = {"in_progress", "review"}


class ReadOnlyTrelloClient:
    def __init__(
        self,
        api_key: str,
        token: str,
        *,
        base_url: str = "https://api.trello.com/1",
        timeout: float = 20.0,
        requester: RequestCallable | None = None,
    ) -> None:
        self._api_key = api_key
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._requester = requester

    async def request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        normalized_method = method.upper()
        if normalized_method != "GET":
            raise RuntimeError(f"Read-only smoke blocked Trello write: {normalized_method} {path}")
        query = {"key": self._api_key, "token": self._token}
        if params:
            query.update(params)
        if self._requester:
            return await self._requester(normalized_method, path, query)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base_url}{path}", params=query)
            response.raise_for_status()
            return response.json()

    async def get_board(self, board_id: str) -> dict[str, Any]:
        return await self.request(
            "GET",
            f"/boards/{board_id}",
            {"fields": "name,closed,url,shortLink"},
        )

    async def get_lists(self, board_id: str) -> list[dict[str, Any]]:
        return await self.request(
            "GET",
            f"/boards/{board_id}/lists",
            {"fields": "name,closed", "filter": "all"},
        )

    async def get_cards(self, board_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return await self.request(
            "GET",
            f"/boards/{board_id}/cards",
            {
                "fields": "name,idList,closed,dateLastActivity",
                "filter": "open",
                "limit": max(1, min(limit, 100)),
            },
        )

    def redact(self, value: str) -> str:
        return sanitize_trello_error(value, self._api_key, self._token)


@dataclass
class SmokeBoardResult:
    alias: str
    name: str
    enabled: bool
    board_id: str | None = None
    remote_name: str | None = None
    auto_confirm_writes: bool = False
    status: str = "ok"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_lists: int = 0
    checked_cards: int | None = None

    def mark_error(self, message: str) -> None:
        self.status = "error"
        self.errors.append(message)

    def warn(self, message: str) -> None:
        if self.status != "error":
            self.status = "warning"
        self.warnings.append(message)


@dataclass
class SmokeReport:
    status: str
    boards: list[SmokeBoardResult]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def exit_code(self) -> int:
        if self.status == "not_configured":
            return 3
        if self.errors or any(board.errors for board in self.boards):
            return 1
        return 0


def sanitize_trello_error(value: Any, api_key: str = "", token: str = "") -> str:
    text = str(value)
    for secret in (api_key, token):
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"([?&](?:key|token)=)[^&\\s]+", r"\1[redacted]", text, flags=re.IGNORECASE)
    return text


def configured_trello_boards(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("boards"), list):
        return [board for board in payload["boards"] if isinstance(board, dict)]
    settings_payload = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    trello = settings_payload.get("trello") if isinstance(settings_payload, dict) else {}
    boards = trello.get("boards") if isinstance(trello, dict) else {}
    if isinstance(boards, dict):
        result = []
        for alias, board in boards.items():
            if not isinstance(board, dict):
                continue
            normalized = dict(board)
            normalized.setdefault("alias", alias)
            result.append(normalized)
        return result
    if isinstance(boards, list):
        return [board for board in boards if isinstance(board, dict)]
    return []


async def run_trello_readonly_smoke(
    settings_payload: dict[str, Any],
    client: ReadOnlyTrelloClient,
    *,
    trello_enabled: bool,
    credentials_ready: bool,
    include_cards: bool = False,
    max_cards: int = 20,
) -> SmokeReport:
    if not trello_enabled or not credentials_ready:
        return SmokeReport(status="not_configured", boards=[], skipped_reason="Trello no está configurado o faltan credenciales.")

    boards = configured_trello_boards(settings_payload)
    enabled_boards = [board for board in boards if bool(board.get("enabled", True))]
    if not enabled_boards:
        return SmokeReport(status="not_configured", boards=[], skipped_reason="No hay boards Trello activos en Settings.")

    board_results: list[SmokeBoardResult] = []
    for board in boards:
        result = await _validate_board(board, client, include_cards=include_cards, max_cards=max_cards)
        board_results.append(result)

    errors = [message for board in board_results for message in board.errors]
    warnings = [message for board in board_results for message in board.warnings]
    status = "error" if errors else "warning" if warnings else "ok"
    return SmokeReport(status=status, boards=board_results, errors=errors, warnings=warnings)


async def _validate_board(
    board: dict[str, Any],
    client: ReadOnlyTrelloClient,
    *,
    include_cards: bool,
    max_cards: int,
) -> SmokeBoardResult:
    alias = str(board.get("alias") or board.get("name") or "board")
    name = str(board.get("name") or alias)
    board_id = str(board.get("board_id") or "").strip()
    enabled = bool(board.get("enabled", True))
    result = SmokeBoardResult(
        alias=alias,
        name=name,
        enabled=enabled,
        board_id=board_id or None,
        auto_confirm_writes=bool(board.get("auto_confirm_writes", False)),
        status="disabled" if not enabled else "ok",
    )
    if not enabled:
        return result
    if not board_id:
        result.warn(f"{alias}: template local sin board_id; lectura remota omitida.")
        return result

    try:
        remote_board = await client.get_board(board_id)
        remote_lists = await client.get_lists(board_id)
        result.remote_name = str(remote_board.get("name") or "")
    except Exception as exc:
        result.mark_error(f"{alias}: no pude leer board/listas en Trello: {client.redact(exc)}")
        return result

    if remote_board.get("closed"):
        result.mark_error(f"{alias}: el board remoto está cerrado.")
    if result.remote_name and result.remote_name != name:
        result.warn(f"{alias}: nombre local '{name}' difiere del remoto '{result.remote_name}'.")

    _validate_workflow_states(board, remote_lists, result)

    if include_cards and not result.errors:
        try:
            cards = await client.get_cards(board_id, limit=max_cards)
            result.checked_cards = len(cards)
        except Exception as exc:
            result.warn(f"{alias}: no pude leer cards acotadas: {client.redact(exc)}")
    return result


def _validate_workflow_states(board: dict[str, Any], remote_lists: list[dict[str, Any]], result: SmokeBoardResult) -> None:
    lists_by_id = {str(item.get("id")): item for item in remote_lists if item.get("id")}
    result.checked_lists = len(lists_by_id)
    workflow_states = _workflow_states(board)
    roles_seen = {str(item.get("role") or item.get("key") or "") for item in workflow_states if bool(item.get("enabled", True))}

    for role in sorted(REQUIRED_ROLES):
        if role not in roles_seen:
            result.mark_error(f"{result.alias}: falta estado requerido activo '{role}'.")

    for item in workflow_states:
        key = str(item.get("key") or item.get("role") or "state")
        role = str(item.get("role") or key)
        label = str(item.get("label") or key)
        enabled = bool(item.get("enabled", True))
        list_id = str(item.get("list_id") or "").strip()
        list_name = str(item.get("list_name") or "").strip()
        if not enabled:
            continue
        if not list_id:
            if role in REQUIRED_ROLES:
                result.mark_error(f"{result.alias}: '{label}' ({role}) requiere list_id.")
            elif role in RECOMMENDED_ROLES:
                result.warn(f"{result.alias}: '{label}' ({role}) no tiene list_id; algunas acciones quedarán limitadas.")
            continue
        remote_list = lists_by_id.get(list_id)
        if not remote_list:
            result.mark_error(f"{result.alias}: list_id '{list_id}' para '{label}' no existe en este board.")
            continue
        if remote_list.get("closed"):
            result.mark_error(f"{result.alias}: lista '{remote_list.get('name') or list_id}' para '{label}' está cerrada.")
        remote_name = str(remote_list.get("name") or "")
        if list_name and remote_name and list_name != remote_name:
            result.warn(f"{result.alias}: '{label}' usa list_id válido, pero list_name local '{list_name}' ahora es '{remote_name}'.")


def _workflow_states(board: dict[str, Any]) -> list[dict[str, Any]]:
    workflow = board.get("workflow_states")
    if isinstance(workflow, list):
        return [item for item in workflow if isinstance(item, dict)]
    states = board.get("states")
    if not isinstance(states, dict):
        return []
    result = []
    for role, mapping in states.items():
        if not isinstance(mapping, dict):
            continue
        result.append(
            {
                "key": role,
                "role": role,
                "label": role,
                "enabled": bool(mapping.get("enabled", True)),
                "list_id": mapping.get("list_id"),
                "list_name": mapping.get("list_name"),
            }
        )
    return result


def format_report(report: SmokeReport) -> str:
    lines = ["Trello read-only smoke"]
    if report.status == "not_configured":
        lines.append(f"SKIPPED: {report.skipped_reason or 'Trello no configurado.'}")
        return "\n".join(lines)
    lines.append(f"Status: {report.status.upper()}")
    for board in report.boards:
        lines.append("")
        state = board.status.upper()
        if not board.enabled:
            lines.append(f"- {board.alias} · {board.name}: DISABLED (skipped)")
            continue
        lines.append(f"- {board.alias} · {board.name}: {state}")
        lines.append(f"  board_id: {'configured' if board.board_id else 'missing'}")
        if board.remote_name:
            lines.append(f"  remote board: {board.remote_name}")
        lines.append(f"  lists checked: {board.checked_lists}")
        lines.append(f"  auto-confirm writes: {'enabled' if board.auto_confirm_writes else 'disabled'} (not executed)")
        if board.checked_cards is not None:
            lines.append(f"  cards sampled: {board.checked_cards}")
        for warning in board.warnings:
            lines.append(f"  WARNING: {warning}")
        for error in board.errors:
            lines.append(f"  ERROR: {error}")
    return "\n".join(lines)
