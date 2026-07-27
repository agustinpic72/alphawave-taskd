import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.briefing import BriefingRun
from app.models.telegram import TelegramSnapshot
from app.schemas.tasks import TaskCreate
from app.services import auth as auth_service
from app.services import briefing as briefing_service
from app.services import briefing_worker
from app.services import integrations
from app.services import telegram_processor
from app.services.tasks import create_task
from app.services.telegram_client import CollectingMessenger
from app.services.time import utc_now_iso


PASSWORD = "correct-horse-battery-staple"
NOW = datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc)


def test_briefing_payload_runs_and_sent_state_are_isolated_per_user(db_session, monkeypatch):
    _configure_briefing(monkeypatch)
    user_a, user_b = _users(db_session)
    create_task(
        db_session,
        TaskCreate(title="A private task", scope="Project A", auto_classify=False),
        user_id=user_a.id,
    )
    create_task(
        db_session,
        TaskCreate(title="B private task", scope="Project B", auto_classify=False),
        user_id=user_b.id,
    )
    destinations = {user_a.id: "chat-a", user_b.id: "chat-b"}
    monkeypatch.setattr(
        briefing_service.integrations,
        "get_telegram_destination_for_user",
        lambda _db, user_id: destinations.get(user_id),
    )
    monkeypatch.setattr(
        briefing_service.integrations,
        "telegram_status_for_user",
        lambda _db, _user_id: {"legacy_fallback": False},
    )
    messenger = CollectingMessenger()

    run_a = asyncio.run(briefing_service.send_briefing(db_session, messenger, now=NOW, user_id=user_a.id))

    assert run_a.user_id == user_a.id
    assert messenger.messages[0][0] == "chat-a"
    assert "A private task" in messenger.messages[0][1]
    assert "B private task" not in messenger.messages[0][1]
    assert briefing_service.status(db_session, now=NOW, user_id=user_a.id).today_sent is True
    assert briefing_service.status(db_session, now=NOW, user_id=user_b.id).today_sent is False
    assert [run.id for run in briefing_service.list_runs(db_session, user_id=user_a.id)] == [run_a.id]
    assert briefing_service.list_runs(db_session, user_id=user_b.id) == []

    run_b = asyncio.run(briefing_service.maybe_send_due_briefing(db_session, messenger, now=NOW, user_id=user_b.id))

    assert run_b is not None
    assert run_b.user_id == user_b.id
    assert messenger.messages[1][0] == "chat-b"
    assert "B private task" in messenger.messages[1][1]
    assert "A private task" not in messenger.messages[1][1]


def test_missing_destination_records_failure_but_never_sent(db_session, monkeypatch):
    _configure_briefing(monkeypatch)
    user = auth_service.create_owner(db_session, email="no-destination@example.com", password=PASSWORD)
    create_task(db_session, TaskCreate(title="Unsent private task", auto_classify=False), user_id=user.id)
    monkeypatch.setattr(
        briefing_service.integrations,
        "get_telegram_destination_for_user",
        lambda _db, _user_id: None,
    )
    messenger = CollectingMessenger()

    with pytest.raises(ValueError, match="No hay chat Telegram configurado"):
        asyncio.run(briefing_service.send_briefing(db_session, messenger, now=NOW, user_id=user.id))

    runs = briefing_service.list_runs(db_session, user_id=user.id)
    assert messenger.messages == []
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].sent_at is None
    assert briefing_service.status(db_session, now=NOW, user_id=user.id).today_sent is False


def test_perpetual_checkin_rejects_snapshot_task_owned_by_another_user(db_session):
    user_a, user_b = _users(db_session)
    task_b = create_task(
        db_session,
        TaskCreate(title="B perpetual", scope="Project B", auto_classify=False),
        user_id=user_b.id,
    )
    task_b.task_kind = "perpetual"
    task_b.next_checkin_at = "2026-07-09T09:00:00+00:00"
    db_session.add(
        TelegramSnapshot(
            id="snapshot-a",
            chat_id="chat-a",
            message_id=10,
            snapshot_type="perpetuals",
            task_ids_json=f'["{task_b.id}"]',
            created_at=utc_now_iso(),
        )
    )
    db_session.commit()

    with pytest.raises(ValueError, match="ya no existe"):
        briefing_service.update_perpetual_checkin(
            db_session,
            "chat-a",
            days=3,
            note="no news",
            user_id=user_a.id,
        )

    db_session.refresh(task_b)
    assert task_b.next_checkin_at == "2026-07-09T09:00:00+00:00"


def test_legacy_scope_and_instance_destination_are_rejected_with_multiple_users(db_session, monkeypatch):
    _configure_briefing(monkeypatch)
    user_a, _user_b = _users(db_session)
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "legacy-chat")
    now = utc_now_iso()
    db_session.add(
        BriefingRun(
            id="ambiguous-legacy-run",
            user_id=None,
            briefing_date="2026-07-10",
            scheduled_for=NOW.isoformat(),
            sent_at=now,
            status="sent",
            reason="auto",
            channel="telegram",
            payload_json=None,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    with pytest.raises(ValueError, match="requiere user_id"):
        briefing_service.generate_payload(db_session, now=NOW)
    assert briefing_service.list_runs(db_session, user_id=user_a.id) == []

    messenger = CollectingMessenger()
    with pytest.raises(ValueError, match="No hay chat Telegram configurado"):
        asyncio.run(briefing_service.send_briefing(db_session, messenger, now=NOW, user_id=user_a.id))

    run = db_session.scalar(select(BriefingRun).where(BriefingRun.user_id == user_a.id))
    assert run is not None
    assert run.status == "failed"
    assert messenger.messages == []


def test_briefing_telegram_owner_requires_explicit_link_in_multi_user_mode(db_session, monkeypatch):
    user_a, _user_b = _users(db_session)
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "legacy-chat")
    integrations.link_telegram_chat(db_session, user_id=user_a.id, chat_id="chat-a")

    assert telegram_processor._briefing_user_id(db_session, "chat-a") == user_a.id
    with pytest.raises(ValueError, match="chat vinculado"):
        telegram_processor._briefing_user_id(db_session, "legacy-chat")


def test_scheduler_processes_each_explicit_user(db_session, monkeypatch):
    user_a, user_b = _users(db_session)
    calls: list[tuple[str, bool]] = []

    async def fake_maybe_send(_db, _client, *, startup=False, user_id=None, **_kwargs):
        calls.append((user_id, startup))

    monkeypatch.setattr(briefing_worker, "maybe_send_due_briefing", fake_maybe_send)

    asyncio.run(briefing_worker._process_briefing_users(db_session, object(), startup=True))

    assert calls == [(user_a.id, True), (user_b.id, True)]


def test_briefing_runs_api_never_exposes_another_users_history(db_session, monkeypatch):
    user_a, user_b = _users(db_session)
    now = utc_now_iso()
    for user, run_id in ((user_a, "run-a"), (user_b, "run-b")):
        db_session.add(
            BriefingRun(
                id=run_id,
                user_id=user.id,
                briefing_date="2026-07-10",
                scheduled_for=NOW.isoformat(),
                sent_at=now,
                status="sent",
                reason="manual",
                channel="telegram",
                payload_json=None,
                created_at=now,
                updated_at=now,
            )
        )
    db_session.commit()
    monkeypatch.setattr(settings, "auth_enabled", True)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client_a = TestClient(app)
        client_b = TestClient(app)
        assert client_a.post("/api/auth/login", json={"email": user_a.email, "password": PASSWORD}).status_code == 200
        assert client_b.post("/api/auth/login", json={"email": user_b.email, "password": PASSWORD}).status_code == 200

        runs_a = client_a.get("/api/briefing/runs")
        runs_b = client_b.get("/api/briefing/runs")
    finally:
        app.dependency_overrides.clear()

    assert runs_a.status_code == 200
    assert [run["id"] for run in runs_a.json()["runs"]] == ["run-a"]
    assert [run["id"] for run in runs_b.json()["runs"]] == ["run-b"]


def _users(db_session):
    user_a = auth_service.create_owner(db_session, email="briefing-a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(
        db_session,
        email="briefing-b@example.com",
        password=PASSWORD,
        allow_existing=True,
    )
    return user_a, user_b


def _configure_briefing(monkeypatch):
    monkeypatch.setattr(settings, "daily_briefing_enabled", True)
    monkeypatch.setattr(settings, "daily_briefing_time", "13:00")
    monkeypatch.setattr(settings, "daily_briefing_late_cutoff", "19:00")
    monkeypatch.setattr(settings, "daily_briefing_timezone", "UTC")
    monkeypatch.setattr(settings, "daily_briefing_send_on_startup", True)
