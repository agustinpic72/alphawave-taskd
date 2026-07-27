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
from app.services.telegram_live_smoke import (  # noqa: E402
    DEFAULT_SMOKE_MESSAGE,
    TelegramSmokeClient,
    format_report,
    parse_allowed_chat_ids,
    redact_chat_id,
    run_telegram_live_smoke,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Telegram live safety smoke.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    base_url = os.environ.get("ALPHAWAVE_BASE_URL", "http://127.0.0.1:8711").rstrip("/")
    timeout = _float_env("ALPHAWAVE_TELEGRAM_SMOKE_TIMEOUT", 5.0)
    send_enabled = os.environ.get("ALPHAWAVE_TELEGRAM_SMOKE_SEND", "0") == "1"
    selected_chat_id = os.environ.get("ALPHAWAVE_TELEGRAM_SMOKE_CHAT_ID")
    smoke_message = os.environ.get("ALPHAWAVE_TELEGRAM_SMOKE_MESSAGE", DEFAULT_SMOKE_MESSAGE)

    try:
        _read_json(f"{base_url}/api/health")
        system_status = _read_json(f"{base_url}/api/system/status")
    except RuntimeError as exc:
        print(f"alphawave-taskd local API is not responding cleanly: {exc}", file=sys.stderr)
        return 2

    allowed_chat_ids = parse_allowed_chat_ids(settings.telegram_allowed_user_id)
    client = TelegramSmokeClient(settings.telegram_bot_token, timeout=timeout) if settings.telegram_bot_token else None
    report = asyncio.run(
        run_telegram_live_smoke(
            system_status,
            client,
            telegram_enabled=bool(settings.telegram_enabled),
            token_configured=bool(settings.telegram_bot_token),
            allowed_chat_ids=allowed_chat_ids,
            send_enabled=send_enabled,
            selected_chat_id=selected_chat_id,
            smoke_message=smoke_message,
        )
    )

    if args.json:
        print(_report_json(report))
    else:
        print(format_report(report))
        if not send_enabled and report.status != "skipped":
            print("Set ALPHAWAVE_TELEGRAM_SMOKE_SEND=1 to send exactly one allowlisted smoke message.")
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


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _report_json(report) -> str:
    return json.dumps(
        {
            "status": report.status,
            "exit_code": report.exit_code,
            "local_app": report.local_app,
            "local_status": report.local_status,
            "get_me_status": report.get_me_status,
            "bot_username": report.bot_username,
            "bot_id_hint": report.bot_id_hint,
            "send_status": report.send_status,
            "send_message_id_hint": redact_chat_id(report.send_message_id_hint or ""),
            "warnings": report.warnings,
            "errors": report.errors,
            "skipped_reason": report.skipped_reason,
        },
        ensure_ascii=True,
        indent=2,
    )


if __name__ == "__main__":
    raise SystemExit(main())
