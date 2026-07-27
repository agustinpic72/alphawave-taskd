from app.services import system


def test_system_status_includes_runtime_commit_hint(db_session, monkeypatch):
    monkeypatch.setattr(system, "_git_commit", lambda: "abc1234")

    status = system.status(db_session)

    assert status.environment["mode"] == "local"
    assert status.environment["app_version"] == system.VERSION
    assert status.environment["git_commit"] == "abc1234"

