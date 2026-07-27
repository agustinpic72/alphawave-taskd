import asyncio
import logging

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.auth import User
from app.services.briefing import maybe_send_due_briefing
from app.services.telegram_client import TelegramApiClient


logger = logging.getLogger(__name__)


async def run_startup_briefing_processing() -> None:
    if not _briefing_ready():
        return
    client = TelegramApiClient(settings.telegram_bot_token)
    with SessionLocal() as db:
        await _process_briefing_users(db, client, startup=True)


async def briefing_scheduler_loop() -> None:
    if not _briefing_ready():
        return
    client = TelegramApiClient(settings.telegram_bot_token)
    while True:
        try:
            with SessionLocal() as db:
                await _process_briefing_users(db, client)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Briefing scheduler failed")
        await asyncio.sleep(60)


def _briefing_ready() -> bool:
    return bool(
        settings.telegram_enabled
        and settings.telegram_bot_token
    )


async def _process_briefing_users(db, client, *, startup: bool = False) -> None:
    user_ids = list(
        db.scalars(
            select(User.id)
            .where(User.status.in_(["active", "bootstrap_required"]))
            .order_by(User.created_at.asc())
        ).all()
    )
    for user_id in user_ids:
        try:
            await maybe_send_due_briefing(db, client, startup=startup, user_id=user_id)
        except Exception:
            logger.exception("Briefing processing failed for user %s", user_id)
