from collections.abc import Awaitable, Callable


StartupStep = Callable[[], Awaitable[object]]


async def run_startup_sequence(
    process_telegram: StartupStep,
    process_reminders: StartupStep,
    process_briefing: StartupStep,
) -> None:
    await process_telegram()
    await process_reminders()
    await process_briefing()
