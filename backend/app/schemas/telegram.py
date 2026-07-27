from pydantic import BaseModel, Field


class SimulateTelegramMessageRequest(BaseModel):
    text: str = Field(min_length=1)
    chat_id: str = "dev-chat"
    user_id: str = "dev-user"
    message_id: int | None = None
    update_id: int | None = None


class TelegramProcessResponse(BaseModel):
    processed: int
    ignored: int = 0
    replies: list[str] = []
    summary_sent: bool = False

