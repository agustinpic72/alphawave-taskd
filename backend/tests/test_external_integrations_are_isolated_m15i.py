from sqlalchemy import create_engine

from app.core import db as db_core
from app.core.config import settings
from app.services import settings_service
from app.services.llm import get_llm_provider
from app.services.reminder_worker import _reminders_ready
from app.services.trello_config import trello_credentials_ready


def test_external_integrations_are_disabled_by_default_in_tests(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trello_board_alpha_id", "")
    monkeypatch.setattr(settings, "trello_board_beta_id", "")
    effective = settings_service.get_settings(db_session)

    assert settings.telegram_enabled is False
    assert settings.telegram_bot_token == ""
    assert settings.telegram_allowed_user_id == ""
    assert settings.trello_enabled is False
    assert settings.trello_api_key == ""
    assert settings.trello_token == ""
    assert settings.alphawave_secret_encryption_key == ""
    assert effective["advanced"]["instance"]["llm_provider"] == "openai"
    assert effective["advanced"]["instance"]["llm_provider_configured"] is False
    assert effective["trello"]["enabled"] is False
    assert all(not board["board_id_configured"] for board in effective["trello"]["boards"].values())


def test_default_test_runtime_cannot_call_external_services(db_session):
    assert _reminders_ready() is False
    assert trello_credentials_ready() is False
    assert get_llm_provider(db_session) is None
    assert settings_service.trello_write_enabled(db_session) is False
    assert settings_service.trello_auto_confirm_writes(db_session) is False
    assert settings_service.llm_enabled(db_session) is False


def test_ensure_schema_is_safe_on_empty_sqlite(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.sqlite'}", future=True)
    monkeypatch.setattr(db_core, "engine", engine)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'empty.sqlite'}")

    db_core.ensure_schema()
