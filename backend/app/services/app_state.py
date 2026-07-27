from sqlalchemy.orm import Session

from app.models.app_state import AppState
from app.services.time import utc_now_iso


def get_state(db: Session, key: str) -> str | None:
    state = db.get(AppState, key)
    return state.value if state else None


def set_state(db: Session, key: str, value: str) -> None:
    state = db.get(AppState, key)
    if state:
        state.value = value
        state.updated_at = utc_now_iso()
    else:
        db.add(AppState(key=key, value=value, updated_at=utc_now_iso()))
    db.commit()

