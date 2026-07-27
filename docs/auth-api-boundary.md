# Auth API Boundary

This document classifies the API for auth. M19E implements the first gate when `ALPHAWAVE_AUTH_ENABLED=true`: `/api/health`, `/api/auth/session`, `/api/auth/login` and `/api/auth/csrf` remain public; non-public `/api/*` endpoints require the owner session.

M19F implements core user ownership for tasks, reminders and confirmations. M19G implements user-scoped personal Settings. M19H adds user-scoped Trello/Telegram integration boundaries while keeping provider credentials in instance `.env`. Diagnostics and backups are still instance/admin scoped.

## Categories

- `public`: no user data and safe for anonymous access.
- `authenticated-user`: requires current user; service queries must filter by `ctx.user_id`.
- `admin-only`: instance-wide or operational data.
- `internal-dev-only`: disabled outside dev/test.
- `dangerous-write`: authenticated plus confirmation, rate limit and audit.
- `integration-callback`: provider-authenticated callback mapped to user/integration.

## Endpoint Boundary Table

| Endpoint group | Future auth requirement | Future scope | Category | Mobile suitability | Risk | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /api/health` | none | instance coarse | public | yes | low | Must not include version paths, settings or integration state. |
| `GET /api/system/health` | admin or coarse public variant | instance | admin-only/public split | no | medium | Split coarse health from admin health. |
| `GET /api/system/info` | admin-only | instance | admin-only | no | medium | Runtime details can aid fingerprinting. |
| `GET /api/system/preflight` | admin-only | instance | admin-only | no | medium | Can reveal config/install state. |
| `GET /api/system/status` | user-safe subset for users; full admin-only | user/admin split | authenticated-user/admin-only | limited | medium | Current full status is too broad for normal users. |
| `GET /api/system/diagnostics` | admin-only | instance | admin-only | no | high | Support bundle/redaction surface. |
| `GET /api/llm/status` | authenticated user | user plus instance provider | authenticated-user | limited | medium | Return user-safe provider readiness and quota. |
| `POST /api/llm/validate` | admin or authenticated user with quota | user/admin | dangerous-write | no initially | medium | Manual action; rate-limit. |
| `/api/tasks` CRUD/list/search | authenticated user | user | authenticated-user | yes | high | M19F: queries filter by `Task.user_id == ctx.user_id`. |
| `/api/tasks/*complete/restore/snooze/delete/reorder/sort` | authenticated user; destructive paths audited | user | dangerous-write for destructive | yes | high | Path IDs need owner lookup, not `db.get`. |
| `/api/tasks/*suggest*` | authenticated user + quota | user | authenticated-user | yes | medium | LLM prompt must include only user's task/context. |
| `/api/planning/*` | authenticated user | user | authenticated-user | yes | high | M19F: task reads are user-scoped. M19G: priority settings are user-scoped. |
| `/api/briefing/status/runs/generate` | authenticated user | user | authenticated-user | partial | medium | Manual generate is user-scoped. |
| `/api/briefing/send`, `/send-test` | authenticated user + notification permission | user | dangerous-write | no initially | medium | Sends messages; rate-limit and audit. |
| `/api/reminders` | authenticated user | user | authenticated-user | yes | high | M19F: reminders are user-owned. |
| `/api/settings` | authenticated user for personal settings; admin for instance sections | user/instance split | authenticated-user/admin-only | web mostly | high | M19G routes personal sections to `user_settings`; Trello/backups/advanced remain instance/global. |
| `/api/settings/schema` | authenticated user | user/admin shaped | authenticated-user | no | low | Schema should reflect role/feature flags. |
| `/api/settings/reset-section` | authenticated user | user/instance split | dangerous-write | no | medium | Personal reset deletes current user's override; instance reset remains admin-only. |
| `/api/settings/trello/*` | authenticated user with Trello integration; owner/admin for instance-env discovery | user integration with instance credentials | authenticated-user/dangerous-write | web/admin | high | M19H scopes board mappings by `ctx.user_id`; OAuth/user token is future. |
| `POST /api/trello/sync` | authenticated user or internal worker | user integration | dangerous-write/internal | no | high | M19H sync accepts `user_id`; worker can iterate integration owners. |
| `GET /api/trello/status` | authenticated user | user integration | authenticated-user | limited | medium | User-safe sync status only. |
| `/api/trello/cards/*` | authenticated user + confirmation/write enabled | user integration | dangerous-write | maybe later | high | M19H resolves board config by task/confirmation owner. |
| `/api/confirmations` | authenticated user | user | authenticated-user | yes | high | M19F: confirmations are user-owned and hidden cross-user. |
| `/api/confirmations/*confirm/cancel/bulk*` | authenticated user + owner check + rate limit | user | dangerous-write | yes | high | Bulk confirm must never cross owner. |
| `/api/jobs` | authenticated user for own jobs; admin for all | user/admin | authenticated-user/admin-only | limited | medium | Job result payloads may include confirmations. |
| `/api/backups/*` | admin-only | instance | admin-only | no | high | Instance-wide data. User export is separate future API. |
| `/api/system/backup*` | admin-only | instance | admin-only | no | high | Legacy aliases should follow backups rules. |
| `POST /api/dev/simulate-message` | disabled in production; dev/test only | dev | internal-dev-only | no | high | Public access would let users spoof Telegram commands. |
| `POST /api/telegram/process-pending` | internal/admin only | worker | internal-dev-only/admin-only | no | high | Should move behind worker/auth boundary. |
| Telegram polling / future webhook | provider verification + linked account | user integration | integration-callback | no | high | M19H maps chat hash to `user_id`; webhook/link-code UI is future. |
| Future OAuth callbacks | provider state verification | user integration | integration-callback | no | high | State/PKCE required for Trello/OIDC. |

## Route Implementation Rule

Future route handlers should choose one of these dependencies:

```python
ctx: RequestContext = Depends(get_request_context)
admin: RequestContext = Depends(require_admin)
dev: DevContext = Depends(require_dev_mode)
```

No user-owned route should only depend on `get_db`.

## Mobile-Safe Subset

Good `/api/v1` candidates:

- tasks list/detail/create/edit/complete/restore/delete;
- planning today/now;
- Inbox processing;
- reminders list/create/cancel;
- confirmations list/confirm/cancel;
- user-safe settings summary;
- user-safe LLM suggestion status.

Not mobile v1:

- full Settings editor;
- Trello board discovery/mapping;
- backups/restore-plan;
- full diagnostics/support bundle;
- dev simulator;
- raw Telegram processing.
