# Dogfooding Findings

## Fixed in M16

- P1: after a systemd restart, the app could stay unavailable for about 40 seconds while startup Telegram/Trello processing waited on external calls. The backend now exposes the API/UI immediately after local initialization and runs Telegram, reminders, briefing and Trello startup work in a cancelable background runtime task with per-step timeout.

## Fixed in M17A

- Added a fast Playwright browser smoke for Settings, System Status, dirty/discard save bar, non-destructive Trello/Backups rendering, local task completion and priority explanation.
- Added a visible local runtime/commit hint in System Status.

## Fixed in M17B

- Added `./scripts/dev/trello-readonly-smoke.sh`, a live Trello validation command that uses only GET requests.
- Added a guarded read-only Trello client for smoke validation; POST, PUT, PATCH and DELETE are blocked before any network call.
- Added mapping checks for enabled boards, required `pending`/`completed` list IDs, remote board/list existence, closed boards/lists, disabled boards and disabled `perpetual`.
- Auto-confirm state is shown in the report but never executed.

## Fixed in M17C

- Added `./scripts/dev/telegram-live-smoke.sh`, a Telegram live safety smoke that validates local status and `getMe` by default without sending messages.
- Added opt-in one-message smoke send through `ALPHAWAVE_TELEGRAM_SMOKE_SEND=1`, restricted to the configured allowlist.
- Added token, Telegram URL and chat id redaction helpers for smoke reports/errors.
- Manual Telegram command flow remains manual; the automated smoke does not create tasks, reminders, updates or webhook changes.

## Fixed in M17D

- Added `./scripts/dev/rc-check.sh`, a local release-candidate dogfooding orchestrator with Markdown reports in `reports/`.
- Added `./scripts/dev/local-api-smoke.sh` for controlled `[M17D SMOKE]` API flows: local completion, reminder cancel, priority proposal, backup validate and restore-plan.
- Fixed the local API smoke to cancel its own `apply_sort_order` confirmation after priority proposal, avoiding stale pending confirmations.
- RC check verifies health/startup timing, system status redaction shape, browser smoke, Trello read-only smoke and Telegram default smoke without writes or sends by default.

## Fixed in M17E

- Added richer runtime metadata to System Status: branch, dirty state, ahead count, Python version, startup timestamp and uptime.
- Added read-only `/api/system/diagnostics` with redacted runtime, services and settings summaries.
- Added `./scripts/dev/export-diagnostics.sh`, generating sanitized support bundles in `reports/support-bundles/`.
- Added UI `Exportar diagnóstico` from Settings Status for a JSON diagnostic download.

## Fixed in M17F

- Added `docs/live-manual-checklist.md` for guided, safe closure of Telegram command round-trip and optional Trello write dogfooding.
- Added `./scripts/dev/live-manual-checklist.sh`, an assistant that runs preflight, records Telegram/Trello live status, keeps Telegram commands manual, refuses Trello writes without explicit opt-in and writes `reports/live-manual-checklist-*.md`.
- Added guarded Trello write-smoke planning: it requires `ALPHAWAVE_TRELLO_WRITE_SMOKE=1`, an explicit board alias and a configured `pending.list_id`; cleanup is a separate opt-in because it is also a remote write.
- Added tests proving write-smoke refusal paths, list-id usage and report redaction without calling live services.

## Fixed in M18A

- Stabilized individual task detail suggestions: gaps endpoint, `missing_fields`, `llm_status`, local fallback and selected-field apply.
- Suggestions now use IA only when `safe_to_use=true`; disabled/not verified/error states fall back to local heuristics with user-visible warnings.
- Applying suggestions validates enum/range/datetime values, refuses to overwrite existing fields by default and never creates Trello cards.
- Trello card recommendation is informational only and excluded from apply/confirmation fields.
- Browser smoke covers opening the task detail suggestion modal and applying selected metadata locally.

## Fixed in M18B

- Added bulk task detail suggestion review: incomplete candidate detection, sync suggestion batch and selected-field bulk apply.
- Bulk suggestions reuse M18A per-task logic and keep IA gated by `safe_to_use=true` with heuristic fallback.
- Backend enforces max 25 task ids and returns per-task errors without rolling back successful applies.
- Frontend toolbar `Sugerir detalles` now opens a review queue with safe default selections, apply selected and discard all.
- Browser smoke covers bulk modal generation and applying one selected task while leaving another unchanged.

## Fixed in M18C

- Task detail panel now exposes primary actions for completing, suggesting details and explicitly creating a Trello card when the local task is linkable.
- Notes are editable from the detail panel and persist through `PATCH /api/tasks/{task_id}` into `metadata_json.notes`.
- Detail metadata editing covers priority, scores, effort, minutes, context, deadline and scope without implicit Trello writes.
- Local tasks in Trello-backed scopes still complete locally unless they are actually Trello-linked by `source_type=trello` and `source_id`.
- Browser smoke covers notes/metadata editing from detail and local completion from a Trello-backed scope without opening the Trello completion modal.

## Fixed in M18D

- Added `Procesar Inbox` triage workflow for active Inbox tasks, oldest first.
- Triage supports title, scope, priority, effort, minutes, context, date and notes with `Guardar y siguiente`.
- Manual suggestions reuse M18A review/apply flow; no LLM or Trello action runs automatically.
- Moving from Inbox records `inbox_processed`; omitting leaves the task unchanged.
- Trello-backed scopes show `Sin card Trello` and explicit create-card CTA without implicit Trello writes.
- Browser smoke covers moving one Inbox task to Personal and omitting the next.

## Fixed in M18E

- Today planning now returns deterministic groups for overdue, today, quick wins, deep work, blocking, review and Inbox when present.
- Now planning returns one next action plus alternatives for quick wins, deep work, low-focus/admin work and Inbox cleanup.
- Telegram `que hago hoy` and `que hago ahora` use concise grouped summaries without IA.
- Planning endpoints remain read-only: no Trello writes, no sort apply and no confirmations.
- Browser smoke covers Today groups and Now alternatives without nullish text.

## Fixed in M18F

- Added responsive hardening for desktop 1440, laptop 1366 and mobile 390 widths with global overflow prevention and local horizontal scroll only where intentional.
- Mobile task detail now behaves like a fixed sheet with sticky header, close button, primary actions, notes and metadata controls reachable above the dock.
- Settings, Trello workflow cards, backups, suggestion modals and Inbox triage now collapse dense rows into full-width stacked controls on small screens.
- Modals use viewport-bounded heights, internal scroll and sticky headers/actions so apply/close controls remain reachable.
- Browser smoke now checks app shell, Hoy/Ahora, mobile task detail, mobile Settings/Trello/Backups and mobile Inbox triage for horizontal overflow and browser errors.

## Fixed in M19A

- Documented the recommended web/VPS deployment architecture: nginx HTTPS public edge, FastAPI private on `127.0.0.1:8711`, static frontend from `frontend/dist`, SQLite only for private single-instance beta.
- Added a hosted roadmap covering private VPS beta, public web auth hardening, multi-user/Postgres foundation, Android client and monetization.
- Added Android strategy notes for API versioning, auth/session choices, push, offline cache, Play Store readiness and free/premium/ads constraints.
- Inventoried multi-user readiness risks for settings, tasks, reminders, confirmations, Trello/Telegram integrations, backups, runtime status, diagnostics and smoke data.
- Classified current API areas for mobile suitability, admin-only use, dangerous writes and web-only/dev helpers.

## Fixed in M19B

- Added non-live deploy scaffolding for a VPS private beta: nginx HTTPS template, systemd backend template and server `.env` template with empty secret placeholders.
- Added service scripts for health, status, logs and guarded restart; restart refuses by default without `ALPHAWAVE_CONFIRM_RESTART=1`.
- Added release scripts for frontend build, full release check against `ALPHAWAVE_BASE_URL` and local VPS artifact preparation.
- Added VPS private beta runbook, release process and production readiness docs.
- Updated operations/checklists to keep FastAPI private on `127.0.0.1:8711`, require HTTPS/private-beta access control and treat SQLite as private single-instance only.

## Fixed in M19C

- Added a multi-user readiness audit documenting P0/P1 blockers: no auth, global tasks/settings/reminders/confirmations, instance-level integrations and global workers.
- Added a data ownership matrix for current tables/resources including tasks, reminders, confirmations, app settings, Trello/Telegram state, backups, diagnostics, LLM and smoke reports.
- Added a phased migration plan from private VPS single-user through auth shell, ownership foundation, per-user integrations, mobile API versioning and monetization entitlements.
- Linked the audit from hosted roadmap, VPS architecture, production readiness and settings runtime contract.

## Fixed in M19D

- Added the auth/user-scope design recommending hybrid auth: HttpOnly cookie sessions with CSRF for web, bearer access plus rotating refresh token for future Android.
- Added `RequestContext`/`CurrentUser` service-scoping design and rules against global user-owned lookups.
- Added per-user settings design with separate `user_settings`, `instance_settings` and `integration_settings` as the recommended hosted model.
- Added user isolation test strategy covering API auth, query scoping, settings, integrations, workers, diagnostics/backups and IDOR regression.
- Added endpoint boundary classification across public, authenticated-user, admin-only, internal-dev-only, dangerous-write and integration-callback surfaces.

## Fixed in M19E

- Added single-owner auth shell with `ALPHAWAVE_AUTH_ENABLED`, HttpOnly cookie sessions and CSRF protection for unsafe methods.
- Added `users` and `auth_sessions` tables for the auth shell only; no user-owned data migration yet.
- Added `/api/auth/login`, `/api/auth/logout`, `/api/auth/session` and `/api/auth/csrf`.
- Added `CurrentUser` and `RequestContext` dependencies plus an auth gate that protects non-public `/api/*` endpoints when enabled.
- Added frontend login/logout flow and CSRF-aware API client while keeping auth disabled by default for local dogfooding.

## Fixed in M19F

- Added nullable `user_id` ownership columns for tasks, task events, reminders and pending confirmations.
- Added SQLite-safe bootstrap/backfill behavior that assigns legacy core rows to the initial/single owner.
- Scoped core task/reminder/confirmation/planning/suggestion/Inbox API flows by `RequestContext.user_id`.
- Added A/B isolation tests for tasks, reminders, confirmations, planning, suggestions, Inbox, legacy backfill and Trello sync owner assignment.
- Kept Settings, Trello credentials/mappings, Telegram account linking, backups and diagnostics documented as single-owner/global limitations.
- Added owner bootstrap script `scripts/admin/create-owner.sh`.

## Fixed in M19G

- Added `user_settings` with unique `(user_id, section, key)` storage for personal settings.
- Settings precedence is now code defaults -> instance `app_settings` -> current user's `user_settings`.
- Scoped personal sections `general`, `reminders`, `briefing`, `modes` and `priority` by authenticated user.
- Kept `trello`, `backups` and `advanced` as instance/global settings, with non-admin protection for instance writes.
- Threaded `user_id` through reminder, briefing, weekend and priority/planning runtime helpers.
- Added A/B tests for per-user Settings isolation, explicit `false`, full draft routing, auth-disabled fallback and owner-aware reminder/briefing/planning behavior.

## Fixed in M19H

- Added `user_integrations` and `telegram_chat_links` tables for integration ownership boundaries.
- Trello board mappings resolve by `user_id`; legacy `app_settings.trello` is still mirrored for the single owner/private beta.
- Trello sync and writes use the task/integration owner, so another user cannot inherit the owner board mapping.
- Telegram inbound commands resolve `chat_id` through hashed chat links, with legacy allowlist fallback for single-owner dogfooding.
- Reminder/briefing outbound Telegram sends require a destination for the reminder/briefing owner and do not mark unsent reminders as sent.
- Added A/B tests for Trello settings visibility, sync ownership, write isolation, Telegram chat links, unknown chat rejection and legacy fallback.

## Fixed in M19I

- Added encrypted `integration_secrets` backed by `cryptography.fernet.Fernet` and `ALPHAWAVE_SECRET_ENCRYPTION_KEY`.
- Added `telegram_link_codes` and `/link CODE` / `/vincular CODE` onboarding for Telegram chat linking.
- Outbound Telegram now prefers encrypted per-user chat destinations before legacy owner fallback.
- Added private-beta manual Trello credentials per user, with redacted status and revoke endpoints.
- Added regression coverage for missing encryption key, redaction, A/B credential isolation and no plaintext secret storage.

## Smoke Executed

- Backend suite with durations before changes: `256 passed` in `12.348s total`.
- Frontend production build before changes: OK.
- Restarted `alphawave-taskd` through `./scripts/restart.sh`.
- Verified `/api/health` after restart.
- Opened the app through Chrome headless against `http://127.0.0.1:8711`.
- Checked `/api/system/status` shape and redacted service status.
- Created `[M16 SMOKE]` local `Personal` task.
- Created `[M16 SMOKE]` task in Trello-backed scope `ALPHA` without a card.
- Completed both smoke tasks locally.
- Generated a priority proposal.
- Created and then cancelled a `[M16 SMOKE]` reminder.
- Created a manual backup and opened its restore plan.
- Ran AI validation; the unconfigured integration stayed unavailable and `safe_to_use=false`.
- Loaded Settings payload through `/api/settings`.

## Backlog P1/P2

- Expand browser smoke later with a dedicated stale/error System Status scenario if a lightweight mock route or test mode is added.

## Nice-to-have

- Add a UI affordance to filter or clean up smoke tasks by `[SMOKE]`/`[M16 SMOKE]` prefix during manual dogfooding.

## Not Verified

- Trello live writes, auto-confirm writes and destructive remote actions.
- Telegram live manual command round-trip remains unverified unless `./scripts/dev/live-manual-checklist.sh --interactive` is completed with commands sent from the real Telegram client.
- Full browser click-path automation for every Trello dropdown and backup modal branch. M17A covers non-destructive rendering and core Settings interactions.
- Mobile touch interactions and drag-and-drop with a real pointer device.
