from __future__ import annotations

import os
import sys
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT, settings
from app.models.reminders import Reminder
from app.models.telegram import TelegramUpdate
from app.schemas.system import InstallationReadiness, PreflightCheck, PreflightReport, ReadinessItem, SystemHealth, SystemInfo, SystemStatus, SystemStatusIntervalItem, SystemStatusItem
from app.services import briefing as briefing_service
from app.services import diagnostics as diagnostics_service
from app.services import integrations
from app.services import llm as llm_service
from app.services import settings_service
from app.services import secret_store
from app.services.backups import backup_status_payload, has_minimum_schema, latest_backup, prepare_restore_candidate
from app.services.time import utc_now_iso
from app.services.trello_config import trello_boards_ready, trello_credentials_ready
from app.services.trello_sync import trello_status


APP_NAME = "alphawave-taskd"
VERSION = "0.1.0"
STARTED_AT = utc_now_iso()
STARTED_MONOTONIC = monotonic()


def health(db: Session) -> SystemHealth:
    database = "ok"
    status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - expose coarse health only.
        database = "error"
        status = "error"
    return SystemHealth(status=status, app=APP_NAME, version=VERSION, time=utc_now_iso(), database=database)


def info() -> SystemInfo:
    return SystemInfo(
        app=APP_NAME,
        version=VERSION,
        repo_path=str(REPO_ROOT),
        database_path=str(settings.sqlite_path) if settings.sqlite_path else None,
        frontend_served=(REPO_ROOT / "frontend" / "dist" / "index.html").exists(),
        telegram_enabled=settings.telegram_enabled,
        trello_enabled=settings.trello_enabled,
        llm_enabled=False,
        briefing_enabled=settings.daily_briefing_enabled,
    )


def status(db: Session, user_id: str | None = None) -> SystemStatus:
    generated_at = utc_now_iso()
    effective = settings_service.get_settings(db, user_id=user_id)
    advanced = effective.get("advanced") or {}
    advanced_status = advanced.get("status") or {}
    reminder_config = effective.get("reminders") or {}
    briefing_config = effective.get("briefing") or {}
    backup_config = effective.get("backups") or {}
    trello = trello_status(db)
    briefing = briefing_service.status(db)
    backup = latest_backup()
    weekend_mode = settings_service.get_weekend_mode_state(db)
    telegram_ready = bool(settings.telegram_enabled and settings.telegram_bot_token and settings.telegram_allowed_user_id)
    trello_ready = bool(settings.trello_enabled and trello_credentials_ready() and trello_boards_ready(db))
    database_service = _database_status(db)
    telegram_service = _telegram_status(db, enabled=settings.telegram_enabled, configured=telegram_ready)
    trello_service = _trello_observability(effective, trello, enabled=settings.trello_enabled, configured=trello_ready)
    reminders_service = _reminders_status(db, reminder_config, telegram_ready)
    briefing_service_status = _briefing_observability(briefing_config, briefing)
    backup_service = _backup_status(backup_config, backup, backup_status_payload(db=db))
    llm_status = _llm_status(db, advanced, user_id=user_id)
    services = {
        "database": database_service,
        "telegram": telegram_service,
        "trello": trello_service,
        "reminders": reminders_service,
        "briefing": briefing_service_status,
        "backups": backup_service,
        "llm": llm_status,
        "weekend_mode": _weekend_mode_observability(weekend_mode),
    }
    overall = _overall_service_status(services)
    return SystemStatus(
        generated_at=generated_at,
        environment={
            "mode": "local",
            "timezone": effective.get("general", {}).get("timezone") or settings.app_timezone,
            "app_version": VERSION,
            "git_commit": _git_commit(),
            "git_branch": _git_branch(),
            "git_dirty": _git_dirty(),
            "git_ahead": _git_ahead(),
            "python_version": sys.version.split()[0],
            "started_at": STARTED_AT,
            "uptime_seconds": round(monotonic() - STARTED_MONOTONIC, 3),
        },
        overall=overall,
        weekend_mode=weekend_mode.to_dict(),
        services=services,
        telegram=SystemStatusItem(
            status=telegram_service["status"],
            detail=telegram_service.get("detail"),
        ),
        trello=SystemStatusItem(
            status=trello_service["status"],
            detail=trello_service.get("last_error"),
        ),
        trello_write=SystemStatusItem(
            status=str(advanced_status.get("trello_write") or "disabled"),
            detail=None,
        ),
        llm=SystemStatusItem(
            status=llm_status["status"],
            detail=llm_status.get("readiness"),
        ),
        reminders=SystemStatusIntervalItem(
            status=reminders_service["status"],
            interval_seconds=reminders_service["interval_seconds"],
        ),
        trello_sync=SystemStatusIntervalItem(
            status=trello_service["status"],
            interval_minutes=max(int(settings.trello_sync_interval_minutes or 0), 1),
            last_sync=trello.last_sync_completed_at,
            detail=trello_service.get("last_error"),
        ),
        briefing=SystemStatusIntervalItem(
            status=briefing_service_status["status"],
            last_run=briefing.last_run.sent_at or briefing.last_run.created_at if briefing.last_run else None,
        ),
        backup=SystemStatusIntervalItem(
            status=backup_service["status"],
            last_backup=backup[1] if backup else None,
        ),
    )


def installation_readiness(db: Session, user_id: str) -> InstallationReadiness:
    telegram_user = integrations.telegram_status_for_user(db, user_id)
    trello_user = integrations.trello_status_for_user(db, user_id)
    runtime_llm = llm_service.llm_status(db, user_id)

    telegram_runtime_ready = bool(settings.telegram_enabled and settings.telegram_bot_token)
    trello_runtime_ready = bool(
        settings.trello_enabled
        and settings.trello_member_id
        and trello_user.get("credentials_present")
    )
    llm_runtime_ready = runtime_llm.safe_to_use
    llm_instance_available = runtime_llm.secret_store_available

    return InstallationReadiness(
        generated_at=utc_now_iso(),
        instance={
            "runtime": ReadinessItem(status="ready", ready=True, detail="Backend y base de datos disponibles."),
            "telegram": ReadinessItem(
                status="ready" if telegram_runtime_ready else ("disabled" if not settings.telegram_enabled else "requires_configuration"),
                ready=telegram_runtime_ready,
                detail="Bot de Telegram disponible para vinculaciones." if telegram_runtime_ready else ("Telegram desactivado en esta instalación." if not settings.telegram_enabled else "Falta configurar el token del bot de Telegram."),
            ),
            "trello": ReadinessItem(
                status="ready" if trello_runtime_ready else ("disabled" if not settings.trello_enabled else "requires_configuration"),
                ready=trello_runtime_ready,
                detail="Runtime Trello disponible." if trello_runtime_ready else ("Trello desactivado en esta instalación." if not settings.trello_enabled else "Faltan credenciales o el member ID requerido por Trello."),
            ),
            "llm": ReadinessItem(
                status="ready" if llm_instance_available else "unavailable",
                ready=llm_instance_available,
                detail="El almacenamiento cifrado está disponible para OpenAI." if llm_instance_available else runtime_llm.reason,
                fallback="El parser y las heurísticas determinísticas siguen disponibles." if not runtime_llm.safe_to_use else None,
            ),
        },
        user={
            "telegram": ReadinessItem(
                status="ready" if telegram_user["configured"] else "requires_configuration",
                ready=telegram_user["configured"],
                detail="Telegram vinculado para este usuario." if telegram_user["configured"] else "Este usuario todavía no vinculó Telegram.",
            ),
            "trello": ReadinessItem(
                status="ready" if trello_user["configured"] else "requires_configuration",
                ready=trello_user["configured"],
                detail="Trello listo para este usuario." if trello_user["configured"] else ("Falta el member ID requerido por Trello." if trello_user.get("credentials_present") else "Este usuario todavía no configuró credenciales Trello completas."),
            ),
            "llm": ReadinessItem(
                status="ready" if runtime_llm.safe_to_use else ("disabled" if runtime_llm.status == "disabled" else "requires_configuration"),
                ready=runtime_llm.safe_to_use,
                detail="OpenAI disponible para este usuario." if runtime_llm.safe_to_use else runtime_llm.reason,
                fallback="El parser y las heurísticas determinísticas siguen disponibles." if not runtime_llm.safe_to_use else None,
            ),
        },
    )


def _database_status(db: Session) -> dict:
    try:
        db.execute(text("SELECT 1"))
        readable = True
        status_value = "active"
        last_error = None
    except Exception as exc:  # noqa: BLE001 - coarse status only.
        readable = False
        status_value = "error"
        last_error = _sanitize_message(str(exc))
    return {
        "status": status_value,
        "configured": True,
        "readable": readable,
        "writable_check": "not_run_read_only",
        "last_error": last_error,
    }


def _telegram_status(db: Session, *, enabled: bool, configured: bool) -> dict:
    latest = db.scalar(select(TelegramUpdate).order_by(desc(TelegramUpdate.received_at)).limit(1))
    latest_error = db.scalar(select(TelegramUpdate).where(TelegramUpdate.error.is_not(None)).order_by(desc(TelegramUpdate.received_at)).limit(1))
    status_value = _status_from_enabled(enabled, configured)
    if enabled and configured and latest_error and latest_error.status == "failed":
        status_value = "warning"
    return {
        "status": status_value,
        "enabled": enabled,
        "configured": configured,
        "polling_active": enabled,
        "manual_commands_available": enabled and configured,
        "last_update_at": latest.processed_at or latest.received_at if latest else None,
        "last_error_at": latest_error.processed_at or latest_error.received_at if latest_error else None,
        "last_error": _sanitize_message(latest_error.error) if latest_error else None,
        "detail": "Desactivado desde Settings" if not enabled else ("Configurado" if configured else "Requiere token y allowlist"),
    }


def _trello_observability(effective: dict, trello, *, enabled: bool, configured: bool) -> dict:
    boards = [_trello_board_status(alias, board) for alias, board in ((effective.get("trello") or {}).get("boards") or {}).items() if isinstance(board, dict)]
    enabled_board_warnings = [board for board in boards if board["enabled"] and board["status"] in {"warning", "error"}]
    status_value = _status_from_enabled(enabled, configured)
    if enabled and configured and trello.last_sync_status == "error":
        status_value = "error"
    elif enabled and (enabled_board_warnings or (configured and not trello.last_sync_completed_at)):
        status_value = "warning"
    return {
        "status": status_value,
        "enabled": enabled,
        "configured": configured,
        "write_enabled": bool((effective.get("advanced") or {}).get("editable", {}).get("trello_write_enabled")),
        "last_sync_at": trello.last_sync_completed_at,
        "last_error_at": trello.last_sync_completed_at if trello.last_sync_status == "error" else None,
        "last_error": _sanitize_message(trello.last_sync_error),
        "boards": boards,
    }


def _trello_board_status(alias: str, board: dict) -> dict:
    enabled = bool(board.get("enabled", True))
    workflow_states = board.get("workflow_states") if isinstance(board.get("workflow_states"), list) else []
    active_states = [state for state in workflow_states if isinstance(state, dict) and state.get("enabled", True)]
    missing_required_roles = []
    for role in ("pending", "completed"):
        if not any(str(state.get("role") or state.get("key")) == role and state.get("list_id") for state in active_states):
            missing_required_roles.append(role)
    validation = board.get("validation") if isinstance(board.get("validation"), dict) else {}
    validation_status = str(validation.get("status") or "pending")
    status_value = "disabled"
    if enabled:
        if not board.get("board_id") or missing_required_roles:
            status_value = "warning"
        elif validation_status not in {"ok", "disabled"}:
            status_value = "warning"
        else:
            status_value = "ok"
    return {
        "alias": str(board.get("alias") or alias),
        "name": str(board.get("name") or board.get("alias") or alias),
        "enabled": enabled,
        "status": status_value,
        "board_id_present": bool(str(board.get("board_id") or "").strip()),
        "validation_status": validation_status,
        "workflow_states_active": len(active_states),
        "missing_required_roles": missing_required_roles if enabled else [],
        "auto_confirm_writes": bool(board.get("auto_confirm_writes", False)),
        "last_sync_at": None,
    }


def _reminders_status(db: Session, config: dict, telegram_ready: bool) -> dict:
    enabled = bool(config.get("enabled", True))
    pending_count = db.scalar(select(func.count()).select_from(Reminder).where(Reminder.status == "pending")) or 0
    overdue_count = db.scalar(select(func.count()).select_from(Reminder).where(Reminder.status == "pending", Reminder.remind_at <= utc_now_iso())) or 0
    last_sent = db.scalar(select(Reminder).where(Reminder.status == "sent").order_by(desc(Reminder.sent_at)).limit(1))
    interval = max(float(settings.reminders_poll_interval_seconds or 0), 1.0)
    status_value = "disabled" if not enabled else ("active" if telegram_ready else "requires_configuration")
    return {
        "status": status_value,
        "enabled": enabled,
        "worker_active": enabled and settings.telegram_enabled,
        "interval_seconds": interval,
        "pending_count": pending_count,
        "overdue_count": overdue_count,
        "last_sent_at": last_sent.sent_at if last_sent else None,
        "last_error": None,
    }


def _briefing_observability(config: dict, briefing) -> dict:
    enabled = bool(config.get("enabled", True))
    latest_error = None
    if briefing.last_run and briefing.last_run.status == "failed":
        latest_error = briefing.last_run
    status_value = "disabled" if not enabled else ("error" if latest_error else "active")
    return {
        "status": status_value,
        "enabled": enabled,
        "time": config.get("time"),
        "late_cutoff": config.get("late_cutoff"),
        "timezone": config.get("timezone"),
        "last_run_at": briefing.last_run.sent_at or briefing.last_run.created_at if briefing.last_run else None,
        "next_run_at": None,
        "last_error": _sanitize_message(latest_error.reason) if latest_error else None,
    }


def _backup_status(config: dict, backup: tuple[Path, str] | None, overview: dict | None = None) -> dict:
    enabled = bool(config.get("enabled", True))
    status_value = "disabled" if not enabled else ("active" if backup else "warning")
    overview = overview or {}
    return {
        "status": status_value,
        "automatic_enabled": enabled,
        "retention_days": int(config.get("retention_days") or settings.backup_retention_days),
        "last_backup_at": backup[1] if backup else None,
        "latest_backup_path_redacted": (overview.get("latest_backup") or {}).get("path_redacted") if overview.get("latest_backup") else (backup[0].name if backup else None),
        "count": overview.get("count", 0),
        "total_size_bytes": overview.get("total_size_bytes", 0),
        "last_error": None,
    }


def _llm_status(db: Session, advanced: dict, *, user_id: str | None = None) -> dict:
    runtime = llm_service.llm_status(db, user_id)
    status_value = _system_llm_status(runtime.status)
    return {
        "status": status_value,
        "enabled": runtime.user_enabled,
        "provider": runtime.provider,
        "configured": runtime.api_key_configured,
        "model": runtime.model,
        "readiness": runtime.status,
        "validation_status": runtime.validation_status,
        "safe_to_use": runtime.safe_to_use,
        "fallback_available": runtime.fallback_available,
        "last_success_at": runtime.last_success_at,
        "last_error_at": runtime.last_error_at,
        "last_error": runtime.last_error,
        "reason": runtime.reason,
    }


def _system_llm_status(status: str) -> str:
    return {
        "requires_encryption_key": "requires_configuration",
        "requires_api_key": "requires_configuration",
        "not_validated": "unverified",
        "ready": "active",
        "error": "error",
        "disabled": "disabled",
    }.get(status, "warning")


def _weekend_mode_observability(weekend_mode: settings_service.WeekendModeState) -> dict:
    return {
        **weekend_mode.to_dict(),
        "status": "active" if weekend_mode.active_today else ("scheduled" if weekend_mode.enabled else "disabled"),
        "detail": (
            "Menos ruido automático; Telegram manual sigue activo"
            if weekend_mode.active_today
            else ("Configurado para próximos días activos" if weekend_mode.enabled else "Sin tratamiento especial")
        ),
    }


def _overall_service_status(services: dict[str, dict]) -> dict:
    values = [service.get("status") for service in services.values()]
    if "error" in values:
        return {"status": "error", "summary": "Hay errores que requieren atención."}
    if any(value in {"warning", "requires_configuration", "unverified"} for value in values):
        return {"status": "warning", "summary": "Sistema operativo con advertencias menores."}
    return {"status": "ok", "summary": "Sistema operativo."}


def _sanitize_message(value: str | None) -> str | None:
    if not value:
        return None
    return diagnostics_service.redact_sensitive(str(value))[:300]


def _git_commit() -> str | None:
    return _git_output(["git", "rev-parse", "--short", "HEAD"])


def _git_branch() -> str | None:
    return _git_output(["git", "branch", "--show-current"])


def _git_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(result.stdout.strip())


def _git_ahead() -> int | None:
    output = _git_output(["git", "rev-list", "--count", "@{u}..HEAD"])
    if output is None:
        return None
    try:
        return int(output)
    except ValueError:
        return None


def _git_output(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = result.stdout.strip()
    return output or None


def preflight(*, strict: bool = False) -> PreflightReport:
    checks: list[PreflightCheck] = []
    _repo_checks(checks, strict=strict)
    _runtime_checks(checks)
    _path_checks(checks)
    _config_checks(checks)
    _integration_checks(checks)
    return PreflightReport(status=_overall_status(checks), checks=checks)


def restore_database_from_backup(backup_path: Path, *, create_safety_backup) -> Path | None:
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")
    if not backup_path.name.endswith(".sqlite.gz"):
        raise ValueError("Backup must be a .sqlite.gz file")
    sqlite_path = settings.sqlite_path
    if not sqlite_path:
        raise ValueError("DATABASE_URL must point to a SQLite database")
    if not sqlite_path.exists():
        raise FileNotFoundError(f"Current database not found: {sqlite_path}")
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path = _restore_temp_path(sqlite_path.parent, ".restore-candidate-")
    rollback_path: Path | None = None
    replaced = False
    safety_backup: Path | None = None
    try:
        prepare_restore_candidate(backup_path, candidate_path)
        safety_result = create_safety_backup()
        safety_backup = getattr(safety_result, "path", safety_result)
        if not safety_backup:
            raise RuntimeError("Could not create the required pre-restore backup")
        safety_backup = Path(safety_backup)
        rollback_path = _restore_temp_path(sqlite_path.parent, ".restore-rollback-")
        prepare_restore_candidate(safety_backup, rollback_path)

        _dispose_database_pool()
        os.replace(candidate_path, sqlite_path)
        replaced = True
        _remove_sqlite_sidecars(sqlite_path)
        _fsync_restore_directory(sqlite_path.parent)
        _verify_restored_database(sqlite_path)
    except Exception as restore_error:
        if replaced and rollback_path and rollback_path.exists():
            try:
                os.replace(rollback_path, sqlite_path)
                rollback_path = None
                _remove_sqlite_sidecars(sqlite_path)
                _fsync_restore_directory(sqlite_path.parent)
                _verify_restored_database(sqlite_path)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"Restore failed and rollback could not be completed: {rollback_error}"
                ) from restore_error
        raise
    finally:
        candidate_path.unlink(missing_ok=True)
        if rollback_path:
            rollback_path.unlink(missing_ok=True)
    return safety_backup


def _restore_temp_path(directory: Path, prefix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".sqlite")
    os.close(descriptor)
    return Path(raw_path)


def _verify_restored_database(path: Path) -> None:
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True) as connection:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        schema_valid = has_minimum_schema(connection)
    if integrity_rows != [("ok",)]:
        raise sqlite3.DatabaseError("Restored database failed SQLite integrity_check")
    if not schema_valid:
        raise sqlite3.DatabaseError("Restored database does not contain the minimum schema")


def _dispose_database_pool() -> None:
    from app.core.db import engine

    engine.dispose()


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _fsync_restore_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def format_preflight_text(report: PreflightReport) -> str:
    lines = [f"Preflight: {report.status}"]
    for check in report.checks:
        lines.append(f"- [{check.status}] {check.name}: {check.message}")
    return "\n".join(lines)


def _repo_checks(checks: list[PreflightCheck], *, strict: bool = False) -> None:
    _add(checks, "repo_root", "ok" if (REPO_ROOT / "backend" / "app").exists() else "error", str(REPO_ROOT))
    _add(checks, "env_example", "ok" if (REPO_ROOT / ".env.example").exists() else "error", ".env.example present")
    env_status = "ok" if (REPO_ROOT / ".env").exists() else ("error" if strict else "warning")
    _add(checks, "env_file", env_status, ".env present" if (REPO_ROOT / ".env").exists() else ".env missing; copy from .env.example")


def _runtime_checks(checks: list[PreflightCheck]) -> None:
    python_path = REPO_ROOT / "backend" / ".venv" / "bin" / "python"
    node_modules = REPO_ROOT / "frontend" / "node_modules"
    dist = REPO_ROOT / "frontend" / "dist" / "index.html"
    _add(checks, "python_backend", "ok" if python_path.exists() else "error", str(python_path) if python_path.exists() else "backend virtualenv missing")
    _add(checks, "frontend_dependencies", "ok" if node_modules.exists() else "warning", "node_modules present" if node_modules.exists() else "run npm install in frontend")
    _add(checks, "frontend_build", "ok" if dist.exists() else "warning", "frontend dist present" if dist.exists() else "run npm run build")


def _path_checks(checks: list[PreflightCheck]) -> None:
    for relative in ("data", "data/backups", "logs"):
        path = REPO_ROOT / relative
        path.mkdir(parents=True, exist_ok=True)
        _add(checks, f"{relative.replace('/', '_')}_writable", "ok" if _is_writable(path) else "error", str(path))
    sqlite_path = settings.sqlite_path
    if sqlite_path:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        _add(checks, "sqlite_path", "ok" if _is_writable(sqlite_path.parent) else "error", str(sqlite_path))
    else:
        _add(checks, "sqlite_path", "error", "DATABASE_URL is not SQLite")


def _config_checks(checks: list[PreflightCheck]) -> None:
    _add(checks, "app_host", "ok" if settings.app_host == "127.0.0.1" else "warning", f"APP_HOST={settings.app_host}")
    _add(checks, "app_port", "ok" if settings.app_port == 8711 else "warning", f"APP_PORT={settings.app_port}")
    _add(checks, "timezone", "ok" if settings.app_timezone else "error", f"APP_TIMEZONE={settings.app_timezone or 'missing'}")
    _add(checks, "briefing_time", "ok" if _valid_hhmm(settings.daily_briefing_time) else "error", settings.daily_briefing_time)
    _add(checks, "briefing_cutoff", "ok" if _valid_hhmm(settings.daily_briefing_late_cutoff) else "error", settings.daily_briefing_late_cutoff)
    _add(checks, "backup_retention", "ok" if settings.backup_retention_days > 0 else "error", f"{settings.backup_retention_days} days")


def _integration_checks(checks: list[PreflightCheck]) -> None:
    if settings.telegram_enabled:
        _add(checks, "telegram_bot_token", "ok" if settings.telegram_bot_token else "error", "configured" if settings.telegram_bot_token else "TELEGRAM_ENABLED=true but TELEGRAM_BOT_TOKEN is empty")
        _add(checks, "telegram_allowed_user", "ok" if settings.telegram_allowed_user_id else "error", "configured" if settings.telegram_allowed_user_id else "TELEGRAM_ENABLED=true but TELEGRAM_ALLOWED_USER_ID is empty")
    else:
        _add(checks, "telegram_config", "ok", "disabled")

    if settings.trello_write_enabled and not settings.trello_enabled:
        _add(checks, "trello_write_config", "error", "TRELLO_WRITE_ENABLED=true requires TRELLO_ENABLED=true")
    if settings.trello_enabled:
        missing = [
            name
            for name, value in {
                "TRELLO_API_KEY": settings.trello_api_key,
                "TRELLO_TOKEN": settings.trello_token,
                "TRELLO_MEMBER_ID": settings.trello_member_id,
            }.items()
            if not value
        ]
        if not (settings.resolved_trello_alpha_board_id or settings.resolved_trello_beta_board_id):
            missing.append("at least one TRELLO_BOARD_*_ID")
        _add(checks, "trello_config", "ok" if not missing else "error", "configured" if not missing else "missing: " + ", ".join(missing))
    else:
        _add(checks, "trello_config", "ok", "disabled")

    _add(
        checks,
        "openai_secret_store",
        "ok" if secret_store.is_secret_store_available() else "warning",
        "encrypted credential storage available" if secret_store.is_secret_store_available() else "encryption key not configured",
    )


def _add(checks: list[PreflightCheck], name: str, status: str, message: str) -> None:
    checks.append(PreflightCheck(name=name, status=status, message=message))


def _status_from_enabled(enabled: bool, configured: bool) -> str:
    if enabled and configured:
        return "active"
    if enabled and not configured:
        return "requires_configuration"
    return "disabled"


def _overall_status(checks: list[PreflightCheck]) -> str:
    if any(check.status == "error" for check in checks):
        return "error"
    if any(check.status == "warning" for check in checks):
        return "warning"
    return "ok"


def _is_writable(path: Path) -> bool:
    target = path if path.is_dir() else path.parent
    probe = target / f".write-test-{datetime.now(timezone.utc).timestamp()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _valid_hhmm(value: str) -> bool:
    try:
        hour, minute = value.split(":", maxsplit=1)
        return 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59
    except (ValueError, AttributeError):
        return False
