import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError
from sqlalchemy import select

from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.auth import User
from app.models.integrations import IntegrationSecret, UserIntegration
from app.schemas.llm import OpenAITaskSuggestion, OpenAITaskSuggestionInput
from app.services import diagnostics, llm
from app.services.time import utc_now_iso


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


class FakeResponses:
    def __init__(self, *, parsed=None, error: Exception | None = None):
        self.parsed = parsed
        self.error = error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(output_parsed=self.parsed)


class FakeClient:
    def __init__(self, responses: FakeResponses):
        self.responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _factory(responses: FakeResponses, captured: dict):
    def create(**kwargs):
        captured.update(kwargs)
        return FakeClient(responses)

    return create


def _suggestion() -> OpenAITaskSuggestion:
    return OpenAITaskSuggestion(
        priority_label="medium_high",
        impact_score=4,
        urgency_score=3,
        effort_bucket="deep",
        estimated_minutes=120,
        context_bucket="deep_work",
        reasoning_summary="La tarea requiere trabajo concentrado.",
    )


def _request() -> OpenAITaskSuggestionInput:
    return OpenAITaskSuggestionInput(
        title="Preparar integración",
        scope="Client Work",
        current={},
        missing_fields=["priority_label", "impact_score"],
        context=None,
        allowed_scopes=["Inbox", "Client Work"],
    )


def _status_ready(db, monkeypatch, user: User, api_key: str = "sk-test-user-a-secret-value"):
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode())
    llm.save_openai_credentials(db, user_id=user.id, api_key=api_key)
    llm._record_observation(db, user.id, validated=True, success=True)
    llm.update_openai_settings(db, user_id=user.id, enabled=True)


def test_credentials_are_encrypted_redacted_user_scoped_and_revocable(db_session, monkeypatch):
    user_a = _user(db_session, "a@example.test")
    user_b = _user(db_session, "b@example.test")
    plaintext = "sk-test-user-a-super-secret-value"
    _status_ready(db_session, monkeypatch, user_a, plaintext)

    secret = db_session.scalar(select(IntegrationSecret).where(IntegrationSecret.user_id == user_a.id))
    serialized_db = json.dumps({column.name: getattr(secret, column.name) for column in secret.__table__.columns})
    assert plaintext not in serialized_db
    assert llm.openai_status(db_session, user_a.id).api_key_hint == "sk-...lue"
    assert llm.openai_status(db_session, user_b.id).api_key_configured is False
    assert llm.get_llm_provider(db_session, user_b.id) is None

    revoked = llm.revoke_openai_credentials(db_session, user_id=user_a.id)
    db_session.refresh(secret)
    assert revoked.api_key_configured is False
    assert revoked.user_enabled is False
    assert secret.ciphertext == ""
    assert secret.redacted_hint is None


def test_missing_encryption_key_blocks_save_without_persisting_plaintext(db_session):
    user = _user(db_session, "a@example.test")

    with pytest.raises(llm.secret_store.SecretStoreUnavailable):
        llm.save_openai_credentials(db_session, user_id=user.id, api_key="sk-test-never-persisted-value")

    assert db_session.scalar(select(IntegrationSecret)) is None


def test_structured_request_uses_official_privacy_and_retry_contract():
    responses = FakeResponses(parsed=_suggestion())
    captured = {}
    provider = llm.OpenAIProvider(
        api_key="sk-test-only",
        model="gpt-5.6-sol",
        client_factory=_factory(responses, captured),
        timeout_seconds=21,
        max_retries=1,
    )

    result = provider.suggest_task_details(_request())

    assert result.impact_score == 4
    assert captured == {"api_key": "sk-test-only", "timeout": 21, "max_retries": 1}
    call = responses.calls[0]
    assert call["store"] is False
    assert "tools" not in call
    assert call["text_format"] is OpenAITaskSuggestion
    assert "previous_response_id" not in call
    assert "sk-test-only" not in json.dumps(call, default=str)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            AuthenticationError(
                "raw auth detail",
                response=httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
                body={"error": "secret"},
            ),
            "API key fue rechazada",
        ),
        (
            RateLimitError(
                "raw rate detail",
                response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
                body={"error": "secret"},
            ),
            "límite temporal",
        ),
        (
            APITimeoutError(httpx.Request("POST", "https://api.openai.com/v1/responses")),
            "tiempo permitido",
        ),
        (
            APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
            "conectar",
        ),
    ],
)
def test_provider_errors_are_controlled(error, message):
    provider = llm.OpenAIProvider(
        api_key="sk-test-only",
        model="gpt-5.6-sol",
        client_factory=_factory(FakeResponses(error=error), {}),
    )

    with pytest.raises(llm.LLMError, match=message) as caught:
        provider.suggest_task_details(_request())

    assert "secret" not in str(caught.value)
    assert "https://" not in str(caught.value)


def test_invalid_structured_output_is_controlled():
    provider = llm.OpenAIProvider(
        api_key="sk-test-only",
        model="gpt-5.6-sol",
        client_factory=_factory(FakeResponses(parsed={"unexpected": True}), {}),
    )

    with pytest.raises(llm.LLMError, match="estructurada inválida"):
        provider.suggest_task_details(_request())


def test_validation_success_allows_enable_and_disable_preserves_validation(db_session, monkeypatch):
    user = _user(db_session, "a@example.test")
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode())
    llm.save_openai_credentials(db_session, user_id=user.id, api_key="sk-test-user-a-secret-value")
    monkeypatch.setattr(llm, "_provider_for_user", lambda *_args, **_kwargs: SimpleNamespace(validate=lambda: True))

    validated = llm.validate_llm(db_session, user.id)
    enabled = llm.update_openai_settings(db_session, user_id=user.id, enabled=True)
    disabled = llm.update_openai_settings(db_session, user_id=user.id, enabled=False)

    assert validated.status.validation_status == "ready"
    assert enabled.safe_to_use is True
    assert disabled.status == "disabled"
    assert disabled.validation_status == "ready"


def test_status_api_never_returns_plaintext_key(db_session, monkeypatch):
    user = llm.auth_service.get_or_create_single_owner_for_backfill(db_session)
    plaintext = "sk-test-api-response-secret-value"
    _status_ready(db_session, monkeypatch, user, plaintext)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/integrations/openai/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert plaintext not in response.text
    assert response.json()["provider"] == "openai"
    assert response.json()["safe_to_use"] is True


def test_invalid_credential_response_does_not_echo_submitted_value(db_session, monkeypatch):
    llm.auth_service.get_or_create_single_owner_for_backfill(db_session)
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode())
    submitted = "short-secret"
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).post(
            "/api/integrations/openai/credentials",
            json={"api_key": submitted},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert submitted not in response.text


def test_unknown_integration_cleanup_is_scoped_and_secret_is_wiped(db_session, monkeypatch):
    user = _user(db_session, "a@example.test")
    other = _user(db_session, "b@example.test")
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode())
    for target in (user, other):
        llm.integrations.set_user_integration(
            db_session,
            user_id=target.id,
            provider="stale-value",
            config={"enabled": True},
        )
        llm.secret_store.store_integration_secret(
            db_session,
            user_id=target.id,
            provider="stale-value",
            secret_type="api_key",
            plaintext="secret-to-remove",
        )

    llm.normalize_ai_integrations(db_session, user_id=user.id)

    assert db_session.scalar(
        select(UserIntegration).where(UserIntegration.user_id == user.id, UserIntegration.provider == "stale-value")
    ) is None
    assert db_session.scalar(
        select(UserIntegration).where(UserIntegration.user_id == other.id, UserIntegration.provider == "stale-value")
    ) is not None
    secret = db_session.scalar(
        select(IntegrationSecret).where(IntegrationSecret.user_id == user.id, IntegrationSecret.provider == "stale-value")
    )
    assert secret.status == "revoked"
    assert secret.ciphertext == ""
    assert secret.redacted_hint is None
    other_secret = db_session.scalar(
        select(IntegrationSecret).where(IntegrationSecret.user_id == other.id, IntegrationSecret.provider == "stale-value")
    )
    assert other_secret.status != "revoked"
    assert other_secret.ciphertext


def test_openai_status_is_read_only_for_unknown_integrations(db_session):
    user = _user(db_session, "status@example.test")
    llm.integrations.set_user_integration(
        db_session,
        user_id=user.id,
        provider="stale-value",
        config={"enabled": True},
    )

    status = llm.openai_status(db_session, user.id)

    assert status.status in {"requires_encryption_key", "requires_api_key"}
    assert db_session.scalar(
        select(UserIntegration).where(UserIntegration.user_id == user.id, UserIntegration.provider == "stale-value")
    ) is not None


def test_diagnostics_exposes_only_sanitized_openai_state(db_session, monkeypatch):
    user = _user(db_session, "a@example.test")
    plaintext = "sk-test-diagnostics-secret-value"
    _status_ready(db_session, monkeypatch, user, plaintext)

    payload = diagnostics.diagnostics_payload(db_session, user_id=user.id)
    rendered = json.dumps(payload)

    assert plaintext not in rendered
    assert "ciphertext" not in rendered
    assert "prompt" not in rendered
    assert '"provider": "openai"' in rendered
