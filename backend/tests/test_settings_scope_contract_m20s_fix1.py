from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.schemas.tasks import TaskCreate
from app.services import auth as auth_service
from app.services import integrations
from app.services import settings_service
from app.services import task_suggestions
from app.services.tasks import create_task


PASSWORD = "correct horse battery"


def _trello_config(*, configured_alias: str, unconfigured_alias: str) -> dict:
    return {
        "enabled": True,
        "write_enabled": False,
        "boards": {
            configured_alias: {
                "alias": configured_alias,
                "name": "Configured board",
                "board_id": "board-configured",
                "enabled": True,
                "states": {},
            },
            unconfigured_alias: {
                "alias": unconfigured_alias,
                "name": "Template only",
                "board_id": "",
                "enabled": True,
                "states": {},
            },
            "DISABLED": {
                "alias": "DISABLED",
                "name": "Disabled board",
                "board_id": "board-disabled",
                "enabled": False,
                "states": {},
            },
        },
    }


def test_available_scopes_are_user_isolated_and_runtime_derived(db_session):
    user_a = auth_service.create_owner(db_session, email="scope-a@example.com", password=PASSWORD)
    user_b = auth_service.create_owner(
        db_session,
        email="scope-b@example.com",
        password=PASSWORD,
        allow_existing=True,
    )
    create_task(
        db_session,
        TaskCreate(title="A scoped task", scope="Client Work", auto_classify=False),
        user_id=user_a.id,
    )
    create_task(
        db_session,
        TaskCreate(title="B private task", scope="Other User Only", auto_classify=False),
        user_id=user_b.id,
    )
    integrations.set_user_integration(
        db_session,
        user_id=user_a.id,
        provider=integrations.PROVIDER_TRELLO,
        config=_trello_config(configured_alias="BOARD_A", unconfigured_alias="TEMPLATE_A"),
    )
    integrations.set_user_integration(
        db_session,
        user_id=user_b.id,
        provider=integrations.PROVIDER_TRELLO,
        config=_trello_config(configured_alias="BOARD_B", unconfigured_alias="TEMPLATE_B"),
    )

    scopes_a = settings_service.available_scopes(db_session, user_id=user_a.id)
    scopes_b = settings_service.available_scopes(db_session, user_id=user_b.id)

    assert scopes_a == ["Inbox", "Personal", "Client Work", "BOARD_A"]
    assert "Other User Only" not in scopes_a
    assert "BOARD_B" not in scopes_a
    assert "TEMPLATE_A" not in scopes_a
    assert "DISABLED" not in scopes_a
    assert "Other User Only" in scopes_b
    assert "BOARD_B" in scopes_b


def test_stale_weekend_selection_is_preserved_but_not_advertised(db_session):
    user = auth_service.create_owner(db_session, email="stale@example.com", password=PASSWORD)
    settings_service.patch_settings(
        db_session,
        {"modes": {"weekend": {"active_scopes": ["Personal", "Archived Focus"]}}},
        user_id=user.id,
    )

    assert settings_service.available_scopes(db_session, user_id=user.id) == ["Inbox", "Personal"]
    assert settings_service.selected_weekend_scopes(db_session, user_id=user.id) == ["Personal", "Archived Focus"]


def test_settings_api_exposes_scope_contract_without_demo_values(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/settings")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["available_scopes"] == ["Inbox", "Personal"]
    assert response.json()["selected_weekend_scopes"] == ["Personal"]
    assert settings_service.schema()["scopes"] == ["Inbox", "Personal"]
    assert response.json()["settings"]["trello"]["boards"] == {}


def test_classification_context_uses_configured_board_name_as_hint(db_session):
    user = auth_service.create_owner(db_session, email="hints@example.com", password=PASSWORD)
    integrations.set_user_integration(
        db_session,
        user_id=user.id,
        provider=integrations.PROVIDER_TRELLO,
        config={
            "enabled": True,
            "write_enabled": False,
            "boards": {
                "CLIENT": {
                    "alias": "CLIENT",
                    "name": "Acme Delivery",
                    "board_id": "board-client",
                    "enabled": True,
                    "states": {},
                }
            },
        },
    )

    context = settings_service.classification_context(db_session, user_id=user.id)

    assert context["existing_scopes"] == ["Inbox", "Personal", "CLIENT"]
    assert context["keyword_hints"]["CLIENT"] == ["CLIENT", "Acme Delivery"]

    task = create_task(
        db_session,
        TaskCreate(title="Local client task", scope="CLIENT", auto_classify=False),
        user_id=user.id,
    )
    suggestion = task_suggestions.suggest_details(db_session, task)
    recommendation = suggestion.suggestions["trello_card_recommendation"].value
    assert recommendation["board_alias"] == "CLIENT"


def test_weekend_filter_uses_persisted_scope_membership_exactly(db_session):
    user = auth_service.create_owner(db_session, email="runtime@example.com", password=PASSWORD)
    saturday = datetime(2026, 7, 4, 10, 0, tzinfo=timezone.utc)
    settings_service.patch_settings(
        db_session,
        {
            "general": {"timezone": "UTC"},
            "modes": {
                "weekend": {
                    "enabled": True,
                    "active_days": ["saturday"],
                    "active_scopes": ["Client Work"],
                }
            },
        },
        user_id=user.id,
    )

    assert settings_service.weekend_scope_allowed(
        db_session,
        "Client Work",
        now=saturday,
        user_id=user.id,
    ) is True
    assert settings_service.weekend_scope_allowed(
        db_session,
        "client work",
        now=saturday,
        user_id=user.id,
    ) is False
    assert settings_service.weekend_scope_allowed(
        db_session,
        "Personal",
        now=saturday,
        user_id=user.id,
    ) is False
