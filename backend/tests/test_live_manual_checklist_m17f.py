from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT_DIR / "scripts" / "dev" / "live_manual_checklist.py"
spec = importlib.util.spec_from_file_location("live_manual_checklist", SCRIPT_PATH)
live_manual = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["live_manual_checklist"] = live_manual
spec.loader.exec_module(live_manual)


class FakeApiClient:
    def __init__(self, payload):
        self.payload = payload

    def request(self, method, path, payload=None):
        assert method == "GET"
        assert path == "/api/settings"
        return self.payload


class FakeTrelloClient:
    def __init__(self):
        self.created = []
        self.closed = []

    def create_card(self, list_id, title, description):
        self.created.append((list_id, title, description))
        return {"id": "abc123456789", "shortUrl": "https://trello.com/c/abcdef12/smoke"}

    def close_card(self, card_id):
        self.closed.append(card_id)
        return {"id": card_id, "closed": True}


def settings_payload(list_id="list-pending-123", include_pending=True):
    states = []
    if include_pending:
        states.append(
            {
                "key": "pending",
                "role": "pending",
                "enabled": True,
                "list_id": list_id,
                "list_name": "Tasks",
            }
        )
    return {
        "settings": {
            "trello": {
                "boards": {
                    "GAMMA": {
                        "alias": "GAMMA",
                        "name": "Project Gamma",
                        "enabled": True,
                        "workflow_states": states,
                    }
                }
            }
        }
    }


def test_trello_write_smoke_refuses_without_opt_in():
    result = live_manual.run_trello_write_if_opted_in(FakeApiClient(settings_payload()), {})

    assert result.status == "skipped"
    assert "ALPHAWAVE_TRELLO_WRITE_SMOKE=1" in result.detail


def test_trello_write_smoke_refuses_without_board_alias():
    result = live_manual.run_trello_write_if_opted_in(
        FakeApiClient(settings_payload()),
        {"ALPHAWAVE_TRELLO_WRITE_SMOKE": "1"},
    )

    assert result.status == "skipped"
    assert "ALPHAWAVE_TRELLO_WRITE_SMOKE_BOARD" in result.detail


def test_trello_write_smoke_refuses_without_pending_list_id():
    result = live_manual.run_trello_write_if_opted_in(
        FakeApiClient(settings_payload(list_id="", include_pending=True)),
        {"ALPHAWAVE_TRELLO_WRITE_SMOKE": "1", "ALPHAWAVE_TRELLO_WRITE_SMOKE_BOARD": "GAMMA"},
    )

    assert result.status == "skipped"
    assert "pending list_id" in result.detail


def test_trello_write_smoke_uses_list_id_not_name():
    fake_trello = FakeTrelloClient()
    result = live_manual.run_trello_write_if_opted_in(
        FakeApiClient(settings_payload(list_id="real-list-id")),
        {"ALPHAWAVE_TRELLO_WRITE_SMOKE": "1", "ALPHAWAVE_TRELLO_WRITE_SMOKE_BOARD": "GAMMA"},
        trello_client=fake_trello,
    )

    assert result.status == "pass"
    assert fake_trello.created[0][0] == "real-list-id"
    assert fake_trello.created[0][1].startswith(live_manual.SMOKE_PREFIX)
    assert result.cleanup == "manual_needed"


def test_trello_write_smoke_cleanup_requires_opt_in():
    fake_trello = FakeTrelloClient()
    result = live_manual.run_trello_write_if_opted_in(
        FakeApiClient(settings_payload(list_id="real-list-id")),
        {
            "ALPHAWAVE_TRELLO_WRITE_SMOKE": "1",
            "ALPHAWAVE_TRELLO_WRITE_SMOKE_BOARD": "GAMMA",
            "ALPHAWAVE_TRELLO_WRITE_SMOKE_CLEANUP": "1",
        },
        trello_client=fake_trello,
    )

    assert result.status == "pass"
    assert fake_trello.closed == ["abc123456789"]
    assert result.cleanup == "closed"


def test_report_redacts_sensitive_values():
    text = live_manual.sanitize_output(
        "https://api.telegram.org/bot123:secret/sendMessage?chat_id=123 "
        "token=trello-secret api_key=abc password=hunter2 chat_id=-123456"
    )

    assert "123:secret" not in text
    assert "trello-secret" not in text
    assert "hunter2" not in text
    assert "-123456" not in text
