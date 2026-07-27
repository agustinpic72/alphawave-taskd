from typing import Any

import httpx


class TrelloApiClient:
    def __init__(self, api_key: str, token: str, *, timeout: float = 20.0) -> None:
        self._auth = {"key": api_key, "token": token}
        self._timeout = timeout
        self._base_url = "https://api.trello.com/1"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = dict(self._auth)
        if params:
            query.update(params)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base_url}{path}", params=query)
            response.raise_for_status()
            return response.json()

    async def _post(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = dict(self._auth)
        if params:
            query.update(params)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(f"{self._base_url}{path}", params=query)
            response.raise_for_status()
            return response.json()

    async def _put(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = dict(self._auth)
        if params:
            query.update(params)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.put(f"{self._base_url}{path}", params=query)
            response.raise_for_status()
            return response.json()

    async def get_lists(self, board_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/boards/{board_id}/lists", {"fields": "name,closed"})

    async def get_member_boards(self, member_id: str) -> list[dict[str, Any]]:
        return await self._get(
            f"/members/{member_id}/boards",
            {"fields": "name,url,shortLink,closed", "filter": "open"},
        )

    async def get_cards(self, board_id: str) -> list[dict[str, Any]]:
        return await self._get(
            f"/boards/{board_id}/cards",
            {
                "fields": "name,desc,due,idList,idMembers,shortUrl,url,dateLastActivity,labels,closed,pos",
            },
        )

    async def get_checklists(self, card_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/cards/{card_id}/checklists", {"fields": "name", "checkItems": "all"})

    async def get_comment_actions(self, card_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/cards/{card_id}/actions", {"filter": "commentCard", "limit": 50})

    async def create_card(self, list_id: str, title: str, description: str | None = None, due_at: str | None = None) -> dict[str, Any]:
        params = {"idList": list_id, "name": title}
        if description:
            params["desc"] = description
        if due_at:
            params["due"] = due_at
        return await self._post("/cards", params)

    async def move_card(self, card_id: str, target_list_id: str) -> dict[str, Any]:
        return await self._put(f"/cards/{card_id}", {"idList": target_list_id})

    async def rename_card(self, card_id: str, new_title: str) -> dict[str, Any]:
        return await self._put(f"/cards/{card_id}", {"name": new_title})

    async def update_due(self, card_id: str, due_at: str | None) -> dict[str, Any]:
        return await self._put(f"/cards/{card_id}", {"due": due_at or ""})
