import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI

from app import main
from app.core.config import REPO_ROOT, Settings
from app.core.instance_lock import InstanceLock, InstanceLockError


def test_instance_lock_is_exclusive_and_released(tmp_path):
    path = tmp_path / "runtime" / "instance.lock"

    with InstanceLock(path):
        assert path.read_text(encoding="utf-8").strip().isdigit()
        with pytest.raises(InstanceLockError, match="another alphawave-taskd instance"):
            with InstanceLock(path):
                pass

    with InstanceLock(path):
        pass


def test_instance_lock_path_defaults_under_data_and_supports_env_override(monkeypatch, tmp_path):
    default = Settings(_env_file=None)
    assert default.resolved_instance_lock_path == REPO_ROOT / "data" / "alphawave-taskd.lock"

    override = tmp_path / "docker" / "taskd.lock"
    monkeypatch.setenv("ALPHAWAVE_INSTANCE_LOCK_PATH", str(override))
    configured = Settings(_env_file=None)
    assert configured.resolved_instance_lock_path == override


def test_lifespan_holds_lock_before_initialization_until_worker_shutdown(tmp_path, monkeypatch):
    path = tmp_path / "instance.lock"
    config = Settings(_env_file=None, instance_lock_path=str(path))
    app = FastAPI()
    app.state.settings = config
    calls = []

    def init_db_while_locked():
        with pytest.raises(InstanceLockError):
            with InstanceLock(path):
                pass
        calls.append("db")

    async def workers_while_locked():
        with pytest.raises(InstanceLockError):
            with InstanceLock(path):
                pass
        calls.append("workers")
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "configure_logging", lambda: calls.append("logging"))
    monkeypatch.setattr(main, "init_db", init_db_while_locked)
    monkeypatch.setattr(main, "run_startup_backup", lambda: calls.append("backup"))
    monkeypatch.setattr(main, "_run_runtime_workers", workers_while_locked)

    async def exercise():
        async with main.lifespan(app):
            await asyncio.sleep(0)
            with pytest.raises(InstanceLockError):
                with InstanceLock(path):
                    pass

    asyncio.run(exercise())
    assert calls == ["logging", "db", "backup", "workers"]
    with InstanceLock(path):
        pass


def test_lifespan_lock_bypass_must_be_explicit(monkeypatch):
    config = Settings(
        _env_file=None,
        instance_lock_path="/proc/alphawave-taskd/instance.lock",
        instance_lock_test_bypass=True,
    )
    app = FastAPI()
    app.state.settings = config

    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "run_startup_backup", lambda: None)

    async def idle_workers():
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "_run_runtime_workers", idle_workers)

    async def exercise():
        async with main.lifespan(app):
            pass

    asyncio.run(exercise())

    config.instance_lock_test_bypass = False
    with pytest.raises(InstanceLockError, match="cannot open instance lock"):
        asyncio.run(exercise())
