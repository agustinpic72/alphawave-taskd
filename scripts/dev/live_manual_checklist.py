#!/usr/bin/env python
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol


ROOT_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT_DIR / "reports"
SMOKE_PREFIX = "[M17F SMOKE]"


class WriteSmokeRefusal(RuntimeError):
    pass


class TrelloWriteClientProtocol(Protocol):
    def create_card(self, list_id: str, title: str, description: str) -> dict[str, Any]:
        ...

    def close_card(self, card_id: str) -> dict[str, Any]:
        ...


@dataclass
class StepResult:
    name: str
    status: str
    detail: str = ""
    duration_seconds: float = 0.0


@dataclass
class ManualResult:
    todo: str = "skipped"
    now: str = "skipped"
    create_task: str = "skipped"
    complete_task: str = "skipped"
    notes: list[str] = field(default_factory=list)


@dataclass
class CleanupResult:
    local_tasks_removed: int = 0
    reminders_cancelled: int = 0
    confirmations_cancelled: int = 0
    active_tasks_remaining: int = 0
    active_reminders_remaining: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class TrelloWriteResult:
    status: str = "skipped"
    board_alias: str | None = None
    list_id_hint: str | None = None
    card_id_hint: str | None = None
    card_url_hint: str | None = None
    cleanup: str = "skipped"
    detail: str = "Opt-in not enabled."


@dataclass
class LiveManualChecklistReport:
    base_url: str
    report_path: Path
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    commit: str = ""
    branch: str = ""
    git_status: str = ""
    runtime_status: dict[str, Any] = field(default_factory=dict)
    diagnostics_bundle_path: str | None = None
    preflight_steps: list[StepResult] = field(default_factory=list)
    telegram_manual: ManualResult = field(default_factory=ManualResult)
    telegram_send: StepResult = field(default_factory=lambda: StepResult("Telegram send smoke", "skipped", "Opt-in not enabled."))
    trello_readonly: StepResult = field(default_factory=lambda: StepResult("Trello read-only smoke", "skipped"))
    trello_write: TrelloWriteResult = field(default_factory=TrelloWriteResult)
    cleanup: CleanupResult = field(default_factory=CleanupResult)
    bugs_found: list[str] = field(default_factory=list)
    bugs_fixed: list[str] = field(default_factory=list)
    not_verified: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        if any(step.status == "fail" for step in self.preflight_steps):
            return True
        if self.telegram_send.status == "fail" or self.trello_readonly.status == "fail":
            return True
        return self.trello_write.status == "fail"


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
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


class TrelloWriteClient:
    def __init__(self, api_key: str, token: str, *, timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.token = token
        self.timeout = timeout
        self.base_url = "https://api.trello.com/1"

    def create_card(self, list_id: str, title: str, description: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/cards",
            {"idList": list_id, "name": title, "desc": description},
        )

    def close_card(self, card_id: str) -> dict[str, Any]:
        return self._request("PUT", f"/cards/{urllib.parse.quote(card_id)}", {"closed": "true"})

    def _request(self, method: str, path: str, params: dict[str, str]) -> dict[str, Any]:
        query = dict(params)
        query.update({"key": self.api_key, "token": self.token})
        data = urllib.parse.urlencode(query).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
        value = json.loads(raw) if raw else {}
        if not isinstance(value, dict):
            raise RuntimeError("Unexpected Trello response.")
        return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the guarded M17F live manual checklist assistant.")
    parser.add_argument("--base-url", default=os.environ.get("ALPHAWAVE_BASE_URL", "http://127.0.0.1:8711"))
    parser.add_argument("--interactive", action="store_true", help="Ask the operator to mark manual Telegram steps.")
    parser.add_argument("--skip-rc", action="store_true", help="Use lightweight preflight instead of rc-check.")
    parser.add_argument("--skip-diagnostics", action="store_true", help="Do not generate a diagnostics bundle.")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report = LiveManualChecklistReport(base_url=base_url, report_path=REPORTS_DIR / f"live-manual-checklist-{timestamp}.md")
    report.commit = run_capture(["git", "rev-parse", "--short", "HEAD"])
    report.branch = run_capture(["git", "branch", "--show-current"])
    report.git_status = run_capture(["git", "status", "--short", "--branch"])

    client = ApiClient(base_url)
    report.preflight_steps.extend(run_preflight(client, base_url, skip_rc=args.skip_rc))
    report.runtime_status = read_runtime_status(client)
    if not args.skip_diagnostics:
        report.diagnostics_bundle_path = run_diagnostics_export(base_url)

    report.trello_readonly = run_command_step(
        "Trello read-only smoke",
        ["./scripts/dev/trello-readonly-smoke.sh"],
        env={"ALPHAWAVE_BASE_URL": base_url},
        timeout=60,
        warning_ok=True,
    )
    report.telegram_send = run_telegram_send_if_opted_in(base_url)
    report.trello_write = run_trello_write_if_opted_in(client, os.environ)

    if args.interactive:
        report.telegram_manual = run_interactive_telegram_checklist(client)
    else:
        report.not_verified.append("Telegram command round-trip manual steps were not executed in this non-interactive run.")

    report.cleanup = cleanup_m17f_data(client)
    if report.trello_write.status == "skipped":
        report.not_verified.append("Trello write smoke was skipped because ALPHAWAVE_TRELLO_WRITE_SMOKE=1 was not set.")
    if report.telegram_send.status == "skipped":
        report.not_verified.append("Telegram one-message send smoke was skipped because ALPHAWAVE_TELEGRAM_SMOKE_SEND=1 was not set.")
    if not args.interactive:
        report.telegram_manual.notes.append("Run with --interactive after sending Telegram commands manually to close the round-trip.")

    REPORTS_DIR.mkdir(exist_ok=True)
    report.report_path.write_text(render_report(report), encoding="utf-8")
    print(f"Live manual checklist report: {report.report_path}")
    print(f"Live manual checklist status: {'FAIL' if report.failed else 'PASS'}")
    return 1 if report.failed else 0


def run_preflight(client: ApiClient, base_url: str, *, skip_rc: bool) -> list[StepResult]:
    if not skip_rc:
        return [
            run_command_step(
                "RC check",
                ["./scripts/dev/rc-check.sh"],
                env={"ALPHAWAVE_BASE_URL": base_url},
                timeout=240,
                warning_ok=True,
            )
        ]
    steps: list[StepResult] = []
    start = time.monotonic()
    try:
        health = client.request("GET", "/api/health")
        status = "pass" if health.get("status") == "ok" else "fail"
        steps.append(StepResult("app health", status, json.dumps(health, indent=2), time.monotonic() - start))
    except Exception as exc:  # noqa: BLE001
        steps.append(StepResult("app health", "fail", sanitize_output(str(exc)), time.monotonic() - start))
    steps.append(run_command_step("Telegram live smoke", ["./scripts/dev/telegram-live-smoke.sh"], env={"ALPHAWAVE_BASE_URL": base_url}, timeout=60))
    return steps


def read_runtime_status(client: ApiClient) -> dict[str, Any]:
    try:
        payload = client.request("GET", "/api/system/status")
        return redacted_runtime_summary(payload)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": sanitize_output(str(exc))}


def run_diagnostics_export(base_url: str) -> str | None:
    step = run_command_step(
        "diagnostics export",
        ["./scripts/dev/export-diagnostics.sh"],
        env={"ALPHAWAVE_BASE_URL": base_url},
        timeout=60,
        warning_ok=True,
    )
    if step.status == "pass":
        match = re.search(r"(reports/support-bundles/alphawave-diagnostics-[^\s]+\.zip)", step.detail)
        return match.group(1) if match else None
    return None


def run_telegram_send_if_opted_in(base_url: str) -> StepResult:
    if os.environ.get("ALPHAWAVE_TELEGRAM_SMOKE_SEND") != "1":
        return StepResult("Telegram send smoke", "skipped", "Set ALPHAWAVE_TELEGRAM_SMOKE_SEND=1 to send one allowlisted message.")
    return run_command_step(
        "Telegram send smoke",
        ["./scripts/dev/telegram-live-smoke.sh"],
        env={"ALPHAWAVE_BASE_URL": base_url, "ALPHAWAVE_TELEGRAM_SMOKE_SEND": "1"},
        timeout=60,
    )


def run_trello_write_if_opted_in(
    api_client: ApiClient,
    env: dict[str, str],
    trello_client: TrelloWriteClientProtocol | None = None,
) -> TrelloWriteResult:
    try:
        settings_payload = api_client.request("GET", "/api/settings")
        plan = trello_write_plan(settings_payload, env)
    except WriteSmokeRefusal as exc:
        return TrelloWriteResult(status="skipped", detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        return TrelloWriteResult(status="fail", detail=sanitize_output(str(exc)))

    result = TrelloWriteResult(
        status="pass",
        board_alias=plan["board_alias"],
        list_id_hint=redact_id(plan["pending_list_id"]),
        detail="Trello write smoke created a prefixed card.",
    )
    try:
        writer = trello_client or trello_client_from_env(env)
        card = writer.create_card(
            plan["pending_list_id"],
            f"{SMOKE_PREFIX} Trello write smoke",
            "Created by guarded M17F live-manual-checklist opt-in.",
        )
        card_id = str(card.get("id") or "")
        result.card_id_hint = redact_id(card_id)
        result.card_url_hint = redact_url(str(card.get("shortUrl") or card.get("url") or ""))
        if env.get("ALPHAWAVE_TRELLO_WRITE_SMOKE_CLEANUP") == "1":
            if not card_id:
                raise RuntimeError("Cannot cleanup Trello smoke card without card id.")
            writer.close_card(card_id)
            result.cleanup = "closed"
        else:
            result.cleanup = "manual_needed"
            result.detail += " Cleanup opt-in was not enabled; archive/delete the card manually."
        return result
    except Exception as exc:  # noqa: BLE001
        result.status = "fail"
        result.detail = sanitize_output(str(exc))
        return result


def trello_write_plan(settings_payload: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    if env.get("ALPHAWAVE_TRELLO_WRITE_SMOKE") != "1":
        raise WriteSmokeRefusal("Set ALPHAWAVE_TRELLO_WRITE_SMOKE=1 to allow a real Trello write.")
    board_alias = (env.get("ALPHAWAVE_TRELLO_WRITE_SMOKE_BOARD") or "").strip()
    if not board_alias:
        raise WriteSmokeRefusal("Set ALPHAWAVE_TRELLO_WRITE_SMOKE_BOARD=<alias> before any Trello write.")
    board = find_trello_board(settings_payload, board_alias)
    if not board:
        raise WriteSmokeRefusal(f"Trello board alias {board_alias!r} is not configured.")
    if not board.get("enabled", True):
        raise WriteSmokeRefusal(f"Trello board alias {board_alias!r} is disabled.")
    pending = find_workflow_state(board, "pending")
    pending_list_id = str((pending or {}).get("list_id") or "").strip()
    if not pending_list_id:
        raise WriteSmokeRefusal(f"Trello board alias {board_alias!r} has no pending list_id.")
    return {"board_alias": str(board.get("alias") or board_alias), "pending_list_id": pending_list_id}


def find_trello_board(settings_payload: dict[str, Any], alias: str) -> dict[str, Any] | None:
    boards = (((settings_payload.get("settings") or {}).get("trello") or {}).get("boards") or {})
    if isinstance(boards, dict):
        for key, board in boards.items():
            if isinstance(board, dict) and str(board.get("alias") or key).casefold() == alias.casefold():
                return board
    return None


def find_workflow_state(board: dict[str, Any], role: str) -> dict[str, Any] | None:
    states = board.get("workflow_states")
    if isinstance(states, list):
        for state in states:
            if isinstance(state, dict) and bool(state.get("enabled", True)) and str(state.get("role") or state.get("key")) == role:
                return state
    legacy_states = board.get("states")
    if isinstance(legacy_states, dict) and isinstance(legacy_states.get(role), dict):
        state = legacy_states[role]
        if bool(state.get("enabled", True)):
            return state
    return None


def trello_client_from_env(env: dict[str, str]) -> TrelloWriteClient:
    root = str(ROOT_DIR / "backend")
    if root not in sys.path:
        sys.path.insert(0, root)
    from app.core.config import settings  # noqa: PLC0415

    if not settings.trello_api_key or not settings.trello_token:
        raise WriteSmokeRefusal("Trello credentials are not configured.")
    return TrelloWriteClient(settings.trello_api_key, settings.trello_token, timeout=float(env.get("ALPHAWAVE_TRELLO_WRITE_SMOKE_TIMEOUT", "20")))


def run_interactive_telegram_checklist(client: ApiClient) -> ManualResult:
    result = ManualResult()
    print("")
    print("Telegram manual round-trip. Send each command from Telegram, then answer here.")
    result.todo = ask_status("1. Send /todo. Did the bot answer?")
    result.now = ask_status("2. Send 'qué hago ahora'. Did it answer with a plan or fallback?")
    result.create_task = ask_status(f"3. Send 'agrega {SMOKE_PREFIX} probar telegram manual'. Was it created?")
    if result.create_task == "pass":
        matches = find_tasks(client, "active", SMOKE_PREFIX)
        if matches:
            result.notes.append(f"Found {len(matches)} active smoke task(s) after Telegram create.")
        else:
            result.create_task = "fail"
            result.notes.append("No active smoke task was found after the create step.")
    result.complete_task = ask_status("4. Complete the smoke task from Telegram or UI. Did it complete?")
    if result.complete_task == "pass" and find_tasks(client, "active", SMOKE_PREFIX):
        result.complete_task = "fail"
        result.notes.append("Active smoke tasks still exist after complete step.")
    return result


def ask_status(prompt: str) -> str:
    answer = input(f"{prompt} [p]ass/[f]ail/[s]kip: ").strip().casefold()
    if answer.startswith("p"):
        return "pass"
    if answer.startswith("f"):
        return "fail"
    return "skipped"


def cleanup_m17f_data(client: ApiClient) -> CleanupResult:
    result = CleanupResult()
    try:
        for status in ("active", "completed", "deleted"):
            for task in find_tasks(client, status, SMOKE_PREFIX):
                task_id = task.get("id")
                if not task_id:
                    continue
                try:
                    if status != "deleted":
                        client.request("DELETE", f"/api/tasks/{task_id}")
                    client.request("DELETE", f"/api/tasks/{task_id}/permanent")
                    result.local_tasks_removed += 1
                except urllib.error.HTTPError as exc:
                    if exc.code not in {404, 409}:
                        raise
        reminders = client.request("GET", "/api/reminders?status=pending&limit=200").get("reminders") or []
        for reminder in reminders:
            if SMOKE_PREFIX in str(reminder.get("message") or ""):
                client.request("POST", f"/api/reminders/{reminder['id']}/cancel")
                result.reminders_cancelled += 1
        confirmations = client.request("GET", "/api/confirmations").get("confirmations") or []
        for confirmation in confirmations:
            payload = json.dumps(confirmation, ensure_ascii=False)
            if SMOKE_PREFIX in payload:
                client.request("POST", f"/api/confirmations/{confirmation['id']}/cancel")
                result.confirmations_cancelled += 1
        result.active_tasks_remaining = len(find_tasks(client, "active", SMOKE_PREFIX)) + len(find_tasks(client, "completed", SMOKE_PREFIX))
        pending_reminders = client.request("GET", "/api/reminders?status=pending&limit=200").get("reminders") or []
        result.active_reminders_remaining = len([item for item in pending_reminders if SMOKE_PREFIX in str(item.get("message") or "")])
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(sanitize_output(str(exc)))
    return result


def find_tasks(client: ApiClient, status: str, query: str) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(query)
    return client.request("GET", f"/api/tasks?status={status}&q={encoded}").get("tasks") or []


def run_command_step(
    name: str,
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = 60,
    warning_ok: bool = False,
) -> StepResult:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    start = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=ROOT_DIR, env=merged_env, check=False, capture_output=True, text=True, timeout=timeout)
        detail = sanitize_output((completed.stdout or "") + (completed.stderr or ""))
        status = "pass" if completed.returncode == 0 else "fail"
        if warning_ok and completed.returncode == 0 and re.search(r"\bWARNING\b|Status:\s*WARNING", detail, re.IGNORECASE):
            status = "pass"
        print(f"[{status.upper()}] {name} ({time.monotonic() - start:.2f}s)")
        return StepResult(name, status, detail, time.monotonic() - start)
    except subprocess.TimeoutExpired as exc:
        detail = sanitize_output((exc.stdout or "") + (exc.stderr or "") + f"\nTimed out after {timeout}s.")
        print(f"[FAIL] {name} ({time.monotonic() - start:.2f}s)")
        return StepResult(name, "fail", detail, time.monotonic() - start)


def run_capture(command: list[str]) -> str:
    try:
        return subprocess.run(command, cwd=ROOT_DIR, check=False, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def render_report(report: LiveManualChecklistReport) -> str:
    lines = [
        "# Live Manual Checklist Report",
        "",
        "## Runtime",
        "",
        f"- generated_at: {report.generated_at}",
        f"- base_url: {report.base_url}",
        f"- branch: {report.branch}",
        f"- commit: {report.commit}",
        f"- status: {'fail' if report.failed else 'pass'}",
        f"- diagnostics_bundle_path: {report.diagnostics_bundle_path or 'not generated'}",
        "",
        "```text",
        sanitize_output(report.git_status),
        "```",
        "",
        "## System Status",
        "",
        "```json",
        json.dumps(report.runtime_status, ensure_ascii=True, indent=2),
        "```",
        "",
        "## Preflight",
        "",
    ]
    for step in report.preflight_steps:
        lines.append(f"- {step.status}: {step.name} ({step.duration_seconds:.2f}s)")
    lines.extend(
        [
            "",
            "## Telegram",
            "",
            f"- getMe/default smoke: {_step_status(report.preflight_steps, 'Telegram live smoke')}",
            f"- manual /todo: {report.telegram_manual.todo}",
            f"- manual que hago ahora: {report.telegram_manual.now}",
            f"- manual create task: {report.telegram_manual.create_task}",
            f"- manual complete task: {report.telegram_manual.complete_task}",
            f"- send smoke opt-in: {report.telegram_send.status}",
        ]
    )
    for note in report.telegram_manual.notes:
        lines.append(f"- note: {note}")
    lines.extend(
        [
            "",
            "## Trello",
            "",
            f"- read-only smoke result: {report.trello_readonly.status}",
            f"- write smoke: {report.trello_write.status}",
            f"- board used: {report.trello_write.board_alias or 'none'}",
            f"- pending list id: {report.trello_write.list_id_hint or 'none'}",
            f"- card created: {report.trello_write.card_id_hint or 'none'}",
            f"- card url: {report.trello_write.card_url_hint or 'none'}",
            f"- cleanup: {report.trello_write.cleanup}",
            f"- detail: {report.trello_write.detail}",
            "",
            "## Cleanup",
            "",
            f"- local tasks removed: {report.cleanup.local_tasks_removed}",
            f"- reminders cancelled: {report.cleanup.reminders_cancelled}",
            f"- confirmations cancelled: {report.cleanup.confirmations_cancelled}",
            f"- active smoke tasks remaining: {report.cleanup.active_tasks_remaining}",
            f"- active smoke reminders remaining: {report.cleanup.active_reminders_remaining}",
            "",
            "## Bugs Found",
            "",
        ]
    )
    lines.extend([f"- {item}" for item in report.bugs_found] or ["- none"])
    lines.extend(["", "## Bugs Fixed", ""])
    lines.extend([f"- {item}" for item in report.bugs_fixed] or ["- none"])
    lines.extend(["", "## Not Verified", ""])
    lines.extend([f"- {item}" for item in report.not_verified] or ["- none"])
    lines.extend(["", "## Step Details", ""])
    for step in [*report.preflight_steps, report.trello_readonly, report.telegram_send]:
        lines.extend(
            [
                f"### {step.name}",
                "",
                f"- status: {step.status}",
                f"- duration_seconds: {step.duration_seconds:.2f}",
                "",
                "```text",
                step.detail.strip() or "(no output)",
                "```",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _step_status(steps: list[StepResult], name: str) -> str:
    for step in steps:
        if step.name == name:
            return step.status
    for step in steps:
        if step.name == "RC check" and "Telegram live smoke" in step.detail:
            return "covered by rc-check"
    return "not run"


def redacted_runtime_summary(payload: dict[str, Any]) -> dict[str, Any]:
    services = payload.get("services") if isinstance(payload.get("services"), dict) else {}
    return {
        "overall": payload.get("overall"),
        "generated_at": payload.get("generated_at"),
        "runtime": payload.get("runtime") or payload.get("environment"),
        "weekend_mode": payload.get("weekend_mode"),
        "services": {
            key: {
                "status": value.get("status"),
                "enabled": value.get("enabled"),
                "configured": value.get("configured"),
                "detail": value.get("detail"),
            }
            for key, value in services.items()
            if isinstance(value, dict)
        },
    }


def sanitize_output(value: str) -> str:
    text = str(value)
    text = re.sub(r"https://api\.telegram\.org/bot[^/\s]+", "https://api.telegram.org/bot[redacted]", text)
    text = re.sub(r"bot[0-9]+:[A-Za-z0-9_-]+", "bot[redacted]", text)
    text = re.sub(r"([?&](?:key|token)=)[^&\s]+", r"\1[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(token|api_key|secret|password)(['\"]?\s*[:=]\s*['\"]?)[^,'\"\s}]+", r"\1\2[redacted]", text)
    text = re.sub(r"(?i)(chat_id|chat id)(['\"]?\s*[:=]\s*['\"]?)-?\d+", r"\1\2[redacted]", text)
    return text[-12000:]


def redact_id(value: str) -> str:
    if not value:
        return ""
    return f"{value[:4]}...{value[-4:]}" if len(value) > 10 else "[redacted]"


def redact_url(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"/c/([^/\s]+)", r"/c/[redacted]", value)


if __name__ == "__main__":
    raise SystemExit(main())
