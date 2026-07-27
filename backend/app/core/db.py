from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import REPO_ROOT, settings


def _sqlite_connect_args() -> dict[str, bool]:
    return {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}


def _ensure_sqlite_parent() -> None:
    sqlite_path = settings.sqlite_path
    if sqlite_path:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_parent()

engine = create_engine(
    settings.sqlalchemy_database_url,
    connect_args=_sqlite_connect_args(),
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app.models import app_state, auth, briefing, confirmations, integrations, jobs, reminders, settings, tasks, telegram, trello

    Base.metadata.create_all(bind=engine)
    ensure_schema()


def ensure_schema() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as connection:
        owner_id = "local-owner"
        user_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(users)")).fetchall()
        }
        if user_columns:
            owner_id = connection.execute(text("SELECT id FROM users ORDER BY created_at ASC LIMIT 1")).scalar()
            if not owner_id:
                owner_id = "local-owner"
                now = connection.execute(text("SELECT datetime('now')")).scalar() or ""
                connection.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, display_name, role, status, created_at, updated_at) "
                        "VALUES (:id, 'owner@local.alphawave', 'unusable$bootstrap-required', 'Local owner', 'owner', "
                        "'bootstrap_required', :now, :now)"
                    ),
                    {"id": owner_id, "now": now},
                )
        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(telegram_updates)")).fetchall()
        }
        if columns and "from_user_id" not in columns:
            connection.execute(text("ALTER TABLE telegram_updates ADD COLUMN from_user_id TEXT"))
        if columns and "raw_json" not in columns:
            connection.execute(text("ALTER TABLE telegram_updates ADD COLUMN raw_json TEXT"))
        snapshot_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(telegram_snapshots)")).fetchall()
        }
        if snapshot_columns and "snapshot_type" not in snapshot_columns:
            connection.execute(text("ALTER TABLE telegram_snapshots ADD COLUMN snapshot_type TEXT NOT NULL DEFAULT 'tasks'"))
        reminder_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(reminders)")).fetchall()
        }
        if reminder_columns and "updated_at" not in reminder_columns:
            connection.execute(text("ALTER TABLE reminders ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"))
            connection.execute(text("UPDATE reminders SET updated_at = created_at WHERE updated_at = ''"))
        if reminder_columns and "source" not in reminder_columns:
            connection.execute(text("ALTER TABLE reminders ADD COLUMN source TEXT"))
        if reminder_columns and "source_update_id" not in reminder_columns:
            connection.execute(text("ALTER TABLE reminders ADD COLUMN source_update_id INTEGER"))
        if reminder_columns and "user_id" not in reminder_columns:
            connection.execute(text("ALTER TABLE reminders ADD COLUMN user_id TEXT"))
        if reminder_columns:
            connection.execute(text("UPDATE reminders SET user_id = :owner_id WHERE user_id IS NULL OR user_id = ''"), {"owner_id": owner_id})
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_reminders_status ON reminders(status)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_reminders_remind_at ON reminders(remind_at)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_reminders_task_id ON reminders(task_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_reminders_user_id ON reminders(user_id)"))
        task_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(tasks)")).fetchall()
        }
        if task_columns and "user_id" not in task_columns:
            connection.execute(text("ALTER TABLE tasks ADD COLUMN user_id TEXT"))
        if task_columns:
            connection.execute(text("UPDATE tasks SET user_id = :owner_id WHERE user_id IS NULL OR user_id = ''"), {"owner_id": owner_id})
        if task_columns and "context_bucket" not in task_columns:
            connection.execute(text("ALTER TABLE tasks ADD COLUMN context_bucket TEXT"))
        for column, ddl in {
            "source_type": "ALTER TABLE tasks ADD COLUMN source_type TEXT NOT NULL DEFAULT 'local'",
            "source_id": "ALTER TABLE tasks ADD COLUMN source_id TEXT",
            "source_url": "ALTER TABLE tasks ADD COLUMN source_url TEXT",
            "origin_label": "ALTER TABLE tasks ADD COLUMN origin_label TEXT",
            "trello_board_id": "ALTER TABLE tasks ADD COLUMN trello_board_id TEXT",
            "trello_board_name": "ALTER TABLE tasks ADD COLUMN trello_board_name TEXT",
            "trello_list_id": "ALTER TABLE tasks ADD COLUMN trello_list_id TEXT",
            "trello_list_name": "ALTER TABLE tasks ADD COLUMN trello_list_name TEXT",
            "trello_state": "ALTER TABLE tasks ADD COLUMN trello_state TEXT",
            "checklist_done": "ALTER TABLE tasks ADD COLUMN checklist_done INTEGER",
            "checklist_total": "ALTER TABLE tasks ADD COLUMN checklist_total INTEGER",
            "last_trello_activity_at": "ALTER TABLE tasks ADD COLUMN last_trello_activity_at TEXT",
            "last_checkin_at": "ALTER TABLE tasks ADD COLUMN last_checkin_at TEXT",
            "next_checkin_at": "ALTER TABLE tasks ADD COLUMN next_checkin_at TEXT",
        }.items():
            if task_columns and column not in task_columns:
                connection.execute(text(ddl))
        task_event_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(task_events)")).fetchall()
        }
        if task_event_columns and "user_id" not in task_event_columns:
            connection.execute(text("ALTER TABLE task_events ADD COLUMN user_id TEXT"))
        if task_event_columns:
            connection.execute(
                text(
                    "UPDATE task_events "
                    "SET user_id = COALESCE((SELECT tasks.user_id FROM tasks WHERE tasks.id = task_events.task_id), :owner_id) "
                    "WHERE user_id IS NULL OR user_id = ''"
                ),
                {"owner_id": owner_id},
            )
        if task_columns:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_source ON tasks(source_type, source_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_user_id ON tasks(user_id)"))
        if task_event_columns:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_task_events_user_id ON task_events(user_id)"))
        deleted_external_ref_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(deleted_external_refs)")).fetchall()
        }
        if deleted_external_ref_columns:
            connection.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS ix_deleted_external_refs_source ON deleted_external_refs(source_type, source_id)")
            )
        confirmation_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(pending_confirmations)")).fetchall()
        }
        if confirmation_columns and "summary" not in confirmation_columns:
            connection.execute(text("ALTER TABLE pending_confirmations ADD COLUMN summary TEXT"))
        if confirmation_columns and "error" not in confirmation_columns:
            connection.execute(text("ALTER TABLE pending_confirmations ADD COLUMN error TEXT"))
        if confirmation_columns and "user_id" not in confirmation_columns:
            connection.execute(text("ALTER TABLE pending_confirmations ADD COLUMN user_id TEXT"))
        if confirmation_columns:
            connection.execute(text("UPDATE pending_confirmations SET user_id = :owner_id WHERE user_id IS NULL OR user_id = ''"), {"owner_id": owner_id})
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_pending_confirmations_user_id ON pending_confirmations(user_id)"))
        briefing_run_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(briefing_runs)")).fetchall()
        }
        if briefing_run_columns and "user_id" not in briefing_run_columns:
            connection.execute(text("ALTER TABLE briefing_runs ADD COLUMN user_id TEXT"))
        user_count = connection.execute(text("SELECT COUNT(*) FROM users")).scalar() if user_columns else 0
        if briefing_run_columns and user_count == 1:
            connection.execute(
                text("UPDATE briefing_runs SET user_id = :owner_id WHERE user_id IS NULL OR user_id = ''"),
                {"owner_id": owner_id},
            )
        if briefing_run_columns:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_briefing_runs_date ON briefing_runs(briefing_date)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_briefing_runs_status ON briefing_runs(status)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_briefing_runs_scheduled ON briefing_runs(scheduled_for)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_briefing_runs_user_id ON briefing_runs(user_id)"))
        settings_audit_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(settings_audit)")).fetchall()
        }
        if settings_audit_columns:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_settings_audit_key ON settings_audit(key)"))
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS user_settings ("
                "id TEXT NOT NULL PRIMARY KEY, "
                "user_id TEXT NOT NULL, "
                "section TEXT NOT NULL, "
                "key TEXT NOT NULL, "
                "value_json TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
        )
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_settings_user_section_key ON user_settings(user_id, section, key)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_user_settings_user_id ON user_settings(user_id)"))
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS user_integrations ("
                "id TEXT NOT NULL PRIMARY KEY, "
                "user_id TEXT NOT NULL, "
                "provider TEXT NOT NULL, "
                "status TEXT NOT NULL, "
                "config_json TEXT NOT NULL, "
                "credentials_source TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
        )
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_integrations_user_provider ON user_integrations(user_id, provider)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_user_integrations_user_id ON user_integrations(user_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_user_integrations_provider ON user_integrations(provider)"))
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS telegram_chat_links ("
                "id TEXT NOT NULL PRIMARY KEY, "
                "user_id TEXT NOT NULL, "
                "chat_id_hash TEXT NOT NULL, "
                "chat_id_redacted TEXT NOT NULL, "
                "status TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
        )
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_telegram_chat_links_chat_id_hash ON telegram_chat_links(chat_id_hash)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_telegram_chat_links_user_id ON telegram_chat_links(user_id)"))
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS integration_secrets ("
                "id TEXT NOT NULL PRIMARY KEY, "
                "user_id TEXT NOT NULL, "
                "provider TEXT NOT NULL, "
                "secret_type TEXT NOT NULL, "
                "ciphertext TEXT NOT NULL, "
                "redacted_hint TEXT, "
                "status TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, "
                "rotated_at TEXT, "
                "revoked_at TEXT)"
            )
        )
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_integration_secrets_user_provider_type ON integration_secrets(user_id, provider, secret_type)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_integration_secrets_user_id ON integration_secrets(user_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_integration_secrets_provider ON integration_secrets(provider)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_integration_secrets_secret_type ON integration_secrets(secret_type)"))
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS telegram_link_codes ("
                "id TEXT NOT NULL PRIMARY KEY, "
                "user_id TEXT NOT NULL, "
                "code_hash TEXT NOT NULL, "
                "expires_at TEXT NOT NULL, "
                "consumed_at TEXT, "
                "created_at TEXT NOT NULL)"
            )
        )
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_telegram_link_codes_code_hash ON telegram_link_codes(code_hash)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_telegram_link_codes_user_id ON telegram_link_codes(user_id)"))
        background_job_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(background_jobs)")).fetchall()
        }
        if background_job_columns:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_background_jobs_kind ON background_jobs(kind)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_background_jobs_status ON background_jobs(status)"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
