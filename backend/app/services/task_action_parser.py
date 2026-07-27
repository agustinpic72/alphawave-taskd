from dataclasses import dataclass
from datetime import datetime, timedelta
import re

from app.services.deadlines import RELATIVE_DAYS, WEEKDAYS, current_app_time
from app.services.text_normalization import normalize_for_matching, normalized_words


DEFAULT_SNOOZE_REMINDER_HOUR = 9
DEFAULT_DEADLINE_HOUR = 12
DEFAULT_SHORT_DELAY_HOURS = 2

WEEKDAY_PATTERN = r"lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
RELATIVE_PATTERN = r"pasado\s+ma(?:[ñn]|ni)ana|ma(?:[ñn]|ni)ana|tomorrow|hoy|today"
TIME_PATTERN = r"(?:\s+a\s+las\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?)?"
WHEN_RE = re.compile(
    rf"(?<!\w)(?P<date>(?:para|hasta|el|este|esta|pr[oó]ximo|al)\s+)?(?P<value>{WEEKDAY_PATTERN}|{RELATIVE_PATTERN}){TIME_PATTERN}(?!\w)",
    re.IGNORECASE,
)
SHORT_DELAY_RE = re.compile(r"\b(?:m[aá]s\s+tarde|later)\b", re.IGNORECASE)

SUFFIX_REMINDER_RE = re.compile(
    r"^(?P<target>.+?)\s+(?:record[aá]melo|recu[eé]rdamelo|recuerdamelo|avisame|av[ií]same)\s+(?P<when>.+)$",
    re.IGNORECASE | re.DOTALL,
)
PREFIX_REMINDER_RE = re.compile(
    r"^(?:recordame|recu[eé]rdame|avisame|av[ií]same|remind\s+me)\s+(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL,
)
SNOOZE_RE = re.compile(
    r"^(?:pospon[eé]|pospone|posterg[aá]|posterga|postergalo|snooze)\s+(?P<target>.+?)\s+(?:hasta|para|a|al)\s+(?P<when>.+)$",
    re.IGNORECASE | re.DOTALL,
)
RESCHEDULE_RE = re.compile(
    r"^(?:cambi[aá]|cambia|pasalo\s+a|dejalo\s+para)\s+(?P<target>.+?)\s+(?:al|a|para|hasta)\s+(?P<when>.+)$",
    re.IGNORECASE | re.DOTALL,
)
NO_NOW_RE = re.compile(
    r"^no\s+quiero\s+hacer\s+(?P<target>.+?)\s+ahora(?:\s*,?\s*(?P<rest>.+))?$",
    re.IGNORECASE | re.DOTALL,
)
DEADLINE_RE = re.compile(
    r"^(?P<target>.+?)\s+(?:deadline|fecha\s+l[ií]mite|fecha\s+limite|vence|vencimiento|due)\s+(?P<when>.+)$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class ParsedTaskAction:
    action: str
    target: str
    when_at: str
    human_when: str
    create_reminder: bool = False
    allow_standalone_reminder: bool = False


@dataclass(frozen=True)
class TaskActionDefaults:
    reminder_time: str = "09:00"
    snooze_time: str = "09:00"
    later_delay_hours: int = DEFAULT_SHORT_DELAY_HOURS

    @property
    def reminder_hour_minute(self) -> tuple[int, int]:
        return _time_parts(self.reminder_time, fallback_hour=DEFAULT_SNOOZE_REMINDER_HOUR)

    @property
    def snooze_hour_minute(self) -> tuple[int, int]:
        return _time_parts(self.snooze_time, fallback_hour=DEFAULT_SNOOZE_REMINDER_HOUR)


def parse_task_action(text: str, *, now: datetime | None = None, defaults: TaskActionDefaults | None = None) -> ParsedTaskAction | None:
    clean = _collapse_spaces(text)
    if not clean:
        return None
    defaults = defaults or TaskActionDefaults()

    no_now = NO_NOW_RE.match(clean)
    if no_now and _has_reminder_hint(no_now.group("rest") or "") and SHORT_DELAY_RE.search(no_now.group("rest") or ""):
        parsed = _parse_when(no_now.group("rest") or "", default_time=defaults.snooze_hour_minute, later_delay_hours=defaults.later_delay_hours, now=now)
        if parsed:
            return ParsedTaskAction(
                "task_reminder_snooze",
                _clean_target(no_now.group("target")),
                parsed[0],
                parsed[1],
                create_reminder=True,
            )

    suffix_reminder = SUFFIX_REMINDER_RE.match(clean)
    if suffix_reminder:
        parsed = _parse_when(suffix_reminder.group("when"), default_time=defaults.snooze_hour_minute, later_delay_hours=defaults.later_delay_hours, now=now)
        if parsed:
            return ParsedTaskAction(
                "task_reminder_snooze",
                _clean_target(suffix_reminder.group("target")),
                parsed[0],
                parsed[1],
                create_reminder=True,
            )

    snooze = SNOOZE_RE.match(clean) or RESCHEDULE_RE.match(clean)
    if snooze:
        parsed = _parse_when(snooze.group("when"), default_time=defaults.snooze_hour_minute, later_delay_hours=defaults.later_delay_hours, now=now)
        if parsed:
            return ParsedTaskAction("task_snooze", _clean_target(snooze.group("target")), parsed[0], parsed[1])

    deadline = DEADLINE_RE.match(clean)
    if deadline:
        parsed = _parse_when(deadline.group("when"), default_time=(DEFAULT_DEADLINE_HOUR, 0), later_delay_hours=defaults.later_delay_hours, now=now)
        if parsed:
            return ParsedTaskAction("task_deadline", _clean_target(deadline.group("target")), parsed[0], parsed[1])

    prefix_reminder = PREFIX_REMINDER_RE.match(clean)
    if prefix_reminder:
        body = prefix_reminder.group("body")
        extracted = _extract_when_from_text(body, default_time=defaults.reminder_hour_minute, later_delay_hours=defaults.later_delay_hours, now=now)
        if extracted:
            target, when_at, human_when = extracted
            return ParsedTaskAction(
                "create_reminder",
                _clean_target(target),
                when_at,
                human_when,
                create_reminder=True,
                allow_standalone_reminder=True,
            )

    return None


def normalize_task_lookup_text(text: str) -> str:
    return normalized_words(text)


def _parse_when(text: str, *, default_time: tuple[int, int], later_delay_hours: int, now: datetime | None = None) -> tuple[str, str] | None:
    current = now or current_app_time()
    if SHORT_DELAY_RE.search(text):
        remind_at = (current + timedelta(hours=later_delay_hours)).replace(second=0, microsecond=0)
        return remind_at.isoformat(), f"más tarde ({remind_at:%H:%M})"

    match = WHEN_RE.search(text)
    if not match:
        return None
    return _datetime_from_match(match, default_time=default_time, now=current)


def _extract_when_from_text(text: str, *, default_time: tuple[int, int], later_delay_hours: int, now: datetime | None = None) -> tuple[str, str, str] | None:
    current = now or current_app_time()
    short = SHORT_DELAY_RE.search(text)
    if short:
        remind_at = (current + timedelta(hours=later_delay_hours)).replace(second=0, microsecond=0)
        target = _remove_span(text, short.span())
        return target, remind_at.isoformat(), f"más tarde ({remind_at:%H:%M})"

    match = WHEN_RE.search(text)
    if not match:
        return None
    when_at, human_when = _datetime_from_match(match, default_time=default_time, now=current)
    return _remove_span(text, match.span()), when_at, human_when


def _datetime_from_match(match: re.Match[str], *, default_time: tuple[int, int], now: datetime) -> tuple[str, str]:
    value = _normalize_key(match.group("value"))
    default_hour, default_minute = default_time
    hour = int(match.group("hour")) if match.groupdict().get("hour") else default_hour
    minute = int(match.group("minute")) if match.groupdict().get("minute") else default_minute
    if hour > 23 or minute > 59:
        hour, minute = default_hour, default_minute

    if value in RELATIVE_DAYS:
        target = now + timedelta(days=RELATIVE_DAYS[value])
    else:
        target_weekday = WEEKDAYS[value]
        days_ahead = (target_weekday - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        target = now + timedelta(days=days_ahead)

    when_at = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return when_at.isoformat(), _human_when(match.group(0), when_at)


def _human_when(raw: str, when_at: datetime) -> str:
    clean = _collapse_spaces(raw.strip(" ,.;:"))
    clean = re.sub(r"\s+a\s+las\s+\d{1,2}(?::\d{2})?$", "", clean, flags=re.IGNORECASE).strip()
    return f"{clean} a las {when_at:%H:%M}"


def _remove_span(text: str, span: tuple[int, int]) -> str:
    return _clean_target(f"{text[: span[0]]} {text[span[1] :]}")


def _has_reminder_hint(text: str) -> bool:
    return bool(re.search(r"\b(?:recordame|record[aá]melo|recu[eé]rdame|recu[eé]rdamelo|avisame|av[ií]same|remind\s+me)\b", text, re.IGNORECASE))


def _clean_target(text: str) -> str:
    cleaned = _collapse_spaces(text)
    cleaned = cleaned.strip(" ,.;:-")
    return _collapse_spaces(cleaned)


def _collapse_spaces(value: str) -> str:
    return " ".join(value.strip().split())


def _normalize_key(value: str) -> str:
    normalized = normalize_for_matching(value)
    return re.sub(r"^(?:para|hasta|el|este|esta|proximo|al)\s+", "", normalized)


def _time_parts(value: str, *, fallback_hour: int) -> tuple[int, int]:
    try:
        hour, minute = value.split(":", 1)
        parsed = (int(hour), int(minute))
        if 0 <= parsed[0] <= 23 and 0 <= parsed[1] <= 59:
            return parsed
    except (AttributeError, ValueError):
        pass
    return fallback_hour, 0
