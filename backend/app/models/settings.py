from sqlalchemy import Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class UserSetting(Base):
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", "section", "key", name="uq_user_settings_user_section_key"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class SettingsAudit(Base):
    __tablename__ = "settings_audit"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    old_value_json: Mapped[str | None] = mapped_column(Text)
    new_value_json: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="ui")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
