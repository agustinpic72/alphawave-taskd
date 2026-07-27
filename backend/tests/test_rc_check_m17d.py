import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str):
    path = ROOT / "scripts" / "dev" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_rc_sanitize_output_redacts_secret_like_values():
    rc_check = load_script("rc_check")
    raw = "url=https://api.telegram.org/bot123:SECRET/sendMessage&token=abc token='secret' api_key=key"

    redacted = rc_check.sanitize_output(raw)

    assert "123:SECRET" not in redacted
    assert "token=abc" not in redacted
    assert "api_key=key" not in redacted
    assert "[redacted]" in redacted


def test_rc_report_does_not_include_sensitive_step_output(tmp_path, monkeypatch):
    rc_check = load_script("rc_check")
    monkeypatch.setattr(rc_check, "run_capture", lambda command: "main" if "branch" in command else "abc123")
    step = rc_check.StepResult(
        name="secret step",
        command="fake",
        status="pass",
        duration_seconds=0.1,
        output=rc_check.sanitize_output("https://api.telegram.org/bot123:SECRET/getMe password=hidden"),
    )

    report = rc_check.build_report(tmp_path / "report.md", [step], [], "http://127.0.0.1:8711")

    assert "123:SECRET" not in report
    assert "password=hidden" not in report
    assert "bot[redacted]" in report


def test_local_api_smoke_result_serializes_partial_cleanup_failure():
    local_api = load_script("local_api_smoke")
    result = local_api.LocalApiSmokeResult()

    result.error("Cleanup failed: boom")
    payload = local_api.result_to_dict(result)

    assert payload["status"] == "error"
    assert payload["exit_code"] == 1
    assert payload["errors"] == ["Cleanup failed: boom"]


def test_local_api_smoke_allows_actionable_ai_status_but_rejects_secret_fields():
    local_api = load_script("local_api_smoke")
    base = {
        "generated_at": "2026-07-18T10:00:00+00:00",
        "services": {
            "backups": {},
            "llm": {
                "status": "requires_configuration",
                "readiness": "requires_api_key",
                "safe_to_use": False,
            },
        },
        "weekend_mode": {},
    }
    allowed = local_api.LocalApiSmokeResult()
    local_api._validate_system_status(base, allowed)
    assert allowed.errors == []

    leaked = local_api.LocalApiSmokeResult()
    base["services"]["llm"]["api_key"] = "sk-example-value-that-must-not-appear"
    local_api._validate_system_status(base, leaked)
    assert leaked.errors
