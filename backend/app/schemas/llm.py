from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EffortBucket = Literal["quick", "medium", "deep", "unknown"]
ContextBucket = Literal["deep_work", "quick_task", "call", "errand", "admin", "review", "unknown"]
PriorityLabel = Literal["high", "medium_high", "medium", "low"]
AIStatusValue = Literal[
    "disabled",
    "requires_encryption_key",
    "requires_api_key",
    "not_validated",
    "ready",
    "error",
]


class StrictAIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskClassification(StrictAIModel):
    normalized_title: str = Field(min_length=1, max_length=500)
    scope: str = Field(min_length=1, max_length=100)
    scope_confidence: float = Field(ge=0.0, le=1.0)
    effort_bucket: EffortBucket = "unknown"
    estimated_minutes: int | None = Field(default=None, ge=5, le=1440)
    context_bucket: ContextBucket = "unknown"
    impact_score: int | None = Field(default=None, ge=1, le=5)
    urgency_score: int | None = Field(default=None, ge=1, le=5)
    blocking_score: int | None = Field(default=None, ge=1, le=5)
    due_at: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=500)


SafeCommandIntent = Literal[
    "list_todo",
    "add_local_task",
    "planning_today",
    "planning_now",
    "list_reminders",
    "briefing",
    "sync_trello",
    "classify_inbox",
    "unknown_or_needs_clarification",
]


class CommandIntentRequest(StrictAIModel):
    text: str = Field(min_length=1, max_length=1000)
    normalized_text: str = Field(min_length=1, max_length=1000)
    allowed_intents: list[str] = Field(max_length=20)


class CommandIntent(StrictAIModel):
    intent: SafeCommandIntent
    confidence: float = Field(ge=0.0, le=1.0)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    reason: str = Field(default="", max_length=500)


class OpenAITaskSuggestionInput(StrictAIModel):
    title: str = Field(min_length=1, max_length=500)
    scope: str = Field(min_length=1, max_length=100)
    current: dict[str, object | None]
    missing_fields: list[str] = Field(max_length=20)
    context: str | None = Field(default=None, max_length=6000)
    allowed_scopes: list[str] = Field(max_length=100)
    include_notes: bool = True
    language: str = "es"


class OpenAITaskSuggestion(StrictAIModel):
    normalized_title: str | None = Field(default=None, min_length=1, max_length=500)
    scope: str | None = Field(default=None, min_length=1, max_length=100)
    priority_label: PriorityLabel | None = None
    impact_score: int | None = Field(default=None, ge=1, le=5)
    urgency_score: int | None = Field(default=None, ge=1, le=5)
    blocking_score: int | None = Field(default=None, ge=1, le=5)
    effort_bucket: EffortBucket | None = None
    estimated_minutes: int | None = Field(default=None, ge=5, le=1440)
    context_bucket: ContextBucket | None = None
    due_at: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)
    reasoning_summary: str = Field(min_length=1, max_length=500)
    warnings: list[str] = Field(default_factory=list, max_length=10)


class OpenAIValidationProbe(StrictAIModel):
    ok: Literal[True]


class LLMStatus(BaseModel):
    enabled: bool
    user_enabled: bool
    provider: Literal["openai"] = "openai"
    status: AIStatusValue
    api_key_configured: bool
    api_key_hint: str | None = None
    model: str
    model_configured: bool
    model_supported: bool = True
    model_available: bool | None = None
    validated_model: str | None = None
    validation_status: AIStatusValue
    secret_store_available: bool
    last_validated_at: str | None = None
    last_success_at: str | None = None
    last_error_at: str | None = None
    last_error: str | None = None
    safe_to_use: bool = False
    fallback_available: bool = True
    reason: str


class LLMValidateResponse(BaseModel):
    status: LLMStatus
    checked: bool = True


class OpenAICredentialsRequest(BaseModel):
    api_key: str


class OpenAISettingsRequest(BaseModel):
    enabled: bool | None = None
    model: str | None = Field(default=None, min_length=1, max_length=100)


class OpenAIModelOption(BaseModel):
    id: str
    display_name: str
    category: str
    recommendation: str | None = None
    created_at: datetime | None = None
    owned_by: str | None = None
    is_current: bool = False
    is_validated: bool = False
    compatibility: Literal["supported", "requires_validation", "unavailable"]


class OpenAIModelCatalog(BaseModel):
    models: list[OpenAIModelOption] = Field(default_factory=list)
    current_model: str | None = None
    recommended_model: str | None = None
    fetched_at: datetime | None = None
    cached: bool = False
    stale: bool = False
    policy_version: str
    status: Literal["ready", "requires_api_key", "requires_encryption_key", "fetch_error"]
    error: str | None = None
