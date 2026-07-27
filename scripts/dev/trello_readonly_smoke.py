#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.services.trello_readonly_smoke import ReadOnlyTrelloClient, format_report, run_trello_readonly_smoke  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Trello live read-only validation.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    base_url = os.environ.get("ALPHAWAVE_BASE_URL", "http://127.0.0.1:8711").rstrip("/")
    include_cards = os.environ.get("ALPHAWAVE_TRELLO_SMOKE_INCLUDE_CARDS", "0") == "1"
    max_cards = _int_env("ALPHAWAVE_TRELLO_SMOKE_MAX_CARDS", 20)

    try:
        _read_json(f"{base_url}/api/health")
        settings_payload = _read_json(f"{base_url}/api/settings")
    except RuntimeError as exc:
        print(f"alphawave-taskd local API is not responding cleanly: {exc}", file=sys.stderr)
        return 2

    client = ReadOnlyTrelloClient(settings.trello_api_key, settings.trello_token)
    credentials_ready = bool(settings.trello_api_key and settings.trello_token)
    report = asyncio.run(
        run_trello_readonly_smoke(
            settings_payload,
            client,
            trello_enabled=bool(settings.trello_enabled),
            credentials_ready=credentials_ready,
            include_cards=include_cards,
            max_cards=max_cards,
        )
    )

    if args.json:
        print(_report_json(report))
    else:
        print(format_report(report))
    return report.exit_code


def _read_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from {url}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected payload from {url}")
    return value


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _report_json(report) -> str:
    return json.dumps(
        {
            "status": report.status,
            "exit_code": report.exit_code,
            "skipped_reason": report.skipped_reason,
            "errors": report.errors,
            "warnings": report.warnings,
            "boards": [
                {
                    "alias": board.alias,
                    "name": board.name,
                    "enabled": board.enabled,
                    "board_id_configured": bool(board.board_id),
                    "remote_name": board.remote_name,
                    "auto_confirm_writes": board.auto_confirm_writes,
                    "status": board.status,
                    "errors": board.errors,
                    "warnings": board.warnings,
                    "checked_lists": board.checked_lists,
                    "checked_cards": board.checked_cards,
                }
                for board in report.boards
            ],
        },
        ensure_ascii=True,
        indent=2,
    )


if __name__ == "__main__":
    raise SystemExit(main())
