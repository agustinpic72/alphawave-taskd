from typing import Any

from pydantic import BaseModel, Field


class SettingsResponse(BaseModel):
    settings: dict[str, Any]
    sources: dict[str, str]
    requires_restart: list[str] = Field(default_factory=list)
    available_scopes: list[str] = Field(default_factory=list)
    selected_weekend_scopes: list[str] = Field(default_factory=list)


class SettingsResetRequest(BaseModel):
    section: str


class TrelloMappingPatch(BaseModel):
    state: str
    list_id: str | None = None
    list_name: str | None = None


class TrelloBoardCreate(BaseModel):
    alias: str
    name: str
    board_id: str
    auto_confirm_writes: bool | None = None


class TrelloBoardPatch(BaseModel):
    alias: str | None = None
    name: str | None = None
    board_id: str | None = None
    enabled: bool | None = None
    auto_confirm_writes: bool | None = None
    workflow_states: list[dict[str, Any]] | None = None
    states: dict[str, Any] | None = None


class TrelloDiscoveryResponse(BaseModel):
    boards: list[dict[str, Any]]


class TrelloValidationResponse(BaseModel):
    status: str
    boards: list[dict[str, Any]]
    settings: dict[str, Any]
