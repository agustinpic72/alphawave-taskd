import asyncio

import pytest

from app.services.trello_readonly_smoke import (
    ReadOnlyTrelloClient,
    configured_trello_boards,
    run_trello_readonly_smoke,
    sanitize_trello_error,
)


def run(coro):
    return asyncio.run(coro)


def board_payload(*, enabled=True, board_id="board-1", workflow_states=None, auto_confirm=False):
    return {
        "settings": {
            "trello": {
                "boards": {
                    "ALPHA": {
                        "alias": "ALPHA",
                        "name": "Project Alpha",
                        "enabled": enabled,
                        "board_id": board_id,
                        "auto_confirm_writes": auto_confirm,
                        "workflow_states": workflow_states
                        if workflow_states is not None
                        else [
                            {"key": "pending", "label": "Tareas", "role": "pending", "enabled": True, "list_id": "list-pending", "list_name": "TAREAS"},
                            {"key": "completed", "label": "Terminadas", "role": "completed", "enabled": True, "list_id": "list-done", "list_name": "TERMINADAS"},
                            {"key": "perpetual", "label": "Perpetuas", "role": "perpetual", "enabled": False, "list_id": None, "list_name": "Perpetuas"},
                        ],
                    }
                }
            }
        }
    }


class FakeRequester:
    def __init__(self):
        self.calls = []

    async def __call__(self, method, path, params):
        self.calls.append((method, path, params))
        assert method == "GET"
        if path == "/boards/board-1":
            return {"id": "board-1", "name": "Project Alpha", "closed": False}
        if path == "/boards/board-1/lists":
            return [
                {"id": "list-pending", "name": "TAREAS", "closed": False},
                {"id": "list-done", "name": "TERMINADAS", "closed": False},
            ]
        if path == "/boards/board-1/cards":
            return [{"id": "card-1", "name": "Card", "idList": "list-pending", "closed": False}]
        raise AssertionError(f"unexpected GET {path}")


def test_readonly_client_allows_get_and_adds_auth_params():
    requester = FakeRequester()
    client = ReadOnlyTrelloClient("secret-key", "secret-token", requester=requester)

    response = run(client.request("GET", "/boards/board-1", {"fields": "name"}))

    assert response["name"] == "Project Alpha"
    assert requester.calls[0][0] == "GET"
    assert requester.calls[0][2]["key"] == "secret-key"
    assert requester.calls[0][2]["token"] == "secret-token"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_readonly_client_blocks_write_methods_before_request(method):
    requester = FakeRequester()
    client = ReadOnlyTrelloClient("secret-key", "secret-token", requester=requester)

    with pytest.raises(RuntimeError, match="blocked Trello write"):
        run(client.request(method, "/cards", {"name": "write"}))

    assert requester.calls == []


def test_sanitize_trello_error_redacts_tokens_and_query_params():
    message = "GET https://api.trello.com/1/boards/x?key=secret-key&token=secret-token failed with token=secret-token"

    redacted = sanitize_trello_error(message, "secret-key", "secret-token")

    assert "secret-key" not in redacted
    assert "secret-token" not in redacted
    assert "[redacted]" in redacted


def test_configured_trello_boards_reads_settings_payload_and_board_endpoint_shape():
    settings_shape = board_payload()
    endpoint_shape = {"boards": [{"alias": "GAMMA", "name": "Project Gamma"}]}

    assert configured_trello_boards(settings_shape)[0]["alias"] == "ALPHA"
    assert configured_trello_boards(endpoint_shape)[0]["alias"] == "GAMMA"


def test_smoke_validation_uses_only_get_requests_and_reports_auto_confirm_without_executing():
    requester = FakeRequester()
    client = ReadOnlyTrelloClient("secret-key", "secret-token", requester=requester)

    report = run(
        run_trello_readonly_smoke(
            board_payload(auto_confirm=True),
            client,
            trello_enabled=True,
            credentials_ready=True,
            include_cards=True,
        )
    )

    assert report.status == "ok"
    assert report.boards[0].auto_confirm_writes is True
    assert [call[0] for call in requester.calls] == ["GET", "GET", "GET"]


def test_disabled_board_is_skipped_without_remote_calls():
    requester = FakeRequester()
    client = ReadOnlyTrelloClient("secret-key", "secret-token", requester=requester)

    report = run(run_trello_readonly_smoke(board_payload(enabled=False), client, trello_enabled=True, credentials_ready=True))

    assert report.status == "not_configured"
    assert requester.calls == []


def test_enabled_template_without_board_id_is_warning_without_remote_calls():
    requester = FakeRequester()
    client = ReadOnlyTrelloClient("secret-key", "secret-token", requester=requester)

    report = run(run_trello_readonly_smoke(board_payload(board_id=""), client, trello_enabled=True, credentials_ready=True))

    assert report.exit_code == 0
    assert report.status == "warning"
    assert "template local sin board_id" in report.warnings[0]
    assert requester.calls == []


def test_disabled_perpetual_does_not_create_error():
    client = ReadOnlyTrelloClient("secret-key", "secret-token", requester=FakeRequester())

    report = run(run_trello_readonly_smoke(board_payload(), client, trello_enabled=True, credentials_ready=True))

    assert report.status == "ok"
    assert report.errors == []


def test_missing_required_pending_or_completed_list_id_is_error():
    workflow = [
        {"key": "pending", "label": "Tareas", "role": "pending", "enabled": True, "list_id": "", "list_name": "TAREAS"},
        {"key": "completed", "label": "Terminadas", "role": "completed", "enabled": True, "list_id": None, "list_name": "TERMINADAS"},
    ]
    client = ReadOnlyTrelloClient("secret-key", "secret-token", requester=FakeRequester())

    report = run(run_trello_readonly_smoke(board_payload(workflow_states=workflow), client, trello_enabled=True, credentials_ready=True))

    assert report.exit_code == 1
    assert any("pending" in error for error in report.errors)
    assert any("completed" in error for error in report.errors)


def test_list_name_mismatch_is_warning_not_error():
    workflow = [
        {"key": "pending", "label": "Tareas", "role": "pending", "enabled": True, "list_id": "list-pending", "list_name": "Tareas viejas"},
        {"key": "completed", "label": "Terminadas", "role": "completed", "enabled": True, "list_id": "list-done", "list_name": "TERMINADAS"},
    ]
    client = ReadOnlyTrelloClient("secret-key", "secret-token", requester=FakeRequester())

    report = run(run_trello_readonly_smoke(board_payload(workflow_states=workflow), client, trello_enabled=True, credentials_ready=True))

    assert report.exit_code == 0
    assert report.status == "warning"
    assert "list_name local" in report.warnings[0]
