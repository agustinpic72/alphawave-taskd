from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol, TypeVar

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.integrations import IntegrationSecret, UserIntegration
from app.schemas.llm import (
    CommandIntent,
    CommandIntentRequest,
    LLMStatus,
    LLMValidateResponse,
    OpenAITaskSuggestion,
    OpenAITaskSuggestionInput,
    OpenAIValidationProbe,
)
from app.services import auth as auth_service
from app.services import integrations, openai_models, secret_store
from app.services.openai_models import DEFAULT_OPENAI_MODEL, PROVIDER_OPENAI, SECRET_API_KEY
from app.services.time import utc_now_iso


logger = logging.getLogger(__name__)
ALLOWED_INTEGRATION_PROVIDERS = {PROVIDER_OPENAI, integrations.PROVIDER_TELEGRAM, integrations.PROVIDER_TRELLO}
PROMPT_VERSION = "task-ai-v1"
SCHEMA_VERSION = "task-ai-schema-v1"
MAX_RETRIES = 1
DEFAULT_TIMEOUT_SECONDS = 25.0
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class TaskAIProvider(Protocol):
    def parse_command_intent(self, request: CommandIntentRequest) -> CommandIntent: ...

    def suggest_task_details(self, request: OpenAITaskSuggestionInput) -> OpenAITaskSuggestion: ...

    def validate(self) -> OpenAIValidationProbe: ...


class OpenAIProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client_factory: Any = OpenAI,
        timeout_seconds: float | None = None,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._api_key = api_key
        self.model = _validated_model(model)
        self._client_factory = client_factory
        self._timeout_seconds = timeout_seconds or min(
            max(float(settings.openai_timeout_seconds or DEFAULT_TIMEOUT_SECONDS), 1.0),
            30.0,
        )
        self._max_retries = min(max(int(max_retries), 0), 2)

    def parse_command_intent(self, request: CommandIntentRequest) -> CommandIntent:
        return self._parse(
            schema=CommandIntent,
            operation="parse_command_intent",
            system=(
                "Interpretá el comando usando únicamente allowed_intents. Ante duda o una acción destructiva, "
                "devolvé unknown_or_needs_clarification. No propongas escrituras externas."
            ),
            payload=request.model_dump(),
        )

    def suggest_task_details(self, request: OpenAITaskSuggestionInput) -> OpenAITaskSuggestion:
        return self._parse(
            schema=OpenAITaskSuggestion,
            operation="suggest_task_details",
            system=(
                "Sugerí metadatos opcionales para una tarea. No inventes fechas, dependencias ni requisitos. "
                "Usá null sin evidencia. Respondé en el idioma indicado, sin markdown. No incluyas IDs externos, "
                "writes externos, un score final ni un orden global. El usuario revisará cada campo."
            ),
            payload=request.model_dump(),
        )

    def validate(self) -> OpenAIValidationProbe:
        return self._parse(
            schema=OpenAIValidationProbe,
            operation="validate",
            system="Respondé únicamente con el objeto estructurado solicitado.",
            payload={"check": "Return ok=true."},
        )

    def _parse(self, *, schema: type[T], operation: str, system: str, payload: dict[str, Any]) -> T:
        request_payload = {
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "provider": PROVIDER_OPENAI,
            "model": self.model,
            "input": sanitize_llm_input(payload),
        }
        try:
            with self._client_factory(
                api_key=self._api_key,
                timeout=self._timeout_seconds,
                max_retries=self._max_retries,
            ) as client:
                response = client.responses.parse(
                    model=self.model,
                    input=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False, separators=(",", ":"))},
                    ],
                    text_format=schema,
                    store=False,
                )
        except AuthenticationError as exc:
            raise _provider_error("authentication", "La API key fue rechazada por OpenAI.", exc)
        except RateLimitError as exc:
            raise _provider_error("rate_limit", "OpenAI alcanzó un límite temporal. Se usó el fallback heurístico.", exc)
        except APITimeoutError as exc:
            raise _provider_error("timeout", "OpenAI no respondió dentro del tiempo permitido.", exc)
        except APIConnectionError as exc:
            raise _provider_error("connection", "No se pudo conectar con OpenAI.", exc)
        except NotFoundError as exc:
            raise _provider_error("model_unavailable", "El modelo configurado no está disponible para esta API key.", exc)
        except BadRequestError as exc:
            raise _provider_error("invalid_request", "OpenAI rechazó la solicitud estructurada.", exc)
        except APIStatusError as exc:
            raise _provider_error("api_error", "OpenAI devolvió un error temporal.", exc)
        except (ValidationError, ValueError, TypeError, AttributeError) as exc:
            raise _provider_error("structured_output", "OpenAI devolvió una respuesta estructurada inválida.", exc)
        except Exception as exc:  # noqa: BLE001 - sanitized provider boundary.
            raise _provider_error("unknown", "No se pudo completar la solicitud a OpenAI.", exc)
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise _provider_error("structured_output", "OpenAI no devolvió una respuesta estructurada utilizable.")
        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise _provider_error("structured_output", "OpenAI devolvió una respuesta estructurada inválida.", exc)


def normalize_ai_integrations(db: Session, *, user_id: str) -> None:
    changed = False
    integrations_query = select(UserIntegration).where(
        UserIntegration.user_id == user_id,
        UserIntegration.provider.notin_(ALLOWED_INTEGRATION_PROVIDERS),
    )
    for row in db.scalars(integrations_query).all():
        db.delete(row)
        changed = True
    now = utc_now_iso()
    secrets_query = select(IntegrationSecret).where(
        IntegrationSecret.user_id == user_id,
        IntegrationSecret.provider.notin_(ALLOWED_INTEGRATION_PROVIDERS),
    )
    for row in db.scalars(secrets_query).all():
        row.status = secret_store.STATUS_REVOKED
        row.ciphertext = ""
        row.redacted_hint = None
        row.updated_at = now
        row.revoked_at = now
        db.add(row)
        changed = True
    if changed:
        db.commit()


def openai_status(db: Session, user_id: str | None) -> LLMStatus:
    resolved_user_id = user_id or auth_service.single_owner_user_id(db)
    row = integrations.get_user_integration(db, resolved_user_id, PROVIDER_OPENAI)
    config = _integration_config(row)
    user_enabled = bool(config.get("enabled", False))
    model = _model_from_config(config)
    model_supported = openai_models.is_supported_task_model(model)
    model_available = openai_models.cached_model_availability(resolved_user_id, model)
    validated_model = _safe_optional_model(config.get("validated_model"))
    secret_store_available = secret_store.is_secret_store_available()
    api_key_configured = bool(
        secret_store_available
        and secret_store.has_integration_secret(
            db,
            user_id=resolved_user_id,
            provider=PROVIDER_OPENAI,
            secret_type=SECRET_API_KEY,
        )
    )
    api_key_hint = (
        secret_store.get_integration_secret_hint(
            db,
            user_id=resolved_user_id,
            provider=PROVIDER_OPENAI,
            secret_type=SECRET_API_KEY,
        )
        if api_key_configured
        else None
    )
    stored_validation_status = str(config.get("validation_status") or "not_validated")
    if stored_validation_status not in {"not_validated", "ready", "error"}:
        stored_validation_status = "not_validated"
    last_error = sanitize_llm_text(config.get("last_error_sanitized")) or None

    if not secret_store_available:
        status = "requires_encryption_key"
        reason = "Falta una clave de cifrado válida en esta instalación."
    elif not api_key_configured:
        status = "requires_api_key"
        reason = "Ingresá y guardá una API key de OpenAI."
    elif not model_supported:
        status = "not_validated"
        reason = "El modelo configurado ya no es compatible con sugerencias de tareas."
    elif model_available is False:
        status = "not_validated"
        reason = "El modelo configurado ya no está disponible para esta API key."
    elif stored_validation_status == "error":
        status = "error"
        reason = last_error or "La última validación de OpenAI falló."
    elif stored_validation_status != "ready":
        status = "not_validated"
        reason = "La API key está guardada pero todavía no fue validada."
    elif not user_enabled:
        status = "disabled"
        reason = "OpenAI está validada y deshabilitada para este usuario."
    else:
        status = "ready"
        reason = "OpenAI está validada y lista para generar sugerencias."

    safe_to_use = bool(
        user_enabled
        and secret_store_available
        and api_key_configured
        and model_supported
        and model_available is not False
        and validated_model == model
        and status == "ready"
    )
    return LLMStatus(
        enabled=user_enabled,
        user_enabled=user_enabled,
        status=status,
        api_key_configured=api_key_configured,
        api_key_hint=api_key_hint,
        model=model,
        model_configured=bool(row and model),
        model_supported=model_supported,
        model_available=model_available,
        validated_model=validated_model,
        validation_status=stored_validation_status,
        secret_store_available=secret_store_available,
        last_validated_at=config.get("last_validated_at"),
        last_success_at=config.get("last_success_at"),
        last_error_at=config.get("last_error_at"),
        last_error=last_error,
        safe_to_use=safe_to_use,
        reason=reason,
    )


def llm_status(db: Session, user_id: str | None = None) -> LLMStatus:
    return openai_status(db, user_id)


def save_openai_credentials(db: Session, *, user_id: str, api_key: str) -> LLMStatus:
    if not secret_store.is_secret_store_available():
        raise secret_store.SecretStoreUnavailable("Falta ALPHAWAVE_SECRET_ENCRYPTION_KEY.")
    normalize_ai_integrations(db, user_id=user_id)
    validated_api_key = _validated_api_key(api_key)
    secret_store.store_integration_secret(
        db,
        user_id=user_id,
        provider=PROVIDER_OPENAI,
        secret_type=SECRET_API_KEY,
        plaintext=validated_api_key,
    )
    openai_models.invalidate_model_catalog(user_id)
    config = _config_for_user(db, user_id)
    config.update(
        {
            "model": _model_from_config(config),
            "validation_status": "not_validated",
            "validated_model": None,
            "last_validated_at": None,
            "last_error_at": None,
            "last_error_sanitized": None,
        }
    )
    _save_config(db, user_id, config)
    return openai_status(db, user_id)


def update_openai_settings(
    db: Session,
    *,
    user_id: str,
    enabled: bool | None = None,
    model: str | None = None,
) -> LLMStatus:
    config = _config_for_user(db, user_id)
    if model is not None:
        validated = _validated_model(model)
        if validated != _model_from_config(config):
            if not openai_models.is_supported_task_model(validated):
                raise ValueError("El modelo elegido no es compatible con sugerencias de tareas.")
            if not openai_models.model_is_in_cached_catalog(user_id, validated):
                raise ValueError("Actualizá el catálogo y elegí un modelo disponible para esta API key.")
            config["model"] = validated
            config["validation_status"] = "not_validated"
            config["validated_model"] = None
            config["last_validated_at"] = None
            config["last_error_at"] = None
            config["last_error_sanitized"] = None
    if enabled is not None:
        if enabled:
            current = openai_status(db, user_id)
            if not current.secret_store_available:
                raise ValueError("Falta una clave de cifrado válida en esta instalación.")
            if not current.api_key_configured:
                raise ValueError("Guardá una API key antes de habilitar OpenAI.")
            if current.validation_status != "ready":
                raise ValueError("Validá la conexión antes de habilitar OpenAI.")
            if current.validated_model != current.model or current.model_available is False:
                raise ValueError("Elegí un modelo disponible y validalo antes de habilitar OpenAI.")
        config["enabled"] = bool(enabled)
    _save_config(db, user_id, config)
    return openai_status(db, user_id)


def revoke_openai_credentials(db: Session, *, user_id: str) -> LLMStatus:
    openai_models.invalidate_model_catalog(user_id)
    secret_store.revoke_integration_secret(
        db,
        user_id=user_id,
        provider=PROVIDER_OPENAI,
        secret_type=SECRET_API_KEY,
    )
    secret = db.scalar(
        select(IntegrationSecret).where(
            IntegrationSecret.user_id == user_id,
            IntegrationSecret.provider == PROVIDER_OPENAI,
            IntegrationSecret.secret_type == SECRET_API_KEY,
        )
    )
    if secret is not None:
        secret.ciphertext = ""
        secret.redacted_hint = None
        db.add(secret)
        db.commit()
    config = _config_for_user(db, user_id)
    config.update(
        {
            "enabled": False,
            "validation_status": "not_validated",
            "validated_model": None,
            "last_validated_at": None,
            "last_success_at": None,
            "last_error_at": None,
            "last_error_sanitized": None,
        }
    )
    _save_config(db, user_id, config)
    return openai_status(db, user_id)


def validate_llm(db: Session, user_id: str | None = None) -> LLMValidateResponse:
    resolved_user_id = user_id or auth_service.single_owner_user_id(db)
    status = openai_status(db, resolved_user_id)
    if not status.secret_store_available or not status.api_key_configured:
        return LLMValidateResponse(status=status, checked=False)
    if not status.model_supported:
        return LLMValidateResponse(status=status, checked=False)
    try:
        provider = _provider_for_user(db, resolved_user_id, require_ready=False)
        provider.validate()
    except LLMError as exc:
        record_llm_error(db, str(exc), user_id=resolved_user_id)
        return LLMValidateResponse(status=openai_status(db, resolved_user_id))
    _record_observation(db, resolved_user_id, validated=True, success=True)
    return LLMValidateResponse(status=openai_status(db, resolved_user_id))


def get_llm_provider(db: Session | None = None, user_id: str | None = None) -> TaskAIProvider | None:
    if db is None:
        return None
    status = openai_status(db, user_id)
    if not status.safe_to_use:
        return None
    resolved_user_id = user_id or auth_service.single_owner_user_id(db)
    return _provider_for_user(db, resolved_user_id, require_ready=True)


def record_llm_success(db: Session | None, *, user_id: str | None = None) -> None:
    if db is None:
        return
    resolved_user_id = user_id or auth_service.single_owner_user_id(db)
    _record_observation(db, resolved_user_id, success=True)


def record_llm_error(db: Session | None, error: str, *, user_id: str | None = None) -> None:
    if db is None:
        return
    resolved_user_id = user_id or auth_service.single_owner_user_id(db)
    _record_observation(db, resolved_user_id, error=error)


def sanitize_llm_input(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return sanitize_llm_input(value.model_dump())
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            if any(word in lowered for word in ("token", "api_key", "apikey", "secret", "password", "database_url", "chat_id")):
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = sanitize_llm_input(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_llm_input(item) for item in value]
    if isinstance(value, str):
        return sanitize_llm_text(value, include_configured_secrets=False)
    return value


def sanitize_llm_text(value: Any, *, include_configured_secrets: bool = True) -> str:
    text = str(value or "")
    secrets = [settings.telegram_bot_token, settings.trello_api_key, settings.trello_token, settings.database_url]
    if include_configured_secrets:
        secrets.append(settings.alphawave_secret_encryption_key)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(token|api[_-]?key|secret|password)=([^\s&]+)", r"\1=[redacted]", text)
    text = re.sub(r"(?i)(telegram|trello|openai)[-_a-z0-9]{16,}", "[redacted]", text)
    return text[:500]


def _provider_for_user(db: Session, user_id: str, *, require_ready: bool) -> OpenAIProvider:
    status = openai_status(db, user_id)
    if require_ready and not status.safe_to_use:
        raise LLMError(status.reason)
    try:
        api_key = secret_store.get_integration_secret(
            db,
            user_id=user_id,
            provider=PROVIDER_OPENAI,
            secret_type=SECRET_API_KEY,
        )
    except (secret_store.SecretStoreUnavailable, secret_store.SecretDecryptionError) as exc:
        raise LLMError("No se pudo acceder a la API key cifrada.") from exc
    if not api_key:
        raise LLMError("Falta una API key de OpenAI.")
    return OpenAIProvider(api_key=api_key, model=status.model)


def _record_observation(
    db: Session,
    user_id: str,
    *,
    validated: bool = False,
    success: bool = False,
    error: str | None = None,
) -> None:
    config = _config_for_user(db, user_id)
    now = utc_now_iso()
    if validated:
        config["last_validated_at"] = now
        config["validated_model"] = _model_from_config(config)
    if success:
        if validated:
            config["validation_status"] = "ready"
        config["last_success_at"] = now
        config["last_error_at"] = None
        config["last_error_sanitized"] = None
    if error is not None:
        config["validation_status"] = "error"
        config["validated_model"] = None
        config["last_error_at"] = now
        config["last_error_sanitized"] = sanitize_llm_text(error)
    _save_config(db, user_id, config)


def _config_for_user(db: Session, user_id: str) -> dict[str, Any]:
    return _integration_config(integrations.get_user_integration(db, user_id, PROVIDER_OPENAI))


def _integration_config(row: UserIntegration | None) -> dict[str, Any]:
    if not row:
        return {
            "enabled": False,
            "model": DEFAULT_OPENAI_MODEL,
            "validation_status": "not_validated",
            "validated_model": None,
        }
    try:
        value = json.loads(row.config_json)
    except (TypeError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    model = _safe_model(value.get("model"))
    validation_status = value.get("validation_status") if value.get("validation_status") in {"not_validated", "ready", "error"} else "not_validated"
    validated_model = _safe_optional_model(value.get("validated_model"))
    if validation_status == "ready" and validated_model is None:
        validated_model = model
    return {
        "enabled": bool(value.get("enabled", False)),
        "model": model,
        "validation_status": validation_status,
        "validated_model": validated_model,
        "last_validated_at": value.get("last_validated_at"),
        "last_success_at": value.get("last_success_at"),
        "last_error_at": value.get("last_error_at"),
        "last_error_sanitized": sanitize_llm_text(value.get("last_error_sanitized")) or None,
    }


def _save_config(db: Session, user_id: str, config: dict[str, Any]) -> None:
    integrations.set_user_integration(
        db,
        user_id=user_id,
        provider=PROVIDER_OPENAI,
        config=config,
        status=integrations.STATUS_ACTIVE if config.get("enabled") else integrations.STATUS_DISABLED,
        credentials_source=integrations.CREDENTIALS_USER_ENCRYPTED,
    )


def _model_from_config(config: dict[str, Any]) -> str:
    return _safe_model(config.get("model"))


def _safe_model(value: Any) -> str:
    raw = str(value or DEFAULT_OPENAI_MODEL).strip()
    if not raw or len(raw) > 100 or not re.fullmatch(r"[A-Za-z0-9._:-]+", raw):
        return DEFAULT_OPENAI_MODEL
    return raw


def _safe_optional_model(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw or len(raw) > 100 or not re.fullmatch(r"[A-Za-z0-9._:-]+", raw):
        return None
    return raw


def _validated_model(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 100 or not re.fullmatch(r"[A-Za-z0-9._:-]+", raw):
        raise ValueError("El modelo de OpenAI no es válido.")
    return raw


def _validated_api_key(value: Any) -> str:
    raw = str(value or "").strip()
    if len(raw) < 20 or len(raw) > 500:
        raise ValueError("La API key de OpenAI no tiene un formato válido.")
    return raw


def _provider_error(category: str, message: str, exc: Exception | None = None) -> LLMError:
    logger.warning("ai.openai.request_failed", extra={"category": category})
    return LLMError(message)
