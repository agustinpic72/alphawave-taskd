from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.text_normalization import normalize_for_matching


@dataclass(frozen=True)
class ParsedReminder:
    message: str
    remind_at: str
    human_when: str


RELATIVE_ES_RE = re.compile(r"^en\s+(\d+)\s+(minuto|minutos|hora|horas)\s+recordame\s+(.+)$", re.IGNORECASE)
RELATIVE_ES_ALT_RE = re.compile(r"^en\s+(\d+)\s+(minuto|minutos|hora|horas)\s+recu[eé]rdame\s+(.+)$", re.IGNORECASE)
RELATIVE_EN_RE = re.compile(r"^remind me in\s+(\d+)\s+(minute|minutes|hour|hours)\s+to\s+(.+)$", re.IGNORECASE)
ES_PREFIX_RE = re.compile(r"^(?:recu[eé]rdame|recordame)\s+(.+?)\s+(hoy|mañana|manana|maniana)\s+a\s+las\s+(\d{1,2})(?::(\d{2}))?$", re.IGNORECASE)
ES_FRONT_RE = re.compile(r"^(hoy|mañana|manana|maniana)\s+a\s+las\s+(\d{1,2})(?::(\d{2}))?\s+(?:avisame que|recordame|recu[eé]rdame)\s+(.+)$", re.IGNORECASE)
EN_TOMORROW_RE = re.compile(r"^remind me to\s+(.+?)\s+tomorrow at\s+(\d{1,2})(?::(\d{2}))?$", re.IGNORECASE)


def parse_reminder(text: str, *, now: datetime | None = None) -> ParsedReminder | None:
    clean = " ".join(text.strip().split())
    if not clean:
        return None
    timezone = ZoneInfo(settings.app_timezone)
    current = now.astimezone(timezone) if now else datetime.now(timezone)

    relative_match = RELATIVE_ES_RE.match(clean) or RELATIVE_ES_ALT_RE.match(clean)
    if relative_match:
        return _relative(relative_match, current)

    relative_en_match = RELATIVE_EN_RE.match(clean)
    if relative_en_match:
        return _relative(relative_en_match, current)

    prefix_match = ES_PREFIX_RE.match(clean)
    if prefix_match:
        message, day_word, hour, minute = prefix_match.groups()
        return _absolute(message, day_word, hour, minute, current)

    front_match = ES_FRONT_RE.match(clean)
    if front_match:
        day_word, hour, minute, message = front_match.groups()
        return _absolute(message, day_word, hour, minute, current)

    en_tomorrow = EN_TOMORROW_RE.match(clean)
    if en_tomorrow:
        message, hour, minute = en_tomorrow.groups()
        return _absolute(message, "tomorrow", hour, minute, current)

    return None


def _relative(match: re.Match[str], current: datetime) -> ParsedReminder:
    amount = int(match.group(1))
    unit = normalize_for_matching(match.group(2))
    message = match.group(3).strip()
    delta = timedelta(hours=amount) if unit.startswith("hora") or unit.startswith("hour") else timedelta(minutes=amount)
    remind_at = (current + delta).replace(second=0, microsecond=0)
    unit_label = "hora" if delta >= timedelta(hours=1) and amount == 1 else unit
    return ParsedReminder(message=message, remind_at=remind_at.isoformat(), human_when=f"en {amount} {unit_label}")


def _absolute(message: str, day_word: str, hour: str, minute: str | None, current: datetime) -> ParsedReminder:
    hour_value = int(hour)
    minute_value = int(minute or 0)
    if hour_value > 23 or minute_value > 59:
        raise ValueError("No pude resolver esa hora de forma segura.")
    normalized_day = normalize_for_matching(day_word)
    days = 0 if normalized_day in {"hoy"} else 1
    remind_at = (current + timedelta(days=days)).replace(hour=hour_value, minute=minute_value, second=0, microsecond=0)
    if remind_at <= current:
        raise ValueError("Esa fecha ya pasó. Decime otra hora o día.")
    human_day = "mañana" if days == 1 else "hoy"
    return ParsedReminder(
        message=message.strip(),
        remind_at=remind_at.isoformat(),
        human_when=f"{human_day} {hour_value:02d}:{minute_value:02d}",
    )
