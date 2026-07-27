from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.auth import AuthSession, User
from app.services.time import utc_now_iso


HASH_ALGORITHM = "pbkdf2_sha256"
TOKEN_BYTES = 32
MIN_PASSWORD_LENGTH = 10
BOOTSTRAP_OWNER_EMAIL = "owner@local.alphawave"
UNUSABLE_PASSWORD_HASH = "unusable$bootstrap-required"


@dataclass(frozen=True)
class SessionTokens:
    session_token: str
    csrf_token: str
    session: AuthSession
    user: User


def owner_exists(db: Session) -> bool:
    return db.scalar(select(func.count(User.id)).where(User.status == "active")) > 0


def get_or_create_single_owner_for_backfill(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.created_at.asc()).limit(1))
    if user:
        return user
    now = utc_now_iso()
    user = User(
        id=str(uuid4()),
        email=BOOTSTRAP_OWNER_EMAIL,
        password_hash=UNUSABLE_PASSWORD_HASH,
        display_name="Local owner",
        role="owner",
        status="bootstrap_required",
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def single_owner_user_id(db: Session) -> str:
    return get_or_create_single_owner_for_backfill(db).id


def create_owner(
    db: Session,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
    allow_existing: bool = False,
) -> User:
    normalized_email = email.strip().casefold()
    if not normalized_email:
        raise ValueError("Email requerido.")
    _validate_password(password)
    existing_count = db.scalar(select(func.count(User.id)))
    bootstrap_owner = db.scalar(select(User).where(User.email == BOOTSTRAP_OWNER_EMAIL, User.status == "bootstrap_required"))
    if bootstrap_owner and existing_count == 1:
        existing_same_email = normalized_email == BOOTSTRAP_OWNER_EMAIL
        existing_email_owner = db.scalar(select(User).where(User.email == normalized_email))
        if existing_email_owner and not existing_same_email:
            raise ValueError("Ya existe un usuario con ese email.")
        bootstrap_owner.email = normalized_email
        bootstrap_owner.password_hash = hash_password(password)
        bootstrap_owner.display_name = display_name or None
        bootstrap_owner.role = "owner"
        bootstrap_owner.status = "active"
        bootstrap_owner.updated_at = utc_now_iso()
        db.commit()
        db.refresh(bootstrap_owner)
        return bootstrap_owner
    if existing_count and not allow_existing:
        raise ValueError("Ya existe un owner configurado.")
    existing = db.scalar(select(User).where(User.email == normalized_email))
    if existing:
        raise ValueError("Ya existe un usuario con ese email.")
    now = utc_now_iso()
    user = User(
        id=str(uuid4()),
        email=normalized_email,
        password_hash=hash_password(password),
        display_name=display_name or None,
        role="owner",
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, *, email: str, password: str) -> User | None:
    normalized_email = email.strip().casefold()
    user = db.scalar(select(User).where(User.email == normalized_email))
    if not user or user.status != "active":
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_session(
    db: Session,
    user: User,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> SessionTokens:
    now = datetime.now(timezone.utc)
    session_token = secrets.token_urlsafe(TOKEN_BYTES)
    csrf_token = secrets.token_urlsafe(TOKEN_BYTES)
    session = AuthSession(
        id=str(uuid4()),
        user_id=user.id,
        session_hash=hash_token(session_token),
        csrf_token_hash=hash_token(csrf_token),
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=max(settings.auth_session_ttl_hours, 1))).isoformat(),
        user_agent=_trim(user_agent, 500),
        ip_hash=hash_token(ip_address) if ip_address else None,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return SessionTokens(session_token=session_token, csrf_token=csrf_token, session=session, user=user)


def resolve_session(db: Session, session_token: str | None) -> tuple[User, AuthSession] | None:
    if not session_token:
        return None
    session = db.scalar(select(AuthSession).where(AuthSession.session_hash == hash_token(session_token)))
    if not session or session.revoked_at:
        return None
    expires_at = _parse_iso(session.expires_at)
    if expires_at <= datetime.now(timezone.utc):
        return None
    user = db.get(User, session.user_id)
    if not user or user.status != "active":
        return None
    return user, session


def revoke_session(db: Session, session: AuthSession) -> None:
    session.revoked_at = utc_now_iso()
    db.commit()


def rotate_csrf_token(db: Session, session: AuthSession) -> str:
    csrf_token = secrets.token_urlsafe(TOKEN_BYTES)
    session.csrf_token_hash = hash_token(csrf_token)
    db.commit()
    return csrf_token


def csrf_valid(session: AuthSession, csrf_token: str | None) -> bool:
    return bool(csrf_token and hmac.compare_digest(hash_token(csrf_token), session.csrf_token_hash))


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = max(int(settings.auth_pbkdf2_iterations), 100_000)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{HASH_ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != HASH_ALGORITHM:
            return False
        iterations = int(raw_iterations)
        salt = bytes.fromhex(raw_salt)
        expected = bytes.fromhex(raw_digest)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def auth_user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
    }


def _validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"La password debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _trim(value: str | None, max_length: int) -> str | None:
    if not value:
        return None
    return value[:max_length]
