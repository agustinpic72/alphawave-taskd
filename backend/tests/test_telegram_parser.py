from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.telegram_parser import parse_command


NOW = datetime(2026, 7, 3, 10, 0, tzinfo=ZoneInfo("UTC"))


def test_parse_natural_today_now_and_todo_commands():
    assert parse_command("buenas, que tengo que hacer hoy?").action == "plan_today"
    assert parse_command("buenas, qué tengo que hacer hoy?").action == "plan_today"
    assert parse_command("hola, qué hago hoy?").action == "plan_today"
    assert parse_command("che, que hago ahora?").action == "plan_now"
    assert parse_command("che, qué hago ahora?").action == "plan_now"
    assert parse_command("buenas, dame la todolist").action == "list"


def test_parse_list_commands():
    assert parse_command("dame la todolist").action == "list"
    assert parse_command("/todo").action == "list"


def test_parse_add_and_bulk_add():
    add = parse_command("agregá comprar café")
    assert add.action == "add"
    assert add.payload["title"] == "comprar café"

    bulk = parse_command("agrega:\n- tarea 1\n- tarea 2")
    assert bulk.action == "bulk_add"
    assert bulk.payload["titles"] == ["tarea 1", "tarea 2"]

    assert parse_command("creá una task nueva revisar algo").payload["title"] == "revisar algo"


def test_parse_sort_aliases():
    for text in (
        "ordenar",
        "ordename la todo list por prioridades",
        "ordéname la todo list por prioridades",
        "prioriza mi lista",
        "priorizá",
        "reordená por prioridad",
        "sort by priority",
    ):
        assert parse_command(text).action == "sort_by_priority_propose"


def test_parse_explicit_add_deadline_as_new_task(monkeypatch):
    monkeypatch.setattr("app.services.deadlines.current_app_time", lambda: NOW)

    quoted = parse_command('agrega "Programar un pricer para las tasks de Project Delta" deadline el viernes de la semana que viene')
    assert quoted.action == "add"
    assert quoted.payload["title"] == "Programar un pricer para las tasks de Project Delta"
    assert quoted.payload["due_at"] == "2026-07-10T12:00:00+00:00"

    plain = parse_command("agregá Programar un pricer para las tasks de Project Delta deadline el viernes de la semana que viene")
    assert plain.action == "add"
    assert plain.payload["title"] == "Programar un pricer para las tasks de Project Delta"
    assert plain.payload["due_at"] == "2026-07-10T12:00:00+00:00"

    new_task = parse_command("crea una task nueva Programar un pricer para las tasks de Project Delta deadline el viernes de la semana que viene")
    assert new_task.action == "add"
    assert new_task.payload["title"] == "Programar un pricer para las tasks de Project Delta"
    assert new_task.payload["due_at"] == "2026-07-10T12:00:00+00:00"

    q_week = parse_command("agrega Programar un pricer para las tasks de Project Delta deadline el viernes de la semana q viene")
    assert q_week.action == "add"
    assert q_week.payload["title"] == "Programar un pricer para las tasks de Project Delta"
    assert q_week.payload["due_at"] == "2026-07-10T12:00:00+00:00"


def test_parse_deadline_without_creation_verb_updates_existing_task():
    parsed = parse_command("comprar cuentas deadline viernes")
    assert parsed.action == "task_deadline"


def test_parse_add_variants_and_clear_date_without_verb():
    assert parse_command("anotá comprar café").payload["title"] == "comprar café"
    assert parse_command("sumá comprar cuentas para el viernes").payload["title"] == "comprar cuentas para el viernes"

    bare = parse_command("mañana comprar café")
    assert bare.action == "add"
    assert bare.payload["title"] == "mañana comprar café"

    ni_bare = parse_command("maniana comprar cafe")
    assert ni_bare.action == "add"
    assert ni_bare.payload["title"] == "maniana comprar cafe"

    assert parse_command("viernes").action == "ambiguous"
    assert parse_command("qué onda?").action == "ambiguous"


def test_parse_index_actions():
    assert parse_command("hecho 2").payload == {"index": 2}
    assert parse_command("done 2").payload == {"index": 2}
    assert parse_command("borra 3").payload == {"index": 3}
    assert parse_command("delete 3").payload == {"index": 3}


def test_parse_rename_and_move():
    rename = parse_command("renombra 4 a nuevo título")
    assert rename.action == "rename_index"
    assert rename.payload == {"index": 4, "title": "nuevo título"}

    rename_en = parse_command("rename 4 to new title")
    assert rename_en.payload == {"index": 4, "title": "new title"}

    move = parse_command("mueve 5 arriba de 2")
    assert move.action == "move_index"
    assert move.payload == {"index": 5, "target_index": 2, "position": "above"}

    move_en = parse_command("move 5 below 2")
    assert move_en.payload == {"index": 5, "target_index": 2, "position": "below"}
