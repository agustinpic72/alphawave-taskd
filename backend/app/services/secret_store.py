from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.integrations import IntegrationSecret
from app.services.time import utc_now_iso

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - exercised only in incomplete deployments.
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment]


STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"


class SecretStoreUnavailable(RuntimeError):
    pass


class SecretDecryptionError(RuntimeError):
    pass


def is_secret_store_available() -> bool:
    if Fernet is None:
        return False
    key = settings.alphawave_secret_encryption_key.strip()
    if not key:
        return False
    try:
        Fernet(key.encode("utf-8"))
    except Exception:
        return False
    return True


def encrypt_secret(plaintext: str) -> str:
    fernet = _fernet()
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    fernet = _fernet()
    try:
        return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretDecryptionError("No se pudo descifrar el secreto de integración.") from exc


def store_integration_secret(
    db: Session,
    *,
    user_id: str,
    provider: str,
    secret_type: str,
    plaintext: str,
) -> IntegrationSecret:
    value = str(plaintext or "").strip()
    if not value:
        raise ValueError("El secreto no puede estar vacío.")
    now = utc_now_iso()
    row = _secret_row(db, user_id=user_id, provider=provider, secret_type=secret_type)
    if row is None:
        row = IntegrationSecret(
            id=str(uuid4()),
            user_id=user_id,
            provider=provider,
            secret_type=secret_type,
            ciphertext="",
            redacted_hint=None,
            status=STATUS_ACTIVE,
            created_at=now,
            updated_at=now,
            rotated_at=None,
            revoked_at=None,
        )
    else:
        row.rotated_at = now
    row.ciphertext = encrypt_secret(value)
    row.redacted_hint = redact_secret_hint(value)
    row.status = STATUS_ACTIVE
    row.updated_at = now
    row.revoked_at = None
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_integration_secret(db: Session, *, user_id: str, provider: str, secret_type: str) -> str | None:
    row = _secret_row(db, user_id=user_id, provider=provider, secret_type=secret_type)
    if row is None or row.status != STATUS_ACTIVE:
        return None
    return decrypt_secret(row.ciphertext)


def get_integration_secret_hint(db: Session, *, user_id: str, provider: str, secret_type: str) -> str | None:
    row = _secret_row(db, user_id=user_id, provider=provider, secret_type=secret_type)
    if row is None or row.status != STATUS_ACTIVE:
        return None
    return row.redacted_hint


def has_integration_secret(db: Session, *, user_id: str, provider: str, secret_type: str) -> bool:
    row = _secret_row(db, user_id=user_id, provider=provider, secret_type=secret_type)
    return bool(row and row.status == STATUS_ACTIVE)


def revoke_integration_secret(db: Session, *, user_id: str, provider: str, secret_type: str) -> bool:
    row = _secret_row(db, user_id=user_id, provider=provider, secret_type=secret_type)
    if row is None or row.status == STATUS_REVOKED:
        return False
    now = utc_now_iso()
    row.status = STATUS_REVOKED
    row.updated_at = now
    row.revoked_at = now
    db.add(row)
    db.commit()
    return True


def redact_secret_hint(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 6:
        return f"{raw[:1]}...{raw[-1:]}" if len(raw) > 1 else "*"
    return f"{raw[:3]}...{raw[-3:]}"


def _secret_row(db: Session, *, user_id: str, provider: str, secret_type: str) -> IntegrationSecret | None:
    return db.scalar(
        select(IntegrationSecret).where(
            IntegrationSecret.user_id == user_id,
            IntegrationSecret.provider == provider,
            IntegrationSecret.secret_type == secret_type,
        )
    )


def _fernet() -> Fernet:
    if Fernet is None:
        raise SecretStoreUnavailable("cryptography no está instalado.")
    key = settings.alphawave_secret_encryption_key.strip()
    if not key:
        raise SecretStoreUnavailable("Falta ALPHAWAVE_SECRET_ENCRYPTION_KEY.")
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise SecretStoreUnavailable("ALPHAWAVE_SECRET_ENCRYPTION_KEY no es una Fernet key válida.") from exc
