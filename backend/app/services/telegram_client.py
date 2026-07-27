from typing import Any, Protocol

import httpx


class TelegramApiError(RuntimeError):
    pass


class TelegramMessenger(Protocol):
    async def send_message(self, chat_id: str, text: str) -> int | None:
        ...


class TelegramApiClient:
    def __init__(self, bot_token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    async def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/getUpdates", params=params)
            _raise_for_telegram_status(response, "getUpdates")
            payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {payload}")
        return list(payload.get("result", []))

    async def send_message(self, chat_id: str, text: str) -> int | None:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/sendMessage", json={"chat_id": chat_id, "text": text})
            _raise_for_telegram_status(response, "sendMessage")
            payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {payload}")
        return payload.get("result", {}).get("message_id")


class CollectingMessenger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str) -> int | None:
        self.messages.append((chat_id, text))
        return len(self.messages)


def _raise_for_telegram_status(response: httpx.Response, method: str) -> None:
    if response.is_success:
        return
    description = ""
    try:
        payload = response.json()
        raw_description = payload.get("description")
        if isinstance(raw_description, str):
            description = f": {raw_description}"
    except ValueError:
        description = ""
    raise TelegramApiError(f"Telegram {method} failed with HTTP {response.status_code}{description}")
