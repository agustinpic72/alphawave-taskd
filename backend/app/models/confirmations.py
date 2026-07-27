from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PendingConfirmation(Base):
    __tablename__ = "pending_confirmations"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(Text, index=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    chat_id: Mapped[str | None] = mapped_column(Text)
    user_message_id: Mapped[int | None] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_at: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
