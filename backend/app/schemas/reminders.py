from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic import field_validator


def _validate_iso_datetime(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("remind_at debe ser una fecha ISO válida.") from exc
    return value


class ReminderCreate(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    remind_at: str = Field(min_length=1)
    task_id: str | None = None
    channel: str = "telegram"
    source: str | None = None

    @field_validator("remind_at")
    @classmethod
    def validate_remind_at(cls, value: str) -> str:
        return _validate_iso_datetime(value) or value


class ReminderUpdate(BaseModel):
    message: str | None = Field(default=None, min_length=1, max_length=1000)
    remind_at: str | None = Field(default=None, min_length=1)
    task_id: str | None = None
    status: str | None = None

    @field_validator("remind_at")
    @classmethod
    def validate_remind_at(cls, value: str | None) -> str | None:
        return _validate_iso_datetime(value)


class ReminderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str | None
    message: str
    remind_at: str
    channel: str
    status: str
    created_at: str
    updated_at: str
    sent_at: str | None
    source: str | None
    source_update_id: int | None


class ReminderList(BaseModel):
    reminders: list[ReminderRead]
