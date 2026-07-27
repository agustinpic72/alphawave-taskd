# Production Readiness

AlphaWave TaskD is ready for local dogfooding and can be prepared for a private single-owner VPS beta. It is not ready for public hosted multi-user usage.

Detailed M19C audit:

- [Multi-user readiness audit](multi-user-readiness-audit.md)
- [Multi-user data ownership matrix](multi-user-data-ownership-matrix.md)
- [Multi-user migration plan](multi-user-migration-plan.md)

M19D auth/user scope design:

- [Auth, user scope and request context design](auth-user-scope-design.md)
- [Auth API boundary](auth-api-boundary.md)
- [Per-user settings design](per-user-settings-design.md)
- [User isolation test strategy](user-isolation-test-strategy.md)

## Ready Now

- Local-first single-user operation.
- FastAPI on `127.0.0.1:8711`.
- React/Vite production build.
- SQLite data with consistent online backups and guarded, validated restore tooling.
- System Status and diagnostics with secret redaction.
- Browser smoke, RC check, Trello read-only smoke and Telegram safety smoke.
- Settings runtime contract for non-secret behavior.

## Ready For VPS Private Beta After Manual Setup

- nginx HTTPS static/proxy template exists.
- systemd backend service template exists.
- server `.env` template exists without secrets.
- `private` and `public` modes fail startup unless app auth is enabled; public mode also requires secure cookies.
- API docs/OpenAPI and development endpoints are disabled outside local mode.
- Core task/reminder/confirmation ownership exists for A/B isolation foundations.
- Owner bootstrap script exists: `scripts/admin/create-owner.sh`.
- service health/status/logs scripts exist.
- guarded restart script refuses by default.
- release check can target `ALPHAWAVE_BASE_URL=https://...`.
- docs retain VPN/IP allowlist or equivalent defense in depth for private beta.

## Not Ready For Public Hosted Product

- Trello/Telegram integration boundaries are user-scoped as of M19I, with encrypted per-user secret storage available when `ALPHAWAVE_SECRET_ENCRYPTION_KEY` is configured.
- Trello OAuth is not implemented; manual per-user credentials are private-beta only and the instance `.env` fallback remains for the local owner.
- SQLite is not enough for multi-user concurrency or app workers.
- Background jobs run in-process; briefing scheduling now iterates owners explicitly, while broader worker architecture remains unsuitable for SaaS scale.
- Backups and diagnostics remain instance-level admin operations even though restore is now validated and guarded.
- No hosted billing, quotas, deletion/export policy or support workflow.
- No Android/mobile auth/session model yet.

## Required Before Public Multi-User

- Add users/accounts and ownership boundaries.
- Add real per-user OAuth/token onboarding. Tasks, reminders, confirmations, personal Settings, integration boundaries and encrypted secret storage already have ownership foundations.
- Migrate to Postgres with explicit migrations.
- Store per-user secrets encrypted at rest.
- Add auth/session strategy for web and mobile.
- Thread `RequestContext`/`user_id` through user-owned services and queries.
- Split background workers from request serving or make workers user-aware.
- Add admin/support tooling that cannot leak user data.
- Add rate limits and abuse controls.
- Add public privacy/security docs and backup retention policy.

## Absolute Public Exposure Warning

Non-local deployment now fails closed without app auth, and API docs/dev endpoints are removed. A reverse proxy, TLS, firewall and preferably VPN/IP allowlisting remain required defense in depth; this is not a claim of public multi-tenant readiness.

App auth plus ownership isolates tasks, reminders, confirmations, briefing delivery, planning, suggestions, Inbox flows, personal Settings and Trello/Telegram integration boundaries/secrets between authenticated users. It still must not be treated as a multi-user SaaS because Trello OAuth, hosted onboarding, backups/diagnostics split and broader worker orchestration remain instance-oriented.
