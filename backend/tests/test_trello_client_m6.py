import asyncio

from app.services.trello_client import TrelloApiClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeAsyncClient:
    calls = []

    def __init__(self, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, params):
        self.calls.append(("post", url, params))
        return FakeResponse({"id": "card"})

    async def put(self, url, params):
        self.calls.append(("put", url, params))
        return FakeResponse({"id": "card"})


def test_trello_client_write_methods_call_expected_endpoints(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.trello_client.httpx.AsyncClient", FakeAsyncClient)
    client = TrelloApiClient("key", "token")

    asyncio.run(client.create_card("list-1", "Título", "desc", "2026-07-03T09:00:00+02:00"))
    asyncio.run(client.move_card("card-1", "list-2"))
    asyncio.run(client.rename_card("card-1", "Nuevo"))
    asyncio.run(client.update_due("card-1", None))

    assert FakeAsyncClient.calls[0] == (
        "post",
        "https://api.trello.com/1/cards",
        {
            "key": "key",
            "token": "token",
            "idList": "list-1",
            "name": "Título",
            "desc": "desc",
            "due": "2026-07-03T09:00:00+02:00",
        },
    )
    assert FakeAsyncClient.calls[1][0:2] == ("put", "https://api.trello.com/1/cards/card-1")
    assert FakeAsyncClient.calls[1][2]["idList"] == "list-2"
    assert FakeAsyncClient.calls[2][2]["name"] == "Nuevo"
    assert FakeAsyncClient.calls[3][2]["due"] == ""
