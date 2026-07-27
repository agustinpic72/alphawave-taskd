from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(Text, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    task_kind: Mapped[str] = mapped_column(Text, nullable=False, default="normal")

    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="local")
    source_id: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)

    scope: Mapped[str] = mapped_column(Text, nullable=False, default="Inbox")
    origin_label: Mapped[str | None] = mapped_column(Text)
    manual_order: Mapped[int] = mapped_column(Integer, nullable=False)

    trello_board_id: Mapped[str | None] = mapped_column(Text)
    trello_board_name: Mapped[str | None] = mapped_column(Text)
    trello_list_id: Mapped[str | None] = mapped_column(Text)
    trello_list_name: Mapped[str | None] = mapped_column(Text)
    trello_state: Mapped[str | None] = mapped_column(Text)

    checklist_done: Mapped[int | None] = mapped_column(Integer)
    checklist_total: Mapped[int | None] = mapped_column(Integer)

    priority_label: Mapped[str | None] = mapped_column(Text)
    impact_score: Mapped[int | None] = mapped_column(Integer)
    urgency_score: Mapped[int | None] = mapped_column(Integer)
    blocking_score: Mapped[int | None] = mapped_column(Integer)

    effort_bucket: Mapped[str | None] = mapped_column(Text)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    context_bucket: Mapped[str | None] = mapped_column(Text)

    due_at: Mapped[str | None] = mapped_column(Text)
    snoozed_until: Mapped[str | None] = mapped_column(Text)

    first_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_touched_at: Mapped[str | None] = mapped_column(Text)
    last_trello_activity_at: Mapped[str | None] = mapped_column(Text)
    last_checkin_at: Mapped[str | None] = mapped_column(Text)
    next_checkin_at: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[str | None] = mapped_column(Text)

    metadata_json: Mapped[str | None] = mapped_column(Text)


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(Text, index=True)
    task_id: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class DeletedExternalRef(Base):
    __tablename__ = "deleted_external_refs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    deleted_at: Mapped[str] = mapped_column(Text, nullable=False)
