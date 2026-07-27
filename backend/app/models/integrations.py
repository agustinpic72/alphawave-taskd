from sqlalchemy import Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserIntegration(Base):
    __tablename__ = "user_integrations"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_integrations_user_provider"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    credentials_source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class TelegramChatLink(Base):
    __tablename__ = "telegram_chat_links"
    __table_args__ = (UniqueConstraint("chat_id_hash", name="uq_telegram_chat_links_chat_hash"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    chat_id_hash: Mapped[str] = mapped_column(Text, nullable=False)
    chat_id_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class IntegrationSecret(Base):
    __tablename__ = "integration_secrets"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "secret_type", name="uq_integration_secrets_user_provider_type"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    secret_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    redacted_hint: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    rotated_at: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[str | None] = mapped_column(Text)


class TelegramLinkCode(Base):
    __tablename__ = "telegram_link_codes"
    __table_args__ = (UniqueConstraint("code_hash", name="uq_telegram_link_codes_code_hash"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    consumed_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
