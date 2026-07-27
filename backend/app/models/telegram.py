from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TelegramUpdate(Base):
    __tablename__ = "telegram_updates"

    update_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(Text, nullable=False)
    from_user_id: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    processed_at: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class TelegramSnapshot(Base):
    __tablename__ = "telegram_snapshots"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    chat_id: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[int | None] = mapped_column(Integer)
    snapshot_type: Mapped[str] = mapped_column(Text, nullable=False, default="tasks")
    task_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class ActionQueueItem(Base):
    __tablename__ = "action_queue"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_update_id: Mapped[int | None] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    processed_at: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
