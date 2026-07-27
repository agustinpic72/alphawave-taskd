from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.reminder_parser import parse_reminder
from app.services.telegram_parser import parse_command


NOW = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo("UTC"))


def test_parse_tomorrow_at_10():
    parsed = parse_reminder("recuérdame llamar a Juan mañana a las 10", now=NOW)

    assert parsed is not None
    assert parsed.message == "llamar a Juan"
    assert parsed.remind_at.startswith("2026-07-03T10:00")

    ascii_keyboard = parse_reminder("recuerdame llamar a Juan maniana a las 10", now=NOW)
    assert ascii_keyboard is not None
    assert ascii_keyboard.message == "llamar a Juan"
    assert ascii_keyboard.remind_at.startswith("2026-07-03T10:00")


def test_parse_today_at_18():
    parsed = parse_reminder("hoy a las 18 recordame revisar Reportes Semanal", now=NOW)

    assert parsed is not None
    assert parsed.message == "revisar Reportes Semanal"
    assert parsed.remind_at.startswith("2026-07-02T18:00")


def test_parse_in_30_minutes():
    parsed = parse_reminder("en 30 minutos recordame revisar el horno", now=NOW)

    assert parsed is not None
    assert parsed.message == "revisar el horno"
    assert parsed.remind_at.startswith("2026-07-02T12:30")


def test_parse_in_2_hours_english():
    parsed = parse_reminder("remind me in 2 hours to leave", now=NOW)

    assert parsed is not None
    assert parsed.message == "leave"
    assert parsed.remind_at.startswith("2026-07-02T14:00")


def test_reject_ambiguous_date():
    command = parse_command("recordame revisar esto el finde")

    assert command.action == "ambiguous"
    assert "fecha" in command.payload["reason"].casefold()
