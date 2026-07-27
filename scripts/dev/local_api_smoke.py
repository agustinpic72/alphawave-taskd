#!/usr/bin/env python
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


SMOKE_PREFIX = "[M17D SMOKE]"


@dataclass
class LocalApiSmokeResult:
    status: str = "ok"
    created_task_ids: list[str] = field(default_factory=list)
    completed_task_ids: list[str] = field(default_factory=list)
    deleted_task_ids: list[str] = field(default_factory=list)
    reminder_id: str | None = None
    reminder_cancelled: bool = False
    backup_id: str | None = None
    backup_created: bool = False
    cleanup_active_tasks_remaining: int = 0
    cleanup_active_reminders_remaining: int = 0
    confirmations_before: int = 0
    confirmations_after: int = 0
    sort_confirmation_id: str | None = None
    priority_items: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 1 if self.errors else 0

    def error(self, message: str) -> None:
        self.status = "error"
        self.errors.append(message)

    def warn(self, message: str) -> None:
        if self.status == "ok":
            self.status = "warning"
        self.warnings.append(message)


class ApiClient:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def run_local_api_smoke(base_url: str) -> LocalApiSmokeResult:
    client = ApiClient(base_url)
    result = LocalApiSmokeResult()
    try:
        client.request("GET", "/api/health")
        _cleanup(client, result)
        result.confirmations_before = len(_pending_confirmations(client))

        personal = client.request(
            "POST",
            "/api/tasks",
            {
                "title": f"{SMOKE_PREFIX} Personal local completion",
                "scope": "Personal",
                "auto_classify": False,
                "priority_label": "high",
                "impact_score": 4,
                "urgency_score": 4,
                "blocking_score": 1,
            },
        )
        alpha_local = client.request(
            "POST",
            "/api/tasks",
            {
                "title": f"{SMOKE_PREFIX} ALPHA local no Trello card completion",
                "scope": "ALPHA",
                "auto_classify": False,
            },
        )
        for task in (personal, alpha_local):
            result.created_task_ids.append(task["id"])
        if alpha_local.get("source_id") is not None:
            result.error("ALPHA local smoke task unexpectedly has source_id.")

        proposal = client.request("POST", "/api/tasks/sort/propose")
        result.sort_confirmation_id = proposal.get("confirmation_id")
        result.priority_items = len(proposal.get("items") or [])
        if not proposal.get("items"):
            result.error("Priority proposal returned no items.")
        for item in proposal.get("items") or []:
            priority = item.get("priority") or {}
            if item.get("title", "").startswith(SMOKE_PREFIX) and not priority.get("summary"):
                result.error("Smoke priority item is missing explanation summary.")
        if result.sort_confirmation_id:
            client.request("POST", f"/api/confirmations/{result.sort_confirmation_id}/cancel")

        for task_id in result.created_task_ids:
            completed = client.request("POST", f"/api/tasks/{task_id}/complete")
            if completed.get("status") != "completed":
                result.error(f"Task {task_id} did not complete locally.")
            result.completed_task_ids.append(task_id)

        result.confirmations_after = len(_pending_confirmations(client))
        if result.confirmations_after != result.confirmations_before:
            result.error("Local smoke changed pending confirmations count; possible Trello flow leak.")

        remind_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        reminder = client.request(
            "POST",
            "/api/reminders",
            {"message": f"{SMOKE_PREFIX} reminder", "remind_at": remind_at, "source": "rc_smoke"},
        )
        result.reminder_id = reminder.get("id")
        cancelled = client.request("POST", f"/api/reminders/{result.reminder_id}/cancel")
        result.reminder_cancelled = cancelled.get("status") == "cancelled"
        if not result.reminder_cancelled:
            result.error("Reminder did not cancel cleanly.")

        backup = client.request("POST", "/api/backups")
        result.backup_created = bool(backup.get("created"))
        backup_payload = backup.get("backup") or {}
        result.backup_id = backup_payload.get("id")
        if result.backup_id:
            client.request("POST", f"/api/backups/{urllib.parse.quote(result.backup_id)}/validate")
            client.request("POST", f"/api/backups/{urllib.parse.quote(result.backup_id)}/restore-plan")
        elif result.backup_created:
            result.warn("Backup was created but response had no id.")

        system_status = client.request("GET", "/api/system/status")
        _validate_system_status(system_status, result)
        _cleanup(client, result)
        _verify_cleanup(client, result)
    except Exception as exc:  # noqa: BLE001 - report and still try cleanup.
        result.error(f"Local API smoke failed: {exc}")
        try:
            _cleanup(client, result)
            _verify_cleanup(client, result)
        except Exception as cleanup_exc:  # noqa: BLE001
            result.error(f"Cleanup failed: {cleanup_exc}")
    return result


def format_result(result: LocalApiSmokeResult) -> str:
    lines = [f"Local API smoke: {result.status.upper()}"]
    lines.append(f"Created tasks: {len(result.created_task_ids)}")
    lines.append(f"Completed tasks: {len(result.completed_task_ids)}")
    lines.append(f"Deleted smoke tasks: {len(result.deleted_task_ids)}")
    lines.append(f"Reminder cancelled: {result.reminder_cancelled}")
    lines.append(f"Priority items: {result.priority_items}")
    lines.append(f"Backup created: {result.backup_created}")
    if result.backup_id:
        lines.append(f"Backup id: {result.backup_id}")
    lines.append(f"Cleanup active tasks remaining: {result.cleanup_active_tasks_remaining}")
    lines.append(f"Cleanup active reminders remaining: {result.cleanup_active_reminders_remaining}")
    for warning in result.warnings:
        lines.append(f"WARNING: {warning}")
    for error in result.errors:
        lines.append(f"ERROR: {error}")
    return "\n".join(lines)


def result_to_dict(result: LocalApiSmokeResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "exit_code": result.exit_code,
        "created_task_ids": result.created_task_ids,
        "completed_task_ids": result.completed_task_ids,
        "deleted_task_ids": result.deleted_task_ids,
        "reminder_id": result.reminder_id,
        "reminder_cancelled": result.reminder_cancelled,
        "backup_id": result.backup_id,
        "backup_created": result.backup_created,
        "cleanup_active_tasks_remaining": result.cleanup_active_tasks_remaining,
        "cleanup_active_reminders_remaining": result.cleanup_active_reminders_remaining,
        "confirmations_before": result.confirmations_before,
        "confirmations_after": result.confirmations_after,
        "sort_confirmation_id": result.sort_confirmation_id,
        "priority_items": result.priority_items,
        "warnings": result.warnings,
        "errors": result.errors,
    }


def _cleanup(client: ApiClient, result: LocalApiSmokeResult) -> None:
    for status in ("active", "completed", "deleted"):
        tasks = client.request("GET", f"/api/tasks?status={status}&q={urllib.parse.quote(SMOKE_PREFIX)}").get("tasks") or []
        for task in tasks:
            task_id = task.get("id")
            if not task_id:
                continue
            try:
                if status != "deleted":
                    client.request("DELETE", f"/api/tasks/{task_id}")
                client.request("DELETE", f"/api/tasks/{task_id}/permanent")
                result.deleted_task_ids.append(task_id)
            except urllib.error.HTTPError as exc:
                if exc.code not in {404, 409}:
                    raise
    reminders = client.request("GET", f"/api/reminders?status=pending&limit=200").get("reminders") or []
    for reminder in reminders:
        if SMOKE_PREFIX in str(reminder.get("message") or ""):
            client.request("POST", f"/api/reminders/{reminder['id']}/cancel")


def _verify_cleanup(client: ApiClient, result: LocalApiSmokeResult) -> None:
    active_tasks = client.request("GET", f"/api/tasks?status=active&q={urllib.parse.quote(SMOKE_PREFIX)}").get("tasks") or []
    completed_tasks = client.request("GET", f"/api/tasks?status=completed&q={urllib.parse.quote(SMOKE_PREFIX)}").get("tasks") or []
    reminders = client.request("GET", "/api/reminders?status=pending&limit=200").get("reminders") or []
    result.cleanup_active_tasks_remaining = len(active_tasks) + len(completed_tasks)
    result.cleanup_active_reminders_remaining = len([item for item in reminders if SMOKE_PREFIX in str(item.get("message") or "")])
    if result.cleanup_active_tasks_remaining:
        result.error("Smoke tasks remain after cleanup.")
    if result.cleanup_active_reminders_remaining:
        result.error("Smoke reminders remain after cleanup.")


def _pending_confirmations(client: ApiClient) -> list[dict[str, Any]]:
    return client.request("GET", "/api/confirmations").get("confirmations") or []


def _validate_system_status(payload: dict[str, Any], result: LocalApiSmokeResult) -> None:
    if not payload.get("generated_at"):
        result.error("System status missing generated_at.")
    if not isinstance(payload.get("services"), dict):
        result.error("System status missing services.")
    if not isinstance(payload.get("weekend_mode"), dict):
        result.error("System status missing weekend_mode.")
    services = payload.get("services") or {}
    if "backups" not in services:
        result.error("System status missing backups service.")
    sensitive_keys = {"token", "api_key", "secret", "password", "ciphertext", "authorization"}
    for path, value in _walk_payload(payload):
        key = path.rsplit(".", maxsplit=1)[-1].casefold()
        if key in sensitive_keys or key.endswith(("_token", "_api_key", "_password", "_ciphertext")):
            result.error(f"System status contains sensitive field: {path}.")
        if isinstance(value, str) and re.search(r"(?i)(?:sk-|bearer\\s+)[a-z0-9_-]{16,}", value):
            result.error(f"System status contains a secret-like value at: {path}.")
    llm = services.get("llm") if isinstance(services.get("llm"), dict) else {}
    if llm.get("status") == "active" and llm.get("safe_to_use") is not True:
        result.error("AI status appears active without safe_to_use.")


def _walk_payload(value: Any, path: str = "root"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_payload(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_payload(item, f"{path}[{index}]")
        return
    yield path, value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local API smoke for RC checks.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8711")
    args = parser.parse_args()
    if args.cleanup_only:
        client = ApiClient(args.base_url)
        result = LocalApiSmokeResult()
        try:
            _cleanup(client, result)
            _verify_cleanup(client, result)
        except Exception as exc:  # noqa: BLE001
            result.error(f"Cleanup-only failed: {exc}")
    else:
        result = run_local_api_smoke(args.base_url)
    if args.json:
        print(json.dumps(result_to_dict(result), ensure_ascii=True, indent=2))
    else:
        print(format_result(result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
