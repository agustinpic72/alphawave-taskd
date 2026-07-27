from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.auth import User
from app.services import llm, openai_models
from app.services.time import utc_now_iso


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    openai_models._clear_model_catalog_cache_for_tests()
    yield
    openai_models._clear_model_catalog_cache_for_tests()


def _user(db, email: str) -> User:
    now = utc_now_iso()
    user = User(
        id=str(uuid4()),
        email=email,
        password_hash="unused",
        display_name=email,
        role="owner",
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    return user


class FakeClient:
    def __init__(self, models=None, error: Exception | None = None):
        self.models = SimpleNamespace(list=self._list)
        self._models = models or []
        self._error = error

    def _list(self):
        if self._error:
            raise self._error
        return SimpleNamespace(data=self._models)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _factory(models=None, error: Exception | None = None, calls: list | None = None):
    def create(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return FakeClient(models, error)

    return create


def _model(model_id: str, created: int = 1):
    return SimpleNamespace(id=model_id, created=created, owned_by="openai")


def _configured_user(db, monkeypatch, email="models@example.test"):
    if not settings.alphawave_secret_encryption_key:
        monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode())
    user = _user(db, email)
    llm.save_openai_credentials(db, user_id=user.id, api_key="sk-test-model-catalog-secret-value")
    return user


def test_catalog_requires_a_user_key_and_never_calls_openai(db_session, monkeypatch):
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode())
    user = _user(db_session, "no-key@example.test")
    calls = []

    catalog = openai_models.list_available_openai_models(
        db_session,
        user.id,
        current_model="gpt-5.6-sol",
        validated_model=None,
        validation_ready=False,
        client_factory=_factory(calls=calls),
    )

    assert catalog.status == "requires_api_key"
    assert calls == []
    assert catalog.models[0].compatibility == "unavailable"
    assert llm.openai_status(db_session, user.id).model_configured is False


def test_models_endpoint_is_server_side_filtered_and_never_returns_key(db_session, monkeypatch):
    user = llm.auth_service.get_or_create_single_owner_for_backfill(db_session)
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode())
    api_key = "sk-test-endpoint-model-catalog-secret-value"
    llm.save_openai_credentials(db_session, user_id=user.id, api_key=api_key)
    calls = []
    monkeypatch.setattr(
        openai_models,
        "OpenAI",
        _factory([_model("gpt-5.6-luna"), _model("text-embedding-3-large")], calls=calls),
    )
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/integrations/openai/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["models"]] == ["gpt-5.6-sol", "gpt-5.6-luna"]
    assert api_key not in response.text
    assert calls[0]["api_key"] == api_key


def test_catalog_filters_snapshots_fine_tunes_and_unrelated_models(db_session, monkeypatch):
    user = _configured_user(db_session, monkeypatch)
    models = [
        _model("gpt-5.6-sol"),
        _model("gpt-5.6-luna"),
        _model("gpt-5-mini"),
        _model("gpt-5.6-sol-2026-07-18"),
        _model("ft:gpt-5-mini:team:custom"),
        _model("text-embedding-3-large"),
        _model("omni-moderation-latest"),
    ]

    catalog = openai_models.list_available_openai_models(
        db_session,
        user.id,
        current_model="gpt-5.6-sol",
        validated_model="gpt-5.6-sol",
        validation_ready=True,
        client_factory=_factory(models),
    )

    assert [item.id for item in catalog.models] == ["gpt-5.6-luna", "gpt-5-mini", "gpt-5.6-sol"]
    assert catalog.recommended_model == "gpt-5.6-luna"
    assert next(item for item in catalog.models if item.id == "gpt-5.6-sol").is_validated is True
    assert all(item.owned_by == "openai" for item in catalog.models)


def test_catalog_cache_is_per_user_and_stale_fallback_preserves_models(db_session, monkeypatch):
    user_a = _configured_user(db_session, monkeypatch, "a@example.test")
    user_b = _configured_user(db_session, monkeypatch, "b@example.test")
    calls = []
    start = datetime(2026, 7, 18, tzinfo=timezone.utc)
    first = openai_models.list_available_openai_models(
        db_session,
        user_a.id,
        current_model="gpt-5.6-sol",
        validated_model=None,
        validation_ready=False,
        client_factory=_factory([_model("gpt-5.6-sol")], calls=calls),
        now_provider=lambda: start,
    )
    cached = openai_models.list_available_openai_models(
        db_session,
        user_a.id,
        current_model="gpt-5.6-sol",
        validated_model=None,
        validation_ready=False,
        client_factory=_factory(error=RuntimeError("must not run"), calls=calls),
        now_provider=lambda: start + timedelta(minutes=10),
    )
    stale = openai_models.list_available_openai_models(
        db_session,
        user_a.id,
        current_model="gpt-5.6-sol",
        validated_model=None,
        validation_ready=False,
        client_factory=_factory(error=RuntimeError("private raw error"), calls=calls),
        now_provider=lambda: start + timedelta(minutes=31),
    )
    other = openai_models.list_available_openai_models(
        db_session,
        user_b.id,
        current_model="gpt-5.6-sol",
        validated_model=None,
        validation_ready=False,
        client_factory=_factory([_model("gpt-5-mini")], calls=calls),
        now_provider=lambda: start,
    )

    assert first.cached is False
    assert cached.cached is True and cached.stale is False
    assert stale.cached is True and stale.stale is True
    assert "private raw error" not in (stale.error or "")
    assert [item.id for item in other.models] == ["gpt-5.6-sol", "gpt-5-mini"]
    assert len(calls) == 3


def test_force_refresh_obeys_debounce_then_fetches_again(db_session, monkeypatch):
    user = _configured_user(db_session, monkeypatch)
    calls = []
    start = datetime(2026, 7, 18, tzinfo=timezone.utc)
    common = {
        "db": db_session,
        "user_id": user.id,
        "current_model": "gpt-5.6-sol",
        "validated_model": None,
        "validation_ready": False,
        "client_factory": _factory([_model("gpt-5.6-sol")], calls=calls),
    }
    openai_models.list_available_openai_models(**common, now_provider=lambda: start)
    debounced = openai_models.list_available_openai_models(
        **common,
        force_refresh=True,
        now_provider=lambda: start + timedelta(seconds=2),
    )
    refreshed = openai_models.list_available_openai_models(
        **common,
        force_refresh=True,
        now_provider=lambda: start + timedelta(seconds=6),
    )

    assert debounced.cached is True
    assert refreshed.cached is False
    assert len(calls) == 2


def test_fetch_failure_without_cache_is_sanitized(db_session, monkeypatch):
    user = _configured_user(db_session, monkeypatch)
    catalog = openai_models.list_available_openai_models(
        db_session,
        user.id,
        current_model="gpt-5.6-sol",
        validated_model=None,
        validation_ready=False,
        client_factory=_factory(error=RuntimeError("sk-private-value raw failure")),
    )

    assert catalog.status == "fetch_error"
    assert catalog.cached is False
    assert "sk-private-value" not in (catalog.error or "")


def test_current_model_is_preserved_when_catalog_no_longer_returns_it(db_session, monkeypatch):
    user = _configured_user(db_session, monkeypatch)
    llm._record_observation(db_session, user.id, validated=True, success=True)
    llm.update_openai_settings(db_session, user_id=user.id, enabled=True)
    catalog = openai_models.list_available_openai_models(
        db_session,
        user.id,
        current_model="gpt-5.6-sol",
        validated_model="gpt-5.6-sol",
        validation_ready=True,
        client_factory=_factory([_model("gpt-5.6-luna")]),
    )

    current = next(item for item in catalog.models if item.id == "gpt-5.6-sol")
    assert current.is_current is True
    assert current.is_validated is False
    assert current.compatibility == "unavailable"
    assert catalog.recommended_model == "gpt-5.6-luna"
    assert llm.openai_status(db_session, user.id).safe_to_use is False


def test_model_change_requires_cached_availability_and_invalidates_validation(db_session, monkeypatch):
    user = _configured_user(db_session, monkeypatch)
    llm._record_observation(db_session, user.id, validated=True, success=True)
    llm.update_openai_settings(db_session, user_id=user.id, enabled=True)
    openai_models.list_available_openai_models(
        db_session,
        user.id,
        current_model="gpt-5.6-sol",
        validated_model="gpt-5.6-sol",
        validation_ready=True,
        client_factory=_factory([_model("gpt-5.6-sol"), _model("gpt-5.6-luna")]),
    )

    changed = llm.update_openai_settings(db_session, user_id=user.id, model="gpt-5.6-luna")

    assert changed.model == "gpt-5.6-luna"
    assert changed.validated_model is None
    assert changed.validation_status == "not_validated"
    assert changed.safe_to_use is False


def test_new_key_invalidates_catalog_and_model_validation(db_session, monkeypatch):
    user = _configured_user(db_session, monkeypatch)
    llm._record_observation(db_session, user.id, validated=True, success=True)
    openai_models.list_available_openai_models(
        db_session,
        user.id,
        current_model="gpt-5.6-sol",
        validated_model="gpt-5.6-sol",
        validation_ready=True,
        client_factory=_factory([_model("gpt-5.6-sol")]),
    )
    assert openai_models.cached_model_availability(user.id, "gpt-5.6-sol") is True

    status = llm.save_openai_credentials(
        db_session,
        user_id=user.id,
        api_key="sk-test-replacement-model-catalog-value",
    )

    assert openai_models.cached_model_availability(user.id, "gpt-5.6-sol") is None
    assert status.validation_status == "not_validated"
    assert status.validated_model is None
    assert status.safe_to_use is False


def test_validation_records_the_exact_selected_model(db_session, monkeypatch):
    user = _configured_user(db_session, monkeypatch)
    monkeypatch.setattr(llm, "_provider_for_user", lambda *_args, **_kwargs: SimpleNamespace(validate=lambda: True))

    response = llm.validate_llm(db_session, user.id)

    assert response.status.validated_model == response.status.model
    assert response.status.validation_status == "ready"


def test_unsupported_selected_model_is_not_validated_or_used(db_session, monkeypatch):
    user = _configured_user(db_session, monkeypatch)
    llm.integrations.set_user_integration(
        db_session,
        user_id=user.id,
        provider=openai_models.PROVIDER_OPENAI,
        config={"enabled": True, "model": "o4-mini", "validation_status": "ready", "validated_model": "o4-mini"},
    )

    response = llm.validate_llm(db_session, user.id)

    assert response.checked is False
    assert response.status.model_supported is False
    assert response.status.safe_to_use is False


def test_suggestion_provider_uses_selected_validated_model(db_session, monkeypatch):
    user = _configured_user(db_session, monkeypatch)
    openai_models.list_available_openai_models(
        db_session,
        user.id,
        current_model="gpt-5.6-sol",
        validated_model=None,
        validation_ready=False,
        client_factory=_factory([_model("gpt-5.6-sol"), _model("gpt-5.6-luna")]),
    )
    llm.update_openai_settings(db_session, user_id=user.id, model="gpt-5.6-luna")
    llm._record_observation(db_session, user.id, validated=True, success=True)
    llm.update_openai_settings(db_session, user_id=user.id, enabled=True)

    provider = llm.get_llm_provider(db_session, user.id)

    assert provider is not None
    assert provider.model == "gpt-5.6-luna"
