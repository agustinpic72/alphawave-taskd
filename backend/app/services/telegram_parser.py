from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.deadlines import WEEKDAYS, parse_clear_deadline
from app.services.reminder_parser import parse_reminder
from app.services.task_action_parser import TaskActionDefaults, parse_task_action
from app.services.text_normalization import normalize_for_matching


@dataclass(frozen=True)
class ParsedCommand:
    action: str
    payload: dict


LIST_PATTERNS = {
    "todo",
    "/todo",
    "dame la todolist",
    "dame la todo list",
    "mostrame la todolist",
    "lista de tareas",
    "mis tareas",
    "list tasks",
}
REMINDER_LIST_PATTERNS = {
    "mis recordatorios",
    "/reminders",
    "my reminders",
}

ADD_PREFIX_RE = re.compile(
    r"^(?:agreg[aá]|a[ñn]ad[eií]|anot[aá]|sum[aá]|cre[aá](?:r)?\s+(?:una?\s+)?(?:task|tarea)(?:\s+nueva)?|create\s+task|add\s+task|add)\b[:\s]*(.*)$",
    re.IGNORECASE | re.DOTALL,
)
DONE_RE = re.compile(r"^(hecho|done)\s+(\d+)\s*$", re.IGNORECASE)
DELETE_RE = re.compile(r"^(borra|delete|elimina)\s+(\d+)\s*$", re.IGNORECASE)
RENAME_RE = re.compile(r"^(?:renombra\s+(\d+)\s+a\s+(.+)|rename\s+(\d+)\s+to\s+(.+))$", re.IGNORECASE | re.DOTALL)
START_RE = re.compile(r"^(?:voy a empezar la|empiezo la|start)\s+(\d+)\s*$", re.IGNORECASE)
FINISH_RE = re.compile(r"^(?:ya termin[eé] la|termin[eé] la)\s+(\d+)\s*$", re.IGNORECASE)
CREATE_CARD_RE = re.compile(
    r"^(?:crea(?:r)? card en|create card in)\s+([A-Za-z0-9_]{2,24})(?:\s*/\s*([^:]+))?\s*:\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
DEADLINE_RE = re.compile(
    r"^(?:pon[eé] deadline a la|pone fecha a la)\s+(\d+)\s+(?:para\s+)?(.+)$|^(?:set due|set deadline)\s+(\d+)\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
MOVE_RE = re.compile(
    r"^(?:mueve\s+(\d+)\s+(arriba|abajo)\s+de\s+(\d+)|move\s+(\d+)\s+(above|below)\s+(\d+))$",
    re.IGNORECASE,
)
CANCEL_REMINDER_RE = re.compile(r"^(?:cancelar recordatorio|cancel reminder)\s+(\d+)\s*$", re.IGNORECASE)
REMINDER_HINT_RE = re.compile(r"\b(recordame|record[aá]melo|recu[eé]rdame|recu[eé]rdamelo|avisame|av[ií]same|remind me)\b", re.IGNORECASE)
SCOPE_CORRECTION_RE = re.compile(
    r"^(?:la\s+(\d+)|esa tarea)\s+es(?:\s+de)?\s+(.+?)(?:,?\s+no\s+de\s+(.+))?$",
    re.IGNORECASE,
)
EFFORT_HOURS_RE = re.compile(r"^(?:la\s+(\d+)|esa tarea)(?:\s+no\s+toma\s+\d+\s+minutos,)?\s+toma\s+(\d+)\s+horas?$", re.IGNORECASE)
EFFORT_BUCKET_RE = re.compile(r"^la\s+(\d+)\s+es\s+(trabajo profundo|tarea r[aá]pida)$", re.IGNORECASE)
TODAY_PATTERNS = {
    "que tengo que hacer hoy",
    "que debo hacer hoy",
    "que hago hoy",
    "plan de hoy",
    "hoy que hago",
    "today",
}
NOW_PATTERNS = {"que hago ahora", "que hago ya", "ahora que hago", "what should i do now", "now"}
INBOX_CLASSIFY_PATTERNS = {"clasificá inbox", "clasifica inbox", "clasificar inbox"}
SORT_PATTERNS = {
    "ordenar",
    "ordena",
    "ordename",
    "ordename la todo list",
    "ordename la todo list por prioridades",
    "ordena por prioridad",
    "ordena las tareas",
    "prioriza",
    "priorizame la lista",
    "prioriza mi lista",
    "reordena por prioridad",
    "sort",
    "sort by priority",
}
CONFIRM_PATTERNS = {"confirmar", "confirm"}
CANCEL_PATTERNS = {"cancelar", "cancel"}
CONFIRM_INDEX_RE = re.compile(r"^(?:confirmar|confirm)\s+(\d+)\s*$", re.IGNORECASE)
CANCEL_INDEX_RE = re.compile(r"^(?:cancelar|cancel)\s+(\d+)\s*$", re.IGNORECASE)
OPTION_RE = re.compile(r"^(?:opci[oó]n\s*)?([1-5])\s*$", re.IGNORECASE)
LOCAL_ONLY_PATTERNS = {"sólo local", "solo local", "local only"}
CONFIRMATION_LIST_PATTERNS = {"confirmaciones", "pending confirmations"}
TRELLO_SYNC_PATTERNS = {"sync trello", "sincronizar trello", "trello sync"}
TRELLO_STATUS_PATTERNS = {"estado trello", "trello status"}
BRIEFING_PATTERNS = {"briefing", "briefing hoy", "mandame el briefing", "enviame el briefing", "daily briefing"}
BRIEFING_STATUS_PATTERNS = {"estado briefing", "briefing status"}
NO_NEWS_RE = re.compile(r"^(?:sin novedades)(?:,\s*preguntame\s+(mañana|manana|maniana|en\s+\d+\s+d[ií]as?))?$", re.IGNORECASE)
ASK_ME_RE = re.compile(r"^preguntame\s+(mañana|manana|maniana|en\s+\d+\s+d[ií]as?|en una semana)$", re.IGNORECASE)
CONVERSATIONAL_PREFIX_RE = re.compile(
    r"^\s*(?:hola|buenas|buen\s+d[ií]a|buenas\s+tardes|buenas\s+noches|che|hey|ey|bro)\b[\s,;:!¡¿?.-]*",
    re.IGNORECASE,
)
TRAILING_PUNCTUATION_RE = re.compile(r"[\s,;:!¡¿?.]+$")


def parse_command(text: str, *, task_action_defaults: TaskActionDefaults | None = None) -> ParsedCommand:
    clean = text.strip()
    intent_text = strip_conversational_prefixes(clean)
    normalized = normalize_command_text(intent_text)
    if normalized in LIST_PATTERNS:
        return ParsedCommand("list", {})
    if normalized in REMINDER_LIST_PATTERNS:
        return ParsedCommand("list_reminders", {})
    if normalized in TODAY_PATTERNS:
        return ParsedCommand("plan_today", {})
    if normalized in NOW_PATTERNS:
        return ParsedCommand("plan_now", {})
    if normalized in INBOX_CLASSIFY_PATTERNS:
        return ParsedCommand("classify_inbox", {})
    if normalized in SORT_PATTERNS:
        return ParsedCommand("sort_by_priority_propose", {})
    if normalized in TRELLO_SYNC_PATTERNS:
        return ParsedCommand("trello_sync", {})
    if normalized in TRELLO_STATUS_PATTERNS:
        return ParsedCommand("trello_status", {})
    if normalized in BRIEFING_PATTERNS:
        return ParsedCommand("briefing", {})
    if normalized in BRIEFING_STATUS_PATTERNS:
        return ParsedCommand("briefing_status", {})
    no_news = NO_NEWS_RE.match(intent_text)
    if no_news:
        return ParsedCommand("perpetual_no_news", {"days": _checkin_days(no_news.group(1) or "mañana"), "note": intent_text})
    ask_me = ASK_ME_RE.match(intent_text)
    if ask_me:
        return ParsedCommand("perpetual_no_news", {"days": _checkin_days(ask_me.group(1)), "note": intent_text})
    if normalized in CONFIRMATION_LIST_PATTERNS:
        return ParsedCommand("list_confirmations", {})
    if normalized in LOCAL_ONLY_PATTERNS:
        return ParsedCommand("local_only", {})
    confirm_index = CONFIRM_INDEX_RE.match(intent_text)
    if confirm_index:
        return ParsedCommand("confirm_pending", {"index": int(confirm_index.group(1))})
    cancel_index = CANCEL_INDEX_RE.match(intent_text)
    if cancel_index:
        return ParsedCommand("cancel_pending", {"index": int(cancel_index.group(1))})
    if normalized in CONFIRM_PATTERNS:
        return ParsedCommand("confirm_pending", {})
    if normalized in CANCEL_PATTERNS:
        return ParsedCommand("cancel_pending", {})

    option_match = OPTION_RE.match(intent_text)
    if option_match:
        return ParsedCommand("choose_option", {"option": int(option_match.group(1))})

    cancel_reminder_match = CANCEL_REMINDER_RE.match(intent_text)
    if cancel_reminder_match:
        return ParsedCommand("cancel_reminder_index", {"index": int(cancel_reminder_match.group(1))})

    effort_hours = EFFORT_HOURS_RE.match(intent_text)
    if effort_hours:
        index = effort_hours.group(1)
        minutes = int(effort_hours.group(2)) * 60
        return ParsedCommand(
            "correct_effort",
            {"index": int(index) if index else None, "effort_bucket": "deep" if minutes >= 90 else "medium", "estimated_minutes": minutes},
        )

    effort_bucket = EFFORT_BUCKET_RE.match(intent_text)
    if effort_bucket:
        bucket_text = effort_bucket.group(2).casefold()
        return ParsedCommand(
            "correct_effort",
            {
                "index": int(effort_bucket.group(1)),
                "effort_bucket": "deep" if "profundo" in bucket_text else "quick",
                "estimated_minutes": 120 if "profundo" in bucket_text else 15,
                "context_bucket": "deep_work" if "profundo" in bucket_text else "quick_task",
            },
        )

    scope_correction = SCOPE_CORRECTION_RE.match(intent_text)
    if scope_correction:
        index = scope_correction.group(1)
        return ParsedCommand(
            "correct_scope",
            {"index": int(index) if index else None, "scope": _canonical_scope(scope_correction.group(2))},
        )

    create_card = CREATE_CARD_RE.match(intent_text)
    if create_card:
        return ParsedCommand(
            "trello_create_card",
            {
                "board_alias": _canonical_board(create_card.group(1)),
                "list_name": create_card.group(2).strip() if create_card.group(2) else None,
                "title": create_card.group(3).strip(),
            },
        )

    add_match = ADD_PREFIX_RE.match(intent_text)
    if add_match:
        return _parse_add_match(add_match)

    start_match = START_RE.match(intent_text)
    if start_match:
        return ParsedCommand("trello_start_index", {"index": int(start_match.group(1))})

    finish_match = FINISH_RE.match(intent_text)
    if finish_match:
        return ParsedCommand("trello_finish_index", {"index": int(finish_match.group(1))})

    deadline_match = DEADLINE_RE.match(intent_text)
    if deadline_match:
        index = deadline_match.group(1) or deadline_match.group(3)
        phrase = (deadline_match.group(2) or deadline_match.group(4)).strip()
        due_at = _parse_due_phrase(phrase)
        if not due_at:
            return ParsedCommand("ambiguous", {"reason": "No pude resolver ese deadline de forma segura."})
        return ParsedCommand("set_deadline_index", {"index": int(index), "due_at": due_at, "human_when": phrase})

    task_action = parse_task_action(intent_text, defaults=task_action_defaults)
    if task_action:
        return ParsedCommand(
            task_action.action,
            {
                "target": task_action.target,
                "when_at": task_action.when_at,
                "human_when": task_action.human_when,
                "create_reminder": task_action.create_reminder,
                "allow_standalone_reminder": task_action.allow_standalone_reminder,
            },
        )

    if REMINDER_HINT_RE.search(intent_text):
        try:
            reminder = parse_reminder(intent_text)
        except ValueError as exc:
            return ParsedCommand("ambiguous", {"reason": str(exc)})
        if reminder:
            return ParsedCommand(
                "create_reminder",
                {"message": reminder.message, "remind_at": reminder.remind_at, "human_when": reminder.human_when},
            )
        return ParsedCommand("ambiguous", {"reason": "No pude resolver fecha y hora de forma segura."})

    done_match = DONE_RE.match(intent_text)
    if done_match:
        return ParsedCommand("complete_index", {"index": int(done_match.group(2))})

    delete_match = DELETE_RE.match(intent_text)
    if delete_match:
        return ParsedCommand("delete_index", {"index": int(delete_match.group(2))})

    rename_match = RENAME_RE.match(intent_text)
    if rename_match:
        index = rename_match.group(1) or rename_match.group(3)
        title = rename_match.group(2) or rename_match.group(4)
        return ParsedCommand("rename_index", {"index": int(index), "title": title.strip()})

    move_match = MOVE_RE.match(intent_text)
    if move_match:
        if move_match.group(1):
            return ParsedCommand(
                "move_index",
                {
                    "index": int(move_match.group(1)),
                    "target_index": int(move_match.group(3)),
                    "position": "above" if move_match.group(2).casefold() == "arriba" else "below",
                },
            )
        return ParsedCommand(
            "move_index",
            {
                "index": int(move_match.group(4)),
                "target_index": int(move_match.group(6)),
                "position": move_match.group(5).casefold(),
            },
        )

    if "\n" in intent_text:
        lines = _task_lines(intent_text)
        if len(lines) > 1:
            return ParsedCommand("bulk_add", {"titles": lines})

    cleaned_title, parsed_due = parse_clear_deadline(intent_text)
    if parsed_due and _looks_like_task_title(cleaned_title):
        return ParsedCommand("add", {"title": intent_text})

    return ParsedCommand("ambiguous", {"reason": "unknown_command"})


def normalize_command_text(text: str) -> str:
    return TRAILING_PUNCTUATION_RE.sub("", normalize_for_matching(text))


def strip_conversational_prefixes(text: str) -> str:
    stripped = text.strip()
    while True:
        updated = CONVERSATIONAL_PREFIX_RE.sub("", stripped, count=1).strip()
        if updated == stripped:
            return updated
        stripped = updated


def _task_lines(text: str) -> list[str]:
    return [line.strip().removeprefix("-").strip() for line in text.splitlines() if line.strip().removeprefix("-").strip()]


def _parse_add_match(add_match: re.Match[str]) -> ParsedCommand:
    content = add_match.group(1).strip()
    if not content:
        return ParsedCommand("ambiguous", {"reason": "missing_task_title"})
    lines = _task_lines(content)
    if len(lines) > 1:
        return ParsedCommand("bulk_add", {"titles": lines})

    title = lines[0] if lines else content
    if _has_explicit_deadline_label(title):
        cleaned_title, due_at = parse_clear_deadline(title)
        if due_at and cleaned_title:
            return ParsedCommand("add", {"title": _strip_wrapping_quotes(cleaned_title), "due_at": due_at})
    return ParsedCommand("add", {"title": _strip_wrapping_quotes(title)})


def _has_explicit_deadline_label(title: str) -> bool:
    return bool(re.search(r"\b(?:deadline|fecha\s+l[ií]mite|vence|due)\b", title, re.IGNORECASE))


def _strip_wrapping_quotes(title: str) -> str:
    stripped = title.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1].strip()
    return stripped


def _looks_like_task_title(title: str) -> bool:
    normalized = normalize_command_text(title)
    if normalized in {"", "hoy", "manana", "pasado manana", *WEEKDAYS.keys()}:
        return False
    return len(normalized.split()) >= 2


def _canonical_scope(value: str) -> str:
    for scope in ("Inbox", "Personal"):
        if scope.casefold() == value.casefold():
            return scope
    return value


def _canonical_board(value: str) -> str:
    return value.strip()


def _parse_due_phrase(phrase: str) -> str | None:
    clean = normalize_for_matching(phrase)
    if clean.startswith("el "):
        clean = clean.removeprefix("el ").strip()
    timezone = ZoneInfo(settings.app_timezone)
    now = datetime.now(timezone)
    if clean in {"mañana", "manana", "tomorrow"}:
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    if clean in {"hoy", "today"}:
        return now.replace(hour=18, minute=0, second=0, microsecond=0).isoformat()
    if clean in WEEKDAYS:
        target = WEEKDAYS[clean]
        days_ahead = (target - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (now + timedelta(days=days_ahead)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    return None


def _checkin_days(value: str) -> int:
    clean = normalize_for_matching(value)
    if clean in {"mañana", "manana"}:
        return 1
    if clean == "en una semana":
        return 7
    match = re.search(r"(\d+)", clean)
    return int(match.group(1)) if match else 1
