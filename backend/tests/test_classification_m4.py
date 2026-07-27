from app.schemas.llm import CommandIntent, CommandIntentRequest
from app.services.classification import classify_task_title, infer_scope
from app.services.telegram_parser import ParsedCommand
from app.services.telegram_processor import _llm_fallback_command


def test_keyword_heuristics():
    assert infer_scope("revisar endpoint Client Work", existing_scopes=["Client Work"]) == "Client Work"
    assert infer_scope("revisar endpoint Project Delta") == "Inbox"
    assert infer_scope("domingo peli con mi novia") == "Personal"
    assert infer_scope("tarea rara sin contexto") == "Inbox"


def test_dynamic_keyword_hints_use_only_available_scopes():
    assert infer_scope(
        "preparar reporte para Acme",
        existing_scopes=["Client Work"],
        keyword_hints={"Client Work": ["Acme"]},
    ) == "Client Work"
    assert infer_scope(
        "preparar reporte para Acme",
        existing_scopes=["Client Work"],
        keyword_hints={"Unavailable": ["Acme"]},
    ) == "Inbox"


def test_automatic_classification_stays_local():
    result = classify_task_title("tarea rara sin contexto", db=object(), user_id="user-a")

    assert result.scope == "Inbox"
    assert result.reason == "fallback_inbox"


def test_automatic_classification_uses_only_local_allowed_scope_hints():
    result = classify_task_title(
        "implementar Client Work",
        db=object(),
        user_id="user-a",
        existing_scopes=["Client Work"],
    )

    assert result.scope == "Client Work"
    assert result.effort_bucket == "deep"


def test_automatic_classification_rejects_unavailable_scope():
    result = classify_task_title("tarea ambigua", db=object(), user_id="user-a")

    assert result.scope == "Inbox"
    assert result.reason == "fallback_inbox"


def test_unknown_command_structured_fallback_maps_safe_intent(db_session, monkeypatch):
    class Provider:
        def parse_command_intent(self, request: CommandIntentRequest) -> CommandIntent:
            return CommandIntent(intent="planning_now", confidence=0.88, reason="Petición de planificación.")

    captured = {}

    def resolve_user(db, chat_id=None):
        captured["chat_id"] = chat_id
        return "user-b"

    def resolve_provider(db, user_id):
        captured["user_id"] = user_id
        return Provider()

    monkeypatch.setattr("app.services.telegram_processor._telegram_user_id", resolve_user)
    monkeypatch.setattr("app.services.telegram_processor.get_llm_provider", resolve_provider)
    monkeypatch.setattr("app.services.telegram_processor.settings_service.llm_enabled", lambda db, user_id: True)

    command = _llm_fallback_command(
        db_session,
        "che, qué hago ya?",
        ParsedCommand("ambiguous", {"reason": "unknown_command"}),
        chat_id="chat-b",
    )

    assert command.action == "plan_now"
    assert captured == {"chat_id": "chat-b", "user_id": "user-b"}
