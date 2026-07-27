from dataclasses import dataclass
from difflib import SequenceMatcher
import json
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.reminders import Reminder
from app.models.auth import User
from app.models.confirmations import PendingConfirmation
from app.models.telegram import ActionQueueItem, TelegramSnapshot, TelegramUpdate
from app.models.tasks import Task
from app.schemas.reminders import ReminderCreate
from app.schemas.tasks import BulkTaskCreate, TaskCreate, TaskUpdate
from app.services import integrations
from app.services import ownership
from app.services import confirmations
from app.services import briefing as briefing_service
from app.services import planning as planning_service
from app.services import reminders as reminder_service
from app.services import settings_service
from app.services import tasks as task_service
from app.services import trello_actions
from app.services import trello_sync
from app.services.app_state import get_state, set_state
from app.services.llm import LLMError, get_llm_provider
from app.services.reminder_messages import (
    format_reminder_cancelled,
    format_reminder_created,
    format_reminder_list,
    format_task_reminder_created,
)
from app.services.task_action_parser import TaskActionDefaults, normalize_task_lookup_text
from app.services.telegram_client import TelegramMessenger
from app.services.telegram_parser import ParsedCommand, normalize_command_text, parse_command, strip_conversational_prefixes
from app.services.time import utc_now_iso
from app.services.trello_config import board_by_alias, workflow_role_enabled
from app.schemas.llm import CommandIntentRequest


LAST_UPDATE_STATE_KEY = "telegram_last_update_id"
SAFE_LLM_COMMAND_INTENTS = {
    "list_todo",
    "add_local_task",
    "planning_today",
    "planning_now",
    "list_reminders",
    "briefing",
    "sync_trello",
    "classify_inbox",
    "unknown_or_needs_clarification",
}
LLM_COMMAND_CONFIDENCE_THRESHOLD = 0.75


@dataclass
class TelegramProcessResult:
    processed: int = 0
    ignored: int = 0
    replies: list[str] | None = None
    summary_sent: bool = False

    def __post_init__(self) -> None:
        if self.replies is None:
            self.replies = []


def get_last_update_id(db: Session) -> int | None:
    value = get_state(db, LAST_UPDATE_STATE_KEY)
    return int(value) if value is not None else None


def set_last_update_id(db: Session, update_id: int) -> None:
    set_state(db, LAST_UPDATE_STATE_KEY, str(update_id))


def store_raw_update(db: Session, raw_update: dict, *, advance_offset: bool = True) -> TelegramUpdate | None:
    update_id = raw_update.get("update_id")
    if update_id is None:
        return None
    existing = db.get(TelegramUpdate, int(update_id))
    if existing:
        if advance_offset:
            set_last_update_id(db, max(int(update_id), get_last_update_id(db) or 0))
        return existing

    message = raw_update.get("message") or raw_update.get("edited_message") or {}
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    text = message.get("text")
    if not chat.get("id"):
        if advance_offset:
            set_last_update_id(db, max(int(update_id), get_last_update_id(db) or 0))
        return None

    update = TelegramUpdate(
        update_id=int(update_id),
        chat_id=str(chat.get("id")),
        from_user_id=str(user.get("id")) if user.get("id") is not None else None,
        message_id=message.get("message_id"),
        text=text,
        raw_json=json.dumps(raw_update, ensure_ascii=False),
        received_at=utc_now_iso(),
        status="pending",
    )
    db.add(update)
    if advance_offset:
        set_last_update_id(db, max(int(update_id), get_last_update_id(db) or 0))
    else:
        db.flush()
    db.refresh(update)
    return update


async def process_pending_updates(
    db: Session,
    messenger: TelegramMessenger,
    *,
    allowed_user_id: str | None = None,
    send_summary: bool = False,
) -> TelegramProcessResult:
    allowed = allowed_user_id if allowed_user_id is not None else settings.telegram_allowed_user_id
    result = TelegramProcessResult()
    updates = list(db.scalars(select(TelegramUpdate).where(TelegramUpdate.status == "pending").order_by(TelegramUpdate.update_id)))
    summary_lines: list[str] = []

    for update in updates:
        link_code = _telegram_link_code(update.text)
        if link_code:
            ok, status, _user_id = integrations.consume_telegram_link_code(db, code=link_code, chat_id=update.chat_id)
            if ok and status == "encrypted":
                reply = "Listo, Telegram quedó vinculado a tu cuenta."
            elif ok:
                reply = "Listo, Telegram quedó vinculado para comandos. Falta ALPHAWAVE_SECRET_ENCRYPTION_KEY para envíos proactivos."
            elif status == "expired":
                reply = "Ese código venció. Generá uno nuevo desde Configuración."
            elif status == "already_used":
                reply = "Ese código ya fue usado. Generá uno nuevo desde Configuración."
            else:
                reply = "No encontré ese código de vinculación. Revisalo o generá uno nuevo."
            await messenger.send_message(update.chat_id, reply)
            update.status = "processed"
            update.processed_at = utc_now_iso()
            result.processed += 1
            result.replies.append(reply)
            summary_lines.append(_summarize_reply(reply))
            db.commit()
            continue
        if not _is_allowed(db, update, allowed):
            update.status = "ignored"
            update.processed_at = utc_now_iso()
            result.ignored += 1
            continue
        if not update.text:
            update.status = "ignored"
            update.processed_at = utc_now_iso()
            result.ignored += 1
            continue

        try:
            command = parse_command(update.text, task_action_defaults=_task_action_defaults(db))
            command = _llm_fallback_command(db, update.text, command, chat_id=update.chat_id)
            reply = await execute_command(db, update.chat_id, command, messenger, source_update_id=update.update_id)
            update.status = "processed"
            update.processed_at = utc_now_iso()
            result.processed += 1
            if reply:
                result.replies.append(reply)
                summary_lines.append(_summarize_reply(reply))
        except ValueError as exc:
            update.status = "processed"
            update.processed_at = utc_now_iso()
            result.processed += 1
            reply = str(exc)
            await messenger.send_message(update.chat_id, reply)
            result.replies.append(reply)
            summary_lines.append(_summarize_reply(reply))
        except Exception as exc:  # noqa: BLE001 - keep poller alive and persist the error.
            update.status = "error"
            update.error = str(exc)
            update.processed_at = utc_now_iso()
            reply = "No pude procesar ese mensaje. Lo dejé registrado para revisar."
            await messenger.send_message(update.chat_id, reply)
            result.replies.append(reply)
        finally:
            db.commit()

    if send_summary and result.processed and summary_lines:
        chat_id = _last_processed_chat_id(db, updates, allowed)
        if chat_id:
            summary = "Procesé mensajes pendientes:\n" + "\n".join(f"- {line}" for line in summary_lines)
            await messenger.send_message(chat_id, summary)
            result.replies.append(summary)
            result.summary_sent = True

    return result


async def execute_command(
    db: Session,
    chat_id: str,
    command: ParsedCommand,
    messenger: TelegramMessenger,
    *,
    source_update_id: int | None = None,
) -> str:
    queue_item = _enqueue_action(db, command, source_update_id)
    try:
        reply = await _execute_command(db, chat_id, command, messenger, source_update_id=source_update_id)
        queue_item.status = "processed"
        queue_item.processed_at = utc_now_iso()
        db.commit()
        return reply
    except Exception as exc:
        queue_item.status = "error"
        queue_item.error = str(exc)
        queue_item.processed_at = utc_now_iso()
        db.commit()
        raise


async def _execute_command(
    db: Session,
    chat_id: str,
    command: ParsedCommand,
    messenger: TelegramMessenger,
    *,
    source_update_id: int | None = None,
) -> str:
    if command.action == "list":
        return await send_todo_list(db, chat_id, messenger)

    if command.action == "add":
        task = task_service.create_task(
            db,
            TaskCreate(title=command.payload["title"], due_at=command.payload.get("due_at"), auto_classify=True),
            user_id=_briefing_user_id(db, chat_id),
        )
        reply = f'Agregué: {task.title} · {task.scope}'
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "bulk_add":
        text = "\n".join(command.payload["titles"])
        tasks = task_service.bulk_create_tasks(db, BulkTaskCreate(text=text, auto_classify=True), user_id=_telegram_user_id(db, chat_id))
        reply = "Agregué:\n" + "\n".join(f"- {task.title} · {task.scope}" for task in tasks)
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "complete_index":
        task = _task_from_snapshot(db, chat_id, command.payload["index"])
        if task_service.is_trello_linked_task(task):
            return await _handle_trello_finish(db, chat_id, task, messenger)
        task_service.complete_task(db, task)
        reply = f"Completé: {task.title}"
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "delete_index":
        task = _task_from_snapshot(db, chat_id, command.payload["index"])
        if task_service.is_trello_linked_task(task):
            return await _reply_trello_read_only(chat_id, task.title, messenger)
        task_service.soft_delete_task(db, task)
        reply = f"Borré: {task.title}"
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "rename_index":
        task = _task_from_snapshot(db, chat_id, command.payload["index"])
        if task_service.is_trello_linked_task(task):
            confirmation = trello_actions.propose_rename_task(db, task, title=command.payload["title"], source="telegram", chat_id=chat_id)
            reply = await _format_trello_confirmation_or_done(db, confirmation)
            await messenger.send_message(chat_id, reply)
            return reply
        old_title = task.title
        task_service.update_task(db, task, TaskUpdate(title=command.payload["title"]))
        reply = f'Renombré "{old_title}" a "{command.payload["title"]}"'
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "move_index":
        task = _task_from_snapshot(db, chat_id, command.payload["index"])
        target = _task_from_snapshot(db, chat_id, command.payload["target_index"])
        if task_service.is_trello_linked_task(task) or task_service.is_trello_linked_task(target):
            return await _reply_trello_read_only(chat_id, task.title, messenger)
        _move_task(db, task.id, target.id, command.payload["position"], chat_id=chat_id)
        reply = f"Moví: {task.title}"
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "task_reminder_snooze":
        reply = await _handle_task_targeted_action(db, chat_id, command, source_update_id=source_update_id)
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "task_snooze":
        reply = await _handle_task_targeted_action(db, chat_id, command, source_update_id=source_update_id)
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "task_deadline":
        reply = await _handle_task_targeted_action(db, chat_id, command, source_update_id=source_update_id)
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "create_reminder":
        reply = _handle_create_reminder(db, chat_id, command, source_update_id=source_update_id)
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "list_reminders":
        return await send_reminder_list(db, chat_id, messenger)

    if command.action == "cancel_reminder_index":
        reminder = _reminder_from_snapshot(db, chat_id, command.payload["index"])
        reminder_service.cancel_reminder(db, reminder)
        reply = format_reminder_cancelled(reminder.message)
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "correct_scope":
        task = _task_from_snapshot(db, chat_id, command.payload.get("index"))
        requested_scope = str(command.payload["scope"]).strip()
        available = settings_service.available_scopes(db, user_id=_telegram_user_id(db, chat_id))
        scope = next((candidate for candidate in available if candidate.casefold() == requested_scope.casefold()), None)
        if scope is None:
            raise ValueError(f"Ese scope no está disponible. Opciones: {', '.join(available)}.")
        task_service.update_task(db, task, TaskUpdate(scope=scope))
        reply = f"Actualicé scope: {task.title} · {scope}"
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "correct_effort":
        task = _task_from_snapshot(db, chat_id, command.payload.get("index"))
        task_service.update_task(
            db,
            task,
            TaskUpdate(
                effort_bucket=command.payload.get("effort_bucket"),
                estimated_minutes=command.payload.get("estimated_minutes"),
                context_bucket=command.payload.get("context_bucket"),
            ),
        )
        reply = f"Actualicé esfuerzo: {task.title} · {command.payload.get('estimated_minutes')} min"
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "classify_inbox":
        reply = _format_inbox_suggestions(planning_service.inbox_suggestions(db, user_id=_telegram_user_id(db, chat_id)))
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "plan_today":
        reply = _format_today_plan(planning_service.today_plan(db, user_id=_telegram_user_id(db, chat_id)))
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "plan_now":
        reply = _format_now_plan(planning_service.now_plan(db, user_id=_telegram_user_id(db, chat_id)))
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action in {"sort_propose", "sort_by_priority_propose"}:
        proposal = planning_service.propose_sort(db, source="telegram", chat_id=chat_id, user_id=_telegram_user_id(db, chat_id))
        if not proposal.items:
            reply = "No hay suficientes tareas activas para reordenar."
        else:
            reply = "Propongo este orden:\n\n" + "\n".join(
                f"{item.new_index}. {item.title} · {item.scope} — {item.reason}" for item in proposal.items
            ) + "\n\nConfirmá con:\nconfirmar\n\nO cancelá con:\ncancelar"
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "trello_sync":
        summary = await trello_sync.run_trello_sync(db, user_id=_telegram_user_id(db, chat_id))
        reply = _format_trello_sync(summary)
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "trello_status":
        status = trello_sync.trello_status(db, user_id=_telegram_user_id(db, chat_id))
        reply = _format_trello_status(status)
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "briefing":
        run = await briefing_service.send_briefing(
            db,
            messenger,
            chat_id=chat_id,
            reason="manual",
            force=True,
            user_id=_telegram_user_id(db, chat_id),
        )
        reply = f"Briefing enviado. Estado: {run.status}"
        return reply

    if command.action == "briefing_status":
        reply = _format_briefing_status(briefing_service.status(db, user_id=_briefing_user_id(db, chat_id)))
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "perpetual_no_news":
        task = briefing_service.update_perpetual_checkin(
            db,
            chat_id,
            days=command.payload["days"],
            note=command.payload["note"],
            user_id=_briefing_user_id(db, chat_id),
        )
        reply = f"Listo. Actualicé check-in: {task.title}\nPróximo: {task.next_checkin_at[:10]}"
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "trello_create_card":
        confirmation = trello_actions.propose_create_card(
            db,
            board_alias=command.payload["board_alias"],
            list_name=command.payload.get("list_name"),
            title=command.payload["title"],
            source="telegram",
            chat_id=chat_id,
        )
        reply = await _format_trello_confirmation_or_done(db, confirmation)
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "trello_start_index":
        task = _task_from_snapshot(db, chat_id, command.payload["index"])
        reply = await _handle_trello_start(db, chat_id, task, messenger)
        return reply

    if command.action == "trello_finish_index":
        task = _task_from_snapshot(db, chat_id, command.payload["index"])
        reply = await _handle_trello_finish(db, chat_id, task, messenger)
        return reply

    if command.action == "set_deadline_index":
        task = _task_from_snapshot(db, chat_id, command.payload["index"])
        if task_service.is_trello_linked_task(task):
            confirmation = trello_actions.propose_update_due(
                db,
                task,
                due_at=command.payload["due_at"],
                source="telegram",
                chat_id=chat_id,
            )
            reply = await _format_trello_confirmation_or_done(db, confirmation)
        else:
            task_service.update_task(db, task, TaskUpdate(due_at=command.payload["due_at"]))
            reply = f"Actualicé deadline: {task.title} · {command.payload['human_when']}"
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "list_confirmations":
        return await send_confirmation_list(db, chat_id, messenger)

    if command.action == "choose_option":
        reply = await _handle_pending_task_action_option(db, chat_id, command.payload["option"], source_update_id=source_update_id)
        if reply is None:
            reply = await _handle_confirmation_option(db, chat_id, command.payload["option"])
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "local_only":
        reply = _handle_local_only(db, chat_id)
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "confirm_pending":
        confirmation = _pending_confirmation(db, chat_id, command.payload.get("index"))
        if confirmation.action_type == "apply_sort_order":
            planning_service.apply_sort(db, confirmation.id, user_id=_telegram_user_id(db, chat_id))
            reply = "Listo. Apliqué el orden propuesto."
        elif confirmation.action_type in trello_actions.TRELLO_ACTION_TYPES:
            reply = await trello_actions.execute_confirmation(db, confirmation)
        else:
            reply = "Esa confirmación necesita una opción numerada."
        await messenger.send_message(chat_id, reply)
        return reply

    if command.action == "cancel_pending":
        confirmation = _pending_confirmation(db, chat_id, command.payload.get("index"))
        trello_actions.cancel_confirmation(db, confirmation)
        reply = "Cancelé la propuesta."
        await messenger.send_message(chat_id, reply)
        return reply

    reason = command.payload.get("reason") if command.action == "ambiguous" else None
    reply = _format_unknown_command(reason=reason, text=command.payload.get("text"))
    await messenger.send_message(chat_id, reply)
    return reply


def _llm_fallback_command(
    db_or_text: Session | str,
    text_or_command: str | ParsedCommand,
    command: ParsedCommand | None = None,
    *,
    chat_id: str | None = None,
) -> ParsedCommand:
    if command is None:
        db = None
        text = str(db_or_text)
        command = text_or_command  # type: ignore[assignment]
    else:
        db = db_or_text
        text = str(text_or_command)
    if command.action != "ambiguous" or command.payload.get("reason") != "unknown_command":
        return command
    user_id = _telegram_user_id(db, chat_id) if db is not None else None
    if db is not None and not settings_service.llm_enabled(db, user_id=user_id):
        return ParsedCommand("ambiguous", {"reason": "unknown_command", "text": text})
    provider = get_llm_provider(db, user_id) if db is not None else None
    if not provider:
        return ParsedCommand("ambiguous", {"reason": "unknown_command", "text": text})
    normalized = normalize_command_text(strip_conversational_prefixes(text))
    try:
        parsed = provider.parse_command_intent(
            CommandIntentRequest(
                text=text,
                normalized_text=normalized,
                allowed_intents=sorted(SAFE_LLM_COMMAND_INTENTS),
            )
        )
    except LLMError:
        return ParsedCommand("ambiguous", {"reason": "unknown_command", "text": text})
    if parsed.intent not in SAFE_LLM_COMMAND_INTENTS or parsed.confidence < LLM_COMMAND_CONFIDENCE_THRESHOLD:
        return ParsedCommand("ambiguous", {"reason": parsed.reason or "unknown_command", "text": text})
    if parsed.intent == "list_todo":
        return ParsedCommand("list", {})
    if parsed.intent == "planning_today":
        return ParsedCommand("plan_today", {})
    if parsed.intent == "planning_now":
        return ParsedCommand("plan_now", {})
    if parsed.intent == "list_reminders":
        return ParsedCommand("list_reminders", {})
    if parsed.intent == "briefing":
        return ParsedCommand("briefing", {})
    if parsed.intent == "sync_trello":
        return ParsedCommand("trello_sync", {})
    if parsed.intent == "classify_inbox":
        return ParsedCommand("classify_inbox", {})
    if parsed.intent == "add_local_task" and parsed.title:
        return ParsedCommand("add", {"title": parsed.title})
    return ParsedCommand("ambiguous", {"reason": parsed.reason or "unknown_command", "text": text})


def _format_unknown_command(*, reason: str | None, text: str | None) -> str:
    lines = ["No entendí ese pedido."]
    suggestion = _near_intent_suggestion(text or "")
    if suggestion:
        lines.extend(["", f'¿Querías decir "{suggestion}"?'])
    lines.extend(
        [
            "",
            "Podés probar:",
            "- /todo",
            "- agregá comprar café",
            "- hecho 2",
            "- recuérdame llamar a Juan mañana a las 10",
            "- qué tengo que hacer hoy",
            "- qué hago ahora",
            "- sync trello",
            "- briefing",
            "- confirmaciones",
        ]
    )
    if reason and reason != "unknown_command":
        lines.append(f"\nDetalle: {reason}")
    return "\n".join(lines)


def _near_intent_suggestion(text: str) -> str | None:
    normalized = normalize_command_text(strip_conversational_prefixes(text))
    if "hoy" in normalized and any(word in normalized for word in ("hacer", "hago", "debo", "tengo")):
        return "qué tengo que hacer hoy"
    if any(word in normalized for word in ("ahora", "ya")) and "hago" in normalized:
        return "qué hago ahora"
    if "todo" in normalized or "tareas" in normalized:
        return "/todo"
    return None


def _task_action_defaults(db: Session) -> TaskActionDefaults:
    config = settings_service.reminder_settings(db, user_id=_telegram_user_id(db))
    return TaskActionDefaults(
        reminder_time=str(config.get("default_time") or "09:00"),
        snooze_time=str(config.get("snooze_default_time") or "09:00"),
        later_delay_hours=int(config.get("later_delay_hours") or 2),
    )


def _handle_create_reminder(db: Session, chat_id: str, command: ParsedCommand, *, source_update_id: int | None) -> str:
    message = command.payload.get("message") or command.payload["target"]
    remind_at = command.payload.get("remind_at") or command.payload["when_at"]
    human_when = command.payload["human_when"]
    matches = _find_active_task_matches(db, message, chat_id=chat_id)
    if len(matches) == 1:
        task = matches[0]
        task_service.snooze_task(db, task, remind_at, reason="telegram_reminder")
        reminder = reminder_service.create_reminder(
            db,
            ReminderCreate(message=task.title, remind_at=remind_at, task_id=task.id, source="telegram"),
            source_update_id=source_update_id,
            user_id=_telegram_user_id(db, chat_id),
        )
        return format_task_reminder_created(task.title, human_when)
    if len(matches) > 1:
        _save_pending_task_action_snapshot(db, chat_id, command, matches)
        return _format_multiple_task_matches(matches)

    reminder = reminder_service.create_reminder(
        db,
        ReminderCreate(message=message, remind_at=remind_at, source="telegram"),
        source_update_id=source_update_id,
        user_id=_telegram_user_id(db, chat_id),
    )
    return format_reminder_created(reminder.message, human_when)


async def _handle_task_targeted_action(db: Session, chat_id: str, command: ParsedCommand, *, source_update_id: int | None) -> str:
    target = command.payload["target"]
    matches = _find_active_task_matches(db, target, chat_id=chat_id)
    if len(matches) == 1:
        return await _apply_task_action(db, matches[0], command, chat_id=chat_id, source_update_id=source_update_id)
    if len(matches) > 1:
        _save_pending_task_action_snapshot(db, chat_id, command, matches)
        return _format_multiple_task_matches(matches)

    if command.action == "task_reminder_snooze":
        _save_pending_task_action_snapshot(db, chat_id, command, [])
        return (
            f'No encontré una tarea clara para "{target}".\n\n'
            "¿Qué querés hacer?\n"
            "1. Crear tarea + recordatorio\n"
            "2. Crear sólo recordatorio\n"
            "3. Cancelar"
        )
    return f'No encontré una tarea clara para "{target}".\n\nPedime /todo o probá con un título más exacto.'


async def _apply_task_action(db: Session, task: Task, command: ParsedCommand, *, chat_id: str, source_update_id: int | None) -> str:
    when_at = command.payload.get("when_at") or command.payload["remind_at"]
    human_when = command.payload["human_when"]
    if command.action == "create_reminder":
        task_service.snooze_task(db, task, when_at, reason="telegram_reminder")
        reminder_service.create_reminder(
            db,
            ReminderCreate(message=task.title, remind_at=when_at, task_id=task.id, source="telegram"),
            source_update_id=source_update_id,
            user_id=_telegram_user_id(db, chat_id),
        )
        return format_task_reminder_created(task.title, human_when)

    if command.action == "task_reminder_snooze":
        task_service.snooze_task(db, task, when_at, reason="telegram_reminder")
        reminder_service.create_reminder(
            db,
            ReminderCreate(message=task.title, remind_at=when_at, task_id=task.id, source="telegram"),
            source_update_id=source_update_id,
            user_id=_telegram_user_id(db, chat_id),
        )
        if "más tarde" in human_when:
            return format_task_reminder_created(task.title, human_when, snoozed=False)
        return format_task_reminder_created(task.title, human_when)

    if command.action == "task_snooze":
        task_service.snooze_task(db, task, when_at, reason="telegram_snooze")
        return f'Listo. Pospuesta "{task.title}" hasta {human_when}.'

    if command.action == "task_deadline":
        if task_service.is_trello_linked_task(task):
            confirmation = trello_actions.propose_update_due(db, task, due_at=when_at, source="telegram")
            return await _format_trello_confirmation_or_done(db, confirmation)
        task_service.update_task(db, task, TaskUpdate(due_at=when_at))
        return f'Listo. Deadline de "{task.title}": {human_when}.'

    raise ValueError("Acción de tarea no soportada.")


async def _handle_pending_task_action_option(
    db: Session,
    chat_id: str,
    option: int,
    *,
    source_update_id: int | None,
) -> str | None:
    snapshot = _latest_snapshot(db, chat_id, "task_action")
    if not snapshot:
        return None
    payload = json.loads(snapshot.task_ids_json)
    command = ParsedCommand(payload["action"], payload["payload"])
    task_ids = payload.get("task_ids") or []

    if task_ids:
        if option < 1 or option > len(task_ids):
            raise ValueError("Ese índice no existe en la lista de tareas parecidas.")
        task = task_service.get_task(db, task_ids[option - 1], user_id=_telegram_user_id(db, chat_id))
        if not task or task.status != "active":
            raise ValueError("Esa tarea ya no está activa.")
        reply = await _apply_task_action(db, task, command, chat_id=chat_id, source_update_id=source_update_id)
        db.delete(snapshot)
        db.commit()
        return reply

    if command.action != "task_reminder_snooze":
        return None
    if option == 1:
        task = task_service.create_task(db, TaskCreate(title=command.payload["target"], auto_classify=True), user_id=_telegram_user_id(db, chat_id))
        task_service.snooze_task(db, task, command.payload["when_at"], reason="telegram_reminder")
        reminder_service.create_reminder(
            db,
            ReminderCreate(message=task.title, remind_at=command.payload["when_at"], task_id=task.id, source="telegram"),
            source_update_id=source_update_id,
            user_id=_telegram_user_id(db, chat_id),
        )
        db.delete(snapshot)
        db.commit()
        return format_task_reminder_created(task.title, command.payload["human_when"])
    if option == 2:
        reminder = reminder_service.create_reminder(
            db,
            ReminderCreate(message=command.payload["target"], remind_at=command.payload["when_at"], source="telegram"),
            source_update_id=source_update_id,
            user_id=_telegram_user_id(db, chat_id),
        )
        db.delete(snapshot)
        db.commit()
        return format_reminder_created(reminder.message, command.payload["human_when"])
    if option == 3:
        db.delete(snapshot)
        db.commit()
        return "Cancelado."
    raise ValueError("Elegí 1, 2 o 3.")


def _find_active_task_matches(db: Session, target: str, *, chat_id: str | None = None) -> list[Task]:
    target_key = normalize_task_lookup_text(target)
    if not target_key or len(target_key.split()) < 2:
        return []
    scored: list[tuple[int, float, Task]] = []
    for task in task_service.list_tasks(db, user_id=_telegram_user_id(db, chat_id)):
        if task.trello_state in ("completed", "ignored"):
            continue
        title_key = normalize_task_lookup_text(task.title)
        if not title_key:
            continue
        if title_key == target_key:
            scored.append((0, 1.0, task))
        elif target_key in title_key or title_key in target_key:
            scored.append((1, _similarity(target_key, title_key), task))
        else:
            ratio = _similarity(target_key, title_key)
            if ratio >= 0.86:
                scored.append((2, ratio, task))
    scored.sort(key=lambda item: (item[0], -item[1], item[2].manual_order))
    return [task for _, _, task in scored[:5]]


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def _format_multiple_task_matches(matches: list[Task]) -> str:
    lines = ["Encontré varias tareas parecidas:", ""]
    for index, task in enumerate(matches, start=1):
        lines.append(f"{index}. {task.title} · {task.scope}")
    lines.extend(["", "¿Cuál querés usar?"])
    return "\n".join(lines)


def _save_pending_task_action_snapshot(db: Session, chat_id: str, command: ParsedCommand, tasks: list[Task]) -> None:
    db.add(
        TelegramSnapshot(
            id=str(uuid4()),
            chat_id=chat_id,
            message_id=None,
            snapshot_type="task_action",
            task_ids_json=json.dumps(
                {
                    "action": command.action,
                    "payload": command.payload,
                    "task_ids": [task.id for task in tasks],
                },
                ensure_ascii=False,
            ),
            created_at=utc_now_iso(),
        )
    )
    db.commit()


async def send_todo_list(db: Session, chat_id: str, messenger: TelegramMessenger) -> str:
    tasks = task_service.list_tasks(db, user_id=_telegram_user_id(db, chat_id))
    if not tasks:
        reply = "TODO list\n\nNo hay tareas activas."
        message_id = await messenger.send_message(chat_id, reply)
        _save_snapshot(db, chat_id, message_id, [], snapshot_type="tasks")
        return reply

    lines = ["TODO list", ""]
    for index, task in enumerate(tasks, start=1):
        due = f" · {task.due_at[:10]}" if task.due_at else ""
        marker = "[!]" if task.task_kind == "attention" else "[ ]"
        source = " · Trello" if task_service.is_trello_linked_task(task) else ""
        lines.append(f"{index}. {marker} {task.title} · {task.scope}{due}{source}")
    lines.extend(["", "Podés responder:", "- hecho 2", "- borra 3", "- renombra 4 a ...", "- mueve 1 abajo de 3"])
    reply = "\n".join(lines)
    message_id = await messenger.send_message(chat_id, reply)
    _save_snapshot(db, chat_id, message_id, [task.id for task in tasks], snapshot_type="tasks")
    return reply


async def send_reminder_list(db: Session, chat_id: str, messenger: TelegramMessenger) -> str:
    reminders = reminder_service.list_reminders(db, status="pending", user_id=_telegram_user_id(db, chat_id))
    if not reminders:
        reply = format_reminder_list([])
        message_id = await messenger.send_message(chat_id, reply)
        _save_snapshot(db, chat_id, message_id, [], snapshot_type="reminders")
        return reply

    reply = format_reminder_list(reminders)
    message_id = await messenger.send_message(chat_id, reply)
    _save_snapshot(db, chat_id, message_id, [reminder.id for reminder in reminders], snapshot_type="reminders")
    return reply


async def send_confirmation_list(db: Session, chat_id: str, messenger: TelegramMessenger) -> str:
    pending = confirmations.list_pending(db, chat_id=chat_id, user_id=_telegram_user_id(db, chat_id))
    if not pending:
        reply = "Confirmaciones pendientes\n\nNo hay confirmaciones pendientes."
        message_id = await messenger.send_message(chat_id, reply)
        _save_snapshot(db, chat_id, message_id, [], snapshot_type="confirmations")
        return reply

    lines = ["Confirmaciones pendientes", ""]
    for index, confirmation in enumerate(pending, start=1):
        lines.append(f"{index}. {confirmation.summary or confirmation.action_type}")
        lines.append(f"   vence: {confirmation.expires_at[:16].replace('T', ' ')}")
    lines.extend(["", "Podés responder:", "- confirmar 1", "- cancelar 1"])
    reply = "\n".join(lines)
    message_id = await messenger.send_message(chat_id, reply)
    _save_snapshot(db, chat_id, message_id, [confirmation.id for confirmation in pending], snapshot_type="confirmations")
    return reply


def _save_snapshot(db: Session, chat_id: str, message_id: int | None, task_ids: list[str], *, snapshot_type: str) -> None:
    db.add(
        TelegramSnapshot(
            id=str(uuid4()),
            chat_id=chat_id,
            message_id=message_id,
            snapshot_type=snapshot_type,
            task_ids_json=json.dumps(task_ids),
            created_at=utc_now_iso(),
        )
    )
    db.commit()


def _latest_snapshot(db: Session, chat_id: str, snapshot_type: str) -> TelegramSnapshot | None:
    return db.scalar(
        select(TelegramSnapshot)
        .where(TelegramSnapshot.chat_id == chat_id, TelegramSnapshot.snapshot_type == snapshot_type)
        .order_by(desc(TelegramSnapshot.created_at))
        .limit(1)
    )


def _task_from_snapshot(db: Session, chat_id: str, index: int | None):
    snapshot = _latest_snapshot(db, chat_id, "tasks")
    if not snapshot:
        raise ValueError("No hay una lista reciente. Pedime /todo primero.")
    task_ids = json.loads(snapshot.task_ids_json)
    if index is None:
        if len(task_ids) != 1:
            raise ValueError("Necesito que me digas el número de tarea. Ejemplo: la 2 es Personal.")
        index = 1
    if index < 1 or index > len(task_ids):
        raise ValueError("Ese índice no existe en la última lista.")
    task = task_service.get_task(db, task_ids[index - 1], user_id=_telegram_user_id(db, chat_id))
    if not task:
        raise ValueError("Esa tarea ya no existe.")
    return task


def _reminder_from_snapshot(db: Session, chat_id: str, index: int) -> Reminder:
    snapshot = _latest_snapshot(db, chat_id, "reminders")
    if not snapshot:
        raise ValueError("No hay una lista reciente de recordatorios. Pedime /reminders primero.")
    reminder_ids = json.loads(snapshot.task_ids_json)
    if index < 1 or index > len(reminder_ids):
        raise ValueError("Ese índice no existe en la última lista de recordatorios.")
    reminder = reminder_service.get_reminder(db, reminder_ids[index - 1], user_id=_telegram_user_id(db, chat_id))
    if not reminder:
        raise ValueError("Ese recordatorio ya no existe.")
    if reminder.status != "pending":
        raise ValueError("Ese recordatorio ya no está pendiente.")
    return reminder


def _pending_confirmation(db: Session, chat_id: str, index: int | None) -> PendingConfirmation:
    if index is not None:
        snapshot = _latest_snapshot(db, chat_id, "confirmations")
        if snapshot:
            confirmation_ids = json.loads(snapshot.task_ids_json)
            if index < 1 or index > len(confirmation_ids):
                raise ValueError("Ese índice no existe en la última lista de confirmaciones.")
            confirmation = confirmations.get_pending(db, confirmation_ids[index - 1], user_id=_telegram_user_id(db, chat_id))
            if not confirmation:
                raise ValueError("Esa confirmación ya no está pendiente.")
            return confirmation
        pending = confirmations.list_pending(db, chat_id=chat_id, user_id=_telegram_user_id(db, chat_id))
        if index < 1 or index > len(pending):
            raise ValueError("Ese índice no existe entre las confirmaciones pendientes.")
        return pending[index - 1]

    pending = confirmations.list_pending(db, chat_id=chat_id, user_id=_telegram_user_id(db, chat_id))
    if not pending:
        raise ValueError("No hay una confirmación pendiente.")
    if len(pending) > 1:
        raise ValueError("Hay varias confirmaciones pendientes. Pedime confirmaciones y usá confirmar N.")
    return pending[0]


def _move_task(db: Session, task_id: str, target_id: str, position: str, *, chat_id: str | None = None) -> None:
    tasks = task_service.list_tasks(db, user_id=_telegram_user_id(db, chat_id))
    ids = [task.id for task in tasks]
    if task_id not in ids or target_id not in ids:
        raise ValueError("Sólo puedo reordenar tareas activas.")
    ids.remove(task_id)
    target_index = ids.index(target_id)
    insert_at = target_index if position == "above" else target_index + 1
    ids.insert(insert_at, task_id)
    task_service.reorder_tasks(db, ids, user_id=_telegram_user_id(db, chat_id))


def _enqueue_action(db: Session, command: ParsedCommand, source_update_id: int | None) -> ActionQueueItem:
    item = ActionQueueItem(
        id=str(uuid4()),
        source_update_id=source_update_id,
        action_type=command.action,
        payload_json=json.dumps(command.payload, ensure_ascii=False),
        status="pending",
        created_at=utc_now_iso(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _telegram_user_id(db: Session, chat_id: str | None = None, legacy_from_user_id: str | None = None) -> str:
    resolved = integrations.resolve_telegram_user_id(db, chat_id, legacy_from_user_id=legacy_from_user_id)
    return resolved or ownership.backfill_core_user_ids(db)


def _briefing_user_id(db: Session, chat_id: str) -> str:
    resolved = integrations.resolve_telegram_user_id(db, chat_id, allow_legacy=False)
    if resolved:
        return resolved
    user_ids = list(db.scalars(select(User.id).limit(2)).all())
    if len(user_ids) > 1:
        raise ValueError("Briefing requiere un chat vinculado a un usuario.")
    return user_ids[0] if user_ids else ownership.backfill_core_user_ids(db)


def _telegram_link_code(text: str | None) -> str | None:
    clean = (text or "").strip()
    if not clean:
        return None
    parts = clean.split()
    if len(parts) != 2:
        return None
    command, code = parts[0].lstrip("/").casefold(), parts[1].strip()
    if command not in {"link", "vincular"}:
        return None
    if not code or len(code) > 32:
        return None
    return code


def _is_allowed(db: Session, update: TelegramUpdate, allowed_user_id: str | None) -> bool:
    if allowed_user_id is not None:
        if str(update.from_user_id) == str(allowed_user_id):
            return True
        return integrations.resolve_telegram_user_id(db, update.chat_id, legacy_from_user_id=None, allow_legacy=False) is not None
    resolved = integrations.resolve_telegram_user_id(db, update.chat_id, legacy_from_user_id=update.from_user_id)
    if resolved:
        return True
    return False


def _last_processed_chat_id(db: Session, updates: list[TelegramUpdate], allowed_user_id: str | None) -> str | None:
    for update in reversed(updates):
        if update.status == "processed" and _is_allowed(db, update, allowed_user_id):
            return update.chat_id
    return None


def _summarize_reply(reply: str) -> str:
    return reply.splitlines()[0][:120]


async def _reply_trello_read_only(chat_id: str, title: str, messenger: TelegramMessenger) -> str:
    reply = f"Esa tarea viene de Trello. En M6 sólo modifico Trello con confirmación: {title}"
    await messenger.send_message(chat_id, reply)
    return reply


async def _handle_trello_start(db: Session, chat_id: str, task, messenger: TelegramMessenger) -> str:
    if not task_service.is_trello_linked_task(task):
        reply = "Esa tarea es local; no hay transición Trello para empezar."
        await messenger.send_message(chat_id, reply)
        return reply
    if task.trello_state == "in_progress":
        reply = "Esa card ya está en EN PROCESO."
        await messenger.send_message(chat_id, reply)
        return reply
    if task.trello_state == "perpetual":
        reply = "Esa card es perpetua. Los check-ins quedan para un milestone futuro."
        await messenger.send_message(chat_id, reply)
        return reply
    confirmation = trello_actions.propose_move_task(
        db,
        task,
        target_state="in_progress",
        action_type="trello_move_card",
        source="telegram",
        chat_id=chat_id,
    )
    reply = await _format_trello_confirmation_or_done(db, confirmation)
    await messenger.send_message(chat_id, reply)
    return reply


async def _handle_trello_finish(db: Session, chat_id: str, task, messenger: TelegramMessenger) -> str:
    if not task_service.is_trello_linked_task(task):
        task_service.complete_task(db, task)
        reply = f"Completé: {task.title}"
        await messenger.send_message(chat_id, reply)
        return reply
    if task.trello_state == "perpetual":
        reply = "Esa card es perpetua. No la completo en M6; conviene registrar un check-in futuro."
        await messenger.send_message(chat_id, reply)
        return reply
    if task.trello_state == "pending":
        confirmation = confirmations.create_confirmation(
            db,
            source="telegram",
            chat_id=chat_id,
            action_type="trello_finish_choice",
            payload={"task_id": task.id, "title": task.title},
            ttl_hours=settings.trello_confirmation_ttl_hours,
            summary=f'Elegir transición para "{task.title}"',
            user_id=_telegram_user_id(db, chat_id),
        )
        reply = "\n".join(
            [
                f'La card "{task.title}" todavía está en TAREAS.',
                "¿Qué querés hacer?",
                "",
                "1. Mover a EN PROCESO",
                "2. Mover a EN REVISION",
                "3. Mover a TERMINADAS",
                "4. Sólo marcar localmente",
                "5. Cancelar",
            ]
        )
        await messenger.send_message(chat_id, reply)
        return reply
    target_state = _finish_target_state(db, task)
    confirmation = trello_actions.propose_move_task(
        db,
        task,
        target_state=target_state,
        action_type="trello_complete_transition",
        source="telegram",
        chat_id=chat_id,
    )
    reply = await _format_trello_confirmation_or_done(db, confirmation)
    await messenger.send_message(chat_id, reply)
    return reply


async def _handle_confirmation_option(db: Session, chat_id: str, option: int) -> str:
    confirmation = confirmations.latest_pending(db, chat_id=chat_id, action_type="trello_finish_choice", user_id=_telegram_user_id(db, chat_id))
    if not confirmation:
        raise ValueError("No hay una opción pendiente para esa respuesta.")
    payload = json.loads(confirmation.payload_json)
    task = task_service.get_task(db, payload["task_id"], user_id=_telegram_user_id(db, chat_id))
    if not task:
        raise ValueError("La tarea ya no existe.")
    if option == 5:
        confirmations.cancel(db, confirmation)
        return "Cancelé la propuesta."
    if option == 4:
        task_service.complete_task(db, task)
        confirmations.cancel(db, confirmation)
        return f"Listo. Marqué sólo localmente: {task.title}"
    target = {1: "in_progress", 2: "review", 3: "completed"}.get(option)
    if not target:
        raise ValueError("Opción inválida.")
    confirmations.cancel(db, confirmation)
    next_confirmation = trello_actions.propose_move_task(
        db,
        task,
        target_state=target,
        action_type="trello_complete_transition",
        source="telegram",
        chat_id=chat_id,
    )
    return await _format_trello_confirmation_or_done(db, next_confirmation)


def _finish_target_state(db: Session, task: Task) -> str:
    board = board_by_alias(task.scope or task.origin_label or "", db, user_id=task.user_id)
    if task.trello_state == "in_progress" and board and workflow_role_enabled(board, "review"):
        return "review"
    if board and not workflow_role_enabled(board, "completed"):
        raise ValueError("Ese board no tiene lista de Terminadas configurada. Las acciones de completar quedan bloqueadas.")
    return "completed"


def _handle_local_only(db: Session, chat_id: str) -> str:
    pending = confirmations.list_pending(db, chat_id=chat_id, user_id=_telegram_user_id(db, chat_id))
    if not pending:
        raise ValueError("No hay una acción pendiente para aplicar sólo local.")
    confirmation = pending[0]
    payload = json.loads(confirmation.payload_json)
    task_id = payload.get("task_id")
    if not task_id:
        raise ValueError("Esa confirmación no tiene tarea local asociada.")
    task = task_service.get_task(db, task_id, user_id=_telegram_user_id(db, chat_id))
    if not task:
        raise ValueError("La tarea ya no existe.")
    if confirmation.action_type == "trello_rename_card":
        task_service.update_task(db, task, TaskUpdate(title=payload["title"]))
        confirmations.cancel(db, confirmation)
        return f'Renombré sólo localmente: {payload["title"]}'
    if confirmation.action_type == "trello_update_due":
        task_service.update_task(db, task, TaskUpdate(due_at=payload.get("due_at")))
        confirmations.cancel(db, confirmation)
        return "Actualicé el deadline sólo localmente."
    if confirmation.action_type in ("trello_local_only_complete", "trello_finish_choice", "trello_move_card", "trello_complete_transition"):
        task_service.complete_task(db, task)
        confirmations.cancel(db, confirmation)
        return f"Listo. Marqué sólo localmente: {task.title}"
    raise ValueError("Esa acción no soporta sólo local.")


def _format_inbox_suggestions(suggestions) -> str:
    if not suggestions.suggestions:
        return "Inbox sin clasificar\n\nNo hay tareas en Inbox."
    lines = ["Inbox sin clasificar", ""]
    for index, suggestion in enumerate(suggestions.suggestions, start=1):
        lines.append(f"{index}. {suggestion.task.title}")
        lines.append(f" Sugerencia: {suggestion.suggested_scope}")
        lines.append(f" Motivo: {suggestion.reason}")
        lines.append(f' Para confirmar: "la {index} es {suggestion.suggested_scope}"')
        lines.append("")
    return "\n".join(lines).strip()


def _format_today_plan(plan) -> str:
    if not plan.groups:
        return "Hoy tenés:\n- No hay tareas activas para planificar."
    lines = ["Hoy tenés:"]
    for group in plan.groups:
        lines.append(f"- {len(group.items)} {group.title.lower()}")
    if plan.summary:
        lines.append("")
        lines.append(plan.summary)
    top_items = plan.recommendations[:3]
    if top_items:
        lines.append("")
        lines.append("Top:")
        for index, item in enumerate(top_items, start=1):
            if not item.task:
                continue
            lines.append(f"{index}. {item.task.title} · {item.task.scope}")
            lines.append(f"   {item.reason}")
    return "\n".join(lines).strip()


def _format_now_plan(plan) -> str:
    lines = ["Ahora haría:", ""]
    if plan.recommended:
        for index, item in enumerate(plan.recommended, start=1):
            band = item.priority.priority_band if item.priority else "prioridad"
            meta = _planning_item_meta(item.task)
            lines.append(f"{index}. {item.task.title} — {band}")
            lines.append(f"   {item.reason}{meta}")
    else:
        lines.append(plan.summary or "No hay una próxima acción clara.")
    if plan.alternatives:
        lines.append("")
        lines.append("Alternativas:")
        for item in plan.alternatives[:4]:
            lines.append(f"• {item.title}")
    return "\n".join(lines[:12]).strip()


def _planning_item_meta(task) -> str:
    parts = []
    if task.estimated_minutes:
        parts.append(f"{task.estimated_minutes} min")
    if task.context_bucket:
        parts.append(str(task.context_bucket).replace("_", " "))
    return f" · {' · '.join(parts)}" if parts else ""


def _format_trello_sync(summary) -> str:
    if summary.status == "disabled" or summary.error:
        return summary.error or "Trello no está disponible."
    return "\n".join(
        [
            "Sync Trello completado.",
            "",
            f"Vistas: {summary.cards_seen} cards",
            f"Nuevas: {summary.tasks_created}",
            f"Actualizadas: {summary.tasks_updated}",
            f"Completadas detectadas: {summary.tasks_completed}",
            f"Requieren atención: {summary.attention_items}",
            f"Ignoradas: {summary.ignored}",
        ]
    )


def _format_trello_status(status) -> str:
    if not status.enabled:
        return "Trello no está habilitado. Revisá TRELLO_ENABLED y variables TRELLO_*."
    if not status.configured:
        return "Trello está habilitado pero faltan credenciales o board IDs."
    lines = ["Estado Trello", "", f"Último estado: {status.last_sync_status or 'sin sync'}"]
    if status.last_sync_completed_at:
        lines.append(f"Última sync: {status.last_sync_completed_at}")
    if status.last_sync_error:
        lines.append(f"Error: {status.last_sync_error}")
    return "\n".join(lines)


def _format_briefing_status(status) -> str:
    lines = [
        "Estado Briefing",
        "",
        f"Habilitado: {'sí' if status.enabled else 'no'}",
        f"Timezone: {status.timezone}",
        f"Hora: {status.time}",
        f"Cutoff: {status.late_cutoff}",
        f"Enviado hoy: {'sí' if status.today_sent else 'no'}",
    ]
    if status.last_run:
        lines.append(f"Último run: {status.last_run.status} · {status.last_run.briefing_date}")
    return "\n".join(lines)


async def _format_trello_confirmation_or_done(db: Session, confirmation: PendingConfirmation) -> str:
    del db
    return _format_confirmation(confirmation)


def _format_confirmation(confirmation: PendingConfirmation) -> str:
    payload = json.loads(confirmation.payload_json)
    lines = ["Preparé esta acción Trello:", ""]
    if confirmation.action_type == "trello_create_card":
        lines.extend(
            [
                "Crear card",
                f"Board: {payload['board_alias']}",
                f"Lista: {payload['list_name']}",
                f"Título: {payload['title']}",
            ]
        )
    elif confirmation.action_type in ("trello_move_card", "trello_complete_transition"):
        lines.extend(["Mover card", f"Tarea: {confirmation.summary}", f"Destino: {payload['target_list_name']}"])
    elif confirmation.action_type == "trello_rename_card":
        lines.extend(["Renombrar card", f"Actual: {payload['old_title']}", f"Nuevo: {payload['title']}"])
    elif confirmation.action_type == "trello_update_due":
        lines.extend(["Actualizar deadline", f"Tarea: {confirmation.summary}", f"Nuevo deadline: {payload.get('due_at') or 'sin deadline'}"])
    else:
        lines.append(confirmation.summary or confirmation.action_type)
    lines.extend(["", "Confirmá con:", "confirmar", "", "O cancelá con:", "cancelar"])
    return "\n".join(lines)
