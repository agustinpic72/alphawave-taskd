from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.auth import AuthSession, User
from app.services import auth as auth_service


PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/session",
    "/api/auth/csrf",
}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    role: str
    is_admin: bool


@dataclass(frozen=True)
class RequestContext:
    user_id: str
    user_email: str
    role: str
    is_admin: bool
    auth_method: str = "cookie_session"


def auth_gate(request: Request, db: Session = Depends(get_db)) -> None:
    if not settings.auth_enabled:
        return
    if request.url.path in PUBLIC_PATHS:
        return
    resolved = _resolve_request_session(request, db)
    if not resolved:
        if not auth_service.owner_exists(db):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No hay usuario owner configurado.",
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado.")
    user, session = resolved
    if request.method in UNSAFE_METHODS:
        csrf_token = request.headers.get("X-CSRF-Token")
        if not auth_service.csrf_valid(session, csrf_token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF inválido.")
    _set_request_context(request, user)


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> CurrentUser | None:
    if not settings.auth_enabled:
        return None
    resolved = _resolve_request_session(request, db)
    if not resolved:
        return None
    user, _session = resolved
    return _current_user(user)


def get_current_user_required(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    if not settings.auth_enabled:
        from app.core.db import ensure_schema
        from app.services import ownership

        ensure_schema()
        user = auth_service.get_or_create_single_owner_for_backfill(db)
        ownership.backfill_core_user_ids(db)
        return _current_user(user)
    resolved = _resolve_request_session(request, db)
    if not resolved:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado.")
    user, _session = resolved
    return _current_user(user)


def get_request_context(current_user: CurrentUser = Depends(get_current_user_required)) -> RequestContext:
    return RequestContext(
        user_id=current_user.id,
        user_email=current_user.email,
        role=current_user.role,
        is_admin=current_user.is_admin,
    )


def require_admin(ctx: RequestContext = Depends(get_request_context)) -> RequestContext:
    if not ctx.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requiere admin.")
    return ctx


def _resolve_request_session(request: Request, db: Session) -> tuple[User, AuthSession] | None:
    session_token = request.cookies.get(settings.auth_cookie_name)
    return auth_service.resolve_session(db, session_token)


def _set_request_context(request: Request, user: User) -> None:
    request.state.current_user = _current_user(user)
    request.state.request_context = RequestContext(
        user_id=user.id,
        user_email=user.email,
        role=user.role,
        is_admin=user.role == "owner",
    )


def _current_user(user: User) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        email=user.email,
        role=user.role,
        is_admin=user.role == "owner",
    )
