from cryptography.fernet import Fernet
import pytest

from app.core.config import settings
from app.models.integrations import IntegrationSecret
from app.services import auth as auth_service
from app.services import diagnostics, secret_store


PASSWORD = "correct horse battery"


def test_secret_store_roundtrip_and_plaintext_not_stored(db_session, monkeypatch):
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", Fernet.generate_key().decode("utf-8"))
    user = auth_service.create_owner(db_session, email="secret@example.com", password=PASSWORD)

    row = secret_store.store_integration_secret(
        db_session,
        user_id=user.id,
        provider="trello",
        secret_type="token",
        plaintext="abcdef123456",
    )

    stored = db_session.get(IntegrationSecret, row.id)
    assert stored is not None
    assert stored.ciphertext != "abcdef123456"
    assert "abcdef123456" not in stored.ciphertext
    assert stored.redacted_hint == "abc...456"
    assert secret_store.get_integration_secret(db_session, user_id=user.id, provider="trello", secret_type="token") == "abcdef123456"


def test_missing_key_disables_new_secret_storage(db_session, monkeypatch):
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", "")
    user = auth_service.create_owner(db_session, email="missing-key@example.com", password=PASSWORD)

    assert not secret_store.is_secret_store_available()
    with pytest.raises(secret_store.SecretStoreUnavailable):
        secret_store.store_integration_secret(
            db_session,
            user_id=user.id,
            provider="telegram",
            secret_type="chat_id",
            plaintext="123456789",
        )


def test_decrypt_failure_is_controlled_and_diagnostics_redact_sensitive_shapes(db_session, monkeypatch):
    key_a = Fernet.generate_key().decode("utf-8")
    key_b = Fernet.generate_key().decode("utf-8")
    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", key_a)
    user = auth_service.create_owner(db_session, email="decrypt@example.com", password=PASSWORD)
    row = secret_store.store_integration_secret(
        db_session,
        user_id=user.id,
        provider="trello",
        secret_type="api_key",
        plaintext="1234567890abcdef",
    )

    monkeypatch.setattr(settings, "alphawave_secret_encryption_key", key_b)

    with pytest.raises(secret_store.SecretDecryptionError):
        secret_store.get_integration_secret(db_session, user_id=user.id, provider="trello", secret_type="api_key")

    redacted = diagnostics.sanitize_diagnostics(
        {
            "ciphertext": row.ciphertext,
            "code_hash": "hash-secret",
            "token": "token-secret",
            "nested": {"chat_id": "123456789"},
        }
    )
    assert redacted["ciphertext"] == "<redacted>"
    assert redacted["code_hash"] == "<redacted>"
    assert redacted["token"] == "<redacted>"
    assert redacted["nested"]["chat_id"] == "123...789"
