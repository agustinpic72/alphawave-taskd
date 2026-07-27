#!/usr/bin/env python
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any
import zipfile


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.services.diagnostics import sanitize_diagnostics, redact_sensitive  # noqa: E402


def main() -> int:
    base_url = os.environ.get("ALPHAWAVE_BASE_URL", "http://127.0.0.1:8711").rstrip("/")
    output_dir = Path(os.environ.get("ALPHAWAVE_DIAGNOSTICS_OUTPUT_DIR", ROOT_DIR / "reports" / "support-bundles"))
    include_rc = os.environ.get("ALPHAWAVE_DIAGNOSTICS_INCLUDE_RC_REPORTS", "1") == "1"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / f"alphawave-diagnostics-{timestamp}.zip"

    app_available = True
    try:
        system_status = read_json(f"{base_url}/api/system/status")
        diagnostics = read_json(f"{base_url}/api/system/diagnostics")
    except Exception as exc:  # noqa: BLE001 - partial bundle is useful.
        app_available = False
        system_status = {"status": "unavailable", "error": redact_sensitive(str(exc))}
        diagnostics = {"generated_at": datetime.now(timezone.utc).isoformat(), "app_status": "unavailable", "error": redact_sensitive(str(exc))}

    manifest = sanitize_diagnostics(
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "base_url": base_url,
            "app_available": app_available,
            "bundle": bundle_path.name,
            "contents": [
                "manifest.json",
                "diagnostics.json",
                "system-status.json",
                "git-summary.txt",
                "docs-snapshot/dogfooding-findings.md",
                "docs-snapshot/settings-regression-matrix.md",
            ],
            "redaction": {"applied": True, "rules_version": 1},
        }
    )
    if include_rc:
        manifest["contents"].append("rc-reports/rc-check-*.md")

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        write_json(bundle, "manifest.json", manifest)
        write_json(bundle, "diagnostics.json", diagnostics)
        write_json(bundle, "system-status.json", system_status)
        write_text(bundle, "git-summary.txt", git_summary())
        write_doc(bundle, "docs/dogfooding-findings.md", "docs-snapshot/dogfooding-findings.md")
        write_doc(bundle, "docs/settings-regression-matrix.md", "docs-snapshot/settings-regression-matrix.md")
        if include_rc:
            for report in latest_rc_reports(limit=3):
                write_text(bundle, f"rc-reports/{report.name}", report.read_text(encoding="utf-8", errors="replace"))

    print(f"Diagnostics bundle: {bundle_path}")
    print(f"App available: {'yes' if app_available else 'no'}")
    return 0


def read_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read().decode("utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected payload from {url}")
    return sanitize_diagnostics(value)


def write_json(bundle: zipfile.ZipFile, name: str, payload: Any) -> None:
    bundle.writestr(name, json.dumps(sanitize_diagnostics(payload), ensure_ascii=True, indent=2))


def write_text(bundle: zipfile.ZipFile, name: str, text: str) -> None:
    bundle.writestr(name, redact_sensitive(text))


def write_doc(bundle: zipfile.ZipFile, source: str, target: str) -> None:
    path = ROOT_DIR / source
    if path.exists():
        write_text(bundle, target, path.read_text(encoding="utf-8", errors="replace"))


def git_summary() -> str:
    commands = [
        ["git", "rev-parse", "--short", "HEAD"],
        ["git", "branch", "--show-current"],
        ["git", "status", "--short", "--branch"],
        ["git", "log", "--oneline", "-10"],
    ]
    sections = []
    for command in commands:
        sections.append("$ " + " ".join(command))
        sections.append(run_capture(command))
    return "\n".join(sections)


def run_capture(command: list[str]) -> str:
    completed = subprocess.run(command, cwd=ROOT_DIR, check=False, capture_output=True, text=True, timeout=5)
    return redact_sensitive((completed.stdout or completed.stderr or "").strip())


def latest_rc_reports(*, limit: int) -> list[Path]:
    reports = sorted((ROOT_DIR / "reports").glob("rc-check-*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    return reports[:limit]


if __name__ == "__main__":
    raise SystemExit(main())
