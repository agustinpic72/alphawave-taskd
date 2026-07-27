import copy
import hashlib
import json
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.integrations import TelegramChatLink, TelegramLinkCode, UserIntegration
from app.services import auth as auth_service
from app.services import secret_store
from app.services.time import utc_now_iso


PROVIDER_TRELLO = "trello"
PROVIDER_TELEGRAM = "telegram"
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUS_REQUIRES_CONFIG = "requires_config"
STATUS_LEGACY_INSTANCE = "legacy_instance"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_REQUIRES_ENCRYPTION_KEY = "requires_encryption_key"
CREDENTIALS_INSTANCE_ENV = "instance_env"
CREDENTIALS_NONE = "none"
CREDENTIALS_USER_ENCRYPTED = "user_encrypted"
SECRET_TRELLO_API_KEY = "api_key"
SECRET_TRELLO_TOKEN = "token"
SECRET_TELEGRAM_CHAT_ID = "chat_id"
LINK_CODE_TTL_MINUTES = 10


@dataclass(frozen=True)
class TrelloCredentials:
    api_key: str
    token: str
    member_id: str
    source: str

    @property
    def ready(self) -> bool:
        return bool(self.api_key and self.token and self.member_id)


def user_can_use_instance_integration(db: Session, user_id: str | None) -> bool:
    return bool(user_id and user_id == auth_service.single_owner_user_id(db))


def get_user_integration(db: Session, user_id: str, provider: str) -> UserIntegration | None:
    return db.scalar(
        select(UserIntegration).where(
            UserIntegration.user_id == user_id,
            UserIntegration.provider == provider,
        )
    )


def set_user_integration(
    db: Session,
    *,
    user_id: str,
    provider: str,
    config: dict[str, Any],
    status: str = STATUS_ACTIVE,
    credentials_source: str = CREDENTIALS_INSTANCE_ENV,
) -> UserIntegration:
    now = utc_now_iso()
    row = get_user_integration(db, user_id, provider)
    created = row is None
    if created:
        row = UserIntegration(
            id=str(uuid4()),
            user_id=user_id,
            provider=provider,
            status=status,
            config_json="{}",
            credentials_source=credentials_source,
            created_at=now,
            updated_at=now,
        )
    row.status = status
    row.config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
    row.credentials_source = credentials_source
    row.updated_at = now
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        if not created:
            raise
        # Concurrent first reads can both observe no row. Keep the unique
        # constraint authoritative, then update the row created by the winner.
        db.rollback()
        row = get_user_integration(db, user_id, provider)
        if row is None:
            raise
        row.status = status
        row.config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
        row.credentials_source = credentials_source
        row.updated_at = now
        db.add(row)
        db.commit()
    db.refresh(row)
    return row


def get_trello_config_for_user(db: Session, user_id: str | None, *, legacy_config: dict[str, Any] | None = None) -> dict[str, Any]:
    if not user_id:
        return copy.deepcopy(legacy_config or {})
    row = get_user_integration(db, user_id, PROVIDER_TRELLO)
    if row and row.status in {STATUS_ACTIVE, STATUS_LEGACY_INSTANCE}:
        return _loads(row.config_json)
    if legacy_config is not None and user_can_use_instance_integration(db, user_id):
        row = set_user_integration(
            db,
            user_id=user_id,
            provider=PROVIDER_TRELLO,
            config=copy.deepcopy(legacy_config),
            status=STATUS_LEGACY_INSTANCE,
            credentials_source=CREDENTIALS_INSTANCE_ENV,
        )
        return _loads(row.config_json)
    return _disabled_trello_config()


def set_trello_config_for_user(db: Session, user_id: str, config: dict[str, Any]) -> dict[str, Any]:
    row = get_user_integration(db, user_id, PROVIDER_TRELLO)
    user_has_credentials = _has_user_trello_credentials(db, user_id)
    if not user_can_use_instance_integration(db, user_id) and not user_has_credentials and not row:
        raise PermissionError("Trello no está configurado para este usuario.")
    credentials_source = CREDENTIALS_USER_ENCRYPTED if user_has_credentials else (row.credentials_source if row else CREDENTIALS_INSTANCE_ENV)
    status = STATUS_ACTIVE if credentials_source == CREDENTIALS_USER_ENCRYPTED else STATUS_LEGACY_INSTANCE
    row = set_user_integration(
        db,
        user_id=user_id,
        provider=PROVIDER_TRELLO,
        config=copy.deepcopy(config),
        status=status,
        credentials_source=credentials_source,
    )
    return _loads(row.config_json)


def trello_sync_user_ids(db: Session) -> list[str]:
    rows = list(
        db.scalars(
            select(UserIntegration).where(
                UserIntegration.provider == PROVIDER_TRELLO,
                UserIntegration.status.in_([STATUS_ACTIVE, STATUS_LEGACY_INSTANCE]),
            )
        ).all()
    )
    user_ids = [row.user_id for row in rows]
    owner_id = auth_service.single_owner_user_id(db)
    return user_ids or [owner_id]


def link_telegram_chat(db: Session, *, user_id: str, chat_id: str, status: str = STATUS_ACTIVE) -> TelegramChatLink:
    now = utc_now_iso()
    chat_hash = chat_id_hash(chat_id)
    row = db.scalar(select(TelegramChatLink).where(TelegramChatLink.chat_id_hash == chat_hash))
    if row is None:
        row = TelegramChatLink(
            id=str(uuid4()),
            user_id=user_id,
            chat_id_hash=chat_hash,
            chat_id_redacted=redact_chat_id(chat_id),
            status=status,
            created_at=now,
            updated_at=now,
        )
    row.user_id = user_id
    row.chat_id_redacted = redact_chat_id(chat_id)
    row.status = status
    row.updated_at = now
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def resolve_telegram_user_id(db: Session, chat_id: str | None, *, legacy_from_user_id: str | None = None, allow_legacy: bool = True) -> str | None:
    if chat_id:
        row = db.scalar(
            select(TelegramChatLink).where(
                TelegramChatLink.chat_id_hash == chat_id_hash(chat_id),
                TelegramChatLink.status == STATUS_ACTIVE,
            )
        )
        if row:
            return row.user_id
    if allow_legacy and _legacy_telegram_allowed(legacy_from_user_id=legacy_from_user_id, chat_id=chat_id):
        return auth_service.single_owner_user_id(db)
    return None


def get_telegram_destination_for_user(db: Session, user_id: str | None) -> str | None:
    if user_id and secret_store.is_secret_store_available():
        try:
            chat_id = secret_store.get_integration_secret(
                db,
                user_id=user_id,
                provider=PROVIDER_TELEGRAM,
                secret_type=SECRET_TELEGRAM_CHAT_ID,
            )
        except secret_store.SecretDecryptionError:
            chat_id = None
        if chat_id:
            return chat_id
    if user_id is None:
        return settings.telegram_allowed_user_id or None
    if user_can_use_instance_integration(db, user_id) and settings.telegram_allowed_user_id:
        return settings.telegram_allowed_user_id
    return None


def create_telegram_link_code(db: Session, *, user_id: str) -> tuple[str, TelegramLinkCode]:
    code = _random_link_code()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    row = TelegramLinkCode(
        id=str(uuid4()),
        user_id=user_id,
        code_hash=link_code_hash(code),
        expires_at=(now + timedelta(minutes=LINK_CODE_TTL_MINUTES)).isoformat(),
        consumed_at=None,
        created_at=now.isoformat(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return code, row


def consume_telegram_link_code(db: Session, *, code: str, chat_id: str) -> tuple[bool, str, str | None]:
    row = db.scalar(
        select(TelegramLinkCode).where(
            TelegramLinkCode.code_hash == link_code_hash(code),
        )
    )
    if row is None:
        return False, "unknown_code", None
    if row.consumed_at:
        return False, "already_used", None
    if _parse_iso(row.expires_at) <= datetime.now(timezone.utc):
        return False, "expired", None
    link_telegram_chat(db, user_id=row.user_id, chat_id=chat_id)
    secret_status = "encrypted"
    if secret_store.is_secret_store_available():
        secret_store.store_integration_secret(
            db,
            user_id=row.user_id,
            provider=PROVIDER_TELEGRAM,
            secret_type=SECRET_TELEGRAM_CHAT_ID,
            plaintext=chat_id,
        )
    else:
        secret_status = "inbound_only"
    row.consumed_at = utc_now_iso()
    db.add(row)
    db.commit()
    return True, secret_status, row.user_id


def telegram_status_for_user(db: Session, user_id: str) -> dict[str, Any]:
    link = db.scalar(
        select(TelegramChatLink).where(
            TelegramChatLink.user_id == user_id,
            TelegramChatLink.status == STATUS_ACTIVE,
        )
    )
    chat_hint = secret_store.get_integration_secret_hint(
        db,
        user_id=user_id,
        provider=PROVIDER_TELEGRAM,
        secret_type=SECRET_TELEGRAM_CHAT_ID,
    ) if secret_store.is_secret_store_available() else None
    legacy = user_can_use_instance_integration(db, user_id) and bool(settings.telegram_allowed_user_id)
    if chat_hint:
        status = "linked"
    elif link:
        status = "linked_inbound_only" if not secret_store.is_secret_store_available() else "linked"
    elif legacy:
        status = "legacy_instance"
    elif not secret_store.is_secret_store_available():
        status = STATUS_REQUIRES_ENCRYPTION_KEY
    else:
        status = "not_linked"
    return {
        "provider": PROVIDER_TELEGRAM,
        "configured": bool(chat_hint or link or legacy),
        "linked": bool(chat_hint or link),
        "status": status,
        "chat_redacted": chat_hint or (link.chat_id_redacted if link else ""),
        "legacy_fallback": bool(legacy and not (chat_hint or link)),
        "secret_store_available": secret_store.is_secret_store_available(),
    }


def unlink_telegram(db: Session, *, user_id: str) -> None:
    links = list(
        db.scalars(
            select(TelegramChatLink).where(
                TelegramChatLink.user_id == user_id,
                TelegramChatLink.status == STATUS_ACTIVE,
            )
        )
    )
    now = utc_now_iso()
    for link in links:
        link.status = STATUS_DISABLED
        link.updated_at = now
        db.add(link)
    secret_store.revoke_integration_secret(
        db,
        user_id=user_id,
        provider=PROVIDER_TELEGRAM,
        secret_type=SECRET_TELEGRAM_CHAT_ID,
    )
    db.commit()


def store_trello_credentials(db: Session, *, user_id: str, api_key: str, token: str) -> None:
    if not secret_store.is_secret_store_available():
        raise secret_store.SecretStoreUnavailable("Falta ALPHAWAVE_SECRET_ENCRYPTION_KEY.")
    secret_store.store_integration_secret(
        db,
        user_id=user_id,
        provider=PROVIDER_TRELLO,
        secret_type=SECRET_TRELLO_API_KEY,
        plaintext=api_key,
    )
    secret_store.store_integration_secret(
        db,
        user_id=user_id,
        provider=PROVIDER_TRELLO,
        secret_type=SECRET_TRELLO_TOKEN,
        plaintext=token,
    )
    row = get_user_integration(db, user_id, PROVIDER_TRELLO)
    config = _loads(row.config_json) if row else _disabled_trello_config()
    config["status"] = STATUS_ACTIVE
    config["credentials_source"] = CREDENTIALS_USER_ENCRYPTED
    set_user_integration(
        db,
        user_id=user_id,
        provider=PROVIDER_TRELLO,
        config=config,
        status=STATUS_ACTIVE,
        credentials_source=CREDENTIALS_USER_ENCRYPTED,
    )


def revoke_trello_credentials(db: Session, *, user_id: str) -> None:
    secret_store.revoke_integration_secret(db, user_id=user_id, provider=PROVIDER_TRELLO, secret_type=SECRET_TRELLO_API_KEY)
    secret_store.revoke_integration_secret(db, user_id=user_id, provider=PROVIDER_TRELLO, secret_type=SECRET_TRELLO_TOKEN)
    row = get_user_integration(db, user_id, PROVIDER_TRELLO)
    if row and row.credentials_source == CREDENTIALS_USER_ENCRYPTED:
        row.credentials_source = CREDENTIALS_NONE
        row.status = STATUS_REQUIRES_CONFIG
        row.updated_at = utc_now_iso()
        db.add(row)
        db.commit()


def get_trello_credentials_for_user(db: Session, user_id: str | None) -> TrelloCredentials | None:
    if user_id and secret_store.is_secret_store_available() and _has_user_trello_credentials(db, user_id):
        try:
            api_key = secret_store.get_integration_secret(db, user_id=user_id, provider=PROVIDER_TRELLO, secret_type=SECRET_TRELLO_API_KEY)
            token = secret_store.get_integration_secret(db, user_id=user_id, provider=PROVIDER_TRELLO, secret_type=SECRET_TRELLO_TOKEN)
        except secret_store.SecretDecryptionError:
            return None
        if api_key and token:
            return TrelloCredentials(api_key=api_key, token=token, member_id=settings.trello_member_id, source=CREDENTIALS_USER_ENCRYPTED)
    if user_id is None or user_can_use_instance_integration(db, user_id):
        if settings.trello_api_key and settings.trello_token and settings.trello_member_id:
            return TrelloCredentials(
                api_key=settings.trello_api_key,
                token=settings.trello_token,
                member_id=settings.trello_member_id,
                source=CREDENTIALS_INSTANCE_ENV,
            )
    return None


def trello_status_for_user(db: Session, user_id: str) -> dict[str, Any]:
    api_hint = secret_store.get_integration_secret_hint(
        db,
        user_id=user_id,
        provider=PROVIDER_TRELLO,
        secret_type=SECRET_TRELLO_API_KEY,
    ) if secret_store.is_secret_store_available() else None
    token_hint = secret_store.get_integration_secret_hint(
        db,
        user_id=user_id,
        provider=PROVIDER_TRELLO,
        secret_type=SECRET_TRELLO_TOKEN,
    ) if secret_store.is_secret_store_available() else None
    credentials = get_trello_credentials_for_user(db, user_id)
    legacy = bool(credentials and credentials.source == CREDENTIALS_INSTANCE_ENV)
    instance_credentials_present = bool(
        user_can_use_instance_integration(db, user_id)
        and settings.trello_api_key
        and settings.trello_token
    )
    credentials_present = bool(api_hint and token_hint) or legacy or instance_credentials_present
    member_id_configured = bool(settings.trello_member_id)
    if api_hint and token_hint and member_id_configured:
        status = "configured"
        source = CREDENTIALS_USER_ENCRYPTED
    elif api_hint and token_hint:
        status = STATUS_REQUIRES_CONFIG
        source = CREDENTIALS_USER_ENCRYPTED
    elif instance_credentials_present:
        status = STATUS_REQUIRES_CONFIG
        source = CREDENTIALS_INSTANCE_ENV
    elif legacy:
        status = STATUS_LEGACY_INSTANCE
        source = CREDENTIALS_INSTANCE_ENV
    elif not secret_store.is_secret_store_available():
        status = STATUS_REQUIRES_ENCRYPTION_KEY
        source = CREDENTIALS_NONE
    else:
        status = STATUS_NOT_CONFIGURED
        source = CREDENTIALS_NONE
    return {
        "provider": PROVIDER_TRELLO,
        "configured": bool(credentials and credentials.ready),
        "credentials_present": credentials_present,
        "member_id_configured": member_id_configured,
        "credentials_source": source,
        "status": status,
        "api_key_hint": api_hint or "",
        "token_hint": token_hint or "",
        "secret_store_available": secret_store.is_secret_store_available(),
    }


def chat_id_hash(chat_id: str) -> str:
    return hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()


def link_code_hash(code: str) -> str:
    return hashlib.sha256(str(code).strip().upper().encode("utf-8")).hexdigest()


def redact_chat_id(chat_id: str | None) -> str:
    if not chat_id:
        return ""
    value = str(chat_id)
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}...{value[-2:]}"


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _disabled_trello_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "write_enabled": False,
        "boards": {},
        "status": STATUS_REQUIRES_CONFIG,
        "credentials_source": CREDENTIALS_NONE,
    }


def _has_user_trello_credentials(db: Session, user_id: str) -> bool:
    return (
        secret_store.has_integration_secret(db, user_id=user_id, provider=PROVIDER_TRELLO, secret_type=SECRET_TRELLO_API_KEY)
        and secret_store.has_integration_secret(db, user_id=user_id, provider=PROVIDER_TRELLO, secret_type=SECRET_TRELLO_TOKEN)
    )


def _random_link_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _legacy_telegram_allowed(*, legacy_from_user_id: str | None, chat_id: str | None) -> bool:
    allowed = str(settings.telegram_allowed_user_id or "")
    if not allowed:
        return False
    return str(legacy_from_user_id or "") == allowed or str(chat_id or "") == allowed
