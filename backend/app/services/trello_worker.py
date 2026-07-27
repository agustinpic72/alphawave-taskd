import asyncio
import logging

from app.core.config import settings
from app.core.db import SessionLocal
from app.services.trello_sync import run_trello_sync


logger = logging.getLogger(__name__)


async def run_startup_trello_sync() -> None:
    if not settings.trello_enabled:
        return
    with SessionLocal() as db:
        await run_trello_sync(db)


async def trello_sync_loop() -> None:
    if not settings.trello_enabled:
        return
    interval = max(settings.trello_sync_interval_minutes, 1) * 60
    while True:
        await asyncio.sleep(interval)
        try:
            with SessionLocal() as db:
                await run_trello_sync(db)
        except Exception:  # noqa: BLE001 - background worker must keep running.
            logger.exception("trello background sync failed")
