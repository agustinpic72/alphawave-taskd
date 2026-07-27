import copy
from dataclasses import dataclass
import json
import re
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.tasks import Task
from app.models.settings import AppSetting, SettingsAudit, UserSetting
from app.services.time import utc_now_iso
from app.services import integrations
from app.services import llm as llm_service
from app.services.trello_config import (
    TrelloBoardConfig,
    TrelloWorkflowState,
    WORKFLOW_ROLES,
    board_by_alias,
    board_configs,
    default_list_name,
    trello_boards_ready,
    trello_credentials_ready,
    workflow_state_for_list,
    workflow_state_for_role_or_key,
)


SETTING_SECTIONS = {"general", "briefing", "reminders", "modes", "priority", "trello", "backups", "advanced"}
USER_SCOPED_SETTINGS = {"general", "briefing", "reminders", "modes", "priority"}
INSTANCE_SCOPED_SETTINGS = {"trello", "backups", "advanced"}
USER_SETTING_SECTION_KEY = "__section__"
EDITABLE_ADVANCED_KEYS = {"trello_write_enabled", "trello_auto_confirm_writes"}
WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKEND_NOTIFICATION_KEYS = {
    "explicit_reminder": "reminders_explicit",
    "overdue_reminder": "reminders_overdue",
    "briefing": "briefing_auto",
    "briefing_auto": "briefing_auto",
    "briefing_late_startup": "briefing_late_startup",
    "checkin": "perpetual_checkins",
    "perpetual": "perpetual_checkins",
    "perpetual_checkins": "perpetual_checkins",
    "stale_in_progress": "stale_in_progress",
}
WEEKEND_ALWAYS_ALLOWED_CATEGORIES = {"manual_telegram", "trello_sync", "backup"}
ACTIONABLE_TRELLO_STATES = ("pending", "in_progress", "review", "completed", "perpetual")
WORKFLOW_STATE_ROLES = (
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
)
LOCAL_SCOPES = ("Inbox", "Personal")
TRELLO_BOARD_ALIAS_RE = re.compile(r"^[A-Za-z0-9_]{2,24}$")

DEFAULT_PRIORITY_CRITERIA = {
    "due_date": {"enabled": True, "weight": 100, "label": "Fecha visible"},
    "manual_priority": {"enabled": True, "weight": 92, "label": "Prioridad manual"},
    "urgency": {"enabled": True, "weight": 84, "label": "Urgencia"},
    "impact": {"enabled": True, "weight": 76, "label": "Impacto"},
    "blocking": {"enabled": True, "weight": 68, "label": "Bloqueo"},
    "stale_in_progress": {"enabled": True, "weight": 60, "label": "EN PROCESO estancada"},
    "source_priority": {"enabled": True, "weight": 52, "label": "Prioridad de Trello"},
    "effort": {"enabled": True, "weight": 44, "label": "Esfuerzo"},
    "age": {"enabled": True, "weight": 36, "label": "Antigüedad"},
}

LEGACY_PRIORITY_CRITERIA = {
    "priority_high",
    "priority_medium_high",
    "priority_medium",
    "priority_low",
    "deadline",
    "effort_quick",
    "effort_medium",
    "effort_deep",
}


@dataclass(frozen=True)
class WeekendModeState:
    enabled: bool
    active_today: bool
    timezone: str
    active_days: list[str]
    active_scopes: list[str]
    notifications: dict[str, bool]
    allowed: list[str]
    muted: list[str]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        summary = "Activo hoy" if self.active_today else ("Programado, no activo hoy" if self.enabled else "Desactivado")
        return {
            "enabled": self.enabled,
            "active_today": self.active_today,
            "timezone": self.timezone,
            "active_days": self.active_days,
            "active_scopes": self.active_scopes,
            "notifications": self.notifications,
            "allowed": self.allowed,
            "muted": self.muted,
            "summary": summary,
            "reason": self.reason,
        }


def default_settings() -> dict[str, Any]:
    return {
        "general": {
            "timezone": settings.app_timezone,
        },
        "briefing": {
            "enabled": settings.daily_briefing_enabled,
            "time": settings.daily_briefing_time,
            "late_cutoff": settings.daily_briefing_late_cutoff,
            "timezone": settings.daily_briefing_timezone,
            "send_on_startup": settings.daily_briefing_send_on_startup,
            "include_inbox": True,
            "include_perpetuals": True,
            "include_confirmations": True,
            "include_attention": True,
            "include_stale_in_progress": True,
            "max_deep_work": settings.daily_briefing_max_deep_work,
            "max_quick_tasks": settings.daily_briefing_max_quick_tasks,
            "max_reviews": settings.daily_briefing_max_reviews,
            "max_attention": settings.daily_briefing_max_attention,
            "max_inbox": settings.daily_briefing_max_inbox,
            "max_perpetuals": settings.daily_briefing_max_perpetuals,
        },
        "reminders": {
            "enabled": settings.reminders_enabled,
            "default_time": "09:00",
            "snooze_default_time": "09:00",
            "later_delay_hours": 2,
            "group_overdue_threshold": 3,
            "send_explicit_in_quiet_modes": True,
            "send_overdue_on_startup": True,
        },
        "modes": {
            "weekend": {
                "enabled": False,
                "active_days": ["saturday", "sunday"],
                "active_scopes": ["Personal"],
                "notifications": {
                    "briefing_auto": False,
                    "briefing_late_startup": False,
                    "reminders_explicit": True,
                    "reminders_overdue": False,
                    "perpetual_checkins": False,
                    "stale_in_progress": False,
                },
            },
            "vacation": {
                "enabled": False,
                "note": "",
            },
        },
        "priority": {
            "preset": "balanced",
            "criteria": copy.deepcopy(DEFAULT_PRIORITY_CRITERIA),
        },
        "trello": {
            "enabled": settings.trello_enabled,
            "write_enabled": settings.trello_write_enabled,
            "boards": _default_trello_boards(),
        },
        "backups": {
            "enabled": settings.backup_enabled,
            "retention_days": settings.backup_retention_days,
        },
        "advanced": _default_advanced_settings(),
    }


def get_settings(db: Session, user_id: str | None = None) -> dict[str, Any]:
    effective = default_settings()
    for row in db.scalars(select(AppSetting)).all():
        if row.key not in SETTING_SECTIONS:
            continue
        value = _loads(row.value_json, {})
        if row.key == "advanced":
            value = _advanced_override_payload(value)
        if isinstance(value, dict) and isinstance(effective.get(row.key), dict):
            effective[row.key] = _merge_settings_section(row.key, effective[row.key], value)
        else:
            effective[row.key] = value
    if user_id:
        for row in db.scalars(select(UserSetting).where(UserSetting.user_id == user_id)).all():
            if row.section not in USER_SCOPED_SETTINGS or row.key != USER_SETTING_SECTION_KEY:
                continue
            value = _loads(row.value_json, {})
            if isinstance(value, dict) and isinstance(effective.get(row.section), dict):
                effective[row.section] = _deep_merge(effective[row.section], value)
            else:
                effective[row.section] = value
        effective["trello"] = integrations.get_trello_config_for_user(db, user_id, legacy_config=effective.get("trello") or {})
    effective["trello"] = _normalize_trello_settings(effective.get("trello") or {})
    effective["priority"] = _normalize_priority_settings(effective.get("priority") or {})
    effective["advanced"] = _refresh_advanced_settings(effective.get("advanced") or {}, effective, db=db, user_id=user_id)
    return effective


def get_sources(db: Session, user_id: str | None = None) -> dict[str, str]:
    sources: dict[str, str] = {}
    for section, value in default_settings().items():
        _flatten_sources(sources, section, value, "env" if section in {"general", "advanced"} else "default")
    for row in db.scalars(select(AppSetting)).all():
        if row.key not in SETTING_SECTIONS:
            continue
        _flatten_sources(sources, row.key, _loads(row.value_json, {}), "sqlite")
    if user_id:
        for row in db.scalars(select(UserSetting).where(UserSetting.user_id == user_id)).all():
            if row.section not in USER_SCOPED_SETTINGS or row.key != USER_SETTING_SECTION_KEY:
                continue
            _flatten_sources(sources, row.section, _loads(row.value_json, {}), "user")
    return sources


def schema() -> dict[str, Any]:
    defaults = default_settings()
    return {
        "sections": list(defaults.keys()),
        "weekdays": WEEKDAY_NAMES,
        "scopes": list(LOCAL_SCOPES),
        "trello_states": ACTIONABLE_TRELLO_STATES,
        "trello_workflow_roles": WORKFLOW_STATE_ROLES,
        "priority_presets": ["balanced", "deadlines_first", "quick_wins", "deep_work"],
        "priority_criteria": defaults["priority"]["criteria"],
        "requires_restart": ["advanced", "general.timezone"],
    }


def available_scopes(db: Session, user_id: str | None = None) -> list[str]:
    values = list(LOCAL_SCOPES)
    if user_id:
        task_scopes = db.scalars(
            select(Task.scope)
            .where(Task.user_id == user_id, Task.scope.is_not(None), Task.scope != "")
            .distinct()
        ).all()
        values.extend(sorted((str(scope).strip() for scope in task_scopes if str(scope).strip()), key=str.casefold))

        trello = get_settings(db, user_id=user_id).get("trello") or {}
        boards = trello.get("boards") if isinstance(trello.get("boards"), dict) else {}
        values.extend(
            str(board.get("alias") or alias).strip()
            for alias, board in boards.items()
            if isinstance(board, dict)
            and bool(board.get("enabled", True))
            and bool(str(board.get("board_id") or "").strip())
        )
    return _dedupe_nonempty(values)


def selected_weekend_scopes(db: Session, user_id: str | None = None) -> list[str]:
    config = ((get_settings(db, user_id=user_id).get("modes") or {}).get("weekend") or {})
    return _dedupe_nonempty(config.get("active_scopes") or [])


def classification_context(db: Session, user_id: str | None = None) -> dict[str, Any]:
    scopes = available_scopes(db, user_id=user_id)
    hints: dict[str, list[str]] = {scope: [scope] for scope in scopes if scope not in LOCAL_SCOPES}
    if user_id:
        trello = get_settings(db, user_id=user_id).get("trello") or {}
        boards = trello.get("boards") if isinstance(trello.get("boards"), dict) else {}
        for alias, board in boards.items():
            if not isinstance(board, dict) or not board.get("enabled", True) or not str(board.get("board_id") or "").strip():
                continue
            scope = str(board.get("alias") or alias).strip()
            hints.setdefault(scope, []).extend([scope, str(board.get("name") or "").strip()])
    return {"existing_scopes": scopes, "keyword_hints": {scope: _dedupe_nonempty(values) for scope, values in hints.items()}}


def _dedupe_nonempty(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        folded = normalized.casefold()
        if not normalized or folded in seen:
            continue
        seen.add(folded)
        result.append(normalized)
    return result


def patch_settings(db: Session, payload: dict[str, Any], *, source: str = "ui", user_id: str | None = None, is_admin: bool = True) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="El payload de settings debe ser un objeto.")
    payload = _normalize_settings_patch(payload)
    invalid = set(payload) - SETTING_SECTIONS
    if invalid:
        raise HTTPException(status_code=422, detail=f"Sección de settings inválida: {', '.join(sorted(invalid))}")

    current = get_settings(db, user_id=user_id)
    next_effective = copy.deepcopy(current)
    for key, value in payload.items():
        if not isinstance(value, dict):
            raise HTTPException(status_code=422, detail=f"La sección {key} debe ser un objeto.")
        if key == "priority":
            value = _normalize_priority_settings(value)
        next_effective[key] = _merge_settings_section(key, next_effective.get(key, {}), value)
    if "trello" in next_effective:
        next_effective["trello"] = _normalize_trello_settings(next_effective.get("trello") or {})
    _validate_settings(next_effective, db=db, user_id=user_id)

    now = utc_now_iso()
    for key in payload:
        value = _advanced_override_payload(next_effective[key]) if key == "advanced" else next_effective[key]
        if user_id and key in USER_SCOPED_SETTINGS:
            old_row = _user_setting_row(db, user_id, key)
            old_value = old_row.value_json if old_row else None
            row = old_row or UserSetting(id=str(uuid4()), user_id=user_id, section=key, key=USER_SETTING_SECTION_KEY, value_json="{}", created_at=now, updated_at=now)
            row.value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
            row.updated_at = now
            db.add(row)
            audit_key = f"user:{user_id}:{key}"
        elif user_id and key == "trello":
            old_app_row = db.get(AppSetting, key)
            old_value = old_app_row.value_json if old_app_row else None
            try:
                integrations.set_trello_config_for_user(db, user_id, value)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            if integrations.user_can_use_instance_integration(db, user_id):
                app_row = old_app_row or AppSetting(key=key, value_json="{}", updated_at=now)
                app_row.value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
                app_row.updated_at = now
                db.add(app_row)
            audit_key = f"user:{user_id}:trello"
            row = None
        else:
            if user_id and key in INSTANCE_SCOPED_SETTINGS and not is_admin:
                raise HTTPException(status_code=403, detail=f"La sección {key} requiere permisos de instancia.")
            old_row = db.get(AppSetting, key)
            old_value = old_row.value_json if old_row else None
            row = old_row or AppSetting(key=key, value_json="{}", updated_at=now)
            row.value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
            row.updated_at = now
            db.add(row)
            audit_key = key
        db.add(
            SettingsAudit(
                id=str(uuid4()),
                key=audit_key,
                old_value_json=old_value,
                new_value_json=row.value_json if row is not None else json.dumps(value, ensure_ascii=False, sort_keys=True),
                source=source,
                created_at=now,
            )
        )
    db.commit()
    return get_settings(db, user_id=user_id)


def reset_section(db: Session, section: str, *, source: str = "ui", user_id: str | None = None, is_admin: bool = True) -> dict[str, Any]:
    if section not in SETTING_SECTIONS:
        raise HTTPException(status_code=422, detail="Sección de settings inválida.")
    if user_id and section in USER_SCOPED_SETTINGS:
        row = _user_setting_row(db, user_id, section)
    else:
        if user_id and section in INSTANCE_SCOPED_SETTINGS and not is_admin:
            raise HTTPException(status_code=403, detail=f"La sección {section} requiere permisos de instancia.")
        row = db.get(AppSetting, section)
    if row:
        db.delete(row)
        db.add(
            SettingsAudit(
                id=str(uuid4()),
                key=f"user:{user_id}:{section}" if user_id and section in USER_SCOPED_SETTINGS else section,
                old_value_json=row.value_json,
                new_value_json=None,
                source=source,
                created_at=utc_now_iso(),
            )
        )
        db.commit()
    return get_settings(db, user_id=user_id)


def _user_setting_row(db: Session, user_id: str, section: str) -> UserSetting | None:
    return db.scalar(
        select(UserSetting).where(
            UserSetting.user_id == user_id,
            UserSetting.section == section,
            UserSetting.key == USER_SETTING_SECTION_KEY,
        )
    )


def briefing_settings(db: Session, user_id: str | None = None) -> dict[str, Any]:
    return get_settings(db, user_id=user_id)["briefing"]


def reminder_settings(db: Session, user_id: str | None = None) -> dict[str, Any]:
    return get_settings(db, user_id=user_id)["reminders"]


def backup_settings(db: Session) -> dict[str, Any]:
    return get_settings(db)["backups"]


def priority_settings(db: Session, user_id: str | None = None) -> dict[str, Any]:
    return get_settings(db, user_id=user_id)["priority"]


def _normalize_priority_settings(priority: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(priority) if isinstance(priority, dict) else {}
    raw_criteria = normalized.get("criteria") if isinstance(normalized.get("criteria"), dict) else {}
    criteria = copy.deepcopy(DEFAULT_PRIORITY_CRITERIA)

    legacy_deadline = raw_criteria.get("deadline")
    legacy_effort = [raw_criteria.get(key) for key in ("effort_quick", "effort_medium", "effort_deep") if isinstance(raw_criteria.get(key), dict)]
    legacy_priority = [raw_criteria.get(key) for key in ("priority_high", "priority_medium_high", "priority_medium", "priority_low") if isinstance(raw_criteria.get(key), dict)]

    legacy_mapping = {
        "due_date": legacy_deadline,
        "manual_priority": _merge_legacy_priority_items(legacy_priority),
        "effort": _merge_legacy_priority_items(legacy_effort),
    }
    for key, item in raw_criteria.items():
        if key in criteria and isinstance(item, dict):
            criteria[key] = {**criteria[key], **item}
    for key, item in legacy_mapping.items():
        if key not in raw_criteria and isinstance(item, dict):
            criteria[key] = {**criteria[key], **item, "label": criteria[key]["label"]}

    normalized["criteria"] = criteria
    normalized["preset"] = normalized.get("preset") if normalized.get("preset") in {"balanced", "deadlines_first", "quick_wins", "deep_work", "custom"} else "balanced"
    return normalized


def _merge_legacy_priority_items(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    enabled = any(item.get("enabled", True) for item in items)
    weight = max(int(item.get("weight") or 0) for item in items)
    return {"enabled": enabled, "weight": weight}


def weekend_settings(db: Session, user_id: str | None = None) -> dict[str, Any]:
    return get_settings(db, user_id=user_id)["modes"]["weekend"]


def get_weekend_mode_state(db: Session, *, now: datetime | None = None, user_id: str | None = None) -> WeekendModeState:
    effective = get_settings(db, user_id=user_id)
    config = ((effective.get("modes") or {}).get("weekend") or {})
    enabled = bool(config.get("enabled"))
    timezone_name = str((effective.get("general") or {}).get("timezone") or settings.app_timezone or "UTC")
    tz = _safe_zoneinfo(timezone_name)
    local = _local_datetime(now, tz)
    raw_active_days = config.get("active_days")
    active_days = _normalize_days(raw_active_days if raw_active_days is not None else ["saturday", "sunday"])
    active_scopes = [str(value) for value in (config.get("active_scopes") or []) if str(value)]
    raw_notifications = config.get("notifications") if isinstance(config.get("notifications"), dict) else {}
    active_today = enabled and WEEKDAY_NAMES[local.weekday()] in set(active_days)
    notifications = _weekend_notification_state(raw_notifications, active_today=active_today)
    allowed = ["manual_telegram", "trello_sync", "backup"]
    muted: list[str] = []
    for key, allowed_value in notifications.items():
        (allowed if allowed_value else muted).append(key)
    reason = "Modo fin de semana activo hoy" if active_today else ("Modo fin de semana programado, no activo hoy" if enabled else "Modo fin de semana desactivado")
    return WeekendModeState(
        enabled=enabled,
        active_today=active_today,
        timezone=timezone_name if tz.key == timezone_name else tz.key,
        active_days=active_days,
        active_scopes=active_scopes,
        notifications=notifications,
        allowed=allowed,
        muted=muted,
        reason=reason,
    )


def is_weekend_mode_active(db: Session, *, now: datetime | None = None, user_id: str | None = None) -> bool:
    return get_weekend_mode_state(db, now=now, user_id=user_id).active_today


def weekend_scope_allowed(db: Session, scope: str | None, *, now: datetime | None = None, user_id: str | None = None) -> bool:
    if not is_weekend_mode_active(db, now=now, user_id=user_id):
        return True
    active_scopes = {str(value) for value in weekend_settings(db, user_id=user_id).get("active_scopes") or []}
    return not active_scopes or (scope or "") in active_scopes


def weekend_notification_enabled(db: Session, key: str, *, now: datetime | None = None, user_id: str | None = None) -> bool:
    return should_send_weekend_category(db, key, now=now, user_id=user_id)


def should_send_weekend_category(db: Session, category: str, *, now: datetime | None = None, user_id: str | None = None) -> bool:
    if category in WEEKEND_ALWAYS_ALLOWED_CATEGORIES:
        return True
    state = get_weekend_mode_state(db, now=now, user_id=user_id)
    if not state.active_today:
        return True
    notification_key = WEEKEND_NOTIFICATION_KEYS.get(category, category)
    return bool(state.notifications.get(notification_key, False))


def trello_write_enabled(db: Session, user_id: str | None = None) -> bool:
    effective = get_settings(db, user_id=user_id)
    advanced = effective.get("advanced") or {}
    return bool((advanced.get("editable") or {}).get("trello_write_enabled") and _trello_settings_ready(effective, db=db, user_id=user_id))


def trello_auto_confirm_writes(db: Session, user_id: str | None = None) -> bool:
    effective = get_settings(db, user_id=user_id)
    advanced = effective.get("advanced") or {}
    editable = advanced.get("editable") or {}
    return bool(
        editable.get("trello_auto_confirm_writes")
        and editable.get("trello_write_enabled")
        and _trello_settings_ready(effective, db=db, user_id=user_id)
    )


def trello_auto_confirm_writes_for_board(db: Session, board_alias: str | None, user_id: str | None = None) -> bool:
    if not board_alias:
        return trello_auto_confirm_writes(db, user_id=user_id)
    effective = get_settings(db, user_id=user_id)
    advanced = effective.get("advanced") or {}
    editable = advanced.get("editable") or {}
    if not (editable.get("trello_write_enabled") and _trello_settings_ready(effective, db=db, user_id=user_id)):
        return False
    board = ((effective.get("trello") or {}).get("boards") or {}).get(board_alias)
    if isinstance(board, dict) and "auto_confirm_writes" in board:
        return bool(board.get("auto_confirm_writes"))
    return bool(editable.get("trello_auto_confirm_writes"))


def llm_enabled(db: Session, user_id: str | None = None) -> bool:
    return llm_service.llm_status(db, user_id).safe_to_use


def trello_mapping_for_state(db: Session, board_alias: str, state: str, user_id: str | None = None) -> dict[str, Any] | None:
    board_config = board_by_alias(board_alias, db, user_id=user_id)
    if board_config:
        workflow_state = workflow_state_for_role_or_key(board_config, state)
        if workflow_state:
            return _workflow_state_to_mapping(workflow_state)
    board = (get_settings(db, user_id=user_id).get("trello") or {}).get("boards", {}).get(board_alias)
    if not isinstance(board, dict):
        return None
    mapping = (board.get("states") or {}).get(state)
    return mapping if isinstance(mapping, dict) else None


def trello_state_for_list(db: Session, board_alias: str, list_id: str | None, list_name: str | None, user_id: str | None = None) -> str | None:
    board_config = board_by_alias(board_alias, db, user_id=user_id)
    if board_config:
        workflow_state = workflow_state_for_list(board_config, list_id, list_name)
        if workflow_state:
            return workflow_state.role
    board = (get_settings(db, user_id=user_id).get("trello") or {}).get("boards", {}).get(board_alias)
    states = (board or {}).get("states") if isinstance(board, dict) else {}
    if list_id:
        for state, mapping in states.items():
            if isinstance(mapping, dict) and mapping.get("list_id") == list_id:
                return state
    if board_config and list_name:
        from app.services.trello_config import list_state_for_name

        return list_state_for_name(board_config, list_name)
    return None


def update_trello_mapping(db: Session, board_alias: str, state: str, *, list_id: str | None, list_name: str | None, user_id: str | None = None) -> dict[str, Any]:
    current = get_settings(db, user_id=user_id)
    boards = current["trello"].setdefault("boards", {})
    legacy = board_by_alias(board_alias)
    board = boards.setdefault(
        board_alias,
        {
            "alias": board_alias,
            "name": legacy.name if legacy else board_alias,
            "board_id": legacy.board_id if legacy else "",
            "enabled": True,
            "states": {},
            "ignored_list_ids": [],
        },
    )
    board.setdefault("states", {})[state] = {"list_id": list_id, "list_name": list_name}
    workflow_states = _board_workflow_states(board_alias, board)
    updated = False
    for workflow_state in workflow_states:
        if workflow_state.get("key") == state or workflow_state.get("role") == state:
            workflow_state["list_id"] = list_id
            workflow_state["list_name"] = list_name
            workflow_state["enabled"] = workflow_state.get("enabled", True)
            updated = True
            break
    if not updated:
        workflow_states.append(_default_workflow_state_payload(state, list_id=list_id, list_name=list_name))
    board["workflow_states"] = workflow_states
    board["states"] = _legacy_states_from_workflow(workflow_states)
    return patch_settings(db, {"trello": current["trello"]}, user_id=user_id)


def trello_boards_response(db: Session, user_id: str | None = None) -> dict[str, Any]:
    boards = get_settings(db, user_id=user_id).get("trello", {}).get("boards", {})
    return {"boards": [copy.deepcopy(board) for board in boards.values() if isinstance(board, dict)]}


def create_trello_board(db: Session, payload: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
    current = get_settings(db, user_id=user_id)
    trello = copy.deepcopy(current.get("trello") or {})
    boards = trello.setdefault("boards", {})
    alias = _clean_trello_alias(payload.get("alias"))
    name = str(payload.get("name") or "").strip()
    board_id = str(payload.get("board_id") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="El board Trello necesita un nombre.")
    if not board_id:
        raise HTTPException(status_code=422, detail="El board Trello necesita board_id para activarse.")
    if alias.casefold() in {str((board.get("alias") if isinstance(board, dict) else key) or key).casefold() for key, board in boards.items()}:
        raise HTTPException(status_code=422, detail=f"Alias Trello duplicado: {alias}")
    _ensure_unique_board_id(boards, board_id)
    workflow_states = _workflow_states_from_states(alias, _default_dynamic_board_states(), legacy=None, enabled_defaults=True)
    for item in workflow_states:
        if item.get("role") == "perpetual":
            item["enabled"] = False
    boards[alias] = {
        "alias": alias,
        "name": name,
        "board_id": board_id,
        "enabled": True,
        "auto_confirm_writes": bool(payload.get("auto_confirm_writes", False)),
        "ignored_list_ids": [],
        "workflow_states": workflow_states,
        "states": _legacy_states_from_workflow(workflow_states),
    }
    return patch_settings(db, {"trello": trello}, source="trello_board_create", user_id=user_id)


def patch_trello_board(db: Session, board_alias: str, payload: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
    current = get_settings(db, user_id=user_id)
    trello = copy.deepcopy(current.get("trello") or {})
    boards = trello.setdefault("boards", {})
    alias = _resolve_board_key(boards, board_alias)
    board = copy.deepcopy(boards[alias])
    if "alias" in payload and str(payload.get("alias") or alias).strip() != alias:
        raise HTTPException(status_code=422, detail="Renombrar alias Trello no está habilitado. Desactivá este board y agregá uno nuevo.")
    if payload.get("name") is not None:
        board["name"] = str(payload["name"]).strip()
    if payload.get("board_id") is not None:
        board_id = str(payload["board_id"] or "").strip()
        if board_id:
            _ensure_unique_board_id(boards, board_id, current_alias=alias)
        board["board_id"] = board_id
    if payload.get("enabled") is not None:
        board["enabled"] = bool(payload["enabled"])
    if payload.get("auto_confirm_writes") is not None:
        board["auto_confirm_writes"] = bool(payload["auto_confirm_writes"])
    if payload.get("workflow_states") is not None:
        board["workflow_states"] = [_normalize_workflow_state(item, index) for index, item in enumerate(payload["workflow_states"]) if isinstance(item, dict)]
        board["states"] = _legacy_states_from_workflow(board["workflow_states"])
    if payload.get("states") is not None:
        board["states"] = payload["states"]
        board["workflow_states"] = _board_workflow_states(alias, board)
    board["alias"] = alias
    boards[alias] = board
    return patch_settings(db, {"trello": trello}, source="trello_board_patch", user_id=user_id)


def disable_trello_board(db: Session, board_alias: str, user_id: str | None = None) -> dict[str, Any]:
    return patch_trello_board(db, board_alias, {"enabled": False}, user_id=user_id)


def trello_discovery_from_lists(lists_by_board: dict[str, list[dict[str, Any]]], db: Session | None = None, user_id: str | None = None) -> dict[str, Any]:
    boards = []
    for board in board_configs(db, include_disabled=True, user_id=user_id):
        lists = lists_by_board.get(board.alias, [])
        boards.append(
            {
                "alias": board.alias,
                "name": board.name,
                "enabled": board.enabled,
                "board_id_configured": bool(board.board_id),
                "lists": [
                    {"id": item.get("id"), "name": item.get("name"), "closed": bool(item.get("closed", False))}
                    for item in lists
                ],
            }
        )
    return {"boards": boards}


def validate_trello_lists(db: Session, lists_by_board: dict[str, list[dict[str, Any]]], user_id: str | None = None) -> dict[str, Any]:
    effective = get_settings(db, user_id=user_id)
    reports = []
    changed = False
    for board in board_configs(db, include_disabled=True, user_id=user_id):
        configured = ((effective.get("trello") or {}).get("boards") or {}).get(board.alias, {})
        workflow_states = _board_workflow_states(board.alias, configured if isinstance(configured, dict) else {})
        states = configured.get("states") if isinstance(configured, dict) else {}
        lists = lists_by_board.get(board.alias, [])
        by_id = {item.get("id"): item for item in lists if item.get("id")}
        by_name = {str(item.get("name", "")).casefold(): item for item in lists if item.get("name")}
        board_report = {"alias": board.alias, "status": "disabled" if not board.enabled else "ok", "states": {}, "capabilities": {}}
        if not board.enabled:
            reports.append(board_report)
            continue
        for workflow_state in workflow_states:
            state = str(workflow_state.get("key") or workflow_state.get("role") or "")
            role = str(workflow_state.get("role") or state)
            enabled = bool(workflow_state.get("enabled", True))
            fallback_name = workflow_state.get("list_name") or default_list_name(board, role)
            if not enabled:
                board_report["states"][state] = {"status": "disabled", "role": role, "message": f"{workflow_state.get('label') or state} desactivado para este board."}
                continue
            if not fallback_name:
                board_report["status"] = "error"
                board_report["states"][state] = {"status": "missing", "role": role, "message": f"Falta lista para {workflow_state.get('label') or state}."}
                continue
            mapping = states.get(role, {}) if isinstance(states, dict) else {}
            mapping = {**mapping, "list_id": workflow_state.get("list_id") or mapping.get("list_id"), "list_name": workflow_state.get("list_name") or mapping.get("list_name")}
            item = by_id.get(mapping.get("list_id")) if isinstance(mapping, dict) else None
            if not item and isinstance(mapping, dict) and mapping.get("list_name"):
                item = by_name.get(str(mapping["list_name"]).casefold())
            if not item:
                item = by_name.get(fallback_name.casefold())
            if item:
                board_report["states"][state] = {"status": "ok", "role": role, "list_id": item.get("id"), "list_name": item.get("name")}
                current_mapping = states.setdefault(role, {}) if isinstance(states, dict) else {}
                if current_mapping.get("list_id") != item.get("id") or current_mapping.get("list_name") != item.get("name"):
                    states[role] = {"list_id": item.get("id"), "list_name": item.get("name")}
                    _update_workflow_state_mapping(workflow_states, state, role, item.get("id"), item.get("name"))
                    changed = True
            else:
                board_report["status"] = "error"
                board_report["states"][state] = {
                    "status": "missing",
                    "role": role,
                    "list_name": fallback_name,
                    "message": f'Falta lista para "{workflow_state.get("label") or state}". Las acciones relacionadas quedan bloqueadas.',
                }
        board_report["capabilities"] = _workflow_capabilities(workflow_states)
        if isinstance(configured, dict):
            configured["workflow_states"] = workflow_states
            configured["states"] = _legacy_states_from_workflow(workflow_states)
            configured["validation"] = {
                "status": board_report["status"],
                "validated_at": utc_now_iso(),
                "errors": [
                    state_report.get("message")
                    for state_report in board_report["states"].values()
                    if isinstance(state_report, dict) and state_report.get("status") == "missing" and state_report.get("message")
                ],
                "warnings": [],
            }
            changed = True
        reports.append(board_report)
    if changed:
        patch_settings(db, {"trello": effective["trello"]}, source="trello_validate", user_id=user_id)
    overall = "error" if any(report["status"] == "error" for report in reports) else "ok"
    return {"status": overall, "boards": reports, "settings": get_settings(db, user_id=user_id)}


def _default_advanced_settings() -> dict[str, Any]:
    trello_base_ready = _trello_base_ready()
    trello_write = bool(settings.trello_write_enabled)
    # SQLite settings are app/user-local preferences today. If multi-user support
    # is added later, app_settings should be scoped by user_id; secrets stay
    # instance-level in .env.
    return {
        "status": {
            "telegram": _status_from_enabled(settings.telegram_enabled, bool(settings.telegram_bot_token and settings.telegram_allowed_user_id)),
            "trello": _status_from_enabled(settings.trello_enabled, trello_base_ready),
            "trello_write": _status_from_enabled(trello_write, trello_base_ready),
            "trello_auto_confirm": _auto_confirm_status(False, trello_write, trello_base_ready),
            "llm": "disabled",
            "briefing": _status_from_enabled(settings.daily_briefing_enabled, True),
            "reminders": _status_from_enabled(settings.reminders_enabled, True),
            "backups": _status_from_enabled(settings.backup_enabled, True),
            "reminders_interval_seconds": settings.reminders_poll_interval_seconds,
            "trello_sync_interval_minutes": settings.trello_sync_interval_minutes,
        },
        "editable": {
            "trello_write_enabled": trello_write,
            "trello_auto_confirm_writes": False,
        },
        "prerequisites": {
            "trello_write_enabled": trello_base_ready,
            "trello_auto_confirm_writes": trello_base_ready and trello_write,
        },
        "instance": {
            "telegram_token_configured": bool(settings.telegram_bot_token),
            "telegram_allowed_user_configured": bool(settings.telegram_allowed_user_id),
            "trello_credentials_configured": trello_credentials_ready(),
            "trello_boards_configured": trello_boards_ready(),
            "llm_provider": "openai",
            "llm_provider_configured": False,
            "llm_provider_status": "disabled",
            "database_path": str(settings.sqlite_path) if settings.sqlite_path else settings.database_url,
            "host": settings.app_host,
            "port": settings.app_port,
        },
        "notes": [
            "Secrets are configured in .env.",
            "SQLite settings are app/user-local preferences. If multi-user support is added later, app_settings should be scoped by user_id.",
        ],
    }


def _refresh_advanced_settings(
    advanced: dict[str, Any],
    effective: dict[str, Any] | None = None,
    *,
    db: Session | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    refreshed = _default_advanced_settings()
    trello_ready = _trello_settings_ready(effective, db=db, user_id=user_id) if effective else _trello_base_ready(db, user_id=user_id)
    editable = {
        **refreshed["editable"],
        **((advanced.get("editable") or {}) if isinstance(advanced.get("editable"), dict) else {}),
    }
    refreshed["editable"] = editable
    refreshed["status"]["trello_write"] = _status_from_enabled(bool(editable.get("trello_write_enabled")), trello_ready)
    refreshed["status"]["trello_auto_confirm"] = _auto_confirm_status(
        bool(editable.get("trello_auto_confirm_writes")),
        bool(editable.get("trello_write_enabled")),
        trello_ready,
    )
    refreshed["prerequisites"]["trello_write_enabled"] = trello_ready
    refreshed["prerequisites"]["trello_auto_confirm_writes"] = trello_ready and bool(editable.get("trello_write_enabled"))
    refreshed["instance"]["trello_boards_configured"] = _trello_boards_from_settings_ready(effective) if effective else trello_boards_ready()
    runtime_ai = llm_service.llm_status(db, user_id) if db is not None else None
    if runtime_ai:
        refreshed["status"]["llm"] = _llm_status_from_enabled(runtime_ai.user_enabled, {"status": runtime_ai.status, "ready": runtime_ai.safe_to_use})
        refreshed["instance"]["llm_provider_configured"] = runtime_ai.api_key_configured
        refreshed["instance"]["llm_provider_status"] = runtime_ai.status
    if effective:
        telegram_ready = bool(settings.telegram_enabled and settings.telegram_bot_token and settings.telegram_allowed_user_id)
        refreshed["status"]["briefing"] = _status_from_enabled(bool((effective.get("briefing") or {}).get("enabled", True)), telegram_ready)
        refreshed["status"]["reminders"] = _status_from_enabled(bool((effective.get("reminders") or {}).get("enabled", True)), telegram_ready)
        refreshed["status"]["backups"] = "active" if (effective.get("backups") or {}).get("enabled", True) else "disabled"
    return refreshed


def _status_from_enabled(enabled: bool, configured: bool) -> str:
    if enabled and configured:
        return "active"
    if enabled and not configured:
        return "requires_configuration"
    return "disabled"


def _auto_confirm_status(auto_confirm_enabled: bool, trello_write_enabled: bool, trello_ready: bool) -> str:
    if not trello_ready:
        return "requires_configuration"
    if not trello_write_enabled:
        return "unavailable"
    return "active" if auto_confirm_enabled else "disabled"


def _trello_base_ready(db: Session | None = None, user_id: str | None = None) -> bool:
    return bool(settings.trello_enabled and trello_credentials_ready(db, user_id=user_id) and trello_boards_ready(db, user_id=user_id))


def _trello_settings_ready(value: dict[str, Any] | None, *, db: Session | None = None, user_id: str | None = None) -> bool:
    return bool(settings.trello_enabled and trello_credentials_ready(db, user_id=user_id) and _trello_boards_from_settings_ready(value))


def _trello_boards_from_settings_ready(value: dict[str, Any] | None) -> bool:
    boards = (((value or {}).get("trello") or {}).get("boards") or {})
    enabled_boards = [board for board in boards.values() if isinstance(board, dict) and board.get("enabled", True)]
    return any(str(board.get("board_id") or "").strip() for board in enabled_boards)


def _llm_status_from_enabled(enabled: bool, provider_status: dict[str, Any]) -> str:
    if not enabled:
        return "disabled"
    if provider_status["status"] == "unverified":
        return "unverified"
    return _status_from_enabled(True, bool(provider_status["ready"]))


def _normalize_settings_patch(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(payload)
    if "advanced" in normalized:
        normalized["advanced"] = _advanced_override_payload(normalized["advanced"])
    return normalized


def _merge_settings_section(section: str, current: Any, patch: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_merge(current if isinstance(current, dict) else {}, patch)
    # Board collections are declarative. Omitting `boards` preserves them; an
    # explicitly supplied mapping, including {}, replaces the alias set. Each
    # supplied alias remains a partial patch over its existing board/template.
    if section == "trello" and "boards" in patch:
        raw_boards = patch["boards"]
        if isinstance(raw_boards, dict):
            current_boards = current.get("boards") if isinstance(current, dict) and isinstance(current.get("boards"), dict) else {}
            merged["boards"] = {
                alias: _deep_merge(current_boards.get(alias, {}), board) if isinstance(board, dict) else copy.deepcopy(board)
                for alias, board in raw_boards.items()
            }
        else:
            merged["boards"] = copy.deepcopy(raw_boards)
    return merged


def _advanced_override_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    raw_editable = value.get("editable") if isinstance(value.get("editable"), dict) else value
    editable = {key: bool(raw_editable[key]) for key in EDITABLE_ADVANCED_KEYS if key in raw_editable}
    return {"editable": editable} if editable else {}


def _default_trello_boards() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for board in board_configs():
        if not board.board_id_configured:
            continue
        workflow_states = _workflow_states_for_legacy_board(board)
        states = _legacy_states_from_workflow(workflow_states)
        result[board.alias] = {
            "alias": board.alias,
            "name": board.name,
            "board_id": board.board_id,
            "enabled": True,
            "board_id_configured": bool(board.board_id),
            "ignored_list_ids": [],
            "states": states,
            "workflow_states": workflow_states,
        }
    return result


def _default_dynamic_board_states() -> dict[str, Any]:
    return {
        "pending": {"list_id": None, "list_name": "TAREAS"},
        "in_progress": {"list_id": None, "list_name": "EN PROCESO"},
        "review": {"list_id": None, "list_name": "EN REVISION"},
        "completed": {"list_id": None, "list_name": "TERMINADAS"},
        "perpetual": {"list_id": None, "list_name": "Perpetuas"},
    }


def _normalize_trello_settings(trello: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(trello)
    boards = normalized.get("boards")
    if not isinstance(boards, dict):
        return normalized
    for alias, board in boards.items():
        if not isinstance(board, dict):
            continue
        workflow_states = _board_workflow_states(str(alias), board)
        legacy_states = board.get("states") if isinstance(board.get("states"), dict) else {}
        for role, mapping in legacy_states.items():
            if not isinstance(mapping, dict):
                continue
            matched = False
            for workflow_state in workflow_states:
                if workflow_state.get("role") == role or workflow_state.get("key") == role:
                    workflow_state["list_id"] = mapping.get("list_id", workflow_state.get("list_id"))
                    workflow_state["list_name"] = mapping.get("list_name", workflow_state.get("list_name"))
                    if "enabled" in mapping:
                        workflow_state["enabled"] = bool(mapping["enabled"])
                    matched = True
                    break
            if not matched:
                workflow_states.append(_default_workflow_state_payload(str(role), list_id=mapping.get("list_id"), list_name=mapping.get("list_name")))
        board["workflow_states"] = workflow_states
        board["states"] = _legacy_states_from_workflow(workflow_states)
        configured = bool(str(board.get("board_id") or "").strip())
        enabled = bool(board.get("enabled", True))
        board["board_id_configured"] = configured
        board["configured"] = configured
        board["read_ready"] = enabled and configured
        board["write_ready"] = enabled and configured
    remote_boards = [board for board in boards.values() if isinstance(board, dict) and board.get("read_ready")]
    normalized["configured"] = bool(remote_boards)
    normalized["read_ready"] = bool(remote_boards)
    normalized["write_ready"] = bool(normalized.get("write_enabled")) and bool(remote_boards)
    return normalized


def _workflow_states_for_legacy_board(board: TrelloBoardConfig) -> list[dict[str, Any]]:
    states = {
        role: {"list_id": None, "list_name": names[0] if names else None}
        for role, names in board.lists.items()
        if role != "ignored"
    }
    workflow = _workflow_states_from_states(board.alias, states, legacy=board, enabled_defaults=True)
    for index, list_name in enumerate(board.lists.get("ignored") or []):
        workflow.append(
            {
                "key": f"ignored_{index + 1}",
                "label": list_name,
                "role": "ignored",
                "enabled": True,
                "required_for": [],
                "list_id": None,
                "list_name": list_name,
            }
        )
    return workflow


def _board_workflow_states(alias: str, board: dict[str, Any]) -> list[dict[str, Any]]:
    workflow_states = board.get("workflow_states")
    if isinstance(workflow_states, list):
        return [_normalize_workflow_state(item, index) for index, item in enumerate(workflow_states) if isinstance(item, dict)]
    legacy = board_by_alias(alias)
    states = board.get("states") if isinstance(board.get("states"), dict) else {}
    return _workflow_states_from_states(alias, states, legacy=legacy, enabled_defaults=True)


def _workflow_states_from_states(alias: str, states: dict[str, Any], *, legacy: TrelloBoardConfig | None, enabled_defaults: bool) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for role in ACTIONABLE_TRELLO_STATES:
        mapping = states.get(role) if isinstance(states.get(role), dict) else {}
        fallback_name = mapping.get("list_name")
        if not fallback_name and legacy:
            fallback_name = default_list_name(legacy, role)
        fallback_name = fallback_name or _default_dynamic_board_states().get(role, {}).get("list_name")
        enabled = bool(mapping.get("enabled", True)) if mapping else _default_workflow_enabled(alias, role, legacy, enabled_defaults)
        result.append(_default_workflow_state_payload(role, list_id=mapping.get("list_id"), list_name=fallback_name, enabled=enabled))
    ignored = states.get("ignored")
    if isinstance(ignored, dict):
        result.append(
            _default_workflow_state_payload(
                "ignored",
                key="ignored_1",
                label=ignored.get("list_name") or "Ignorar",
                list_id=ignored.get("list_id"),
                list_name=ignored.get("list_name"),
                enabled=bool(ignored.get("enabled", True)),
            )
        )
    return result


def _default_workflow_enabled(alias: str, role: str, legacy: TrelloBoardConfig | None, enabled_defaults: bool) -> bool:
    if role == "perpetual":
        return bool(legacy and role in legacy.lists)
    if enabled_defaults:
        return role in {"pending", "in_progress", "review", "completed"}
    return role in {"pending", "completed"}


def _default_workflow_state_payload(
    role: str,
    *,
    key: str | None = None,
    label: str | None = None,
    list_id: str | None = None,
    list_name: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    clean_role = role if role in WORKFLOW_ROLES else "custom_actionable"
    clean_key = key or clean_role
    return {
        "key": clean_key,
        "label": label or _workflow_label(clean_role),
        "role": clean_role,
        "enabled": enabled,
        "required_for": list(_workflow_required_for(clean_role)),
        "list_id": list_id,
        "list_name": list_name,
    }


def _normalize_workflow_state(item: dict[str, Any], index: int) -> dict[str, Any]:
    role = str(item.get("role") or item.get("key") or "custom_actionable").strip()
    if role not in WORKFLOW_ROLES:
        role = "custom_actionable"
    key = str(item.get("key") or role or f"state_{index + 1}").strip()
    return {
        "key": key,
        "label": str(item.get("label") or _workflow_label(role)).strip(),
        "role": role,
        "enabled": bool(item.get("enabled", True)),
        "required_for": [str(value) for value in item.get("required_for") or _workflow_required_for(role)],
        "list_id": str(item.get("list_id") or "") or None,
        "list_name": str(item.get("list_name") or "") or None,
    }


def _legacy_states_from_workflow(workflow_states: list[dict[str, Any]]) -> dict[str, Any]:
    states: dict[str, Any] = {}
    for item in workflow_states:
        if not item.get("enabled", True):
            continue
        role = str(item.get("role") or item.get("key") or "")
        if role in states or role == "ignored":
            continue
        states[role] = {"list_id": item.get("list_id"), "list_name": item.get("list_name")}
    return states


def _workflow_state_to_mapping(state: TrelloWorkflowState) -> dict[str, Any]:
    return {
        "list_id": state.list_id,
        "list_name": state.list_name,
        "enabled": state.enabled,
        "role": state.role,
        "key": state.key,
    }


def _update_workflow_state_mapping(workflow_states: list[dict[str, Any]], key: str, role: str, list_id: str | None, list_name: str | None) -> None:
    for item in workflow_states:
        if item.get("key") == key or item.get("role") == role:
            item["list_id"] = list_id
            item["list_name"] = list_name
            return


def _workflow_capabilities(workflow_states: list[dict[str, Any]]) -> dict[str, str]:
    enabled_roles = {str(item.get("role")) for item in workflow_states if item.get("enabled", True) and item.get("list_id")}
    return {
        "create_card": "ok" if "pending" in enabled_roles else "missing",
        "start_transition": "ok" if "in_progress" in enabled_roles else "missing",
        "review": "ok" if "review" in enabled_roles else "missing",
        "complete": "ok" if "completed" in enabled_roles else "missing",
        "perpetual": "ok" if "perpetual" in enabled_roles else "disabled",
    }


def _workflow_label(role: str) -> str:
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
    }.get(role, role)


def _workflow_required_for(role: str) -> tuple[str, ...]:
    return {
        "pending": ("create_card", "start_transition"),
        "in_progress": ("start_transition", "stale_checkins"),
        "review": ("finish_from_in_progress",),
        "completed": ("complete",),
    }.get(role, ())


def _validate_settings(value: dict[str, Any], *, db: Session | None = None, user_id: str | None = None) -> None:
    general = value.get("general") or {}
    briefing = value.get("briefing") or {}
    reminders = value.get("reminders") or {}
    weekend = ((value.get("modes") or {}).get("weekend") or {})
    priority = value.get("priority") or {}
    backups = value.get("backups") or {}
    advanced = value.get("advanced") or {}
    trello = value.get("trello") or {}

    _validate_timezone(general.get("timezone") or settings.app_timezone)
    _validate_timezone(briefing.get("timezone") or general.get("timezone") or settings.app_timezone)
    for label, raw in {
        "briefing.time": briefing.get("time"),
        "briefing.late_cutoff": briefing.get("late_cutoff"),
        "reminders.default_time": reminders.get("default_time"),
        "reminders.snooze_default_time": reminders.get("snooze_default_time"),
    }.items():
        _validate_hhmm(label, raw)
    if _hhmm_minutes(briefing.get("late_cutoff")) <= _hhmm_minutes(briefing.get("time")):
        raise HTTPException(status_code=422, detail="No enviar después de esta hora debe ser posterior a la hora del briefing.")
    for label, raw in {
        "briefing.max_deep_work": briefing.get("max_deep_work"),
        "briefing.max_quick_tasks": briefing.get("max_quick_tasks"),
        "briefing.max_reviews": briefing.get("max_reviews"),
        "briefing.max_attention": briefing.get("max_attention"),
        "briefing.max_inbox": briefing.get("max_inbox"),
        "briefing.max_perpetuals": briefing.get("max_perpetuals"),
    }.items():
        _validate_int(label, raw, minimum=0, maximum=365)
    _validate_int("reminders.later_delay_hours", reminders.get("later_delay_hours"), minimum=1, maximum=365)
    _validate_int("reminders.group_overdue_threshold", reminders.get("group_overdue_threshold"), minimum=1, maximum=365)
    _validate_int("backups.retention_days", backups.get("retention_days"), minimum=1, maximum=365)
    weekend["active_days"] = _normalize_days(weekend.get("active_days") or [])
    advanced_editable = advanced.get("editable") or {}
    write_preference_ready = (
        _trello_settings_ready(value, db=db, user_id=user_id)
        if user_id
        else bool(settings.trello_enabled and trello_credentials_ready(db, user_id=None))
    )
    if advanced_editable.get("trello_write_enabled") and not write_preference_ready:
        raise HTTPException(
            status_code=422,
            detail="Configurá Trello con TRELLO_ENABLED=true y credenciales antes de habilitar la preferencia de escrituras.",
        )
    _validate_trello_boards(trello.get("boards") or {})
    if advanced_editable.get("trello_auto_confirm_writes") and not advanced_editable.get("trello_write_enabled"):
        raise HTTPException(status_code=422, detail="Primero activá Escritura en Trello para auto-confirmar acciones.")
    criteria = priority.get("criteria") or {}
    for key, item in criteria.items():
        if key not in DEFAULT_PRIORITY_CRITERIA:
            raise HTTPException(status_code=422, detail=f"Criterio de prioridad inválido: {key}")
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail=f"Criterio de prioridad inválido: {key}")
        _validate_int(f"priority.criteria.{key}.weight", item.get("weight"), minimum=-100, maximum=200)


def _validate_trello_boards(boards: Any) -> None:
    if not isinstance(boards, dict):
        raise HTTPException(status_code=422, detail="trello.boards debe ser un objeto.")
    aliases: set[str] = set()
    board_ids: set[str] = set()
    for key, board in boards.items():
        if not isinstance(board, dict):
            raise HTTPException(status_code=422, detail=f"Board Trello inválido: {key}")
        alias = str(board.get("alias") or key).strip()
        name = str(board.get("name") or "").strip()
        board_id = str(board.get("board_id") or "").strip()
        if not TRELLO_BOARD_ALIAS_RE.match(alias):
            raise HTTPException(status_code=422, detail=f"Alias Trello inválido: {alias or key}")
        alias_key = alias.casefold()
        if alias_key in aliases:
            raise HTTPException(status_code=422, detail=f"Alias Trello duplicado: {alias}")
        aliases.add(alias_key)
        if not name:
            raise HTTPException(status_code=422, detail=f"El board {alias} necesita un nombre.")
        if board_id:
            if board_id in board_ids:
                raise HTTPException(status_code=422, detail=f"Board ID Trello duplicado: {board_id}")
            board_ids.add(board_id)
        states = board.get("states") or {}
        workflow_states = board.get("workflow_states") or []
        if not isinstance(states, dict):
            raise HTTPException(status_code=422, detail=f"Estados Trello inválidos para {alias}.")
        if not isinstance(workflow_states, list):
            raise HTTPException(status_code=422, detail=f"Workflow Trello inválido para {alias}.")
        workflow_keys: set[str] = set()
        for index, workflow_state in enumerate(workflow_states):
            if not isinstance(workflow_state, dict):
                raise HTTPException(status_code=422, detail=f"Clasificación Trello inválida para {alias}.")
            role = str(workflow_state.get("role") or workflow_state.get("key") or "").strip()
            key = str(workflow_state.get("key") or role or "").strip()
            if role not in WORKFLOW_ROLES:
                raise HTTPException(status_code=422, detail=f"Rol Trello inválido en {alias}: {role}")
            if not key:
                raise HTTPException(status_code=422, detail=f"Clasificación Trello sin key en {alias}.")
            if key in workflow_keys:
                raise HTTPException(status_code=422, detail=f"Clasificación Trello duplicada en {alias}: {key}")
            workflow_keys.add(key)
            if workflow_state.get("enabled", True) and not (workflow_state.get("list_id") or workflow_state.get("list_name")):
                raise HTTPException(status_code=422, detail=f'Falta lista para "{workflow_state.get("label") or key}" en {alias}.')


def _clean_trello_alias(value: Any) -> str:
    alias = str(value or "").strip()
    if not TRELLO_BOARD_ALIAS_RE.match(alias):
        raise HTTPException(status_code=422, detail="Alias Trello inválido. Usá 2 a 24 letras, números o guión bajo.")
    return alias


def _resolve_board_key(boards: dict[str, Any], board_alias: str) -> str:
    for key, board in boards.items():
        alias = str((board.get("alias") if isinstance(board, dict) else key) or key).strip()
        if alias.casefold() == board_alias.casefold() or key.casefold() == board_alias.casefold():
            return key
    raise HTTPException(status_code=404, detail="Board Trello no encontrado.")


def _ensure_unique_board_id(boards: dict[str, Any], board_id: str, *, current_alias: str | None = None) -> None:
    for key, board in boards.items():
        if current_alias and key.casefold() == current_alias.casefold():
            continue
        if isinstance(board, dict) and str(board.get("board_id") or "").strip() == board_id:
            raise HTTPException(status_code=422, detail=f"Board ID Trello duplicado: {board_id}")


def _validate_timezone(value: str) -> None:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail=f"Timezone inválido: {value}") from exc


def _safe_zoneinfo(value: str) -> ZoneInfo:
    for candidate in (value, settings.app_timezone, "UTC"):
        try:
            return ZoneInfo(str(candidate or "UTC"))
        except ZoneInfoNotFoundError:
            continue
    return ZoneInfo("UTC")


def _local_datetime(now: datetime | None, tz: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(tz)
    if now.tzinfo:
        return now.astimezone(tz)
    return now.replace(tzinfo=tz)


def _weekend_notification_state(raw_notifications: dict[str, Any], *, active_today: bool) -> dict[str, bool]:
    values = {
        "reminders_explicit": bool(raw_notifications.get("reminders_explicit", True)),
        "reminders_overdue": bool(raw_notifications.get("reminders_overdue", False)),
        "briefing_auto": bool(raw_notifications.get("briefing_auto", raw_notifications.get("briefing", False))),
        "briefing_late_startup": bool(raw_notifications.get("briefing_late_startup", False)),
        "stale_in_progress": bool(raw_notifications.get("stale_in_progress", False)),
        "perpetual_checkins": bool(raw_notifications.get("perpetual_checkins", False)),
    }
    if not active_today:
        return {key: True for key in values} | {
            "telegram_proactive": True,
            "briefing": True,
            "checkins": True,
            "perpetuals": True,
        }
    return values | {
        "telegram_proactive": any(
            values[key]
            for key in (
                "reminders_overdue",
                "briefing_auto",
                "briefing_late_startup",
                "stale_in_progress",
                "perpetual_checkins",
            )
        ),
        "briefing": values["briefing_auto"] or values["briefing_late_startup"],
        "checkins": values["stale_in_progress"] or values["perpetual_checkins"],
        "perpetuals": values["perpetual_checkins"],
    }


def _validate_hhmm(label: str, value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise HTTPException(status_code=422, detail=f"{label} debe tener formato HH:MM.")
    hour, minute = value.split(":", 1)
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        raise HTTPException(status_code=422, detail=f"{label} debe tener formato HH:MM.")


def _hhmm_minutes(value: Any) -> int:
    if not isinstance(value, str):
        return 0
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _validate_int(label: str, value: Any, *, minimum: int, maximum: int) -> None:
    if not isinstance(value, int) or value < minimum or value > maximum:
        raise HTTPException(status_code=422, detail=f"{label} debe ser un entero entre {minimum} y {maximum}.")


def _normalize_days(days: list[Any]) -> list[str]:
    normalized = []
    for day in days:
        if isinstance(day, int) and 0 <= day <= 6:
            name = WEEKDAY_NAMES[day]
        else:
            name = str(day).strip().lower()
        if name not in WEEKDAY_NAMES:
            raise HTTPException(status_code=422, detail=f"Día de weekend inválido: {day}")
        if name not in normalized:
            normalized.append(name)
    return normalized


def _loads(raw: str, fallback: Any) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _flatten_sources(output: dict[str, str], prefix: str, value: Any, source: str) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            _flatten_sources(output, f"{prefix}.{key}", nested, source)
    else:
        output[prefix] = source
