# Auth, User Scope and Request Context Design

M19D was a design milestone. M19E implemented the first auth shell from this design: cookie sessions, CSRF, owner bootstrap and `RequestContext` availability.

M19F adds core ownership for `tasks`, `reminders`, `pending_confirmations` and `task_events`. Core task/reminder/confirmation/planning/suggestion/Inbox API paths now thread `RequestContext.user_id` into service queries, so authenticated user A cannot list or mutate user B core resources.

Important limitation: M19I is not complete SaaS isolation. Core data, personal Settings, Trello/Telegram integration boundaries and encrypted integration secrets are user-scoped, but Trello OAuth, hosted onboarding polish, diagnostics and backups remain future/admin scoped.

## Decision Summary

Recommended model: hybrid auth.

- Web MVP: HttpOnly Secure cookie session with CSRF protection.
- Future Android/mobile: bearer access token plus rotating refresh token, stored in Android Keystore.
- Shared backend: same `users` table, same authorization checks and same `RequestContext`; only the credential transport differs.

Why hybrid:

- Browser users get the safer default of HttpOnly cookies instead of JavaScript-readable tokens.
- Android is not forced into cookie-only browser semantics.
- Service-layer ownership checks remain identical for web and mobile.

## Auth Options Compared

| Option | Pros | Cons | Recommendation |
| --- | --- | --- | --- |
| Cookie session + CSRF | Good web fit; HttpOnly cookies reduce token theft from XSS; familiar logout/session invalidation. | Requires CSRF design; not ideal as the only Android auth. | Use for public web hardening. |
| JWT bearer access + refresh | Strong API/mobile fit; easy for non-browser clients. | Web storage is risky if tokens are readable by JS; refresh rotation/revocation must be correct. | Use for Android/mobile later. |
| Hybrid | Best fit for both web and mobile; shared user model and auth service. | Two transports to test; more docs and test surface. | Chosen direction. |
| External OIDC provider | Outsources password/MFA/reset. | Provider dependency and setup complexity. | Revisit before public multi-user; not required for initial admin shell. |

## Recommended Auth Model

Phase 1 public web hardening:

- one initial admin user;
- password auth or invite-created admin credentials;
- Argon2id preferred for password hashing; bcrypt acceptable if dependency simplicity wins;
- session stored server-side or in signed/encrypted cookie with server-side revocation metadata;
- cookie flags: `HttpOnly`, `Secure`, `SameSite=Lax` at minimum, `SameSite=Strict` if UX allows;
- CSRF token for unsafe methods if cookie auth is used;
- CORS restricted to the production web origin;
- login and unsafe endpoint rate limits;
- audit log for login, logout, password changes and integration writes.

Future mobile:

- `/api/v1/auth/login` or OIDC flow returns short-lived access token plus rotating refresh token;
- refresh token stored in Android Keystore;
- logout revokes refresh token/device session;
- access token never contains sensitive task data;
- refresh token reuse is treated as compromise and revokes that session family.

## User Model Draft

Implemented minimal table:

```sql
users (
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NULL,
  display_name TEXT,
  role TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
```

Implemented session table plus possible future audit table:

```sql
auth_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  session_hash TEXT NOT NULL,
  auth_method TEXT NOT NULL,
  user_agent TEXT,
  ip_hash TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
)

auth_audit (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  event_type TEXT NOT NULL,
  metadata_json TEXT,
  created_at TEXT NOT NULL
)
```

`role` starts with `admin` and `user`. Do not add team/organization roles until workspace ownership exists.

## Request Context Design

Future FastAPI pseudocode:

```python
from dataclasses import dataclass
from fastapi import Depends, Request

@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    role: str
    status: str

@dataclass(frozen=True)
class RequestContext:
    user_id: str
    is_admin: bool
    auth_method: str
    request_id: str | None = None

def get_current_user(request: Request, db=Depends(get_db)) -> CurrentUser:
    # Resolve session cookie or bearer token.
    # Raise 401 if missing/invalid.
    ...

def get_request_context(current_user=Depends(get_current_user)) -> RequestContext:
    return RequestContext(
        user_id=current_user.id,
        is_admin=current_user.role == "admin",
        auth_method="cookie",
    )
```

Public endpoints may use `Optional[RequestContext]`; all user data endpoints must require `RequestContext`.

## Backend Service Scoping Pattern

M19F implementation:

- `Task.user_id`, `Reminder.user_id`, `PendingConfirmation.user_id` and `TaskEvent.user_id` are nullable for SQLite migration safety.
- New service writes set `user_id`; local/auth-disabled writes use a bootstrap/single-owner user.
- SQLite `ensure_schema()` adds missing `user_id` columns, creates a bootstrap owner if needed and backfills rows without an owner.
- Cross-user task/reminder/confirmation access returns 404 from user-scoped lookup helpers.
- M19I adds `integration_secrets` and `telegram_link_codes`: Telegram chats can be linked with `/link CODE`, outbound chat IDs can be stored encrypted, and Trello manual credentials can be stored encrypted for private beta. Real Trello OAuth remains future work.

Chosen pattern: explicit `ctx` or `user_id` parameter threaded through route -> service -> query.

Future route shape:

```python
@router.get("/api/tasks")
def list_tasks(
    status: str = Query(default="active"),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
) -> TaskList:
    return TaskList(tasks=task_service.list_tasks(db, status=status, user_id=ctx.user_id))
```

Future service/query shape:

```python
def list_tasks(db: Session, user_id: str, status: str = "active") -> list[Task]:
    return list(
        db.scalars(
            select(Task)
            .where(Task.user_id == user_id)
            .where(Task.status == status)
            .order_by(Task.manual_order.asc(), Task.created_at.asc())
        )
    )

def get_task_for_user(db: Session, user_id: str, task_id: str) -> Task | None:
    return db.scalar(select(Task).where(Task.user_id == user_id, Task.id == task_id))
```

Rules:

- Do not call `db.get(Task, task_id)` for user-owned resources.
- Do not accept `user_id` from request payloads for ordinary user endpoints.
- Admin endpoints must use explicit admin service methods and audit events.
- Query scoping belongs in service/repository helpers, not only in routes.
- API tests cover A/B isolation for tasks, reminders, confirmations, planning, suggestions and Inbox.

Repository layer can be added if service functions become too large, but the first implementation can keep the current service modules with mandatory `user_id`.

## Endpoint Auth Classes

| Class | Meaning | Examples |
| --- | --- | --- |
| `public` | No user data, coarse status only. | `/api/health` |
| `authenticated-user` | Requires current user and filters by `ctx.user_id`. | `/api/tasks`, `/api/planning/*`, `/api/reminders` |
| `dangerous-write` | Authenticated plus confirmation, audit and rate limit. | Trello writes, bulk confirmation, permanent delete |
| `admin-only` | Requires admin role; instance-wide data. | backups, diagnostics, full system status |
| `internal-dev-only` | Disabled outside dev/test mode. | `/api/dev/simulate-message`, raw Telegram process endpoint |
| `integration-callback` | Authenticated by provider signature/code and mapped to user/integration. | future Telegram webhook, Trello OAuth callback |

Detailed endpoint map: [Auth API boundary](auth-api-boundary.md).

## Admin and Dev-Only Boundary

Admin-only:

- full diagnostics/support bundle;
- backups, backup validation, restore-plan, restore refusal;
- full system status with runtime/integration detail;
- deployment/release metadata;
- instance settings.

Dev-only:

- Telegram simulator;
- manual process-pending endpoint;
- smoke helpers and test-only endpoints.

Dev-only endpoints must be disabled by environment flag in hosted production, not merely hidden in the frontend.

## Mobile Implications

- Android should target `/api/v1`, not the current UI-shaped route surface.
- Android must use bearer access token + rotating refresh token or OIDC.
- Token refresh, revocation and device/session management must be tested before Play Store.
- Mobile should receive only user-safe status, not admin diagnostics/backups.
- Offline v1 should be read-through cache only; destructive writes remain server-authoritative.

## Security Risks

- IDOR if routes use global ids without `ctx.user_id`.
- CSRF if cookie auth protects unsafe methods without CSRF token.
- XSS impact if web stores bearer tokens in localStorage/sessionStorage.
- Session fixation if login does not rotate session ids.
- Replay if refresh token rotation is absent.
- User data leakage through diagnostics, logs, LLM prompts or support bundles.
- Remote Trello writes if confirmations are not owner-scoped.

## Implementation Phases

1. Auth shell: create users/session primitives, protect non-public endpoints, keep data global for the single admin only. Implemented in M19E with `ALPHAWAVE_AUTH_ENABLED`.
2. Request context: introduce `CurrentUser` and `RequestContext`, but keep behavior behind compatibility tests.
3. Ownership migration: add/backfill `user_id`, then update service methods to require owner scope.
4. Admin split: move backups/diagnostics/instance settings behind admin-only guards.
5. Mobile auth: add `/api/v1` and token/refresh flow only after user scoping tests pass.

## Explicitly Out Of Scope For M19D

- functional login UI;
- real auth dependencies;
- DB schema changes;
- `user_id` columns;
- endpoint behavior changes;
- external OAuth;
- Android implementation.

## M19E Runtime Notes

Auth is controlled by infrastructure config:

```env
ALPHAWAVE_AUTH_ENABLED=false
ALPHAWAVE_AUTH_COOKIE_SECURE=false
ALPHAWAVE_AUTH_SESSION_TTL_HOURS=168
ALPHAWAVE_AUTH_COOKIE_NAME=alphawave_session
```

Local dogfooding defaults to disabled. VPS private beta should set:

```env
ALPHAWAVE_AUTH_ENABLED=true
ALPHAWAVE_AUTH_COOKIE_SECURE=true
```

Bootstrap one owner:

```bash
./scripts/admin/create-owner.sh
```

Do not create multiple real users yet. M19E has no per-user data isolation.
