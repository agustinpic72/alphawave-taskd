# Multi-User Readiness Audit

M19C audits the current codebase for hosted multi-user readiness. It does not implement authentication, add ownership columns, migrate data, connect to external services or change product behavior.

## Executive Summary

AlphaWave TaskD is a strong local-first, single-owner app and is now scaffolded for a private VPS beta. It is not ready to be exposed as a public multi-user product.

Top blockers:

- P0 - No app authentication or authorization boundary.
- P0 - Task, reminder, confirmation, settings and integration state are global.
- P0 - Public access without Basic Auth/VPN/IP allowlist would expose all data and side-effect endpoints.
- P1 - Telegram and Trello now have user-scoped integration boundaries, but credentials still come from instance `.env` instead of per-user OAuth/link onboarding.
- P1 - Background workers scan/process global queues and would mix users.
- P2 - Backups, diagnostics and runtime status are instance/admin surfaces and need strict gating.

The correct path is auth shell first, then ownership foundation, then per-user integrations, then mobile/API versioning, then entitlements.

M19D details the next design layer:

- [Auth, user scope and request context design](auth-user-scope-design.md)
- [Per-user settings design](per-user-settings-design.md)
- [User isolation test strategy](user-isolation-test-strategy.md)
- [Auth API boundary](auth-api-boundary.md)

## Current Single-User Assumptions

- SQLite is the only data store and represents one owner.
- `app_settings` stores global runtime behavior by section key.
- `.env` stores instance-level Telegram, Trello, LLM and infrastructure secrets.
- `Task.manual_order` is one global ordering.
- Telegram chat links hash `chat_id` and resolve inbound processing to `user_id`; legacy allowlist remains only as a single-owner fallback.
- Trello sync resolves active board mappings by integration owner and upserts into that user's task table.
- Reminder and briefing workers send only when a destination exists for the task owner; legacy allowlist remains for the single owner.
- Backups and diagnostics describe the whole instance.
- Frontend state has no current user, login/logout, role or tenant context.

## Severity Key

- P0: blocker absolute for public/multi-user.
- P1: required before beta with real public users.
- P2: required before Android/mobile scale.
- P3: improvement or cleanup.

## Major Blockers

| Severity | Finding | Impact | Required direction |
| --- | --- | --- | --- |
| P0 | No auth for UI/API. | Anyone with URL can read and mutate data. | Add auth shell before public exposure. |
| P0 | `tasks`, `reminders`, `pending_confirmations`, `app_settings` are global. | Cross-user leakage and destructive writes. | Add owner scope and enforce in every query/write. |
| P0 | Task lookup helpers use global IDs only. | User A could fetch/write User B task if auth were added without ownership filters. | Introduce request user context before route handlers call services. |
| P0 | Settings writes are global. | One user can change every user's Trello/Telegram/priority/backup behavior. | Split user settings, integration settings and instance/admin settings. |
| P1 | Trello credentials are still instance-level. | User-scoped mappings prevent accidental cross-user config reuse, but all enabled Trello users still rely on the same `.env` credential source. | Move to per-user OAuth/token storage with encrypted secrets. |
| P1 | Telegram link onboarding is not user-facing yet. | `telegram_chat_links` scopes inbound chats, but hosted users still need an account-link flow. | Add link-code flow: Telegram chat/user id -> `user_id`, with hashed identifiers only in diagnostics. |
| P1 | Workers process global records. | Notifications, sync, confirmations and check-ins can cross users. | Workers must iterate user-owned jobs/integrations with locks. |
| P2 | Diagnostics/backups expose instance state. | Sensitive operational or user data leaks in hosted support. | Make admin-only and add user-safe subsets. |

## Security Risks If Public Without Auth

Without app auth or a private-beta guard, anyone who can reach the URL could:

- list, create, edit, complete, delete, restore and permanently delete tasks;
- read notes, Trello metadata, checklist summaries and task history;
- change global Settings, including Trello mappings, weekend mode, LLM toggle, backups and priority weights;
- create or confirm Trello write actions if writes are enabled;
- trigger briefing/reminder sends and Telegram processing paths;
- inspect backups, restore plans, system diagnostics and runtime metadata;
- run LLM validation and potentially consume compute if a provider is configured.

Critical endpoint groups:

- task writes: `/api/tasks/*`;
- settings writes: `/api/settings*`;
- Trello writes: `/api/trello/cards/*`, `/api/confirmations/*`;
- backups/diagnostics: `/api/backups*`, `/api/system/diagnostics`;
- dev/internal: `/api/dev/simulate-message`, `/api/telegram/process-pending`;
- LLM validate: `/api/llm/validate`.

Phase 0/1 private beta must stay behind VPN, Basic Auth, IP allowlist or equivalent until app-level auth exists.

## Data Ownership Findings

See [Multi-user data ownership matrix](multi-user-data-ownership-matrix.md) for the full table.

Summary:

- User-owned: tasks, task metadata, task events, reminders, confirmations, Telegram snapshots, per-user settings, Trello board mappings, LLM preferences and notification preferences.
- Integration-owned per user: Trello credentials/boards/workflow states, Telegram chat link, provider status and quota counters.
- Instance/admin-owned: host `.env`, DB URL, service port, backup storage, runtime diagnostics, release reports, global health and deployment settings.
- Derived/cache/runtime: Trello sync runs, Telegram updates/action queue, briefing runs, background jobs, diagnostics/support bundles, smoke reports.

Most current data can migrate from global to an initial owner in a single-user import step, but every query must be audited before any second user exists.

## Endpoint Findings

| Endpoint group | Current auth | Future auth requirement | Scope | Mobile suitability | Risk |
| --- | --- | --- | --- | --- | --- |
| `/api/health` | None | Public coarse health allowed | instance | yes, coarse | low if no internals |
| `/api/system/status`, `/api/system/diagnostics` | None | admin-only or user-safe subset | instance/admin | limited | P2 leak risk |
| `/api/tasks` CRUD/reorder/trash | None | authenticated user | user-api | yes | P0 global data/write |
| `/api/tasks/*suggest*` | None | authenticated user + quotas | user-api | yes | P1 LLM/quota/data leak |
| `/api/planning/today`, `/api/planning/now` | None | authenticated user | user-api | yes | P0 global planning |
| `/api/reminders` | None | authenticated user | user-api | yes | P0 notification leak |
| `/api/briefing/*` | None | authenticated user; send endpoints side-effect guarded | user/admin | partial | P1 notification side effect |
| `/api/settings*` | None | authenticated; some admin-only | user/admin/integration | mostly web | P0 global config writes |
| `/api/settings/trello/*` | None | authenticated integration owner | user integration | web/admin | P1 credential/board leakage |
| `/api/trello/sync`, `/api/trello/status` | None | authenticated integration owner or worker/admin | user/internal | limited | P1 external API fan-out |
| `/api/trello/cards/*` | None | authenticated dangerous-write + confirmation | user integration | maybe | P1 remote write |
| `/api/confirmations*`, `/api/jobs*` | None | authenticated user; bulk confirm rate-limited | user-api | yes | P0 action hijack |
| `/api/backups*`, `/api/system/backup*` | None | admin-only | instance/admin | no | P2 data export/restore risk |
| `/api/llm/status`, `/api/llm/validate` | None | status user-safe; validate user/admin with quotas | user/admin | limited | P2 quota/provider risk |
| `/api/dev/simulate-message` | None | internal-dev-only; disabled in production | dev-only | no | P0 public abuse |
| `/api/telegram/process-pending` | None | internal/admin only | worker/internal | no | P1 processing side effects |

## Worker Findings

| Worker | Current behavior | Multi-user issue | Future model | Rate/lock concern |
| --- | --- | --- | --- | --- |
| `reminder_worker` | Scans due reminders and sends only when the owner has a Telegram destination. | Needs hosted account-link onboarding and per-user notification policy beyond the legacy owner fallback. | Query due reminders by user/integration and send through user's notification channel. | Per-user send rate, idempotent mark-sent lock. |
| `briefing_worker` | Schedules briefings using user settings and sends only when the owner has a Telegram destination. | Needs hosted account-link onboarding and per-user scheduling controls. | Per-user schedules and quiet modes. | Avoid thundering herd at popular times. |
| `telegram_poller` / processor | Polls one bot and resolves inbound chats through `telegram_chat_links` or legacy single-owner allowlist fallback. | No user-facing link-code flow yet; one platform bot remains instance-owned. | Platform bot + linking code maps Telegram identity to user. | Per-chat idempotency, replay/offset isolation. |
| `trello_worker` / sync | Iterates active user integration mappings, using instance `.env` Trello credentials. | No per-user OAuth/encrypted token storage yet. | Iterate active Trello integrations per user with per-user credentials. | Trello API limits, per-user sync locks, stale-run recovery. |
| `background_jobs` | Bulk confirmations/jobs are global rows. | Bulk job could act on another user's confirmations. | Jobs have owner and optional integration id. | One job per user/action type lock. |
| backups/prune | Instance-level SQLite backup and retention. | Hosted user export is not the same as instance backup. | Admin instance backups plus separate user export/delete jobs. | Backup lock and restore isolation. |
| runtime status/diagnostics | Reports instance and integration state. | User could see admin/system/integration state. | Split admin diagnostics from user-safe account status. | Avoid expensive checks on polling. |
| LLM readiness | Global provider status and Settings toggle. | User quota/plan/provider choice absent. | Server-side provider with per-user quota and redaction. | Quota, timeout, concurrency limits. |

## Integration Findings

### Telegram

Option A - one platform bot, user links chat:

- One AlphaWave bot.
- User logs into web app, requests a link code, sends it to bot.
- Store `telegram_accounts(user_id, chat_id, telegram_user_id, linked_at, status)`.
- Recommended for hosted MVP because onboarding is smoother and credentials stay platform-owned.

Option B - user-provided bot token:

- Each user creates/provides their own Telegram bot token.
- Stronger user control, worse onboarding and secret-management burden.
- Better as an advanced/self-hosted option later.

Recommendation: hosted MVP should use Option A. Keep the current `.env` bot token as platform/instance secret, not per-user setting. Never expose chat ids in user diagnostics beyond masked labels.

### Trello

- Current: one `TRELLO_API_KEY`, `TRELLO_TOKEN`, `TRELLO_MEMBER_ID` and global board mappings.
- Future: per-user OAuth/token, encrypted at rest, with user-owned board mappings and workflow states.
- `Task.source_id` uniqueness must become `(user_id, source_type, source_id)` to avoid collisions.
- Auto-confirm must be user/integration scoped and auditable.

### OpenAI

- Current: global readiness and `.env` provider config.
- Future hosted MVP: server-side provider controlled by the app, with per-user quota/plan gates.
- Do not send user secrets, tokens, raw logs, `.env`, DB URLs, chat ids or unrelated tasks to the provider.
- OpenAI credentials and enablement must remain isolated per user.

## Frontend Findings

- `frontend/src/App.tsx` assumes one global application state and no current-user context.
- `frontend/src/api.ts` has no auth headers, token refresh, 401 handling or logout.
- Settings combines user preferences, integration settings and instance/admin controls in one view.
- Diagnostics, backups, Trello discovery and LLM validation are visible as ordinary app controls.
- Future UI needs login/logout, account menu, user context, integration pages, admin-only Settings sections and mobile-safe API errors.

## Recommended Migration Phases

Short version:

1. Phase 0 - private VPS single-user behind network/access guard.
2. Phase 1 - auth shell/admin-only hardening with current global data.
3. Phase 2 - ownership foundation and initial-owner migration.
4. Phase 3 - per-user integrations and user-aware workers.
5. Phase 4 - `/api/v1` and mobile readiness.
6. Phase 5 - plans, quotas, premium/free/ads domain model.

Detailed plan: [Multi-user migration plan](multi-user-migration-plan.md).

## What Not To Do Yet

- Do not add `user_id` piecemeal without request user context and query tests.
- Do not expose the VPS publicly without Basic Auth/VPN/IP allowlist before app auth.
- Do not move to per-user Trello/Telegram credentials before ownership boundaries.
- Do not build Android against the current global API.
- Do not add payments/ads until auth, ownership and quotas exist.
- Do not treat backups/diagnostics as user-safe until split and tested.

## Validation Gates For M19D/M19E

- All user-scoped endpoints require auth.
- Tests prove User A cannot read/write User B data.
- Settings explicit `false` override still works per user.
- Workers process only the correct user's records.
- Confirmations cannot execute actions for another user.
- Trello source uniqueness is scoped by user/integration.
- Telegram chat link cannot be hijacked or reused across users.
- Diagnostics redacts and scopes user/admin payloads.
- Backups are admin/instance-only or per-user export is explicitly separate.
- Dev endpoints are unavailable in production mode.
