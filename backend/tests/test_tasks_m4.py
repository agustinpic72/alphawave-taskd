from datetime import datetime
from zoneinfo import ZoneInfo

from app.schemas.tasks import BulkTaskCreate, TaskCreate
from app.services.tasks import bulk_create_tasks, create_task


NOW = datetime(2026, 7, 3, 10, 0, tzinfo=ZoneInfo("UTC"))


def test_api_create_does_not_invent_unconfigured_scope(db_session):
    task = create_task(db_session, TaskCreate(title="revisar endpoint de Project Delta"))

    assert task.scope == "Inbox"
    assert task.context_bucket in {"deep_work", "review"}


def test_bulk_add_classifies_multiple_tasks(db_session):
    tasks = bulk_create_tasks(db_session, BulkTaskCreate(text="AlphaWave agente\ncomprar café\nsin contexto raro"))

    assert [task.scope for task in tasks] == ["Inbox", "Personal", "Inbox"]


def test_ambiguous_task_falls_to_inbox(db_session):
    task = create_task(db_session, TaskCreate(title="sin contexto raro"))

    assert task.scope == "Inbox"


def test_natural_deadlines_for_tasks(db_session, monkeypatch):
    monkeypatch.setattr("app.services.deadlines.current_app_time", lambda: NOW)

    sunday = create_task(db_session, TaskCreate(title="domingo ver peli con mi novia"))
    ambiguous = create_task(db_session, TaskCreate(title="el finde hacer X"))

    assert sunday.title == "ver peli con mi novia"
    assert sunday.scope == "Personal"
    assert sunday.due_at == "2026-07-05T12:00:00+00:00"
    assert ambiguous.title == "el finde hacer X"
    assert ambiguous.due_at is None


def test_task_date_extraction_cleans_title_before_classification(db_session, monkeypatch):
    monkeypatch.setattr("app.services.deadlines.current_app_time", lambda: NOW)

    friday = create_task(db_session, TaskCreate(title="comprar cuentas el viernes"))
    para = create_task(db_session, TaskCreate(title="comprar cuentas para el viernes"))
    tomorrow = create_task(db_session, TaskCreate(title="mañana comprar café"))
    ni_tomorrow = create_task(db_session, TaskCreate(title="maniana comprar cafe"))
    project_delta = create_task(db_session, TaskCreate(title="revisar endpoint de Project Delta el viernes"))
    timed = create_task(db_session, TaskCreate(title="comprar cuentas el viernes a las 18"))
    next_week = create_task(db_session, TaskCreate(title="Programar un pricer para las tasks de Project Delta deadline el viernes de la semana que viene"))
    q_next_week = create_task(db_session, TaskCreate(title="revisar endpoint de Project Delta deadline el viernes de la semana q viene"))
    proximo = create_task(db_session, TaskCreate(title="revisar endpoint de Project Delta el proximo viernes"))

    assert friday.title == "comprar cuentas"
    assert friday.due_at == "2026-07-10T12:00:00+00:00"
    assert para.title == "comprar cuentas"
    assert para.due_at == "2026-07-10T12:00:00+00:00"
    assert tomorrow.title == "comprar café"
    assert tomorrow.scope == "Personal"
    assert tomorrow.due_at == "2026-07-04T12:00:00+00:00"
    assert ni_tomorrow.title == "comprar cafe"
    assert ni_tomorrow.scope == "Personal"
    assert ni_tomorrow.due_at == "2026-07-04T12:00:00+00:00"
    assert project_delta.title == "revisar endpoint de Project Delta"
    assert project_delta.scope == "Inbox"
    assert project_delta.due_at == "2026-07-10T12:00:00+00:00"
    assert timed.title == "comprar cuentas"
    assert timed.due_at == "2026-07-10T18:00:00+00:00"
    assert next_week.title == "Programar un pricer para las tasks de Project Delta"
    assert next_week.scope == "Inbox"
    assert next_week.due_at == "2026-07-10T12:00:00+00:00"
    assert q_next_week.title == "revisar endpoint de Project Delta"
    assert q_next_week.due_at == "2026-07-10T12:00:00+00:00"
    assert proximo.title == "revisar endpoint de Project Delta"
    assert proximo.due_at == "2026-07-10T12:00:00+00:00"


def test_task_date_extraction_handles_long_whitespace_before_punctuation(db_session, monkeypatch):
    monkeypatch.setattr("app.services.deadlines.current_app_time", lambda: NOW)

    task = create_task(db_session, TaskCreate(title=f"revisar informe{' ' * 400}, el viernes"))

    assert task.title == "revisar informe"
    assert task.due_at == "2026-07-10T12:00:00+00:00"
