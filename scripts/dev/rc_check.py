#!/usr/bin/env python
from __future__ import annotations

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
import urllib.request
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT_DIR / "reports"


@dataclass
class StepResult:
    name: str
    command: str
    status: str
    duration_seconds: float
    output: str = ""
    warnings: list[str] = field(default_factory=list)


def main() -> int:
    base_url = os.environ.get("ALPHAWAVE_BASE_URL", "http://127.0.0.1:8711").rstrip("/")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_path = REPORTS_DIR / f"rc-check-{timestamp}.md"
    steps: list[StepResult] = []
    warnings: list[str] = []

    print(f"RC check base URL: {base_url}")
    print("Telegram send: opt-in" if os.environ.get("ALPHAWAVE_RC_SEND_TELEGRAM") == "1" else "Telegram send: disabled")

    if os.environ.get("ALPHAWAVE_RC_RESTART") == "1":
        steps.append(run_command("restart service", ["./scripts/restart.sh"]))
    startup_step = wait_for_health(base_url)
    steps.append(startup_step)
    if startup_step.status == "pass" and startup_step.duration_seconds > 5:
        message = f"Startup health took {startup_step.duration_seconds:.2f}s."
        startup_step.warnings.append(message)
        warnings.append(message)
    if startup_step.status == "pass" and startup_step.duration_seconds > 10:
        startup_step.status = "fail"

    steps.append(run_command("git status", ["git", "status", "--short", "--branch"]))
    steps.append(run_command("backend pytest durations", ["backend/.venv/bin/pytest", "-q", "--durations=50"], timeout=120))
    steps.append(run_command("frontend build", ["npm", "run", "build"], cwd=ROOT_DIR / "frontend", timeout=120))
    steps.append(system_status_sanity(base_url))

    if os.environ.get("ALPHAWAVE_RC_SKIP_BROWSER") != "1":
        steps.append(run_command("browser smoke", ["./scripts/dev/browser-smoke.sh"], env={"ALPHAWAVE_BASE_URL": base_url}, timeout=120))
    else:
        steps.append(StepResult("browser smoke", "skipped by ALPHAWAVE_RC_SKIP_BROWSER", "skip", 0))

    if os.environ.get("ALPHAWAVE_RC_SKIP_TRELLO") != "1":
        trello_env = {"ALPHAWAVE_BASE_URL": base_url}
        if os.environ.get("ALPHAWAVE_RC_INCLUDE_TRELLO_CARDS") == "1":
            trello_env["ALPHAWAVE_TRELLO_SMOKE_INCLUDE_CARDS"] = "1"
        steps.append(run_command("Trello read-only smoke", ["./scripts/dev/trello-readonly-smoke.sh"], env=trello_env, timeout=60, warning_ok=True))
    else:
        steps.append(StepResult("Trello read-only smoke", "skipped by ALPHAWAVE_RC_SKIP_TRELLO", "skip", 0))

    if os.environ.get("ALPHAWAVE_RC_SKIP_TELEGRAM") != "1":
        telegram_env = {"ALPHAWAVE_BASE_URL": base_url}
        if os.environ.get("ALPHAWAVE_RC_SEND_TELEGRAM") == "1":
            telegram_env["ALPHAWAVE_TELEGRAM_SMOKE_SEND"] = "1"
        steps.append(run_command("Telegram live smoke", ["./scripts/dev/telegram-live-smoke.sh"], env=telegram_env, timeout=60))
    else:
        steps.append(StepResult("Telegram live smoke", "skipped by ALPHAWAVE_RC_SKIP_TELEGRAM", "skip", 0))

    steps.append(run_command("local API smoke", ["./scripts/dev/local-api-smoke.sh"], env={"ALPHAWAVE_BASE_URL": base_url}, timeout=60))
    steps.append(
        run_command(
            "cleanup verification",
            ["backend/.venv/bin/python", "scripts/dev/local_api_smoke.py", "--cleanup-only", "--base-url", base_url],
            timeout=60,
        )
    )

    failures = [step for step in steps if step.status == "fail"]
    report = build_report(report_path, steps, warnings, base_url)
    REPORTS_DIR.mkdir(exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print("")
    print(f"RC report: {report_path}")
    print(f"RC status: {'FAIL' if failures else 'PASS'}")
    return 1 if failures else 0


def run_command(
    name: str,
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 30,
    warning_ok: bool = False,
) -> StepResult:
    display = " ".join(command)
    start = time.monotonic()
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd or ROOT_DIR,
            env=merged_env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = sanitize_output((completed.stdout or "") + (completed.stderr or ""))
        duration = time.monotonic() - start
        status = "pass" if completed.returncode == 0 else "fail"
        warnings = []
        if warning_ok and completed.returncode == 0 and re.search(r"\bWARNING\b|Status:\s*WARNING", output, re.IGNORECASE):
            status = "pass"
            warnings.append("Completed with warnings.")
        print(f"[{status.upper()}] {name} ({duration:.2f}s)")
        return StepResult(name, display, status, duration, output=output, warnings=warnings)
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        output = sanitize_output((exc.stdout or "") + (exc.stderr or "") + f"\nTimed out after {timeout}s.")
        print(f"[FAIL] {name} ({duration:.2f}s)")
        return StepResult(name, display, "fail", duration, output=output)


def wait_for_health(base_url: str) -> StepResult:
    start = time.monotonic()
    deadline = start + 15
    last_error = ""
    while time.monotonic() < deadline:
        try:
            payload = read_json(f"{base_url}/api/health")
            duration = time.monotonic() - start
            status = "pass" if payload.get("status") == "ok" else "fail"
            print(f"[{status.upper()}] app health ({duration:.2f}s)")
            return StepResult("app health", f"GET {base_url}/api/health", status, duration, output=json.dumps(payload, indent=2))
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(0.25)
    duration = time.monotonic() - start
    print(f"[FAIL] app health ({duration:.2f}s)")
    return StepResult("app health", f"GET {base_url}/api/health", "fail", duration, output=sanitize_output(last_error))


def system_status_sanity(base_url: str) -> StepResult:
    start = time.monotonic()
    command = f"GET {base_url}/api/system/status"
    errors: list[str] = []
    warnings: list[str] = []
    try:
        payload = read_json(f"{base_url}/api/system/status")
        if not payload.get("generated_at"):
            errors.append("missing generated_at")
        services = payload.get("services")
        if not isinstance(services, dict):
            errors.append("missing services")
            services = {}
        if not isinstance(payload.get("weekend_mode"), dict):
            errors.append("missing weekend_mode")
        if "backups" not in services:
            errors.append("missing backups service")
        serialized = json.dumps(payload, ensure_ascii=True).casefold()
        for marker in ("token", "api_key", "secret", "password"):
            if marker in serialized:
                errors.append(f"sensitive marker present: {marker}")
        llm = services.get("llm") if isinstance(services.get("llm"), dict) else {}
        if llm.get("status") == "active" and "not_verified" in str(llm.get("detail", "")).casefold():
            errors.append("LLM not_verified appears active")
        trello = services.get("trello") if isinstance(services.get("trello"), dict) else {}
        if trello.get("boards") and not isinstance(trello.get("boards"), list):
            errors.append("trello boards should be a dynamic list")
        output = json.dumps(redacted_status_summary(payload), ensure_ascii=True, indent=2)
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
        output = str(exc)
    duration = time.monotonic() - start
    status = "fail" if errors else "pass"
    print(f"[{status.upper()}] system status sanity ({duration:.2f}s)")
    if warnings:
        output += "\nWarnings:\n" + "\n".join(warnings)
    if errors:
        output += "\nErrors:\n" + "\n".join(errors)
    return StepResult("system status sanity", command, status, duration, sanitize_output(output), warnings)


def read_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read().decode("utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected payload from {url}")
    return value


def redacted_status_summary(payload: dict[str, Any]) -> dict[str, Any]:
    services = payload.get("services") if isinstance(payload.get("services"), dict) else {}
    return {
        "generated_at": payload.get("generated_at"),
        "overall": payload.get("overall"),
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
        "weekend_mode": payload.get("weekend_mode"),
    }


def sanitize_output(value: str) -> str:
    text = str(value)
    text = re.sub(r"https://api\.telegram\.org/bot[^/\s]+", "https://api.telegram.org/bot[redacted]", text)
    text = re.sub(r"bot[0-9]+:[A-Za-z0-9_-]+", "bot[redacted]", text)
    text = re.sub(r"([?&](?:key|token)=)[^&\s]+", r"\1[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(token|api_key|secret|password)(['\"]?\s*[:=]\s*['\"]?)[^,'\"\s}]+", r"\1\2[redacted]", text)
    return text[-12000:]


def build_report(report_path: Path, steps: list[StepResult], warnings: list[str], base_url: str) -> str:
    head = run_capture(["git", "rev-parse", "--short", "HEAD"])
    branch = run_capture(["git", "branch", "--show-current"])
    status = run_capture(["git", "status", "--short", "--branch"])
    failures = [step for step in steps if step.status == "fail"]
    lines = [
        "# RC Check Report",
        "",
        f"- timestamp: {datetime.now(timezone.utc).isoformat()}",
        f"- base_url: {base_url}",
        f"- branch: {branch}",
        f"- commit: {head}",
        f"- report: {report_path}",
        "",
        "## Git",
        "",
        "```text",
        sanitize_output(status),
        "```",
        "",
        "## Summary",
        "",
    ]
    for step in steps:
        label = "PASS" if step.status == "pass" else "SKIP" if step.status == "skip" else "FAIL"
        lines.append(f"- {label}: {step.name} ({step.duration_seconds:.2f}s)")
        for warning in step.warnings:
            lines.append(f"  - warning: {warning}")
    lines.extend(["", "## Step Details", ""])
    for step in steps:
        lines.extend(
            [
                f"### {step.name}",
                "",
                f"- status: {step.status}",
                f"- command: `{step.command}`",
                f"- duration_seconds: {step.duration_seconds:.2f}",
                "",
                "```text",
                step.output.strip() or "(no output)",
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Warnings",
            "",
            *(f"- {warning}" for warning in warnings),
            *([] if warnings else ["- none"]),
            "",
            "## Failures",
            "",
            *(f"- {step.name}" for step in failures),
            *([] if failures else ["- none"]),
            "",
            "## Manual Checks Still Needed",
            "",
            "- Telegram command round-trip: `/todo`, `qué hago ahora`, optional `[M17D SMOKE]` task creation and cleanup.",
            "- Visual dogfooding beyond Playwright smoke for mobile/touch and long Settings sessions.",
            "- Trello writes and auto-confirm remain intentionally untested by RC check.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_capture(command: list[str]) -> str:
    completed = subprocess.run(command, cwd=ROOT_DIR, check=False, capture_output=True, text=True)
    return sanitize_output((completed.stdout or completed.stderr or "").strip())


if __name__ == "__main__":
    raise SystemExit(main())
