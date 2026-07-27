from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT, settings
from app.services import llm as llm_service
from app.services import settings_service
from app.services.backups import backup_status_payload


REDACTION_RULES_VERSION = 1
SENSITIVE_KEYS = {
    "token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "authorization",
    "auth",
    "cookie",
    "session",
    "chat_id",
    "ciphertext",
    "code_hash",
    "link_code",
    "telegram_bot_token",
    "trello_token",
    "trello_api_key",
    "database_url",
    "db_url",
}


def diagnostics_payload(
    db: Session,
    status_payload: dict[str, Any] | None = None,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    from app.services import system

    status_payload = status_payload or system.status(db, user_id).model_dump()
    effective = settings_service.get_settings(db, user_id=user_id)
    runtime = deepcopy(status_payload.get("environment") or {})
    services = deepcopy(status_payload.get("services") or {})
    payload = {
        "generated_at": status_payload.get("generated_at"),
        "runtime": runtime,
        "status": {
            "overall": status_payload.get("overall"),
            "services": services,
            "weekend_mode": status_payload.get("weekend_mode"),
        },
        "settings_summary": settings_summary(db, effective, user_id=user_id),
        "backup_summary": backup_status_payload(db=db),
        "warnings": _diagnostic_warnings(status_payload),
        "redaction": {"applied": True, "rules_version": REDACTION_RULES_VERSION},
    }
    return sanitize_diagnostics(payload)


def settings_summary(
    db: Session,
    effective: dict[str, Any] | None = None,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    effective = effective or settings_service.get_settings(db, user_id=user_id)
    trello_boards = []
    for alias, board in ((effective.get("trello") or {}).get("boards") or {}).items():
        if not isinstance(board, dict):
            continue
        workflow_states = board.get("workflow_states") if isinstance(board.get("workflow_states"), list) else []
        validation = board.get("validation") if isinstance(board.get("validation"), dict) else {}
        trello_boards.append(
            {
                "alias": str(board.get("alias") or alias),
                "name": str(board.get("name") or board.get("alias") or alias),
                "enabled": bool(board.get("enabled", True)),
                "board_id_present": bool(str(board.get("board_id") or "").strip()),
                "workflow_states_active": len([item for item in workflow_states if isinstance(item, dict) and item.get("enabled", True)]),
                "validation_status": validation.get("status") or "pending",
                "auto_confirm_writes": bool(board.get("auto_confirm_writes", False)),
            }
        )
    advanced = effective.get("advanced") or {}
    editable = advanced.get("editable") or {}
    llm_status = llm_service.llm_status(db, user_id)
    priority = effective.get("priority") or {}
    criteria = priority.get("criteria") if isinstance(priority.get("criteria"), dict) else {}
    return sanitize_diagnostics(
        {
            "general": {"timezone": (effective.get("general") or {}).get("timezone")},
            "reminders": {
                "enabled": bool((effective.get("reminders") or {}).get("enabled", True)),
                "default_time": (effective.get("reminders") or {}).get("default_time"),
            },
            "briefing": {
                "enabled": bool((effective.get("briefing") or {}).get("enabled", True)),
                "time": (effective.get("briefing") or {}).get("time"),
                "late_cutoff": (effective.get("briefing") or {}).get("late_cutoff"),
                "timezone": (effective.get("briefing") or {}).get("timezone"),
            },
            "trello": {
                "enabled": bool((effective.get("trello") or {}).get("enabled", False)),
                "write_enabled": bool(editable.get("trello_write_enabled")),
                "boards": trello_boards,
            },
            "llm": {
                "enabled": llm_status.user_enabled,
                "provider": llm_status.provider,
                "status": llm_status.status,
                "configured": llm_status.api_key_configured,
                "model": llm_status.model,
                "validation_status": llm_status.validation_status,
                "safe_to_use": llm_status.safe_to_use,
                "fallback_available": llm_status.fallback_available,
            },
            "priority": {
                "preset": priority.get("preset"),
                "criteria_count": len(criteria),
                "enabled_criteria": [key for key, value in criteria.items() if isinstance(value, dict) and value.get("enabled", True)],
            },
            "backups": {
                "enabled": bool((effective.get("backups") or {}).get("enabled", True)),
                "retention_days": (effective.get("backups") or {}).get("retention_days"),
            },
        }
    )


def sanitize_diagnostics(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            clean_key = str(key)
            if _is_sensitive_key(clean_key):
                sanitized[clean_key] = _redact_chat_id(str(item)) if "chat" in clean_key.casefold() else "<redacted>"
            else:
                sanitized[clean_key] = sanitize_diagnostics(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_diagnostics(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_diagnostics(item) for item in value]
    if isinstance(value, Path):
        return _redact_path(str(value))
    if isinstance(value, str):
        return redact_sensitive(value)
    return value


def redact_sensitive(text: str) -> str:
    sanitized = str(text)
    for secret in (settings.telegram_bot_token, settings.telegram_allowed_user_id, settings.trello_api_key, settings.trello_token):
        if secret:
            replacement = _redact_chat_id(secret) if secret == settings.telegram_allowed_user_id else "<redacted>"
            sanitized = sanitized.replace(secret, replacement)
    sanitized = re.sub(r"https://api\.telegram\.org/bot[^/\s]+", "https://api.telegram.org/bot<redacted>", sanitized)
    sanitized = re.sub(r"bot[0-9]+:[A-Za-z0-9_-]+", "bot<redacted>", sanitized)
    sanitized = re.sub(r"([?&](?:key|token)=)[^&\s]+", r"\1<redacted>", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"postgres(?:ql)?://([^:/\s]+):([^@\s]+)@", r"postgresql://\1:<redacted>@", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"(?i)(token|api_key|apikey|secret|password|passwd|authorization|cookie|session)(['\"]?\s*[:=]\s*['\"]?)[^,'\"\s}]+", r"\1\2<redacted>", sanitized)
    sanitized = _redact_path(sanitized)
    return sanitized


def _diagnostic_warnings(status_payload: dict[str, Any]) -> list[str]:
    warnings = []
    services = status_payload.get("services") if isinstance(status_payload.get("services"), dict) else {}
    for name, service in services.items():
        if isinstance(service, dict) and service.get("status") in {"warning", "error", "requires_configuration", "unverified"}:
            warnings.append(f"{name}: {service.get('status')}")
    return warnings


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in SENSITIVE_KEYS or any(part in lowered for part in ("token", "api_key", "apikey", "secret", "password", "passwd", "authorization", "cookie", "session"))


def _redact_chat_id(value: str) -> str:
    clean = str(value or "")
    if not clean:
        return ""
    sign = "-" if clean.startswith("-") else ""
    digits = clean[1:] if sign else clean
    if not digits.isdigit():
        return "<redacted>"
    if len(digits) <= 6:
        return f"{sign}{'*' * len(digits)}"
    return f"{sign}{digits[:3]}...{digits[-3:]}"


def _redact_path(value: str) -> str:
    home = str(Path.home())
    repo = str(REPO_ROOT)
    sanitized = value.replace(home, "~")
    sanitized = sanitized.replace(repo, "<repo>")
    return sanitized
