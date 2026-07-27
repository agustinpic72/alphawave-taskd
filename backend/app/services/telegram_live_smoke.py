from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Awaitable, Callable

import httpx


RequestCallable = Callable[[str, str, dict[str, Any] | None, dict[str, Any] | None], Awaitable[dict[str, Any]]]
DEFAULT_SMOKE_MESSAGE = "[M17 SMOKE] Telegram live smoke OK desde AlphaWave local. No requiere accion."


class TelegramSmokeClient:
    def __init__(
        self,
        token: str,
        *,
        timeout: float = 5.0,
        requester: RequestCallable | None = None,
    ) -> None:
        self._token = token
        self._timeout = timeout
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._requester = requester

    async def get_me(self) -> dict[str, Any]:
        return await self._request("GET", "getMe")

    async def send_smoke_message(
        self,
        chat_id: str,
        text: str = DEFAULT_SMOKE_MESSAGE,
        *,
        send_enabled: bool = False,
        allowed_chat_ids: set[str] | None = None,
    ) -> int | None:
        if not send_enabled:
            raise RuntimeError("Telegram smoke send blocked: set ALPHAWAVE_TELEGRAM_SMOKE_SEND=1 to send one message.")
        clean_chat_id = str(chat_id or "").strip()
        if not clean_chat_id:
            raise RuntimeError("Telegram smoke send blocked: no chat id configured.")
        if allowed_chat_ids is not None and clean_chat_id not in allowed_chat_ids:
            raise RuntimeError("Telegram smoke send blocked: chat id is not allowlisted.")
        payload = await self._request("POST", "sendMessage", json={"chat_id": clean_chat_id, "text": text})
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        return result.get("message_id")

    async def _request(
        self,
        method: str,
        action: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._requester:
            payload = await self._requester(method.upper(), action, params, json)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if method.upper() == "GET":
                    response = await client.get(f"{self._base_url}/{action}", params=params)
                else:
                    response = await client.post(f"{self._base_url}/{action}", json=json)
                payload = _telegram_payload(response)
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram {action} failed: {payload.get('description') or 'unknown error'}")
        return payload

    def redact(self, value: Any) -> str:
        return sanitize_telegram_error(value, self._token)


@dataclass
class TelegramSmokeReport:
    status: str
    local_app: str = "unknown"
    local_status: dict[str, Any] = field(default_factory=dict)
    get_me_status: str = "skipped"
    bot_username: str | None = None
    bot_id_hint: str | None = None
    send_status: str = "skipped"
    send_message_id_hint: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def exit_code(self) -> int:
        return 1 if self.errors else 0

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        if self.status == "ok":
            self.status = "warning"

    def error(self, message: str) -> None:
        self.errors.append(message)
        self.status = "error"


async def run_telegram_live_smoke(
    system_status: dict[str, Any],
    client: TelegramSmokeClient | None,
    *,
    telegram_enabled: bool,
    token_configured: bool,
    allowed_chat_ids: set[str],
    send_enabled: bool = False,
    selected_chat_id: str | None = None,
    smoke_message: str = DEFAULT_SMOKE_MESSAGE,
) -> TelegramSmokeReport:
    local_telegram = _local_telegram_status(system_status)
    report = TelegramSmokeReport(status="ok", local_app="ok", local_status=local_telegram)
    if not telegram_enabled:
        report.status = "skipped"
        report.skipped_reason = "Telegram desactivado."
        report.send_status = "skipped_disabled"
        return report
    if not token_configured or not client:
        report.status = "skipped"
        report.skipped_reason = "Telegram requiere token."
        report.send_status = "skipped_requires_config"
        return report
    if not allowed_chat_ids:
        report.status = "skipped"
        report.skipped_reason = "Telegram requiere allowlist/chat id."
        report.send_status = "skipped_requires_config"
        return report

    try:
        get_me = await client.get_me()
        bot = get_me.get("result") if isinstance(get_me.get("result"), dict) else {}
        report.get_me_status = "ok"
        username = bot.get("username")
        report.bot_username = f"@{username}" if username else None
        report.bot_id_hint = redact_chat_id(str(bot.get("id") or ""))
    except Exception as exc:
        report.get_me_status = "error"
        report.error(f"Telegram getMe failed: {client.redact(exc)}")
        return report

    target_chat = _resolve_send_chat(allowed_chat_ids, selected_chat_id)
    if not send_enabled:
        report.send_status = "skipped_opt_in_required"
        return report
    if selected_chat_id and selected_chat_id not in allowed_chat_ids:
        report.send_status = "blocked_chat_not_allowlisted"
        report.error("Telegram send smoke blocked: selected chat id is not allowlisted.")
        return report
    if not target_chat:
        report.send_status = "blocked_requires_explicit_chat"
        report.error("Telegram send smoke blocked: multiple allowlisted chats require ALPHAWAVE_TELEGRAM_SMOKE_CHAT_ID.")
        return report

    try:
        message_id = await client.send_smoke_message(
            target_chat,
            smoke_message,
            send_enabled=True,
            allowed_chat_ids=allowed_chat_ids,
        )
        report.send_status = "ok"
        report.send_message_id_hint = redact_chat_id(str(message_id or ""))
    except Exception as exc:
        report.send_status = "error"
        report.error(f"Telegram send smoke failed: {client.redact(exc)}")
    return report


def sanitize_telegram_error(value: Any, token: str = "", chat_ids: set[str] | None = None) -> str:
    text = str(value)
    if token:
        text = text.replace(token, "[redacted]")
    text = re.sub(r"https://api\.telegram\.org/bot[^/\s]+", "https://api.telegram.org/bot[redacted]", text)
    text = re.sub(r"bot[0-9]+:[A-Za-z0-9_-]+", "bot[redacted]", text)
    for chat_id in chat_ids or set():
        if chat_id:
            text = text.replace(chat_id, redact_chat_id(chat_id))
    return text


def parse_allowed_chat_ids(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in re.split(r"[,;\s]+", value) if item.strip()}


def redact_chat_id(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    sign = "-" if clean.startswith("-") else ""
    digits = clean[1:] if sign else clean
    if len(digits) <= 4:
        return f"{sign}{'*' * len(digits)}"
    return f"{sign}{digits[:2]}...{digits[-2:]}"


def format_report(report: TelegramSmokeReport) -> str:
    lines = ["Telegram live smoke", f"Local app: {report.local_app.upper()}"]
    local = report.local_status
    if local:
        lines.append(f"Local Telegram: {local.get('status', 'unknown')} · {local.get('detail', 'sin detalle')}")
        lines.append(f"Manual commands: {'available' if local.get('manual_commands_available') else 'not available'}")
        lines.append(f"Polling active: {bool(local.get('polling_active'))}")
    if report.status == "skipped":
        lines.append(f"Status: SKIPPED · {report.skipped_reason}")
        return "\n".join(lines)
    lines.append(f"getMe: {report.get_me_status.upper()}")
    if report.bot_username:
        lines.append(f"Bot: {report.bot_username}")
    if report.bot_id_hint:
        lines.append(f"Bot id: {report.bot_id_hint}")
    lines.append(f"Send smoke: {report.send_status}")
    if report.send_message_id_hint:
        lines.append(f"Message id: {report.send_message_id_hint}")
    for warning in report.warnings:
        lines.append(f"WARNING: {warning}")
    for error in report.errors:
        lines.append(f"ERROR: {error}")
    return "\n".join(lines)


def _local_telegram_status(system_status: dict[str, Any]) -> dict[str, Any]:
    services = system_status.get("services") if isinstance(system_status.get("services"), dict) else {}
    telegram = services.get("telegram") if isinstance(services.get("telegram"), dict) else {}
    return telegram


def _resolve_send_chat(allowed_chat_ids: set[str], selected_chat_id: str | None) -> str | None:
    if selected_chat_id:
        return selected_chat_id if selected_chat_id in allowed_chat_ids else None
    if len(allowed_chat_ids) == 1:
        return next(iter(allowed_chat_ids))
    return None


def _telegram_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {"ok": False, "description": f"HTTP {response.status_code}"}
    if response.is_success:
        return payload
    description = payload.get("description") if isinstance(payload, dict) else None
    return {"ok": False, "description": description or f"HTTP {response.status_code}"}
