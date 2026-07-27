from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(Text, index=True)
    task_id: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    remind_at: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False, default="telegram")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    source_update_id: Mapped[int | None] = mapped_column(Integer)


Index("ix_reminders_status", Reminder.status)
Index("ix_reminders_remind_at", Reminder.remind_at)
Index("ix_reminders_task_id", Reminder.task_id)
