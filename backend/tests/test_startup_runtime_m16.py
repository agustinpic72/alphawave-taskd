import asyncio

from app import main


def test_startup_steps_timeout_and_continue(monkeypatch):
    events: list[str] = []

    async def slow_telegram():
        events.append("telegram:start")
        await asyncio.sleep(1)
        events.append("telegram:end")

    async def reminders():
        events.append("reminders")

    async def briefing():
        events.append("briefing")

    async def trello():
        events.append("trello")

    monkeypatch.setattr(main, "STARTUP_STEP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(main, "run_startup_telegram_processing", slow_telegram)
    monkeypatch.setattr(main, "run_startup_reminder_processing", reminders)
    monkeypatch.setattr(main, "run_startup_briefing_processing", briefing)
    monkeypatch.setattr(main, "run_startup_trello_sync", trello)

    asyncio.run(main._run_startup_steps())

    assert events == ["telegram:start", "reminders", "briefing", "trello"]

