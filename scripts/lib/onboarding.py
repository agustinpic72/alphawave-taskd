#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "http://127.0.0.1:8711"
SECRET_KEYS = {"TELEGRAM_BOT_TOKEN", "TRELLO_TOKEN", "TRELLO_API_KEY"}


@dataclass
class Check:
    name: str
    status: str
    message: str


def main() -> None:
    parser = argparse.ArgumentParser(prog="onboarding")
    parser.add_argument("--env", default=str(REPO_ROOT / ".env"))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    subcommands = parser.add_subparsers(dest="command", required=True)

    telegram = subcommands.add_parser("telegram-whoami")
    telegram.add_argument("--write", action="store_true")
    telegram.add_argument("--yes", action="store_true")

    trello = subcommands.add_parser("trello-discover")
    trello.add_argument("--write", action="store_true")
    trello.add_argument("--yes", action="store_true")

    live = subcommands.add_parser("live-check")
    live.add_argument("--telegram", action="store_true")
    live.add_argument("--trello", action="store_true")
    live.add_argument("--openai", action="store_true")
    live.add_argument("--all", action="store_true")
    live.add_argument("--dry-run", action="store_true")

    importer = subcommands.add_parser("import-tasks")
    importer.add_argument("task_file")
    importer.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    env_path = Path(args.env)
    if args.command == "telegram-whoami":
        raise SystemExit(telegram_whoami(env_path, write=args.write, assume_yes=args.yes))
    if args.command == "trello-discover":
        raise SystemExit(trello_discover(env_path, write=args.write, assume_yes=args.yes))
    if args.command == "live-check":
        raise SystemExit(live_check(env_path, base_url=args.base_url, include_telegram=args.telegram or args.all, include_trello=args.trello or args.all, include_openai=args.openai or args.all, dry_run=args.dry_run))
    if args.command == "import-tasks":
        raise SystemExit(import_tasks(Path(args.task_file), base_url=args.base_url, dry_run=args.dry_run))
    raise SystemExit(2)


def telegram_whoami(env_path: Path, *, write: bool = False, assume_yes: bool = False) -> int:
    env = read_env(env_path)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("TELEGRAM_BOT_TOKEN is missing in .env", file=sys.stderr)
        return 1
    print("Send any message to your Telegram bot, then press Enter.")
    if not assume_yes:
        input()
    try:
        payload = http_get_json(f"https://api.telegram.org/bot{urllib.parse.quote(token)}/getUpdates")
    except Exception:  # noqa: BLE001 - do not risk printing tokenized URLs.
        print("Telegram getUpdates failed. Check TELEGRAM_BOT_TOKEN.", file=sys.stderr)
        return 1
    users = telegram_users_from_updates(payload)
    if not users:
        print("No Telegram users detected. Send a message to the bot and retry.")
        return 1
    print("Detected Telegram users:\n")
    for index, user in enumerate(users, start=1):
        print(f"{index}. id: {user['id']}")
        if user.get("username"):
            print(f"   username: {user['username']}")
        if user.get("first_name"):
            print(f"   first_name: {user['first_name']}")
        if user.get("last_message"):
            print(f"   last_message: {user['last_message']}")
    selected = users[0]
    print(f"\nSet this in .env:\nTELEGRAM_ALLOWED_USER_ID={selected['id']}")
    if write:
        if not assume_yes and input("Write TELEGRAM_ALLOWED_USER_ID to .env? Type WRITE: ") != "WRITE":
            print("Not writing .env.")
            return 0
        write_env_value(env_path, "TELEGRAM_ALLOWED_USER_ID", str(selected["id"]))
        print("Updated TELEGRAM_ALLOWED_USER_ID in .env.")
    return 0


def telegram_users_from_updates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    users: dict[str, dict[str, Any]] = {}
    for update in payload.get("result", []):
        message = update.get("message") or update.get("edited_message") or {}
        user = message.get("from") or {}
        user_id = user.get("id")
        if user_id is None:
            continue
        key = str(user_id)
        users[key] = {
            "id": key,
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
            "last_message": message.get("text", ""),
        }
    return list(users.values())


def trello_discover(env_path: Path, *, write: bool = False, assume_yes: bool = False) -> int:
    env = read_env(env_path)
    key = env.get("TRELLO_API_KEY", "")
    token = env.get("TRELLO_TOKEN", "")
    if not key or not token:
        print("TRELLO_API_KEY and TRELLO_TOKEN are required in .env", file=sys.stderr)
        return 1
    auth = urllib.parse.urlencode({"key": key, "token": token})
    try:
        member = http_get_json(f"https://api.trello.com/1/members/me?{auth}")
        boards = http_get_json(f"https://api.trello.com/1/members/me/boards?fields=name&{auth}")
    except Exception:  # noqa: BLE001 - do not risk printing tokenized URLs.
        print("Trello discovery failed. Check TRELLO_API_KEY and TRELLO_TOKEN.", file=sys.stderr)
        return 1
    suggestions = {"TRELLO_MEMBER_ID": str(member.get("id", ""))}
    print("Trello member:")
    print(f"- id: {suggestions['TRELLO_MEMBER_ID']}")
    print(f"- username: {member.get('username', '')}")
    print("\nBoards:")
    for board in boards:
        print(f"- {board.get('name')} ({board.get('id')})")
        alias = _board_alias(board.get("name", ""))
        if alias:
            suggestions[f"TRELLO_BOARD_{alias.upper()}_ID"] = board.get("id", "")
            try:
                lists = http_get_json(f"https://api.trello.com/1/boards/{board.get('id')}/lists?fields=name&{auth}")
            except Exception:  # noqa: BLE001
                lists = []
                print("  Lists: unavailable")
            print("  Lists:")
            for item in lists:
                print(f"  - {item.get('name')} ({item.get('id')})")
    print("\nSuggested .env values:\n")
    for key_name, value in suggestions.items():
        print(f"{key_name}={value}")
    if write:
        if not assume_yes and input("Write suggested Trello values to .env? Type WRITE: ") != "WRITE":
            print("Not writing .env.")
            return 0
        for key_name, value in suggestions.items():
            if value:
                write_env_value(env_path, key_name, str(value))
        print("Updated Trello values in .env.")
    return 0


def live_check(env_path: Path, *, base_url: str, include_telegram: bool, include_trello: bool, include_openai: bool, dry_run: bool = False) -> int:
    env = read_env(env_path)
    checks: list[Check] = []
    if dry_run:
        planned = ["backend health", "preflight", "briefing preview"]
        if include_telegram:
            planned.append("Telegram getMe")
        if include_trello:
            planned.append("Trello me/boards/lists")
        if include_openai:
            planned.append("OpenAI status")
        print("Live-check dry-run:")
        for item in planned:
            print(f"- {item}")
        return 0

    checks.append(_check_backend_health(base_url))
    checks.append(_check_endpoint(base_url, "/api/system/preflight", "preflight"))
    checks.append(_check_endpoint(base_url, "/api/briefing/generate", "briefing_preview", method="POST", payload={"manual": True}))
    if include_telegram:
        checks.append(_check_telegram(env))
    if include_trello:
        checks.extend(_check_trello(env))
    if include_openai:
        checks.append(_check_endpoint(base_url, "/api/integrations/openai/status", "openai_status"))
    print_checks(checks)
    return 1 if any(check.status == "error" for check in checks) else 0


def import_tasks(task_file: Path, *, base_url: str, dry_run: bool = False) -> int:
    lines = [line.strip() for line in task_file.read_text(encoding="utf-8").splitlines()]
    titles = [line.removeprefix("-").strip() for line in lines if line and not line.lstrip().startswith("#")]
    if not titles:
        print("No tasks found.")
        return 1
    existing = []
    try:
        existing_payload = http_request_json("GET", f"{base_url.rstrip()}/api/tasks?status=active")
        existing = [item["title"].casefold() for item in existing_payload.get("tasks", [])]
    except Exception:  # noqa: BLE001 - dry runs may happen without backend.
        existing = []
    duplicates = [title for title in titles if title.casefold() in existing]
    if dry_run:
        print(f"Import dry-run: {len(titles)} lines")
        for title in titles:
            marker = " duplicate" if title in duplicates else ""
            print(f"- {title}{marker}")
        return 0
    payload = http_request_json("POST", f"{base_url.rstrip()}/api/tasks/bulk", {"text": "\n".join(titles), "auto_classify": True})
    tasks = payload.get("tasks", [])
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.get("scope", "Inbox")] = counts.get(task.get("scope", "Inbox"), 0) + 1
    print(f"Created: {len(tasks)}")
    for scope in ("Inbox", "Personal", "ALPHA", "BETA", "GAMMA", "DELTA"):
        print(f"- {scope}: {counts.get(scope, 0)}")
    if duplicates:
        print("Possible duplicates:")
        for title in duplicates:
            print(f"- {title}")
    return 0


def _check_backend_health(base_url: str) -> Check:
    return _check_endpoint(base_url, "/api/system/health", "backend_health")


def _check_endpoint(base_url: str, path: str, name: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Check:
    try:
        data = http_request_json(method, f"{base_url.rstrip()}{path}", payload)
    except Exception as exc:  # noqa: BLE001
        return Check(name, "error", str(exc))
    status = data.get("status", "ok")
    return Check(name, "ok" if status != "error" else "error", status)


def _check_telegram(env: dict[str, str]) -> Check:
    if env.get("TELEGRAM_ENABLED", "false").casefold() != "true":
        return Check("telegram", "ok", "disabled")
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    allowed = env.get("TELEGRAM_ALLOWED_USER_ID", "")
    if not token or not allowed:
        return Check("telegram", "error", "TELEGRAM_ENABLED=true but token or allowed user is missing")
    try:
        data = http_get_json(f"https://api.telegram.org/bot{urllib.parse.quote(token)}/getMe")
    except Exception:  # noqa: BLE001
        return Check("telegram", "error", "Telegram getMe failed")
    return Check("telegram", "ok" if data.get("ok") else "error", "token valid" if data.get("ok") else "getMe failed")


def _check_trello(env: dict[str, str]) -> list[Check]:
    if env.get("TRELLO_ENABLED", "false").casefold() != "true":
        return [Check("trello", "ok", "disabled")]
    key = env.get("TRELLO_API_KEY", "")
    token = env.get("TRELLO_TOKEN", "")
    if not key or not token:
        return [Check("trello", "error", "TRELLO_ENABLED=true but key/token missing")]
    auth = urllib.parse.urlencode({"key": key, "token": token})
    checks: list[Check] = []
    try:
        http_get_json(f"https://api.trello.com/1/members/me?{auth}")
        checks.append(Check("trello_auth", "ok", "auth valid"))
        for env_key in ("TRELLO_BOARD_ALPHA_ID", "TRELLO_BOARD_BETA_ID"):
            board_id = env.get(env_key, "")
            if not board_id:
                checks.append(Check(env_key.lower(), "error", f"{env_key} missing"))
                continue
            lists = http_get_json(f"https://api.trello.com/1/boards/{board_id}/lists?fields=name&{auth}")
            names = {item.get("name") for item in lists}
            expected = {"TAREAS", "EN PROCESO", "EN REVISION", "TERMINADAS"}
            missing = sorted(expected - names)
            checks.append(Check(env_key.lower(), "ok" if not missing else "error", "lists ok" if not missing else "missing lists: " + ", ".join(missing)))
    except Exception:  # noqa: BLE001
        checks.append(Check("trello", "error", "Trello request failed"))
    return checks


def print_checks(checks: list[Check]) -> None:
    status = "error" if any(check.status == "error" for check in checks) else "ok"
    print(f"Live-check: {status}")
    for check in checks:
        print(f"- [{check.status}] {check.name}: {check.message}")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", maxsplit=1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    rendered = f"{key}={value}"
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = rendered
            replaced = True
            break
    if not replaced:
        lines.append(rendered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def http_get_json(url: str) -> dict[str, Any] | list[dict[str, Any]]:
    return http_request_json("GET", url)


def http_request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _board_alias(name: str) -> str | None:
    lowered = name.casefold()
    if "project alpha" in lowered:
        return "ALPHA"
    if "project beta" in lowered:
        return "BETA"
    return None


if __name__ == "__main__":
    main()
