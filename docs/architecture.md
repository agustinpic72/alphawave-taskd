# Architecture

AlphaWave TaskD is a local-first task app with optional external integrations. The core design principle is that local state remains usable when Telegram, Trello or LLM services are disabled or unavailable.

## Runtime Shape

- `backend/`: FastAPI application, SQLite persistence, background jobs, Telegram/Trello/LLM adapters, auth shell, settings contracts and tests.
- `frontend/`: React/Vite UI for task management, planning, confirmations, Settings and system status.
- `scripts/`: local development, diagnostics, smoke tests, release checks, onboarding and service helpers.
- `deploy/`: private-beta templates for systemd, nginx and environment files.
- `docs/`: operational contracts, dogfooding notes, deployment design and release readiness documentation.

## Docker Runtime

The default packaged runtime has two services:

```text
browser -> web (nginx, published host port)
             |-> React static files
             `-> /api/* -> api:8711 (internal network only)
                              |-> SQLite named volume
                              `-> backup named volume
```

The API port is not published on the host. nginx is the single gateway and preserves the `/api` prefix when proxying to FastAPI. The `web` service waits for API health, both services run as non-root users, and Compose applies restart, healthcheck, and bounded log rotation policies. No repository bind mounts, host networking, privileged mode, or Docker socket mounts are part of the default stack.

SQLite and backups are separate named volumes mounted at `/app/data` and `/app/data/backups`. Container replacement and `docker compose down` preserve them; the explicitly destructive `docker compose down -v` removes them. See [Docker](docker.md) for the operational contract.

## Data Model

SQLite is the default local database. Local data includes tasks, reminders, confirmations, planner state, Trello mirrors, briefing runs, user settings and encrypted per-user integration secrets when configured.

Generated runtime data lives under ignored paths such as `data/`, `reports/`, backups and diagnostic bundles. These artifacts are intentionally excluded from the public repository.

In Docker, equivalent runtime state lives in named volumes rather than the checkout. This keeps mutable state out of image layers and makes upgrades independent from container replacement.

## Settings And Environment

`.env` configures infrastructure and instance secrets: host/port, database URL, auth toggles, encryption keys and platform integration credentials.

The Settings UI configures runtime behavior: briefing, reminders, weekend mode, priority scoring, backups, Trello boards/lists and system status. Per-user settings are separated from instance-level infrastructure settings.

## External Integrations

Telegram is optional and disabled by default. Allowed-user checks and default smoke scripts avoid accidental sends.

Trello is optional and disabled by default. Read-only sync can mirror board/list/card metadata, while writes require explicit confirmation and are gated by `TRELLO_WRITE_ENABLED`.

OpenAI support is optional and configured per user. Task detail suggestions use strict Structured Outputs through the Responses API and fall back to deterministic heuristics when unavailable. The final priority score remains deterministic.

## Safety Boundaries

- No external write should happen without an explicit confirmation or opt-in smoke variable.
- Diagnostics and status endpoints must avoid leaking secrets.
- Publication checks should block tracked local databases, backups, reports, dependency folders and build output.
- Private-beta auth exists, but public multi-tenant hosting is still future work.
- The local Compose defaults disable auth and external integrations. Non-local deployment modes retain the fail-closed authentication and secure-cookie checks.
