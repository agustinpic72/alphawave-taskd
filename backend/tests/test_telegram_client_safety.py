import httpx
import pytest

from app.services.telegram_client import TelegramApiError, _raise_for_telegram_status


def test_telegram_http_error_does_not_include_tokenized_url():
    request = httpx.Request("POST", "https://api.telegram.org/botsecret-token/sendMessage")
    response = httpx.Response(
        400,
        request=request,
        json={"ok": False, "description": "Bad Request: chat not found"},
    )

    with pytest.raises(TelegramApiError) as exc:
        _raise_for_telegram_status(response, "sendMessage")

    message = str(exc.value)
    assert "secret-token" not in message
    assert "https://api.telegram.org" not in message
    assert "chat not found" in message
