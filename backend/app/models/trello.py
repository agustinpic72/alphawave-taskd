from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TrelloSyncRun(Base):
    __tablename__ = "trello_sync_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    cards_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tasks_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tasks_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tasks_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attention_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ignored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
