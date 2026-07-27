from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class BriefingRun(Base):
    __tablename__ = "briefing_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(Text, index=True)
    briefing_date: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text, nullable=False, default="telegram")
    payload_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
