import re
from sqlalchemy.orm import Session

from app.schemas.llm import TaskClassification
from app.services.text_normalization import normalized_words


BASE_SCOPES = ("Inbox", "Personal")

BASE_SCOPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Personal": ("novia", "casa", "compras", "familia", "peli", "medico", "médico", "cafe", "café"),
}


def infer_scope(title: str, *, existing_scopes: list[str] | None = None, keyword_hints: dict[str, list[str]] | None = None) -> str:
    return infer_scope_with_confidence(title, existing_scopes=existing_scopes, keyword_hints=keyword_hints)[0]


def infer_scope_with_confidence(
    title: str,
    *,
    existing_scopes: list[str] | None = None,
    keyword_hints: dict[str, list[str]] | None = None,
) -> tuple[str, float, str]:
    normalized = normalized_words(title)
    allowed = _allowed_scopes(existing_scopes)
    hints = {scope: list(keywords) for scope, keywords in BASE_SCOPE_KEYWORDS.items()}
    for scope in allowed:
        if scope not in BASE_SCOPES:
            hints.setdefault(scope, []).append(scope)
    for scope, keywords in (keyword_hints or {}).items():
        canonical = _canonical_allowed_scope(scope, allowed)
        if canonical:
            hints.setdefault(canonical, []).extend(keywords)
    for scope, keywords in hints.items():
        for keyword in keywords:
            pattern = r"(^|\W)" + re.escape(normalized_words(keyword)) + r"($|\W)"
            if re.search(pattern, normalized):
                return scope, 0.95, f"keyword:{keyword}"
    return "Inbox", 0.0, "no_keyword"


def classify_task_title(
    title: str,
    *,
    db: Session | None = None,
    user_id: str | None = None,
    auto_classify: bool = True,
    llm_enabled: bool = True,
    user_text: str | None = None,
    existing_scopes: list[str] | None = None,
    keyword_hints: dict[str, list[str]] | None = None,
) -> TaskClassification:
    allowed_scopes = _allowed_scopes(existing_scopes)
    scope, confidence, reason = infer_scope_with_confidence(
        title,
        existing_scopes=allowed_scopes,
        keyword_hints=keyword_hints,
    )
    if scope != "Inbox":
        return TaskClassification(
            normalized_title=title.strip(),
            scope=scope,
            scope_confidence=confidence,
            effort_bucket=_infer_effort(title),
            estimated_minutes=_infer_minutes(title),
            context_bucket=_infer_context(title, scope),
            reason=reason,
        )

    return TaskClassification(
        normalized_title=title.strip(),
        scope="Inbox",
        scope_confidence=0.0,
        effort_bucket=_infer_effort(title),
        estimated_minutes=_infer_minutes(title),
        context_bucket=_infer_context(title, "Inbox"),
        reason="fallback_inbox",
    )


def _allowed_scopes(existing_scopes: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in [*BASE_SCOPES, *(existing_scopes or [])]:
        normalized = str(value).strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def _canonical_allowed_scope(value: str, allowed_scopes: list[str]) -> str | None:
    key = str(value).strip().casefold()
    return next((scope for scope in allowed_scopes if scope.casefold() == key), None)


def _infer_effort(title: str) -> str:
    lowered = title.casefold()
    if any(word in lowered for word in ("endpoint", "arquitectura", "implementar", "refactor", "flujo")):
        return "deep"
    if any(word in lowered for word in ("llamar", "comprar", "revisar", "cambiar", "fix")):
        return "quick"
    return "unknown"


def _infer_minutes(title: str) -> int | None:
    effort = _infer_effort(title)
    if effort == "quick":
        return 15
    if effort == "deep":
        return 120
    return None


def _infer_context(title: str, scope: str) -> str:
    lowered = title.casefold()
    if any(word in lowered for word in ("llamar", "call", "llamada")):
        return "call"
    if scope == "Personal" and any(word in lowered for word in ("comprar", "retiro", "cajero", "casa", "peli")):
        return "errand"
    if any(word in lowered for word in ("review", "revisar", "seguimiento")):
        return "review"
    if _infer_effort(title) == "deep":
        return "deep_work"
    if _infer_effort(title) == "quick":
        return "quick_task"
    return "unknown"
