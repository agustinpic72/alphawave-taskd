from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, OpenAI, RateLimitError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.llm import OpenAIModelCatalog, OpenAIModelOption
from app.services import secret_store


PROVIDER_OPENAI = "openai"
SECRET_API_KEY = "api_key"
OPENAI_TASK_MODEL_POLICY_VERSION = "2026-07-18.1"
DEFAULT_OPENAI_MODEL = "gpt-5.6-sol"
MODEL_CATALOG_TTL = timedelta(minutes=30)
MODEL_REFRESH_DEBOUNCE = timedelta(seconds=5)
RECOMMENDATION_ORDER = (
    "gpt-5.6-luna",
    "gpt-5.4-mini",
    "gpt-5-mini",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
)
_MODEL_ID = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
_DATED_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_SUPPORTED_FAMILIES = (
    re.compile(r"^gpt-5(?:\.\d+)?(?:-mini)?$"),
    re.compile(r"^gpt-5\.6-(?:luna|terra|sol)$"),
)


@dataclass(frozen=True)
class ModelPolicy:
    display_name: str
    category: str
    recommendation: str | None


@dataclass(frozen=True)
class CachedModels:
    models: tuple[dict[str, Any], ...]
    fetched_at: datetime


MODEL_POLICY: dict[str, ModelPolicy] = {
    "gpt-5.6-luna": ModelPolicy("GPT-5.6 Luna", "Más económico", "Recomendado para tareas · Optimizado para costo/volumen"),
    "gpt-5.4-mini": ModelPolicy("GPT-5.4 mini", "Más económico", "Económico y rápido"),
    "gpt-5-mini": ModelPolicy("GPT-5 mini", "Más económico", "Económico"),
    "gpt-5.6-terra": ModelPolicy("GPT-5.6 Terra", "Equilibrado", "Equilibrado"),
    "gpt-5.6-sol": ModelPolicy("GPT-5.6 Sol", "Máxima calidad", "Máxima calidad · Mayor costo esperado"),
}

_cache: dict[str, CachedModels] = {}
_cache_lock = threading.Lock()


def is_supported_task_model(model_id: str) -> bool:
    value = str(model_id or "").strip()
    if not _MODEL_ID.fullmatch(value):
        return False
    if value.startswith("ft:") or _DATED_SNAPSHOT.search(value):
        return False
    return any(pattern.fullmatch(value) for pattern in _SUPPORTED_FAMILIES)


def invalidate_model_catalog(user_id: str) -> None:
    with _cache_lock:
        _cache.pop(user_id, None)


def model_is_in_cached_catalog(user_id: str, model_id: str) -> bool:
    with _cache_lock:
        entry = _cache.get(user_id)
    return bool(entry and any(item["id"] == model_id for item in entry.models))


def cached_model_availability(user_id: str, model_id: str) -> bool | None:
    with _cache_lock:
        entry = _cache.get(user_id)
    if entry is None:
        return None
    return any(item["id"] == model_id for item in entry.models)


def list_available_openai_models(
    db: Session,
    user_id: str,
    *,
    current_model: str,
    validated_model: str | None,
    validation_ready: bool,
    force_refresh: bool = False,
    client_factory: Any | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> OpenAIModelCatalog:
    now = (now_provider or _utc_now)()
    cached_entry = _cached_entry(user_id)
    if not secret_store.is_secret_store_available():
        return _catalog_without_fetch(
            current_model=current_model,
            validated_model=validated_model,
            status="requires_encryption_key",
            error=None,
        )
    if not secret_store.has_integration_secret(
        db,
        user_id=user_id,
        provider=PROVIDER_OPENAI,
        secret_type=SECRET_API_KEY,
    ):
        return _catalog_without_fetch(
            current_model=current_model,
            validated_model=validated_model,
            status="requires_api_key",
            error=None,
        )
    if cached_entry and not force_refresh and now - cached_entry.fetched_at < MODEL_CATALOG_TTL:
        return _catalog_from_entry(
            cached_entry,
            current_model=current_model,
            validated_model=validated_model,
            validation_ready=validation_ready,
            cached=True,
            stale=False,
        )
    if cached_entry and force_refresh and now - cached_entry.fetched_at < MODEL_REFRESH_DEBOUNCE:
        return _catalog_from_entry(
            cached_entry,
            current_model=current_model,
            validated_model=validated_model,
            validation_ready=validation_ready,
            cached=True,
            stale=False,
        )

    try:
        api_key = secret_store.get_integration_secret(
            db,
            user_id=user_id,
            provider=PROVIDER_OPENAI,
            secret_type=SECRET_API_KEY,
        )
        if not api_key:
            return _catalog_without_fetch(
                current_model=current_model,
                validated_model=validated_model,
                status="requires_api_key",
                error=None,
            )
        with (client_factory or OpenAI)(
            api_key=api_key,
            timeout=min(max(float(settings.openai_timeout_seconds), 1.0), 30.0),
            max_retries=1,
        ) as client:
            response = client.models.list()
        raw_models = getattr(response, "data", response)
        metadata = tuple(
            sorted(
                (
                    {
                        "id": str(getattr(item, "id", "")).strip(),
                        "created": getattr(item, "created", None),
                        "owned_by": _safe_text(getattr(item, "owned_by", None), 100),
                    }
                    for item in raw_models
                    if is_supported_task_model(str(getattr(item, "id", "")))
                ),
                key=lambda item: _model_sort_key(item["id"]),
            )
        )
        entry = CachedModels(models=metadata, fetched_at=now)
        with _cache_lock:
            _cache[user_id] = entry
        return _catalog_from_entry(
            entry,
            current_model=current_model,
            validated_model=validated_model,
            validation_ready=validation_ready,
            cached=False,
            stale=False,
        )
    except (AuthenticationError, RateLimitError, APITimeoutError, APIConnectionError, APIStatusError):
        error = "No se pudo actualizar el catálogo de modelos de OpenAI."
    except Exception:
        error = "No se pudo leer el catálogo de modelos de OpenAI."

    if cached_entry:
        return _catalog_from_entry(
            cached_entry,
            current_model=current_model,
            validated_model=validated_model,
            validation_ready=validation_ready,
            cached=True,
            stale=True,
            error=error,
        )
    return _catalog_without_fetch(
        current_model=current_model,
        validated_model=validated_model,
        status="fetch_error",
        error=error,
    )


def _catalog_from_entry(
    entry: CachedModels,
    *,
    current_model: str,
    validated_model: str | None,
    validation_ready: bool,
    cached: bool,
    stale: bool,
    error: str | None = None,
) -> OpenAIModelCatalog:
    options = [
        _option(
            item,
            current_model=current_model,
            validated_model=validated_model,
            validation_ready=validation_ready,
        )
        for item in entry.models
    ]
    available_ids = {option.id for option in options}
    if current_model and current_model not in available_ids:
        options.insert(0, _unavailable_option(current_model, validated_model))
    recommended = next((model_id for model_id in RECOMMENDATION_ORDER if model_id in available_ids), None)
    if recommended is None:
        recommended = next((option.id for option in options if option.compatibility != "unavailable"), None)
    return OpenAIModelCatalog(
        models=options,
        current_model=current_model,
        recommended_model=recommended,
        fetched_at=entry.fetched_at,
        cached=cached,
        stale=stale,
        policy_version=OPENAI_TASK_MODEL_POLICY_VERSION,
        status="ready",
        error=error,
    )


def _catalog_without_fetch(
    *,
    current_model: str,
    validated_model: str | None,
    status: str,
    error: str | None,
) -> OpenAIModelCatalog:
    models = [_unavailable_option(current_model, validated_model)] if current_model else []
    return OpenAIModelCatalog(
        models=models,
        current_model=current_model or None,
        recommended_model=None,
        fetched_at=None,
        cached=False,
        stale=False,
        policy_version=OPENAI_TASK_MODEL_POLICY_VERSION,
        status=status,
        error=error,
    )


def _option(
    item: dict[str, Any],
    *,
    current_model: str,
    validated_model: str | None,
    validation_ready: bool,
) -> OpenAIModelOption:
    model_id = item["id"]
    policy = MODEL_POLICY.get(model_id)
    is_validated = bool(validation_ready and validated_model == model_id)
    return OpenAIModelOption(
        id=model_id,
        display_name=policy.display_name if policy else model_id,
        category=policy.category if policy else "Compatible",
        recommendation=policy.recommendation if policy else None,
        created_at=_created_at(item.get("created")),
        owned_by=item.get("owned_by"),
        is_current=model_id == current_model,
        is_validated=is_validated,
        compatibility="supported" if is_validated else "requires_validation",
    )


def _unavailable_option(model_id: str, validated_model: str | None) -> OpenAIModelOption:
    policy = MODEL_POLICY.get(model_id)
    return OpenAIModelOption(
        id=model_id,
        display_name=policy.display_name if policy else model_id,
        category=policy.category if policy else "No disponible",
        recommendation=policy.recommendation if policy else None,
        is_current=True,
        is_validated=False,
        compatibility="unavailable",
    )


def _cached_entry(user_id: str) -> CachedModels | None:
    with _cache_lock:
        return _cache.get(user_id)


def _model_sort_key(model_id: str) -> tuple[int, str]:
    try:
        return (RECOMMENDATION_ORDER.index(model_id), model_id)
    except ValueError:
        return (len(RECOMMENDATION_ORDER), model_id)


def _created_at(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc) if value is not None else None
    except (TypeError, ValueError, OSError):
        return None


def _safe_text(value: Any, max_length: int) -> str | None:
    text = str(value or "").strip()
    return text[:max_length] or None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clear_model_catalog_cache_for_tests() -> None:
    with _cache_lock:
        _cache.clear()
