import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

from app.core.config import settings
from app.services import system as system_service


ROOT = Path(__file__).resolve().parents[2]
ONBOARDING_PATH = ROOT / "scripts" / "lib" / "onboarding.py"
spec = importlib.util.spec_from_file_location("onboarding", ONBOARDING_PATH)
onboarding = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["onboarding"] = onboarding
spec.loader.exec_module(onboarding)


def test_env_example_required_vars_and_gitignore():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_USER_ID",
        "TRELLO_API_KEY",
        "TRELLO_TOKEN",
        "TRELLO_MEMBER_ID",
        "TRELLO_BOARD_ALPHA_ID",
        "TRELLO_BOARD_BETA_ID",
        "DAILY_BRIEFING_TIME",
        "BACKUP_RETENTION_DAYS",
    ):
        assert f"{key}=" in env_example
    assert ".env" in gitignore
    assert "data/backups/" in gitignore
    assert "logs/" in gitignore


def test_setup_env_does_not_overwrite_existing_env(tmp_path):
    fake_root = tmp_path / "repo"
    shutil.copytree(ROOT / "scripts", fake_root / "scripts")
    (fake_root / "backend").mkdir()
    (fake_root / "backend" / "pyproject.toml").write_text("", encoding="utf-8")
    (fake_root / "frontend").mkdir()
    (fake_root / "frontend" / "package.json").write_text("{}", encoding="utf-8")
    (fake_root / ".env.example").write_text("APP_HOST=127.0.0.1\n", encoding="utf-8")
    (fake_root / ".env").write_text("KEEP=1\n", encoding="utf-8")

    result = subprocess.run([str(fake_root / "scripts" / "setup-env.sh")], cwd=fake_root, check=False, capture_output=True, text=True)

    assert result.returncode == 0
    assert "Not overwriting" in result.stdout
    assert (fake_root / ".env").read_text(encoding="utf-8") == "KEEP=1\n"


def test_telegram_whoami_parses_updates_and_masks_token(tmp_path, monkeypatch, capsys):
    env_path = tmp_path / ".env"
    env_path.write_text("TELEGRAM_BOT_TOKEN=123456:super-secret-token\n", encoding="utf-8")

    def fake_get_json(url):
        assert "super-secret-token" in url
        return {"result": [{"message": {"from": {"id": 123, "username": "demo-user", "first_name": "Demo"}, "text": "/start"}}]}

    monkeypatch.setattr(onboarding, "http_get_json", fake_get_json)

    code = onboarding.telegram_whoami(env_path, assume_yes=True)
    output = capsys.readouterr().out

    assert code == 0
    assert "TELEGRAM_ALLOWED_USER_ID=123" in output
    assert "super-secret-token" not in output


def test_telegram_whoami_write_updates_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("TELEGRAM_BOT_TOKEN=token\nTELEGRAM_ALLOWED_USER_ID=\n", encoding="utf-8")
    monkeypatch.setattr(onboarding, "http_get_json", lambda url: {"result": [{"message": {"from": {"id": 456}, "text": "hi"}}]})

    code = onboarding.telegram_whoami(env_path, write=True, assume_yes=True)

    assert code == 0
    assert "TELEGRAM_ALLOWED_USER_ID=456" in env_path.read_text(encoding="utf-8")


def test_trello_discover_detects_boards_lists_and_does_not_print_tokens(tmp_path, monkeypatch, capsys):
    env_path = tmp_path / ".env"
    env_path.write_text("TRELLO_API_KEY=key-secret\nTRELLO_TOKEN=token-secret\n", encoding="utf-8")

    def fake_get_json(url):
        assert "key-secret" in url
        if "/members/me/boards" in url:
            return [
                {"id": "alpha-board", "name": "Project Alpha"},
                {"id": "beta-board", "name": "Project Beta"},
            ]
        if "/boards/alpha-board/lists" in url:
            return [{"id": "l1", "name": "TAREAS"}]
        if "/boards/beta-board/lists" in url:
            return [{"id": "l2", "name": "EN PROCESO"}]
        if "/members/me" in url:
            return {"id": "member-1", "username": "demo-user"}
        raise AssertionError(url)

    monkeypatch.setattr(onboarding, "http_get_json", fake_get_json)

    code = onboarding.trello_discover(env_path)
    output = capsys.readouterr().out

    assert code == 0
    assert "TRELLO_MEMBER_ID=member-1" in output
    assert "TRELLO_BOARD_ALPHA_ID=alpha-board" in output
    assert "TRELLO_BOARD_BETA_ID=beta-board" in output
    assert "key-secret" not in output
    assert "token-secret" not in output


def test_live_check_dry_run(capsys, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")

    code = onboarding.live_check(env_path, base_url="http://test", include_telegram=True, include_trello=True, include_openai=True, dry_run=True)
    output = capsys.readouterr().out

    assert code == 0
    assert "Live-check dry-run" in output
    assert "Telegram getMe" in output
    assert "Trello me/boards/lists" in output


def test_live_check_with_mocks(tmp_path, monkeypatch, capsys):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "TELEGRAM_ENABLED=true",
                "TELEGRAM_BOT_TOKEN=telegram-secret",
                "TELEGRAM_ALLOWED_USER_ID=123",
                "TRELLO_ENABLED=true",
                "TRELLO_API_KEY=trello-key",
                "TRELLO_TOKEN=trello-token",
                "TRELLO_BOARD_ALPHA_ID=alpha-board",
                "TRELLO_BOARD_BETA_ID=beta-board",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(onboarding, "http_request_json", lambda method, url, payload=None: {"status": "ok"})
    monkeypatch.setattr(
        onboarding,
        "http_get_json",
        lambda url: [{"name": name} for name in ("TAREAS", "EN PROCESO", "EN REVISION", "TERMINADAS")] if "/lists" in url else {"ok": True},
    )

    code = onboarding.live_check(env_path, base_url="http://test", include_telegram=True, include_trello=True, include_openai=False)
    output = capsys.readouterr().out

    assert code == 0
    assert "telegram-secret" not in output
    assert "trello-token" not in output
    assert "Live-check: ok" in output


def test_import_tasks_dry_run_and_real(tmp_path, monkeypatch, capsys):
    task_file = tmp_path / "tasks.txt"
    task_file.write_text("Retiro EUR\nDELTA: revisar endpoint de transactions\n", encoding="utf-8")

    code = onboarding.import_tasks(task_file, base_url="http://test", dry_run=True)
    dry_output = capsys.readouterr().out
    assert code == 0
    assert "Import dry-run: 2 lines" in dry_output

    def fake_request(method, url, payload=None):
        if method == "GET":
            return {"tasks": [{"title": "Retiro EUR"}]}
        assert method == "POST"
        return {"tasks": [{"title": "Retiro EUR", "scope": "Personal"}, {"title": "DELTA: revisar endpoint de transactions", "scope": "DELTA"}]}

    monkeypatch.setattr(onboarding, "http_request_json", fake_request)
    code = onboarding.import_tasks(task_file, base_url="http://test", dry_run=False)
    output = capsys.readouterr().out

    assert code == 0
    assert "Created: 2" in output
    assert "Personal: 1" in output
    assert "DELTA: 1" in output
    assert "Possible duplicates" in output


def test_preflight_strict_errors_without_env(tmp_path, monkeypatch):
    prepare_repo(tmp_path)
    monkeypatch.setattr(system_service, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'data' / 'alphawave-taskd.sqlite'}")
    (tmp_path / ".env").unlink()

    default = system_service.preflight(strict=False)
    strict = system_service.preflight(strict=True)

    assert default.status == "warning"
    assert strict.status == "error"


def prepare_repo(path: Path) -> None:
    (path / "backend" / "app").mkdir(parents=True)
    (path / "backend" / ".venv" / "bin").mkdir(parents=True)
    (path / "backend" / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    (path / "frontend" / "node_modules").mkdir(parents=True)
    (path / "frontend" / "dist").mkdir(parents=True)
    (path / "frontend" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    (path / ".env.example").write_text("APP_HOST=127.0.0.1\n", encoding="utf-8")
    (path / ".env").write_text("APP_HOST=127.0.0.1\n", encoding="utf-8")
    (path / "data" / "backups").mkdir(parents=True)
    (path / "logs").mkdir()
