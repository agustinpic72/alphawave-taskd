import asyncio
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.services.telegram_client import TelegramApiClient
from app.services.telegram_processor import get_last_update_id, process_pending_updates, store_raw_update


logger = logging.getLogger(__name__)


async def run_startup_telegram_processing() -> None:
    if not _telegram_ready():
        return
    client = TelegramApiClient(settings.telegram_bot_token)
    with SessionLocal() as db:
        await fetch_and_store_updates(db, client)
        await process_pending_updates(db, client, send_summary=True)


async def telegram_polling_loop() -> None:
    if not _telegram_ready():
        return
    client = TelegramApiClient(settings.telegram_bot_token)
    while True:
        try:
            with SessionLocal() as db:
                await fetch_and_store_updates(db, client)
                await process_pending_updates(db, client)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram polling failed")
        await asyncio.sleep(settings.telegram_poll_interval_seconds)


async def fetch_and_store_updates(db: Session, client: TelegramApiClient) -> int:
    last_update_id = get_last_update_id(db)
    offset = last_update_id + 1 if last_update_id is not None else None
    updates = await client.get_updates(offset=offset, timeout=0)
    count = 0
    for raw_update in updates:
        if store_raw_update(db, raw_update):
            count += 1
    return count


def _telegram_ready() -> bool:
    return bool(settings.telegram_enabled and settings.telegram_bot_token and settings.telegram_allowed_user_id)

