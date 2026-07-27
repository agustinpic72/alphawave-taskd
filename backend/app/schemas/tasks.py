from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskBase(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    scope: str = "Inbox"
    priority_label: str | None = None
    due_at: str | None = None
    impact_score: int | None = Field(default=None, ge=1, le=5)
    urgency_score: int | None = Field(default=None, ge=1, le=5)
    blocking_score: int | None = Field(default=None, ge=1, le=5)
    effort_bucket: str | None = None
    estimated_minutes: int | None = Field(default=None, ge=1)
    context_bucket: str | None = None


class TaskCreate(TaskBase):
    auto_classify: bool = True


class BulkTaskCreate(BaseModel):
    text: str = Field(min_length=1)
    auto_classify: bool = True


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    scope: str | None = None
    notes: str | None = Field(default=None, max_length=10000)
    priority_label: str | None = None
    due_at: str | None = None
    snoozed_until: str | None = None
    impact_score: int | None = Field(default=None, ge=1, le=5)
    urgency_score: int | None = Field(default=None, ge=1, le=5)
    blocking_score: int | None = Field(default=None, ge=1, le=5)
    effort_bucket: str | None = None
    estimated_minutes: int | None = Field(default=None, ge=1)
    context_bucket: str | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: str
    task_kind: str
    source_type: str
    source_id: str | None
    source_url: str | None
    scope: str
    origin_label: str | None
    manual_order: int
    trello_board_name: str | None
    trello_list_name: str | None
    trello_state: str | None
    trello_board_id: str | None = None
    trello_list_id: str | None = None
    checklist_done: int | None
    checklist_total: int | None
    priority_label: str | None
    impact_score: int | None
    urgency_score: int | None
    blocking_score: int | None
    effort_bucket: str | None
    estimated_minutes: int | None
    context_bucket: str | None
    due_at: str | None
    snoozed_until: str | None
    last_trello_activity_at: str | None = None
    next_checkin_at: str | None = None
    metadata_json: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None
    deleted_at: str | None


class TaskList(BaseModel):
    tasks: list[TaskRead]


class ReorderRequest(BaseModel):
    task_ids: list[str]


class BulkTaskIdsRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1)


class TrashDeleteResult(BaseModel):
    deleted_count: int


class TrashRestoreResult(BaseModel):
    restored_count: int


class TaskSnoozeRequest(BaseModel):
    snoozed_until: str = Field(min_length=1)
    reason: str | None = None


class TaskSuggestionRequest(BaseModel):
    provider: str = "default"
    include_notes: bool = True
    include_checklist: bool = False
    allow_existing_field_updates: bool = False


class SuggestionItem(BaseModel):
    value: Any = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    applies_to_empty_field: bool = True


class TaskSuggestionResponse(BaseModel):
    task_id: str
    suggestions: dict[str, SuggestionItem]
    source: str
    llm_status: str = "disabled"
    missing_fields: list[str] = []
    created_at: str
    warnings: list[str] = []


class TaskSuggestionApplyRequest(BaseModel):
    suggestions: dict[str, Any]
    fields: list[str] = Field(min_length=1)
    allow_existing_field_updates: bool = False
    source: str = "unknown"


class TaskSuggestionApplySkip(BaseModel):
    field: str
    reason: str


class TaskSuggestionApplyResponse(BaseModel):
    task: TaskRead
    applied_fields: list[str] = Field(default_factory=list)
    skipped_fields: list[TaskSuggestionApplySkip] = Field(default_factory=list)
    remaining_missing_fields: list[str] = Field(default_factory=list)
    is_candidate: bool = False
    priority_explanation: dict[str, Any] = Field(default_factory=dict)


class TaskDetailGapsResponse(BaseModel):
    task_id: str
    missing_fields: list[str]
    is_candidate: bool


class IncompleteTaskRead(BaseModel):
    id: str
    title: str
    scope: str
    status: str
    source_type: str
    source_id: str | None = None
    updated_at: str
    version_token: str
    missing_fields: list[str]
    missing_count: int
    candidate: bool
    current: dict[str, Any] = Field(default_factory=dict)


class IncompleteTaskList(BaseModel):
    tasks: list[IncompleteTaskRead]
    total_candidates: int = 0
    limit: int = 25


class ManualTaskDetailsUpdate(BaseModel):
    expected_updated_at: str
    expected_version_token: str
    notes: str | None = Field(default=None, max_length=10000)
    priority_label: Literal["high", "medium_high", "medium", "low"] | None = None
    impact_score: int | None = Field(default=None, ge=1, le=5)
    urgency_score: int | None = Field(default=None, ge=1, le=5)
    blocking_score: int | None = Field(default=None, ge=1, le=5)
    effort_bucket: Literal["quick", "medium", "deep"] | None = None
    estimated_minutes: int | None = Field(default=None, ge=1)
    context_bucket: Literal["deep_work", "quick_task", "call", "errand", "admin", "review"] | None = None

    @model_validator(mode="after")
    def require_manual_change(self):
        changed_fields = self.model_fields_set.difference({"expected_updated_at", "expected_version_token"})
        if not changed_fields or not any(getattr(self, field) is not None for field in changed_fields):
            raise ValueError("Incluí al menos un detalle para guardar.")
        return self


class ManualTaskDetailsResponse(BaseModel):
    task: TaskRead
    remaining_missing_fields: list[str] = Field(default_factory=list)
    is_candidate: bool = False
    priority_explanation: dict[str, Any] = Field(default_factory=dict)


class BulkTaskSuggestionRequest(BaseModel):
    task_ids: list[str] | None = None
    limit: int = Field(default=10, ge=1, le=25)
    include_notes: bool = True
    include_checklist: bool = False
    allow_existing_field_updates: bool = False


class BulkTaskSuggestionResult(BaseModel):
    task_id: str
    ok: bool
    suggestion: TaskSuggestionResponse | None = None
    error: str | None = None


class BulkTaskSuggestionResponse(BaseModel):
    mode: str = "sync"
    source_summary: dict[str, int]
    results: list[BulkTaskSuggestionResult]
    warnings: list[str] = Field(default_factory=list)


class BulkTaskSuggestionApplyItem(BaseModel):
    task_id: str
    suggestions: dict[str, Any]
    fields: list[str] = Field(min_length=1)
    allow_existing_field_updates: bool = False
    source: str = "unknown"


class BulkTaskSuggestionApplyError(BaseModel):
    task_id: str
    error: str


class BulkTaskSuggestionApplyRequest(BaseModel):
    items: list[BulkTaskSuggestionApplyItem] = Field(min_length=1, max_length=25)


class BulkTaskSuggestionApplyResponse(BaseModel):
    applied: int
    errors: list[BulkTaskSuggestionApplyError] = Field(default_factory=list)
    tasks: list[TaskRead] = Field(default_factory=list)
    results: list[TaskSuggestionApplyResponse] = Field(default_factory=list)


class TaskSuggestionConfirmationResponse(BaseModel):
    created: int
    skipped: int
    confirmation_ids: list[str]


class TrelloCardLinkRequest(BaseModel):
    target_state: str = "pending"


class TrelloLinkedCard(BaseModel):
    id: str
    url: str | None = None


class TrelloCardLinkResponse(BaseModel):
    status: str
    task: TaskRead
    card: TrelloLinkedCard


class HealthResponse(BaseModel):
    status: str
    app: str
