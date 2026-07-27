import asyncio

from app.services.telegram_live_smoke import (
    TelegramSmokeClient,
    format_report,
    parse_allowed_chat_ids,
    redact_chat_id,
    run_telegram_live_smoke,
    sanitize_telegram_error,
)


def run(coro):
    return asyncio.run(coro)


def system_status_payload(*, status="active", configured=True):
    return {
        "services": {
            "telegram": {
                "status": status,
                "enabled": True,
                "configured": configured,
                "polling_active": True,
                "manual_commands_available": configured,
                "detail": "Configurado" if configured else "Requiere token y allowlist",
            }
        }
    }


class FakeTelegramRequester:
    def __init__(self, *, get_me=None, send=None):
        self.calls = []
        self.get_me = get_me or {"ok": True, "result": {"id": 123456789, "username": "alpha_bot"}}
        self.send = send or {"ok": True, "result": {"message_id": 987654321}}

    async def __call__(self, method, action, params, json):
        self.calls.append((method, action, params, json))
        if action == "getMe":
            return self.get_me
        if action == "sendMessage":
            return self.send
        raise AssertionError(action)


def test_get_me_success_report_redacts_bot_id_and_token():
    requester = FakeTelegramRequester()
    client = TelegramSmokeClient("123:SECRET_TOKEN", requester=requester)

    report = run(
        run_telegram_live_smoke(
            system_status_payload(),
            client,
            telegram_enabled=True,
            token_configured=True,
            allowed_chat_ids={"123456789"},
        )
    )
    output = format_report(report)

    assert report.status == "ok"
    assert report.get_me_status == "ok"
    assert report.send_status == "skipped_opt_in_required"
    assert "@alpha_bot" in output
    assert "123:SECRET_TOKEN" not in output
    assert "123456789" not in output
    assert "12...89" in output


def test_get_me_401_error_redacts_token_and_bot_url():
    requester = FakeTelegramRequester(get_me={"ok": False, "description": "bad url https://api.telegram.org/bot123:SECRET/getMe"})
    client = TelegramSmokeClient("123:SECRET", requester=requester)

    report = run(
        run_telegram_live_smoke(
            system_status_payload(),
            client,
            telegram_enabled=True,
            token_configured=True,
            allowed_chat_ids={"123456789"},
        )
    )
    output = format_report(report)

    assert report.exit_code == 1
    assert "123:SECRET" not in output
    assert "bot123:SECRET" not in output
    assert "bot[redacted]" in output


def test_send_smoke_blocked_when_opt_in_false():
    requester = FakeTelegramRequester()
    client = TelegramSmokeClient("token", requester=requester)

    report = run(
        run_telegram_live_smoke(
            system_status_payload(),
            client,
            telegram_enabled=True,
            token_configured=True,
            allowed_chat_ids={"chat-1"},
            send_enabled=False,
        )
    )

    assert report.exit_code == 0
    assert report.send_status == "skipped_opt_in_required"
    assert [call[1] for call in requester.calls] == ["getMe"]


def test_send_smoke_allowed_when_opt_in_true_and_chat_allowlisted():
    requester = FakeTelegramRequester()
    client = TelegramSmokeClient("token", requester=requester)

    report = run(
        run_telegram_live_smoke(
            system_status_payload(),
            client,
            telegram_enabled=True,
            token_configured=True,
            allowed_chat_ids={"chat-1"},
            selected_chat_id="chat-1",
            send_enabled=True,
        )
    )

    assert report.exit_code == 0
    assert report.send_status == "ok"
    assert [call[1] for call in requester.calls] == ["getMe", "sendMessage"]
    assert requester.calls[-1][3]["chat_id"] == "chat-1"


def test_send_smoke_blocked_if_chat_not_allowlisted():
    requester = FakeTelegramRequester()
    client = TelegramSmokeClient("token", requester=requester)

    report = run(
        run_telegram_live_smoke(
            system_status_payload(),
            client,
            telegram_enabled=True,
            token_configured=True,
            allowed_chat_ids={"chat-1"},
            selected_chat_id="chat-2",
            send_enabled=True,
        )
    )

    assert report.exit_code == 1
    assert report.send_status == "blocked_chat_not_allowlisted"
    assert [call[1] for call in requester.calls] == ["getMe"]


def test_multiple_allowlisted_chats_require_explicit_selection_for_send():
    requester = FakeTelegramRequester()
    client = TelegramSmokeClient("token", requester=requester)

    report = run(
        run_telegram_live_smoke(
            system_status_payload(),
            client,
            telegram_enabled=True,
            token_configured=True,
            allowed_chat_ids={"chat-1", "chat-2"},
            send_enabled=True,
        )
    )

    assert report.exit_code == 1
    assert report.send_status == "blocked_requires_explicit_chat"
    assert [call[1] for call in requester.calls] == ["getMe"]


def test_timeout_or_network_error_returns_human_redacted_error():
    async def timeout_requester(method, action, params, json):
        raise TimeoutError("timeout https://api.telegram.org/bottoken-secret/getMe")

    client = TelegramSmokeClient("token-secret", requester=timeout_requester)

    report = run(
        run_telegram_live_smoke(
            system_status_payload(),
            client,
            telegram_enabled=True,
            token_configured=True,
            allowed_chat_ids={"chat-1"},
        )
    )

    assert report.exit_code == 1
    assert "token-secret" not in format_report(report)
    assert "Telegram getMe failed" in report.errors[0]


def test_telegram_disabled_and_requires_config_skip_cleanly():
    disabled = run(
        run_telegram_live_smoke(
            system_status_payload(),
            None,
            telegram_enabled=False,
            token_configured=False,
            allowed_chat_ids=set(),
        )
    )
    missing_chat = run(
        run_telegram_live_smoke(
            system_status_payload(configured=False),
            TelegramSmokeClient("token", requester=FakeTelegramRequester()),
            telegram_enabled=True,
            token_configured=True,
            allowed_chat_ids=set(),
        )
    )

    assert disabled.exit_code == 0
    assert disabled.status == "skipped"
    assert missing_chat.exit_code == 0
    assert missing_chat.status == "skipped"


def test_redaction_helpers_hide_token_and_chat_ids():
    text = "https://api.telegram.org/bot123:SECRET/sendMessage chat 123456789"

    redacted = sanitize_telegram_error(text, "123:SECRET", {"123456789"})

    assert "123:SECRET" not in redacted
    assert "123456789" not in redacted
    assert "12...89" in redacted
    assert redact_chat_id("-100123456789") == "-10...89"
    assert parse_allowed_chat_ids("1, 2;3 4") == {"1", "2", "3", "4"}
