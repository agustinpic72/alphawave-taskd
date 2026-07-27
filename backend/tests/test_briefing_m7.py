import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.briefing import BriefingRun
from app.models.confirmations import PendingConfirmation
from app.models.tasks import Task
from app.schemas.tasks import TaskCreate
from app.services import briefing as briefing_service
from app.services import confirmations
from app.services.startup import run_startup_sequence
from app.services.tasks import create_task
from app.services.telegram_client import CollectingMessenger
from app.services.telegram_parser import parse_command
from app.services.telegram_processor import process_pending_updates, store_raw_update
from app.services.time import utc_now_iso


TZ = ZoneInfo("UTC")


def test_briefing_scheduler_time_cutoff_and_idempotency(db_session, monkeypatch):
    configure_briefing(monkeypatch)
    messenger = CollectingMessenger()

    before = datetime(2026, 7, 2, 12, 59, tzinfo=TZ)
    due = datetime(2026, 7, 2, 13, 0, tzinfo=TZ)
    cutoff = datetime(2026, 7, 3, 19, 0, tzinfo=TZ)

    assert asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=before)) is None
    assert messenger.messages == []

    sent = asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=due))
    assert sent is not None
    assert sent.status == "sent"
    assert len(messenger.messages) == 1

    duplicate = asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=due + timedelta(minutes=5)))
    assert duplicate is None
    assert len(messenger.messages) == 1

    skipped = asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=cutoff, startup=True))
    assert skipped is not None
    assert skipped.status == "skipped"
    assert skipped.reason == "late_cutoff"
    assert len(messenger.messages) == 1

    skipped_again = asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=cutoff + timedelta(minutes=1)))
    assert skipped_again is None


def test_startup_late_briefing_before_cutoff_sends(db_session, monkeypatch):
    configure_briefing(monkeypatch)
    messenger = CollectingMessenger()
    now = datetime(2026, 7, 2, 15, 0, tzinfo=TZ)

    run = asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=now, startup=True))

    assert run is not None
    assert run.status == "sent"
    assert messenger.messages[0][1].startswith("🌅 Briefing diario · 13:00")


def test_manual_briefing_can_send_after_auto(db_session, monkeypatch):
    configure_briefing(monkeypatch)
    messenger = CollectingMessenger()
    now = datetime(2026, 7, 2, 13, 0, tzinfo=TZ)
    asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=now))

    manual = asyncio.run(briefing_service.send_briefing(db_session, messenger, reason="manual", now=now, force=True))

    assert manual.status == "sent"
    assert len(messenger.messages) == 2


def test_startup_order_runs_telegram_reminders_then_briefing():
    calls: list[str] = []

    async def telegram():
        calls.append("telegram")

    async def reminders():
        calls.append("reminders")

    async def briefing():
        calls.append("briefing")

    asyncio.run(run_startup_sequence(telegram, reminders, briefing))

    assert calls == ["telegram", "reminders", "briefing"]


def test_briefing_content_groups_and_filters(db_session, monkeypatch):
    configure_briefing(monkeypatch)
    now = datetime(2026, 7, 2, 13, 0, tzinfo=TZ)
    make_task(db_session, "Trabajo fuerte [FEATURE]_safe(title)", effort_bucket="deep", scope="AW", due_at="2026-07-04T09:00:00+02:00", priority_label="high")
    make_task(db_session, "Quick pendiente", source_type="trello", scope="BETA", trello_state="pending")
    make_task(db_session, "Review feature", source_type="trello", scope="BETA", trello_state="review")
    make_task(db_session, "Llamar proveedor", context_bucket="call", scope="BETA")
    make_task(db_session, "Comprar café", context_bucket="errand", scope="Personal")
    make_task(db_session, "Mención sin asignar", task_kind="attention", source_type="trello", scope="BETA", trello_state="pending")
    make_task(db_session, "revisar endpoint raro", scope="Inbox")
    make_task(
        db_session,
        "Reportes",
        source_type="trello",
        scope="ALPHA",
        trello_state="in_progress",
        last_trello_activity_at=(now - timedelta(days=3)).isoformat(),
    )
    make_task(
        db_session,
        "Proceso reciente",
        source_type="trello",
        scope="ALPHA",
        trello_state="in_progress",
        last_trello_activity_at=(now - timedelta(hours=12)).isoformat(),
    )
    for index in range(4):
        make_task(
            db_session,
            f"Perpetua {index}",
            source_type="trello",
            task_kind="perpetual",
            scope="ALPHA",
            trello_state="perpetual",
            next_checkin_at=(now - timedelta(days=1)).isoformat(),
        )
    make_task(db_session, "Hecha", status="completed", scope="AW")
    make_task(db_session, "Ignorada", source_type="trello", scope="ALPHA", trello_state="ignored")
    make_task(db_session, "Snoozeada", scope="AW", snoozed_until=(now + timedelta(days=1)).isoformat())
    confirmations.create_confirmation(
        db_session,
        source="telegram",
        chat_id="chat",
        action_type="trello_move_card",
        payload={"task_id": "x"},
        summary='Mover "X" a EN REVISION',
    )

    payload = briefing_service.generate_payload(db_session, now=now)

    assert "deep_work" in payload.groups
    assert "calls_contact" in payload.groups
    assert "errands_personal" in payload.groups
    assert "requires_attention" in payload.groups
    assert "inbox_classification" in payload.groups
    assert "stale_in_progress" in payload.groups
    assert "pending_confirmations" in payload.groups
    assert len(payload.groups["perpetual_due"]) == 3
    assert "🌅 Briefing diario · 13:00" in payload.text
    assert "🧠 Trabajo profundo" in payload.text
    assert "⚡ Tareas rápidas" in payload.text
    assert "🔎 Revisión / seguimiento" in payload.text
    assert "📞 Llamadas / contacto" in payload.text
    assert "🏃 Errands / personal" in payload.text
    assert "👀 Requiere atención" in payload.text
    assert "📥 Inbox para clasificar" in payload.text
    assert "⏳ EN PROCESO estancadas" in payload.text
    assert "♾️ Perpetuas vencidas" in payload.text
    assert "✅ Confirmaciones pendientes" in payload.text
    assert "1. Trabajo fuerte [FEATURE]_safe(title)" in payload.text
    assert "   AW · vence 04 jul · alta" in payload.text
    assert "   Motivo: tiene deadline visible" in payload.text
    assert "BETA · pendiente" in payload.text
    assert "BETA · en revisión" in payload.text
    assert "ALPHA · en proceso" in payload.text
    assert "ALPHA · perpetua" in payload.text
    assert "Proceso reciente" not in payload.text
    assert "Hecha" not in payload.text
    assert "Ignorada" not in payload.text
    assert "Snoozeada" not in payload.text


def test_briefing_formats_relative_due_dates(db_session, monkeypatch):
    configure_briefing(monkeypatch)
    now = datetime(2026, 7, 2, 13, 0, tzinfo=TZ)
    make_task(db_session, "Vence hoy", effort_bucket="deep", scope="AW", due_at="2026-07-02T18:00:00+02:00")
    make_task(db_session, "Vence mañana", effort_bucket="deep", scope="AW", due_at="2026-07-03T09:00:00+02:00")
    make_task(db_session, "Vencida", effort_bucket="deep", scope="AW", due_at="2026-06-30T09:00:00+02:00")

    payload = briefing_service.generate_payload(db_session, now=now)

    assert "AW · vence hoy" in payload.text
    assert "AW · vence mañana" in payload.text
    assert "AW · vencida desde 30 jun" in payload.text


def test_send_briefing_saves_snapshot_and_inbox_correction_uses_it(db_session, monkeypatch):
    configure_briefing(monkeypatch)
    task = create_task(db_session, TaskCreate(title="sin contexto raro", auto_classify=False))
    messenger = CollectingMessenger()

    asyncio.run(briefing_service.send_briefing(db_session, messenger, chat_id="chat", reason="manual", force=True))
    store_raw_update(db_session, raw_update(1, "allowed", "chat", "la 1 es Personal"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    db_session.refresh(task)
    assert task.scope == "Personal"


def test_perpetual_no_news_updates_next_checkin(db_session, monkeypatch):
    configure_briefing(monkeypatch)
    fixed = datetime(2026, 7, 2, 13, 0, tzinfo=TZ)

    def fake_local_now(now):
        if now is None:
            return fixed
        return now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)

    monkeypatch.setattr(briefing_service, "_local_now", fake_local_now)
    task = make_task(
        db_session,
        "Reporte Semanal",
        source_type="trello",
        task_kind="perpetual",
        scope="ALPHA",
        trello_state="perpetual",
        next_checkin_at=(fixed - timedelta(days=1)).isoformat(),
    )
    messenger = CollectingMessenger()
    asyncio.run(briefing_service.send_briefing(db_session, messenger, chat_id="chat", reason="manual", now=fixed, force=True))

    store_raw_update(db_session, raw_update(1, "allowed", "chat", "sin novedades, preguntame en 3 días"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    db_session.refresh(task)
    assert task.next_checkin_at.startswith("2026-07-05T09:00:00")
    assert json.loads(task.metadata_json)["last_user_update"].startswith("sin novedades")


def test_perpetual_no_news_asks_for_clarification_when_ambiguous(db_session, monkeypatch):
    configure_briefing(monkeypatch)
    now = datetime(2026, 7, 2, 13, 0, tzinfo=TZ)
    for title in ("Perpetua A", "Perpetua B"):
        make_task(
            db_session,
            title,
            source_type="trello",
            task_kind="perpetual",
            scope="ALPHA",
            trello_state="perpetual",
            next_checkin_at=(now - timedelta(days=1)).isoformat(),
        )
    messenger = CollectingMessenger()
    asyncio.run(briefing_service.send_briefing(db_session, messenger, chat_id="chat", reason="manual", now=now, force=True))

    store_raw_update(db_session, raw_update(1, "allowed", "chat", "sin novedades"))
    asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert "Hay más de una perpetua reciente" in messenger.messages[-1][1]


def test_briefing_telegram_commands_and_allowlist(db_session, monkeypatch):
    configure_briefing(monkeypatch)
    assert parse_command("briefing").action == "briefing"
    assert parse_command("estado briefing").action == "briefing_status"
    assert parse_command("sin novedades").action == "perpetual_no_news"
    assert parse_command("preguntame en una semana").payload["days"] == 7

    store_raw_update(db_session, raw_update(1, "bad", "chat", "briefing"))
    store_raw_update(db_session, raw_update(2, "allowed", "chat", "estado briefing"))
    messenger = CollectingMessenger()
    result = asyncio.run(process_pending_updates(db_session, messenger, allowed_user_id="allowed"))

    assert result.ignored == 1
    assert result.processed == 1
    assert "Briefing" in messenger.messages[-1][1]


def test_briefing_api_endpoints(db_session, monkeypatch):
    configure_briefing(monkeypatch)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        status = client.get("/api/briefing/status")
        preview = client.post("/api/briefing/generate", json={"manual": True})
        runs = client.get("/api/briefing/runs")
    finally:
        app.dependency_overrides.clear()

    assert status.status_code == 200
    assert status.json()["time"] == "13:00"
    assert preview.status_code == 200
    assert preview.json()["text"].startswith("🌅 Briefing diario · 13:00")
    assert runs.status_code == 200


def test_briefing_send_test_endpoint_uses_service(db_session, monkeypatch):
    configure_briefing(monkeypatch)

    async def fake_send(db, messenger, **kwargs):
        return BriefingRun(
            id="run-1",
            briefing_date="2026-07-02",
            scheduled_for="2026-07-02T13:00:00+02:00",
            sent_at="2026-07-02T11:00:00+00:00",
            status="sent",
            reason=kwargs.get("reason"),
            channel="telegram",
            payload_json=None,
            created_at="2026-07-02T11:00:00+00:00",
            updated_at="2026-07-02T11:00:00+00:00",
        )

    monkeypatch.setattr(briefing_service, "send_briefing", fake_send)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.post("/api/briefing/send-test")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "sent", "id": "run-1"}


def configure_briefing(monkeypatch):
    monkeypatch.setattr(settings, "daily_briefing_enabled", True)
    monkeypatch.setattr(settings, "daily_briefing_time", "13:00")
    monkeypatch.setattr(settings, "daily_briefing_late_cutoff", "19:00")
    monkeypatch.setattr(settings, "daily_briefing_timezone", "UTC")
    monkeypatch.setattr(settings, "daily_briefing_send_on_startup", True)
    monkeypatch.setattr(settings, "daily_briefing_max_deep_work", 3)
    monkeypatch.setattr(settings, "daily_briefing_max_quick_tasks", 5)
    monkeypatch.setattr(settings, "daily_briefing_max_reviews", 5)
    monkeypatch.setattr(settings, "daily_briefing_max_attention", 5)
    monkeypatch.setattr(settings, "daily_briefing_max_inbox", 5)
    monkeypatch.setattr(settings, "daily_briefing_max_perpetuals", 3)
    monkeypatch.setattr(settings, "checkins_enabled", True)
    monkeypatch.setattr(settings, "in_progress_stale_days", 2)
    monkeypatch.setattr(settings, "perpetual_max_per_day", 3)
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "chat")


def make_task(
    db_session,
    title,
    *,
    status="active",
    source_type="local",
    task_kind="normal",
    scope="Inbox",
    trello_state=None,
    effort_bucket=None,
    context_bucket=None,
    last_trello_activity_at=None,
    next_checkin_at=None,
    snoozed_until=None,
    due_at=None,
    priority_label=None,
):
    now = utc_now_iso()
    task = Task(
        id=f"{title}-{len(db_session.query(Task).all())}",
        title=title,
        status=status,
        task_kind=task_kind,
        source_type=source_type,
        source_id=title if source_type == "trello" else None,
        source_url="https://trello.test" if source_type == "trello" else None,
        scope=scope,
        origin_label=scope if source_type == "trello" else None,
        manual_order=len(db_session.query(Task).all()) + 1,
        trello_board_id="alpha-board" if source_type == "trello" else None,
        trello_board_name="Project Alpha" if source_type == "trello" else None,
        trello_list_id="list" if source_type == "trello" else None,
        trello_list_name="EN PROCESO" if trello_state == "in_progress" else "TAREAS",
        trello_state=trello_state,
        effort_bucket=effort_bucket,
        context_bucket=context_bucket,
        due_at=due_at,
        priority_label=priority_label,
        last_trello_activity_at=last_trello_activity_at,
        next_checkin_at=next_checkin_at,
        snoozed_until=snoozed_until,
        first_seen_at=now,
        last_touched_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


def raw_update(update_id: int, user_id: str, chat_id: str, text: str):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 100,
            "from": {"id": user_id},
            "chat": {"id": chat_id},
            "text": text,
        },
    }
