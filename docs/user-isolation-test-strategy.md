# User Isolation Test Strategy

M19D defined the tests required before public multi-user beta. M19F adds the first executable A/B isolation suite in `backend/tests/test_core_ownership_m19f.py`. M19G adds per-user settings isolation in `backend/tests/test_user_settings_m19g.py`.

## Gate

No public multi-user beta until A/B isolation tests pass for every user-scoped endpoint and worker path.

## Fixtures

Recommended backend fixtures:

```python
@pytest.fixture
def user_a(db_session): ...

@pytest.fixture
def user_b(db_session): ...

@pytest.fixture
def admin_user(db_session): ...

@pytest.fixture
def auth_client_a(app, user_a): ...

@pytest.fixture
def auth_client_b(app, user_b): ...

@pytest.fixture
def admin_client(app, admin_user): ...
```

Recommended helpers:

```python
def create_task_for_user(db, user, **overrides): ...
def create_reminder_for_user(db, user, **overrides): ...
def create_confirmation_for_user(db, user, **overrides): ...
def assert_not_found_or_forbidden(response): ...
```

Tests should prefer real API calls for route authorization and direct service tests for lower-level query scoping.

## Test Categories

### API Authorization

- Anonymous request to `/api/tasks` returns 401.
- Anonymous unsafe methods return 401 and do not mutate DB.
- Non-admin request to backups/diagnostics returns 403.
- `/api/health` remains public and coarse.
- Dev endpoints are disabled in production mode.

### Query Scoping

- User A creates task; User B cannot list/read/update/delete/complete/restore it.
- User B cannot reorder User A task IDs.
- Search for User A task title as User B returns no result.
- Completed/trash views are scoped.
- Permanent delete rejects another user's deleted task.

M19F covered:

- User B cannot list/update/complete User A tasks.
- User B cannot list/cancel User A reminders; worker due lookup can be user-scoped.
- User B cannot list/confirm/cancel User A confirmations.
- User B planning, incomplete-details, suggestions and Inbox suggestions do not include User A tasks.
- Legacy no-owner rows can be backfilled to the single owner.
- Trello sync-created tasks receive the single integration owner.

### Settings Scoping

- User A disables reminders; User B reminders remain enabled.
- User A changes timezone; User B planning dates are unchanged.
- User A priority criteria do not change User B scoring.
- Explicit `false` override works per user.
- Reset removes only the current user's override.

M19G covers:

- Settings API isolates user preferences and source metadata.
- Full draft PATCH routes personal sections to `user_settings` and instance sections to `app_settings`.
- Reminder worker uses the reminder owner's settings.
- Briefing/planning helpers use user settings.
- Auth-disabled mode uses the single owner fallback.

Still future:

- Trello board mappings and Telegram linked accounts are not per-user settings yet.

### Integration Scoping

- User A Trello boards do not appear in User B settings.
- User A Trello sync cannot create/update User B tasks.
- User A confirmation cannot execute User B Trello write.
- `source_id` collision from Trello card IDs is safe across users.
- User A Telegram snapshot index cannot resolve User B task.

M19F compatibility only: Trello sync and Telegram processing map to the single integration owner. Real per-user Trello credentials and Telegram chat linking remain future work.

### Worker Scoping

- Reminder worker sends only User A reminders through User A notification mapping.
- Briefing worker generates per-user payloads with only that user's tasks.
- Telegram update from User A chat acts only on User A account.
- Trello worker holds per-user/per-integration locks.
- Failed User A integration does not block User B worker run.

### Diagnostics and Backups

- User-safe diagnostics exclude other users' task titles, chat IDs, board IDs and paths.
- Full diagnostics require admin.
- Backups list/create/validate/restore-plan require admin.
- Support bundle redaction still excludes `.env`, tokens, SQLite DB and backups.

### IDOR Regression

Every endpoint with path IDs must have a cross-user test:

- `task_id`;
- `reminder_id`;
- `confirmation_id`;
- `job_id`;
- `backup_id` where admin-only;
- Trello board alias/integration id;
- Telegram link id, future.

Expected result for non-owner should be 404 when revealing existence is risky, otherwise 403 for role failures.

## Example Tests

```python
def test_user_b_cannot_complete_user_a_task(auth_client_b, user_a_task):
    response = auth_client_b.post(f"/api/tasks/{user_a_task.id}/complete")
    assert response.status_code in {403, 404}
```

```python
def test_settings_explicit_false_is_per_user(auth_client_a, auth_client_b):
    auth_client_a.patch("/api/settings", json={"reminders": {"enabled": False}})
    assert auth_client_a.get("/api/settings").json()["settings"]["reminders"]["enabled"] is False
    assert auth_client_b.get("/api/settings").json()["settings"]["reminders"]["enabled"] is True
```

```python
def test_trello_source_id_collision_is_user_scoped(db_session, user_a, user_b):
    create_trello_task(db_session, user_a, source_id="card-1")
    create_trello_task(db_session, user_b, source_id="card-1")
    assert list_tasks(db_session, user_a.id)[0].source_id == "card-1"
    assert list_tasks(db_session, user_b.id)[0].source_id == "card-1"
```

```python
def test_reminder_worker_uses_user_notification_channel(db_session, user_a, user_b, messenger):
    create_due_reminder(db_session, user_a, message="A")
    create_due_reminder(db_session, user_b, message="B")
    send_due_reminders_for_user(db_session, user_a.id, messenger)
    assert messenger.sent_to(user_a.telegram_chat_id, "A")
    assert not messenger.sent_text("B")
```

## Frontend/Auth Tests

- Logged-out app shows login, not task data.
- 401 response clears session state and sends user to login.
- Logout invalidates server session and clears client user context.
- Admin-only Settings sections are hidden for normal users and rejected by API.
- API helper sends credentials for cookie session and handles CSRF token on unsafe methods.

## CI Gate

Before public beta:

```bash
backend/.venv/bin/pytest -q backend/tests/test_auth_*.py backend/tests/test_user_isolation_*.py
backend/.venv/bin/pytest -q
cd frontend && npm run build
./scripts/dev/rc-check.sh
```

Add a dedicated smoke later that creates User A/User B data and exercises the browser through login/logout.
