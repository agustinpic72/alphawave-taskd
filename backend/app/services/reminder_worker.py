import asyncio
from collections import defaultdict
from datetime import datetime
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.reminders import Reminder
from app.models.telegram import TelegramUpdate
from app.services import integrations
from app.services.reminder_messages import format_overdue_group, format_reminder_due
from app.services import reminders as reminder_service
from app.services import settings_service
from app.services.telegram_client import TelegramApiClient, TelegramMessenger
from app.services.time import utc_now_iso


logger = logging.getLogger(__name__)


async def run_startup_reminder_processing() -> None:
    if not _reminders_ready():
        return
    client = TelegramApiClient(settings.telegram_bot_token)
    with SessionLocal() as db:
        try:
            await send_due_reminders(db, client)
        except Exception:
            logger.exception("Startup reminder processing failed")


async def reminder_polling_loop() -> None:
    if not _reminders_ready():
        return
    client = TelegramApiClient(settings.telegram_bot_token)
    while True:
        try:
            with SessionLocal() as db:
                await send_due_reminders(db, client)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder polling failed")
        await asyncio.sleep(settings.reminders_poll_interval_seconds)


async def send_due_reminders(
    db: Session,
    messenger: TelegramMessenger,
    *,
    now: datetime | None = None,
) -> int:
    current = now.isoformat() if now else utc_now_iso()
    due = reminder_service.due_reminders(db, current)
    filtered_due: list[Reminder] = []
    for reminder in due:
        reminder_config = settings_service.reminder_settings(db, user_id=reminder.user_id)
        if not reminder_config.get("enabled", True):
            continue
        if settings_service.is_weekend_mode_active(db, now=now, user_id=reminder.user_id):
            if settings_service.should_send_weekend_category(db, "overdue_reminder", now=now, user_id=reminder.user_id):
                pass
            elif settings_service.should_send_weekend_category(db, "explicit_reminder", now=now, user_id=reminder.user_id) and reminder.source in {"telegram", "ui"}:
                pass
            else:
                continue
        filtered_due.append(reminder)
    due = filtered_due
    if not due:
        return 0

    by_chat: dict[str, list[Reminder]] = defaultdict(list)
    for reminder in due:
        chat_id = _chat_id_for_reminder(db, reminder)
        if chat_id:
            by_chat[chat_id].append(reminder)

    sent = 0
    for chat_id, reminders in by_chat.items():
        reminder_config = settings_service.reminder_settings(db, user_id=reminders[0].user_id if reminders else None)
        if len(reminders) > int(reminder_config.get("group_overdue_threshold", 3)):
            if not await _send_safely(messenger, chat_id, format_overdue_group(reminders)):
                continue
            for reminder in reminders:
                reminder_service.mark_sent(db, reminder)
                sent += 1
        else:
            for reminder in reminders:
                if not await _send_safely(messenger, chat_id, format_reminder_due(reminder.message)):
                    continue
                reminder_service.mark_sent(db, reminder)
                sent += 1
    return sent


async def _send_safely(messenger: TelegramMessenger, chat_id: str, text: str) -> bool:
    try:
        await messenger.send_message(chat_id, text)
        return True
    except Exception:
        logger.exception("Reminder Telegram send failed")
        return False


def _chat_id_for_reminder(db: Session, reminder: Reminder) -> str | None:
    if reminder.source_update_id is not None:
        update = db.get(TelegramUpdate, reminder.source_update_id)
        if update and integrations.resolve_telegram_user_id(db, update.chat_id, legacy_from_user_id=update.from_user_id) == reminder.user_id:
            return update.chat_id
    return integrations.get_telegram_destination_for_user(db, reminder.user_id)


def _reminders_ready() -> bool:
    return bool(
        settings.telegram_enabled
        and settings.telegram_bot_token
        and settings.telegram_allowed_user_id
    )
