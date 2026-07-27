from pydantic import BaseModel


class TrelloSyncSummary(BaseModel):
    status: str
    cards_seen: int = 0
    tasks_created: int = 0
    tasks_updated: int = 0
    tasks_completed: int = 0
    attention_items: int = 0
    ignored: int = 0
    error: str | None = None


class TrelloStatus(BaseModel):
    enabled: bool
    configured: bool
    last_sync_started_at: str | None = None
    last_sync_completed_at: str | None = None
    last_sync_status: str | None = None
    last_sync_error: str | None = None


class TrelloCreateCardProposal(BaseModel):
    board_alias: str
    title: str
    list_name: str | None = None
    description: str | None = None
    due_at: str | None = None


class TrelloCreateCardNow(BaseModel):
    board_alias: str
    title: str
    description: str | None = None
    due_at: str | None = None
    priority: str | None = None


class TrelloMoveProposal(BaseModel):
    target_state: str


class TrelloRenameProposal(BaseModel):
    title: str


class TrelloDueProposal(BaseModel):
    due_at: str | None
