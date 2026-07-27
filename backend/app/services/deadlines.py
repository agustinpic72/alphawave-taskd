from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.text_normalization import normalize_for_matching


DEFAULT_TASK_DUE_HOUR = 12

WEEKDAYS = {
    "lunes": 0,
    "monday": 0,
    "martes": 1,
    "tuesday": 1,
    "miercoles": 2,
    "miércoles": 2,
    "wednesday": 2,
    "jueves": 3,
    "thursday": 3,
    "viernes": 4,
    "friday": 4,
    "sabado": 5,
    "sábado": 5,
    "saturday": 5,
    "domingo": 6,
    "sunday": 6,
}
RELATIVE_DAYS = {
    "hoy": 0,
    "today": 0,
    "mañana": 1,
    "manana": 1,
    "tomorrow": 1,
    "pasado mañana": 2,
    "pasado manana": 2,
}


@dataclass(frozen=True)
class DeadlineMatch:
    title: str
    due_at: str | None
    matched_text: str | None = None


WEEKDAY_PATTERN = r"lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
RELATIVE_PATTERN = r"pasado\s+ma(?:[ñn]|ni)ana|ma(?:[ñn]|ni)ana|tomorrow|hoy|today"
TIME_PATTERN = r"(?:\s+a\s+las\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?)?"
WEEKDAY_PREFIX = r"(?:(?:para|el|este|esta|pr[oó]ximo)\s+){0,3}"
WEEKDAY_DATE = rf"{WEEKDAY_PREFIX}(?P<weekday>{WEEKDAY_PATTERN})"
NEXT_WEEK_SUFFIX = r"(?:\s+(?:de\s+la\s+semana\s+(?:que|q)\s+viene|de\s+la\s+pr[oó]xima\s+semana|next\s+week))"
EXPLICIT_DEADLINE_LABEL = r"deadline|fecha\s+l[ií]mite|vence|due"

RELATIVE_RE = re.compile(rf"(?<!\w)(?:para\s+)?(?P<relative>{RELATIVE_PATTERN}){TIME_PATTERN}(?!\w)", re.IGNORECASE)
EXPLICIT_NEXT_WEEKDAY_RE = re.compile(
    rf"(?<!\w)(?:{EXPLICIT_DEADLINE_LABEL})\s+(?:el\s+)?(?P<weekday>{WEEKDAY_PATTERN}){NEXT_WEEK_SUFFIX}{TIME_PATTERN}(?!\w)",
    re.IGNORECASE,
)
EXPLICIT_DEADLINE_RE = re.compile(
    rf"(?<!\w)(?:{EXPLICIT_DEADLINE_LABEL})\s+(?P<date>(?:para\s+|el\s+|este\s+|esta\s+|pr[oó]ximo\s+)*"
    rf"(?:(?P<weekday>{WEEKDAY_PATTERN})|(?P<relative>{RELATIVE_PATTERN}))){TIME_PATTERN}(?!\w)",
    re.IGNORECASE,
)
NEXT_WEEKDAY_RE = re.compile(
    rf"(?<!\w)(?:el\s+)?(?P<weekday>{WEEKDAY_PATTERN}){NEXT_WEEK_SUFFIX}{TIME_PATTERN}(?!\w)",
    re.IGNORECASE,
)
WEEKDAY_WITH_PREFIX_RE = re.compile(rf"(?<!\w)(?P<date>{WEEKDAY_DATE}){TIME_PATTERN}(?!\w)", re.IGNORECASE)
BARE_WEEKDAY_AT_START_RE = re.compile(rf"^\s*(?P<weekday>{WEEKDAY_PATTERN}){TIME_PATTERN}\s+", re.IGNORECASE)


def parse_clear_deadline(raw_title: str, *, now: datetime | None = None) -> tuple[str, str | None]:
    match = extract_clear_deadline(raw_title, now=now)
    return match.title, match.due_at


def extract_clear_deadline(raw_title: str, *, now: datetime | None = None) -> DeadlineMatch:
    title = _collapse_spaces(raw_title)
    if not title:
        return DeadlineMatch(raw_title, None)

    current = now or current_app_time()
    explicit_next_weekday = EXPLICIT_NEXT_WEEKDAY_RE.search(title)
    if explicit_next_weekday:
        cleaned = _clean_title(title, explicit_next_weekday)
        if cleaned:
            due = _next_weekday_next_week(current, WEEKDAYS[_normalize_key(explicit_next_weekday.group("weekday"))], explicit_next_weekday)
            return DeadlineMatch(cleaned, due.isoformat(), explicit_next_weekday.group(0).strip())

    explicit = EXPLICIT_DEADLINE_RE.search(title)
    if explicit:
        cleaned = _clean_title(title, explicit)
        if cleaned:
            if explicit.groupdict().get("relative"):
                due = _due_in_days(current, RELATIVE_DAYS[_normalize_key(explicit.group("relative"))], explicit)
            else:
                due = _next_weekday(current, WEEKDAYS[_normalize_key(explicit.group("weekday"))], explicit)
            return DeadlineMatch(cleaned, due.isoformat(), explicit.group(0).strip())

    relative = _find_relative(title)
    if relative:
        cleaned = _clean_title(title, relative)
        if cleaned:
            due = _due_in_days(current, RELATIVE_DAYS[_normalize_key(relative.group("relative"))], relative)
            return DeadlineMatch(cleaned, due.isoformat(), relative.group(0).strip())

    next_weekday = NEXT_WEEKDAY_RE.search(title)
    if next_weekday:
        cleaned = _clean_title(title, next_weekday)
        if cleaned:
            due = _next_weekday_next_week(current, WEEKDAYS[_normalize_key(next_weekday.group("weekday"))], next_weekday)
            return DeadlineMatch(cleaned, due.isoformat(), next_weekday.group(0).strip())

    weekday = _find_weekday(title)
    if weekday:
        cleaned = _clean_title(title, weekday)
        if cleaned:
            due = _next_weekday(current, WEEKDAYS[_normalize_key(weekday.group("weekday"))], weekday)
            return DeadlineMatch(cleaned, due.isoformat(), weekday.group(0).strip())

    return DeadlineMatch(title, None)


def current_app_time() -> datetime:
    return datetime.now(ZoneInfo(settings.app_timezone))


def _next_weekday(now: datetime, target_weekday: int, match: re.Match[str] | None = None) -> datetime:
    days_ahead = (target_weekday - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    due = now + timedelta(days=days_ahead)
    hour, minute = _match_time(match)
    return due.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _next_weekday_next_week(now: datetime, target_weekday: int, match: re.Match[str] | None = None) -> datetime:
    days_until_next_monday = 7 - now.weekday()
    due = now + timedelta(days=days_until_next_monday + target_weekday)
    hour, minute = _match_time(match)
    return due.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _due_in_days(now: datetime, days: int, match: re.Match[str] | None = None) -> datetime:
    due = now + timedelta(days=days)
    hour, minute = _match_time(match)
    return due.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _match_time(match: re.Match[str] | None) -> tuple[int, int]:
    if not match or not match.groupdict().get("hour"):
        return DEFAULT_TASK_DUE_HOUR, 0
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    if hour > 23 or minute > 59:
        return DEFAULT_TASK_DUE_HOUR, 0
    return hour, minute


def _find_relative(title: str) -> re.Match[str] | None:
    return RELATIVE_RE.search(title)


def _find_weekday(title: str) -> re.Match[str] | None:
    start = BARE_WEEKDAY_AT_START_RE.search(title)
    if start:
        return start
    for match in WEEKDAY_WITH_PREFIX_RE.finditer(title):
        matched = match.group("date").strip()
        if _collapse_spaces(matched.casefold()) != _collapse_spaces(match.group("weekday").casefold()):
            return match
    return None


def _clean_title(title: str, match: re.Match[str]) -> str:
    cleaned = f"{title[: match.start()]} {title[match.end() :]}".strip()
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = cleaned.strip(" ,.;:-")
    return _collapse_spaces(cleaned)


def _collapse_spaces(value: str) -> str:
    return " ".join(value.strip().split())


def _normalize_key(value: str) -> str:
    normalized = normalize_for_matching(value)
    return re.sub(r"^(?:para|el|este|esta|proximo)\s+", "", normalized)
