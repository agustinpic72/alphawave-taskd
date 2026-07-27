from typing import Literal

from pydantic import BaseModel, Field


class SystemHealth(BaseModel):
    status: str
    app: str
    version: str
    time: str
    database: str


class SystemInfo(BaseModel):
    app: str
    version: str
    repo_path: str
    database_path: str | None
    frontend_served: bool
    telegram_enabled: bool
    trello_enabled: bool
    llm_enabled: bool
    briefing_enabled: bool


class PreflightCheck(BaseModel):
    name: str
    status: str
    message: str


class PreflightReport(BaseModel):
    status: str
    checks: list[PreflightCheck]


class BackupStatus(BaseModel):
    latest_path: str | None = None
    latest_created_at: str | None = None
    automatic_enabled: bool | None = None
    retention_days: int | None = None
    backup_dir: str | None = None
    latest_backup: dict | None = None
    count: int = 0
    total_size_bytes: int = 0
    last_error: str | None = None


class BackupList(BaseModel):
    backups: list[dict]


class BackupCreateResponse(BaseModel):
    created: bool
    backup: dict | None = None
    path: str | None = None
    reason: str | None = None


class BackupValidateResponse(BaseModel):
    backup: dict


class BackupRestorePlan(BaseModel):
    backup: dict
    current_db: dict | None = None
    actions: list[str]
    requires_confirmation: bool = True
    confirmation_phrase: str = "RESTAURAR BACKUP"
    automatic_restore_available: bool = False
    manual_commands: list[str] = []
    reason: str | None = None


class BackupRestoreRequest(BaseModel):
    confirmation_phrase: Literal["RESTAURAR BACKUP"]


class BackupRestoreResponse(BaseModel):
    restored: bool = True
    backup_id: str
    safety_backup: dict
    message: str = Field(default="Restore completed and post-restore integrity verified.")


class SystemStatusItem(BaseModel):
    status: str
    detail: str | None = None


class SystemStatusIntervalItem(SystemStatusItem):
    interval_seconds: float | None = None
    interval_minutes: int | None = None
    last_sync: str | None = None
    last_backup: str | None = None
    last_run: str | None = None


class SystemStatus(BaseModel):
    generated_at: str | None = None
    environment: dict | None = None
    overall: dict | None = None
    services: dict | None = None
    weekend_mode: dict | None = None
    telegram: SystemStatusItem
    trello: SystemStatusItem
    trello_write: SystemStatusItem
    llm: SystemStatusItem
    reminders: SystemStatusIntervalItem
    trello_sync: SystemStatusIntervalItem
    briefing: SystemStatusIntervalItem
    backup: SystemStatusIntervalItem


class ReadinessItem(BaseModel):
    status: Literal["ready", "requires_configuration", "unavailable", "disabled"]
    ready: bool
    detail: str
    fallback: str | None = None


class InstallationReadiness(BaseModel):
    generated_at: str
    instance: dict[str, ReadinessItem]
    user: dict[str, ReadinessItem]
