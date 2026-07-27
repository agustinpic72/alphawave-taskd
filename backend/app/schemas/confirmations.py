from pydantic import BaseModel, ConfigDict


class ConfirmationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    chat_id: str | None
    user_message_id: int | None
    action_type: str
    payload_json: str
    status: str
    created_at: str
    expires_at: str
    resolved_at: str | None
    summary: str | None = None
    error: str | None = None


class ConfirmationList(BaseModel):
    confirmations: list[ConfirmationRead]


class ConfirmationBulkRequest(BaseModel):
    confirmation_ids: list[str]


class ConfirmationBulkResult(BaseModel):
    id: str
    status: str
    message: str | None = None
    error: str | None = None


class ConfirmationBulkResponse(BaseModel):
    status: str
    total: int
    confirmed: int = 0
    cancelled: int = 0
    failed: int = 0
    results: list[ConfirmationBulkResult]
