import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.db import Base
from app.models import (
    AppState,
    BriefingRun,
    PendingConfirmation,
    Reminder,
    Task,
    TaskEvent,
    TelegramSnapshot,
    TelegramUpdate,
    TrelloSyncRun,
)


@pytest.fixture(autouse=True)
def isolate_external_integrations(monkeypatch):
    monkeypatch.setattr(settings, "app_timezone", "UTC")
    monkeypatch.setattr(settings, "daily_briefing_timezone", "UTC")
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", "")
    monkeypatch.setattr(settings, "openai_timeout_seconds", 25)
    monkeypatch.setattr(settings, "telegram_enabled", False)
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "telegram_allowed_user_id", "")
    monkeypatch.setattr(settings, "trello_enabled", False)
    monkeypatch.setattr(settings, "trello_api_key", "")
    monkeypatch.setattr(settings, "trello_token", "")
    monkeypatch.setattr(settings, "trello_member_id", "")
    monkeypatch.setattr(settings, "trello_write_enabled", False)


@pytest.fixture()
def db_session(tmp_path):
    db_path = tmp_path / "test.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with TestingSession() as session:
        yield session
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def minimal_app_db():
    def create(path):
        engine = create_engine(f"sqlite:///{path}", future=True)
        Base.metadata.create_all(bind=engine)
        engine.dispose()
        return path

    return create
