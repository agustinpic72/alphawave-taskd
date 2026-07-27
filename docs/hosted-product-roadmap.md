# Hosted Product Roadmap

This roadmap translates the M19 product direction into deployable phases. It intentionally avoids implementing auth, multi-user, Android, payments or ads in M19A.

M19C adds the detailed multi-user audit and should be treated as the input for the next design milestones:

- [Multi-user readiness audit](multi-user-readiness-audit.md)
- [Multi-user data ownership matrix](multi-user-data-ownership-matrix.md)
- [Multi-user migration plan](multi-user-migration-plan.md)

M19D adds the auth and request-context design:

- [Auth, user scope and request context design](auth-user-scope-design.md)
- [Auth API boundary](auth-api-boundary.md)
- [Per-user settings design](per-user-settings-design.md)
- [User isolation test strategy](user-isolation-test-strategy.md)

## Recommended Path

```text
Phase 1 - Web/VPS single-user private beta
Phase 2 - Public web hardening with auth single-user/admin
Phase 3 - Multi-user backend foundation
Phase 4 - Android app API client
Phase 5 - Premium/free monetization
```

## Phase 1 - VPS Web Private Beta

Scope:

- Deploy the current app to one VPS.
- Use nginx + HTTPS.
- Keep FastAPI private on `127.0.0.1:8711`.
- Keep SQLite and existing `data/backups`.
- Keep one owner/admin user operationally, even if app auth is not implemented yet.
- Use existing Trello/Telegram settings and confirmation gates.

Success criteria:

- Browser can open the app over HTTPS.
- TODO/Hoy/Ahora, detail panel, Inbox, Settings and backups work like local.
- No global horizontal overflow in responsive smoke.
- Trello read-only and Telegram safe smoke pass or are explicitly skipped.
- No Trello writes or Telegram sends in default RC flow.

Technical requirements:

- VPS package install: Python, Node/npm, nginx, systemd, Chrome or Playwright Chromium for smoke.
- `frontend/dist` built during deploy.
- `.env` exists server-side and is not served.
- nginx serves static assets and proxies `/api`.
- Persistent disk for `data/`, `logs/` and reports.
- Off-server copy strategy for backups.

Risks:

- No app-level auth yet.
- SQLite is acceptable only for private single-instance use.
- Current diagnostics can reveal operational metadata if public.

Exit criteria:

- `./scripts/dev/rc-check.sh` passes against the HTTPS URL.
- Manual backup, validation and restore-plan pass.
- Security doc updated with VPS private-beta constraints.
- Deployment rollback path documented.

What not to do yet:

- Do not offer public signups.
- Do not migrate to Postgres unless multi-user starts.
- Do not implement mobile push, payments or ads.

## Phase 2 - Public Web Hardening

Scope:

- Add authentication for the web app before any public access.
- Treat the deployment as one admin/user, not general multi-user.
- Harden public HTTP surface.

Success criteria:

- Anonymous users cannot access app data or diagnostics.
- Health endpoint remains safe and coarse.
- Dev-only endpoints are disabled or strongly guarded.
- CSRF/session or token strategy is documented and tested.

Technical requirements:

- Auth/session model.
- Hybrid strategy: cookie session + CSRF for web, bearer access/refresh tokens for future Android.
- `CurrentUser` and `RequestContext` dependencies for user-scoped routes.
- Secure cookie or token storage decision.
- CORS limited to production origin.
- Login/logout, session expiry and password/secret rotation plan.
- Rate limiting for auth and side-effect endpoints.
- Admin-only access for diagnostics, backups, RC reports and restore-plan.

Risks:

- Bolting auth onto global data can create false confidence.
- Public admin-only mode is still not a multi-user product.

Exit criteria:

- Browser smoke covers logged-in and logged-out access.
- Security tests prove protected endpoints reject anonymous requests.
- Support bundles still redact secrets and sensitive identifiers.

What not to do yet:

- Do not introduce paid/free plans.
- Do not store per-user integration tokens until user ownership model is ready.

## Phase 3 - Multi-User Backend Foundation

Scope:

- Convert from global single-user data to user/workspace-owned data.
- Move to Postgres.
- Introduce reliable migrations and user-scoped integrations.

Success criteria:

- Every task, reminder, confirmation, setting and integration has an owner.
- Cross-user access tests exist for every major endpoint group.
- Background workers process per-user jobs without leaking data.
- Per-user Trello/Telegram credentials are encrypted at rest.

Technical requirements:

- `users`, `accounts` or `workspaces` table.
- Ownership columns on tasks, events, reminders, confirmations, settings, Telegram snapshots/updates and Trello sync runs.
- Migration path from current SQLite owner data to first user.
- Uniqueness for external refs scoped by user/integration, for example Trello card ids.
- Postgres deployment, backup and restore strategy.
- Per-user scheduled job model.
- Admin diagnostics separated from user diagnostics.
- Quotas/limits infrastructure, even if all plans are free at first.

Risks:

- Data leakage if queries miss ownership filters.
- Worker fan-out can hit Telegram/Trello rate limits.
- Secrets migration is sensitive.

Exit criteria:

- Multi-user isolation test matrix passes.
- Migration from current single-user data to owner user is rehearsed.
- Postgres backup/restore tested.
- Trello/Telegram account unlink flows exist.

What not to do yet:

- Do not launch Android broadly until API contracts are stable.
- Do not monetize until plan/entitlement checks are server-side.

## Phase 4 - Android App API Client

Scope:

- Build Android as an authenticated API client.
- Reuse hosted backend as source of truth.
- Add mobile-friendly API contracts and notification strategy.

Success criteria:

- Android can list/create/edit/complete tasks.
- Android can show Hoy/Ahora.
- Android can process Inbox basics.
- Android auth works without sharing web session secrets unsafely.
- Offline/cache behavior is explicit.

Technical requirements:

- Versioned API contracts, likely `/api/v1`.
- Token/session strategy suitable for mobile.
- Push notification decision: FCM, Telegram fallback or no push in v1.
- Sync cursors or updated-since endpoints for mobile cache.
- Mobile-safe error codes.
- Device/session management.

Risks:

- Offline edits can conflict with server state.
- Push notifications add new privacy and token storage concerns.

Exit criteria:

- API contract tests for Android flows.
- Play Store internal test release checklist ready.
- Crash/log redaction policy ready.

What not to do yet:

- Do not make Android authoritative for task state.
- Do not embed Trello/Telegram secrets in the app.

## Phase 5 - Premium/Free Monetization

Scope:

- Introduce entitlements, quotas and billing/ad decisions after multi-user and mobile foundations exist.

Possible premium features:

- More integrations or boards.
- Higher AI suggestion quota.
- Advanced planning.
- Mobile push.
- Longer backup/history retention.
- Team/shared spaces in a later phase.

Possible free tier constraints:

- Task count limits.
- AI suggestion quota.
- Limited integrations/boards.
- Shorter history/backup retention.
- Ads in web/mobile free tier if chosen.

Success criteria:

- Entitlements are enforced server-side.
- Limits fail gracefully and explain upgrade paths.
- Ads do not compromise privacy or task readability.
- Billing/ad SDKs do not receive task content.

Technical requirements:

- Plans, subscriptions, entitlements and quota counters.
- Server-side checks for AI, integration count, backup/history and mobile push.
- Audit logs for entitlement changes.
- Privacy review for ad providers.

Risks:

- Premature monetization can distort core UX.
- Ads near private task content can feel unsafe.
- Billing complexity can distract from product fit.

Exit criteria:

- Terms/privacy policy ready.
- Refund/cancellation/account deletion path ready.
- Quota tests pass.

What not to do yet:

- Do not design ads before auth/user model.
- Do not put monetization code into local dogfooding runtime.
- Do not make AI provider costs invisible to quotas.

## Cross-Phase Validation Gates

Every phase should keep:

- backend tests passing;
- frontend build passing;
- browser smoke passing;
- RC check passing;
- diagnostics redaction checks passing;
- backup and restore-plan checked;
- no default Telegram sends;
- no default Trello writes.
