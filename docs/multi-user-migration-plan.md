# Multi-User Migration Plan

This plan is the recommended sequence after M19C. It avoids partial multi-user implementation before auth, ownership and integration boundaries are ready.

M19D adds detailed design inputs:

- [Auth, user scope and request context design](auth-user-scope-design.md)
- [Per-user settings design](per-user-settings-design.md)
- [User isolation test strategy](user-isolation-test-strategy.md)
- [Auth API boundary](auth-api-boundary.md)

## Phase 0 - Private VPS Single-User

Current/M19B state.

Goals:

- Run the existing app as one-owner private beta.
- Keep nginx HTTPS as public edge and FastAPI on `127.0.0.1:8711`.
- Protect the app with VPN, Basic Auth, IP allowlist or equivalent.

Required schema changes:

- None.

Code areas touched:

- Deploy templates, scripts and docs only.

Risks:

- Public exposure without auth is full data/control exposure.
- SQLite is single-instance only.

Validation gates:

- `ALPHAWAVE_BASE_URL=https://... ./scripts/dev/rc-check.sh`
- health/status/scripts pass;
- backups validate;
- no default Telegram send or Trello write.

Rollback notes:

- Restore prior release directory/commit.
- Restore SQLite only from a validated backup and with service stopped.

## Phase 1 - Auth Shell / Admin-Only Public Hardening

Goals:

- Add login/logout and protect UI/API.
- Still operate as one admin/owner.
- Disable or gate dev/internal endpoints.
- Keep global data temporarily, but never present this as multi-user.

Required schema changes:

- `users` table or admin identity table.
- Session/token storage depending on auth choice.
- Optional `auth_audit` table.

Code areas touched:

- FastAPI dependencies for current principal.
- Frontend login/logout/current-user context.
- CORS/CSRF/session/token handling.
- Route guards for backups, diagnostics, dev and settings.

Risks:

- Auth over global data can create false confidence.
- Cookie/token handling mistakes can expose the whole instance.

Validation gates:

- Anonymous requests rejected for all non-public endpoints.
- `/api/health` remains coarse and public-safe.
- Dev endpoints unavailable in production mode.
- Browser smoke covers logged-out and logged-in flows.

Rollback notes:

- Keep a local/admin recovery path documented.
- Do not migrate user data yet, so rollback is auth-layer only.

### Concrete Step 1 - Users/Auth Tables

Goal: protect the app before pretending it is multi-user.

- Add `users`, session/token storage and `auth_audit`.
- Create the initial owner/admin.
- Add `CurrentUser` and `RequestContext` dependencies.
- Protect all non-public endpoints, but keep current global data reads for the single admin only.
- Keep `/api/health` coarse and public.
- Disable `/api/dev/*` in production.

Rollback:

- Auth tables can be ignored/rolled back while no data ownership migration has happened.
- Keep recovery instructions for local admin credentials.

## Phase 2 - Data Ownership Foundation

M19F implements the first slice of this phase for core user resources: `tasks`, `task_events`, `reminders` and `pending_confirmations`. M19G adds per-user runtime settings for personal preferences. Jobs, Trello/Telegram integration state, backups and diagnostics remain later work.

Goals:

- Add owner scope to core data.
- Migrate current global data to the initial owner.
- Make every user-facing query filter by current user.

Required schema changes:

- `users` and possibly `workspaces`.
- M19F: nullable `user_id` on `tasks`, `task_events`, `reminders`, `pending_confirmations`.
- M19G: `user_settings(user_id, section, key, value_json)` for personal settings sections.
- Later: `user_id` on `deleted_external_refs`, `background_jobs`, `settings_audit`, `telegram_updates`, `telegram_snapshots`, `action_queue`, `briefing_runs`, `trello_sync_runs`; integration-specific tables instead of user-owned `app_settings`.
- Unique constraints for external refs such as `(user_id, source_type, source_id)`.
- Indexes for `(user_id, status)`, `(user_id, manual_order)`, `(user_id, remind_at)`.

Code areas touched:

- Route dependencies pass current user to services.
- Task/reminder/confirmation/settings/planning services.
- Telegram snapshot lookup.
- Trello sync upsert and tombstones.
- Tests and fixtures.

Risks:

- One missing filter leaks or mutates another user's data.
- JSON payloads may contain task ids or external refs that also need ownership checks.

Validation gates:

- User A cannot list/read/write/delete User B tasks.
- User A cannot execute User B confirmations.
- Settings explicit `false` override works per user for personal sections.
- Planning and priority only use current user's tasks.
- Migration rehearsal converts existing DB to initial owner.

Rollback notes:

- Before migration, create and validate backup.
- Keep migration reversible while no second user exists.
- After second user data exists, rollback must preserve ownership.

### Concrete Step 2 - Nullable Ownership and Backfill

Goal: introduce ownership without breaking existing dogfooding data.

- Add nullable `user_id` columns to user-owned tables.
- Backfill every existing row to the initial owner.
- Backfill related tables from parent ownership where possible, for example `task_events` from `tasks`.
- Add indexes for expected scoped queries.
- Keep compatibility checks that fail if any user-owned row remains null after backfill.

Rollback:

- Restore the pre-migration SQLite backup if backfill fails.
- Do not create second-user data until backfill is verified.

### Concrete Step 3 - Enforce Non-Null Ownership

Goal: make global reads impossible by default.

- New writes require `ctx.user_id`.
- Service functions require `user_id` or `RequestContext`.
- User-owned lookups use scoped queries, never bare `db.get`.
- Add not-null constraints once all tests and backfills pass.
- Add A/B isolation tests for every endpoint group.

Rollback:

- After non-null ownership and multiple users, rollback must preserve owner columns.
- Treat user ownership migration as a release boundary.

### Concrete Step 4 - Per-User Settings Runtime

M19G status: implemented for personal preferences.

- Added `user_settings` with `UNIQUE(user_id, section, key)`.
- Effective settings now merge code defaults, instance `app_settings` and user overrides.
- Personal sections: `general`, `reminders`, `briefing`, `modes`, `priority`.
- Instance/global sections: `trello`, `backups`, `advanced`.
- Settings API reads/writes personal sections through `RequestContext.user_id`.
- Reminder, briefing, weekend and priority/planning helpers accept `user_id`.

Limitations:

- User overrides are stored as full section payloads.
- Scheduled briefing iteration is still single-owner/process-oriented.
- Trello/Telegram integration settings are intentionally not per-user yet.

## Phase 3 - Per-User Integrations

M19H implements the boundary/scaffolding slice of this phase. M19I adds encrypted per-user integration secrets and Telegram link-code onboarding. Trello OAuth and hosted-grade onboarding remain future work.

Goals:

- Move Trello and Telegram from instance-level behavior to user-linked integration boundaries.
- Make workers user-aware.
- Add encrypted storage for per-user integration secrets.

Required schema changes:

- M19H: `user_integrations(user_id, provider, status, config_json, credentials_source)`.
- M19H: `telegram_chat_links(user_id, chat_id_hash, chat_id_redacted, status)`.
- M19I: `integration_secrets(user_id, provider, secret_type, ciphertext, redacted_hint, status, rotated_at, revoked_at)`.
- M19I: `telegram_link_codes(user_id, code_hash, expires_at, consumed_at)`.
- Future: `telegram_accounts(user_id, chat_id_encrypted, telegram_user_id, status, linked_at)`.
- `trello_accounts(user_id, encrypted_token, member_id, status)`.
- `trello_boards(user_id, integration_id, alias, board_id, display_name, enabled)`.
- `trello_workflow_states(board_id, role, list_id, list_name, enabled)`.
- Quota/rate metadata for LLM and integrations.

Code areas touched:

- M19H: Telegram processor ownership mapping.
- M19H: Trello sync/write mapping by integration owner.
- M19I: Telegram link-code flow and manual Trello encrypted credential foundation.
- Future: Trello OAuth/token flow and hosted onboarding.
- Settings UI split into Account, Integrations and Admin.
- Background workers, locks and rate-limit handling.
- Diagnostics redaction for identifiers.

Risks:

- Secret migration/storage mistakes.
- Telegram chat hijacking if link codes are weak or reusable.
- Trello API rate limits with multiple users.

Validation gates:

- Telegram update from linked chat acts only on linked user.
- Trello sync for User A cannot affect User B.
- Integration unlink disables workers and remote writes.
- Per-user worker locks prevent duplicate sends/syncs.
- Tokens never appear in diagnostics/logs.

Rollback notes:

- Keep integration unlink/revoke path.
- Do not delete global `.env` integration settings until hosted path is stable.

### Concrete Step 4 - User-Aware Integrations and Workers

Goal: prevent external actions from crossing users.

- Add Telegram account linking: platform bot token stays instance-level, chat/user ids map to `user_id`.
- Add Trello account/board/workflow tables per user integration.
- Worker loops iterate enabled users/integrations with per-user locks.
- Confirmation payloads include owner/integration ids and validate them before execution.
- Add rate limits per user and per integration provider.

Rollback:

- Keep global `.env` integration fallback only for private single-user until per-user path is verified.
- Provide unlink/revoke for each integration.

### Concrete Step 5 - Admin/Instance Resources

Goal: separate hosted admin operations from user product APIs.

- Backups remain admin/instance-level.
- Full diagnostics remain admin-only.
- Add user-safe account status separately.
- Keep restore-plan admin-only and restore execution manual/disabled.

Rollback:

- Admin resources can remain private/VPN-only until app role checks are complete.

## Phase 4 - API Versioning / Mobile Readiness

Goals:

- Introduce stable client APIs for web/mobile.
- Prepare Android without changing server authority.
- Add mobile auth/session strategy.

Required schema changes:

- Device/session table if using first-party auth.
- Optional sync cursor/change log table.
- Notification device tokens if push is added.

Code areas touched:

- `/api/v1` route layer.
- API error codes and pagination/updated-since.
- Frontend API client and future mobile client contracts.
- Rate limits and quotas.

Risks:

- Freezing unstable UI-specific contracts too early.
- Mobile token storage/revocation mistakes.
- Offline conflicts.

Validation gates:

- Contract tests for list/create/edit/complete/planning/reminders.
- Token refresh/revocation tests.
- User-scoped API tests remain green.
- Mobile-safe diagnostics exclude admin data.

Rollback notes:

- Keep legacy web API until v1 parity is proven.
- Version new clients explicitly.

## Phase 5 - Monetization Entitlements

Goals:

- Add plans, quotas and premium/free feature flags.
- Keep monetization server-side.
- Prepare ads only after privacy review.

Required schema changes:

- `plans`, `subscriptions`, `entitlements`, `quota_counters`.
- Billing provider customer/subscription ids if paid.
- Audit log for plan changes.

Code areas touched:

- AI suggestion quota.
- Integration/board count limits.
- Backup/history retention policy.
- Mobile push entitlement if added.
- UI upgrade/account pages.

Risks:

- Ads or billing near private task content.
- Quota bugs blocking core task access.
- Provider cost abuse if LLM quotas are weak.

Validation gates:

- Entitlements enforced server-side.
- Quotas fail gracefully.
- Free tier cannot bypass premium limits via API.
- Billing/ad logs do not include task content.
- Account deletion/export policy tested.

Rollback notes:

- Feature flags for premium enforcement.
- Billing changes require audit and manual support path.

## Cross-Phase Rules

- Do not expose public multi-user access before ownership tests exist.
- Do not store per-user integration secrets unencrypted.
- Do not add Android before `/api/v1` and auth/session are stable.
- Do not add ads/payments to local dogfooding runtime.
- Keep RC check, diagnostics redaction and backup validation green at every phase.
