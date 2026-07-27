# Multi-User Data Ownership Matrix

This matrix maps current single-user resources to future hosted scopes. It is an audit artifact only; no schema changes are implemented in M19C.

| Entity / Table / Resource | Current scope | Future scope | Sensitive? | Needs `user_id`? | Needs org/team later? | Difficulty | Risks | Recommended approach |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tasks` | Global SQLite table | User-owned task, maybe workspace task later | Yes: titles, deadlines, Trello links | Yes | Maybe | High | Cross-user task leak/write; global order collisions | Add `user_id`; scope every query; uniqueness on `(user_id, source_type, source_id)` when external. |
| `tasks.metadata_json` | Global JSON blob | User-owned task detail | Yes: notes, Trello description/checklists | Inherits task | Maybe | Medium | Hidden sensitive data in JSON sent to UI/LLM | Keep under task owner; add JSON redaction rules for diagnostics/LLM. |
| `task_events` | Global event log | User-owned audit/history | Yes | Yes | Maybe | Medium | Cross-user history and action leak | Add `user_id`; backfill from task owner; keep task id nullable only for system events. |
| `deleted_external_refs` | Global tombstone | User/integration-owned tombstone | External IDs | Yes | Maybe | Medium | One user's tombstone can suppress another user's card import | Scope by `user_id` and integration account id. |
| `reminders` | Global reminders | User-owned reminder | Yes: message, schedule, task link | Yes | Maybe | High | Wrong-user notification | Add `user_id`; channel points to user's linked notification integration. |
| `pending_confirmations` | Global queue | User-owned confirmation | Yes: action payload, chat id | Yes | Maybe | High | User confirms another user's Trello write/delete/sort | Add owner, integration id and authorization check before execution. |
| `background_jobs` | Global job rows | User-owned or admin job | Payload may be sensitive | Yes for user jobs | Maybe | Medium | Bulk job acts on wrong confirmations/tasks | Add owner/scope/kind constraints; workers load by owner. |
| `app_settings` | Global section key | Split: user settings, integration settings, instance settings | Mixed; no secrets intended | Yes for user prefs | Maybe | High | One user changes all runtime behavior | Replace with scoped settings table or key namespace with owner. |
| `settings_audit` | Global audit | User/admin audit | Yes: old/new settings | Yes for user changes | Maybe | Medium | Audit reveals other user's settings | Add actor/owner/scope; redact admin secrets. |
| Trello boards/settings/workflow states | Stored inside global `app_settings.trello` | Per-user Trello integration config | Board IDs/list IDs | Yes | Maybe | High | Board mapping collisions; remote writes to wrong board | Store by user integration id; include display names as cache only. |
| Trello cards synced/local task links | `tasks.source_type=trello`, `source_id`, board/list fields | Per-user integration mirror | Yes | Yes | Maybe | High | Same card id across users; wrong remote action | Scope external refs and Trello action payloads by user/integration. |
| `trello_sync_runs` | Global sync run | Per-user/per-integration run plus admin summary | Errors can reveal board data | Yes | Maybe | Medium | User sees another user's sync status | Add integration id and owner; expose user-safe status only. |
| Telegram config/allowlist | `.env` global bot token and allowed id | Platform bot plus per-user chat link | Yes: token/chat ids | Chat mapping needs user_id | Maybe | High | Cross-user command processing | Keep platform bot token in env; store chat link table with verification code. |
| `telegram_updates` | Global update queue | Per-chat/user linked update | Yes: raw message text/chat id | Yes after link | Maybe | High | Raw command data leak; wrong account action | Link update to user before executing; mask raw JSON in diagnostics. |
| `telegram_snapshots` | Chat-scoped task id snapshots | User/chat scoped snapshot | Yes: task ids | Yes | Maybe | High | Snapshot index can reference another user's task | Store owner and validate task ownership on lookup. |
| `action_queue` | Global Telegram action queue | User-owned action queue | Yes | Yes | Maybe | Medium | Replay/action confusion across users | Add owner, source channel and idempotency key. |
| `briefing_runs` | Global daily run | User-owned scheduled briefing run | Yes: payload summary | Yes | Maybe | Medium | One user's briefing status/payload visible to all | Add owner; schedule per user/timezone. |
| Briefing/check-in status | Derived from tasks/settings | User-owned read model | Yes | Yes | Maybe | Medium | Wrong tasks in summary | Filter planning/briefing by user and active scopes. |
| Priority settings | Global `app_settings.priority` | User preference, maybe workspace default | Low/medium | Yes | Maybe | Medium | One user changes another's planning | Scope settings; allow admin default copied at user creation. |
| Backups | Instance SQLite files | Admin instance backup; separate user export later | Very high | No for instance; yes for user export | Maybe | High | Public download/restore exposes all users | Keep admin-only; define per-user export/delete separately. |
| Runtime status/events | Instance service status | Admin full status; user-safe subset | Medium | No full; user subset maybe | Maybe | Medium | Operational/integration metadata leak | Gate admin status; expose coarse user account health. |
| Diagnostics/support bundles | Instance support zip | Admin-only bundle; user-safe export later | High | User subset yes | Maybe | High | Bundle leaks all users/tasks/settings | Keep admin-only and continue redaction tests. |
| LLM status/config | Global `.env` and app setting | Server provider plus per-user quota/preference | Provider config sensitive | User usage/quota yes | No initially | Medium | Cost abuse; prompt data leakage | Keep provider server-side; add quota counters and prompt minimization. |
| Browser smoke data/reports | Global DB prefixes and `reports/` | Dev/test tenant only | Can contain task titles | Test user/tenant | No | Medium | Smoke pollutes production user data | Disable in prod or run against dedicated test account. |
| RC reports | Local `reports/` files | Admin release artifact | Medium | No | No | Low/medium | Operational detail leak | Keep out of public web; admin/support only. |
| `.env` | Instance file | Instance/admin secret config | High | No | No | Low | Secret leak if served/committed | Never expose; user secrets move to encrypted DB/secret store later. |
| `frontend` client state | Single global app state | Authenticated current-user state | Depends on data | N/A | Maybe | Medium | UI shows admin controls to users | Add current user context, roles and route guards. |

## Ownership Defaults For First Migration

When Phase 2 introduces ownership, existing global data should migrate to one initial owner:

- every active/completed/deleted task;
- task events and tombstones;
- reminders and confirmations;
- app settings and settings audit;
- Telegram snapshots/updates/action queue;
- Trello sync runs and board settings;
- briefing runs/background jobs.

This preserves dogfooding data while making the ownership model explicit before a second user exists.
