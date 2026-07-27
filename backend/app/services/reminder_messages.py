from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models.reminders import Reminder


def format_reminder_due(message: str, scope: str | None = None) -> str:
    header = "🔔 Recordatorio"
    if scope:
        header += f" · {scope}"
    return f"{header}\n\n{message}"


def format_reminder_created(message: str, human_when: str) -> str:
    return f"✅ Recordatorio creado\n\n🔔 {message}\n🕘 {human_when}"


def format_task_reminder_created(message: str, human_when: str, *, snoozed: bool = True) -> str:
    suffix = "\n\nLa oculto hasta entonces." if snoozed else ""
    return f"✅ Recordatorio creado\n\n🔔 {message}\n🕘 {human_when}{suffix}"


def format_reminder_list(reminders: list[Reminder]) -> str:
    if not reminders:
        return "🔔 Recordatorios pendientes\n\nNo hay recordatorios pendientes."

    lines = ["🔔 Recordatorios pendientes", ""]
    for index, reminder in enumerate(reminders, start=1):
        lines.append(f"{index}. {reminder.message}")
        lines.append(f"   🕘 {_format_due(reminder.remind_at)}")
        lines.append("")
    lines.extend(["Podés responder:", "- cancelar recordatorio N"])
    return "\n".join(lines).strip()


def format_overdue_group(reminders: list[Reminder]) -> str:
    lines = [f"🔔 Tenías {len(reminders)} recordatorios pendientes", ""]
    for index, reminder in enumerate(reminders, start=1):
        lines.append(f"{index}. {reminder.message}")
        lines.append(f"   vencía: {_format_due(reminder.remind_at)}")
        lines.append("")
    return "\n".join(lines).strip()


def format_reminder_cancelled(message: str) -> str:
    return f"🗑️ Recordatorio cancelado\n\n{message}"


def _format_due(value: str) -> str:
    timezone = ZoneInfo(settings.app_timezone)
    due = datetime.fromisoformat(value).astimezone(timezone)
    today = datetime.now(timezone).date()
    if due.date() == today:
        return due.strftime("hoy %H:%M")
    if due.date() == today + timedelta(days=1):
        return due.strftime("mañana %H:%M")
    return due.strftime("%Y-%m-%d %H:%M")
