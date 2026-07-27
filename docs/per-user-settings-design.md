# Per-User Settings Design

M19D designed the future settings model. M19G implements the first runtime slice with a dedicated `user_settings` table for personal preferences while keeping instance/integration settings in `app_settings`.

## Current State

Instance settings are still stored as global rows in `app_settings`:

```text
key = section name
value_json = section payload
```

The current runtime contract has one important semantic that must survive multi-user settings:

```text
missing override != explicit false
```

An explicit `false` stored in SQLite must continue to beat defaults from `.env` or instance defaults.

Personal settings now use `user_settings` rows:

```text
user_id = owner
section = settings section
key = __section__
value_json = full effective section payload
```

## Options Compared

### Option A - Keep `app_settings` With Nullable `user_id`

```sql
app_settings (
  id TEXT PRIMARY KEY,
  user_id TEXT NULL,
  section TEXT NOT NULL,
  key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
```

Meaning:

- `user_id IS NULL` = instance/admin default;
- `user_id IS NOT NULL` = user override.

Pros:

- Evolves current table name.
- Fewer new service concepts.

Cons:

- Easy to mix instance/admin and user state in one table.
- Harder to express integration-scoped settings.
- Nullable ownership is a footgun for query scoping.

### Option B - Separate Tables By Scope

```sql
instance_settings (
  section TEXT NOT NULL,
  key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (section, key)
)

user_settings (
  user_id TEXT NOT NULL,
  section TEXT NOT NULL,
  key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, section, key)
)

integration_settings (
  integration_id TEXT NOT NULL,
  section TEXT NOT NULL,
  key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (integration_id, section, key)
)
```

Pros:

- Clear boundaries between user, integration and admin scopes.
- No nullable owner footgun.
- Easier to gate UI sections by role.

Cons:

- More migration work.
- More service functions than the current `app_settings` merge.

Recommendation: Option B for hosted multi-user. M19G starts this path with `user_settings`; `app_settings` remains the instance table until a later cleanup introduces `instance_settings` and integration-specific tables.

## Runtime Precedence

M19G precedence:

```text
code defaults
-> app_settings instance defaults
-> user overrides
```

Rules:

- Missing row means inherit.
- Explicit `false`, `0`, empty list or empty string means user chose that value.
- Reset deletes the user override, not the instance default.
- UI should show source: default, instance, user, and later plan/integration.
- M19G stores user overrides at whole-section granularity. Changing `reminders.enabled`, for example, copies the effective `reminders` section into `user_settings`; resetting the section deletes that user row.

## Settings Classification

| Setting | Current location | Future scope | Reason | Migration notes |
| --- | --- | --- | --- | --- |
| `general.timezone` | `user_settings.general` with fallback to `app_settings.general` and `.env` | per-user | Planning/reminders/briefing are personal. | M19G runtime implemented. |
| `reminders.enabled` | `user_settings.reminders` | per-user | Notification preference. | M19G preserves explicit false. |
| `reminders.default_time`, `snooze_default_time`, `later_delay_hours` | `user_settings.reminders` | per-user | Personal scheduling defaults. | M19G runtime implemented. |
| `reminders.group_overdue_threshold` | `user_settings.reminders` | per-user | Personal notification noise. | Could also have instance max guard later. |
| `briefing.enabled`, `time`, `late_cutoff`, `timezone` | `user_settings.briefing` | per-user | Personal digest schedule. | M19G manual/runtime helpers accept `user_id`; scheduled multi-user iteration remains future. |
| `briefing.include_*`, `max_*` | `user_settings.briefing` | per-user | Personal digest shape. | M19G runtime implemented. |
| `modes.weekend` | `user_settings.modes` | per-user | Personal quiet mode. | M19G runtime implemented. |
| `modes.vacation` | `app_settings.modes` | per-user | Personal availability. | Not admin setting. |
| `priority.preset`, `criteria` | `user_settings.priority` | per-user | Personal planning preferences. | M19G runtime implemented. |
| `trello.enabled` | `app_settings.trello` + `.env` | future per-user integration enabled flag | Each user connects own Trello account. | M19G keeps Trello instance/global. |
| `trello.boards` | `app_settings.trello` | future per-user integration settings | Board IDs/list IDs belong to user's Trello account. | M19G keeps Trello instance/global; move in M19H/later. |
| Trello `write_enabled` | `app_settings.trello`/advanced | per-user integration, with instance kill switch | User chooses writes; admin can globally disable. | Effective value = instance allow AND user enable. |
| Trello auto-confirm | global advanced + board override | per-user integration/board | Dangerous remote writes must be owner scoped. | Audit every auto-confirm decision. |
| `backups.enabled`, `retention_days` | `app_settings.backups` | instance/admin for VPS; future user export policy separate | SQLite backup is whole instance. | Do not expose as user setting in SaaS. |
| `advanced.llm_enabled` | `app_settings.advanced` | per-user preference plus entitlement | User can enable suggestions; provider/quota admin controlled. | Effective value = provider ready AND entitlement AND user enable. |
| LLM provider/model/secrets | `.env` | instance/admin | Provider secrets are not user settings. | Future per-user BYO key is separate integration design. |
| system status/diagnostics | derived | admin-only plus user-safe subset | Full status is operational. | Split payloads. |
| host/port/DB/log paths | `.env` | instance/admin | Infrastructure. | Never user-editable. |

## Service Shape

Future user settings call:

```python
settings_service.get_user_settings(db, ctx.user_id)
settings_service.patch_user_settings(db, ctx.user_id, payload, source="ui")
settings_service.get_instance_settings(db, ctx)  # admin-only
settings_service.get_integration_settings(db, ctx.user_id, integration_id)
```

Future merge shape:

```python
def effective_user_settings(db, user_id: str) -> dict:
    defaults = default_settings()
    instance = load_instance_defaults(db)
    user = load_user_overrides(db, user_id)
    return deep_merge_preserving_explicit_false(defaults, instance, user)
```

Do not pass a bare `db` into settings consumers once multi-user work starts. Consumers should accept `ctx` or `user_id`.

## Audit Requirements

Settings writes should record:

- actor user id;
- target scope: user, integration, instance;
- section/key;
- old/new redacted payload;
- source: UI, import, migration, admin tool;
- timestamp.

Secrets must stay out of settings audit payloads.

## Migration Plan

1. Add auth shell with one admin, still reading current global `app_settings`. Done in M19E.
2. Add core ownership for tasks/reminders/confirmations. Done in M19F.
3. Add `user_settings` and route personal sections there while preserving `app_settings` as instance fallback. Done in M19G.
4. Move Trello board settings into integration-owned tables when per-user Trello lands.
5. Move backup/system/diagnostic knobs to `instance_settings` or `.env` only.
6. Stop reading global `app_settings` for user preferences after migration/backfill tests pass.

## Tests Required

- User A changes reminder default; User B unchanged. Covered by `backend/tests/test_user_settings_m19g.py`.
- User A disables briefing; User B remains unchanged. Covered by `backend/tests/test_user_settings_m19g.py`.
- User A Trello boards do not appear in User B settings. Future per-user integrations.
- Explicit `false` beats inherited `true`. Covered by `backend/tests/test_user_settings_m19g.py`.
- Reset removes only User A override.
- Admin instance kill switch disables Trello writes even if user setting enables them.
