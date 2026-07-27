# VPS Private Beta Runbook

M19B prepares safe scaffolding for a future private VPS deploy. It does not deploy to a server, connect over SSH, edit host nginx/systemd, run certbot or restart any production service.

## Target Shape

Phase 1 private beta:

```text
browser -> nginx HTTPS -> frontend/dist
browser -> nginx HTTPS /api -> FastAPI on 127.0.0.1:8711
FastAPI -> SQLite on persistent VPS disk
```

SQLite is acceptable only for a single-instance private beta. It is not the hosted multi-user architecture.

## Repo Artifacts

- `deploy/nginx/alphawave-taskd.nginx.example`: nginx HTTPS static/proxy template.
- `deploy/systemd/alphawave-taskd.service.example`: systemd service template for the backend.
- `deploy/env/alphawave-taskd.env.example`: server `.env` template with placeholders only.
- `scripts/service/health.sh`: read-only health/status check.
- `scripts/service/status.sh`: read-only systemd status.
- `scripts/service/logs.sh`: read-only journal logs.
- `scripts/service/restart.sh`: guarded restart, refuses by default.
- `scripts/release/check-release.sh`: local/public URL release gate.
- `scripts/release/prepare-vps-artifacts.sh`: copies templates/docs/build output into a local artifact folder.

## Manual Server Setup Outline

These are human instructions for a VPS operator, not commands an automation tool should run against a real host.

1. Create a non-root `alphawave` user.
2. Place the repo or release bundle under `/opt/alphawave-taskd`.
3. Create a Python virtualenv under `backend/.venv` and install backend dependencies.
4. Build the frontend so `frontend/dist` exists.
5. Copy `deploy/env/alphawave-taskd.env.example` to `/opt/alphawave-taskd/.env` and fill secrets on the server only.
6. Keep `APP_HOST=127.0.0.1` and `APP_PORT=8711`.
7. Keep `ALPHAWAVE_DEPLOYMENT_MODE=private`, `ALPHAWAVE_AUTH_ENABLED=true`, `ALPHAWAVE_AUTH_COOKIE_SECURE=true`, and `APP_DEV_ENDPOINTS_ENABLED=false`. The service refuses to start with private mode and auth disabled.
8. Create the single owner on the server:

```bash
./scripts/admin/create-owner.sh
```

9. Copy `deploy/systemd/alphawave-taskd.service.example` to a real systemd unit and review paths/user.
10. Copy `deploy/nginx/alphawave-taskd.nginx.example` to a real nginx site and replace the domain/cert paths.
11. Add a private-beta defense-in-depth guard before exposing the hostname: VPN, IP allowlist or equivalent.
12. Enable HTTPS and redirect HTTP to HTTPS.

API docs and OpenAPI are not registered in private mode. Keep the deployment single-instance and treat it as private beta despite the user-ownership foundations.

## Release Gate

Before treating a VPS deploy as valid:

```bash
ALPHAWAVE_BASE_URL=https://taskd.example.com ./scripts/dev/browser-smoke.sh
ALPHAWAVE_BASE_URL=https://taskd.example.com ./scripts/dev/rc-check.sh
ALPHAWAVE_BASE_URL=https://taskd.example.com ./scripts/release/check-release.sh
```

Default RC behavior is safe: no Telegram send, no Trello write and no restore.

## Service Commands

Local user-service example:

```bash
ALPHAWAVE_SYSTEMD_USER=1 ./scripts/service/status.sh
ALPHAWAVE_SYSTEMD_USER=1 ./scripts/service/logs.sh
```

System service example for VPS:

```bash
./scripts/service/status.sh
./scripts/service/logs.sh --lines 300
```

Restart is intentionally guarded:

```bash
./scripts/service/restart.sh
```

The command above refuses and prints the explicit opt-in command. Use the opt-in only when you intend to restart the service:

```bash
ALPHAWAVE_CONFIRM_RESTART=1 ./scripts/service/restart.sh
```

## Update Procedure

1. Run local release validation from the candidate commit.
2. Build frontend.
3. Create a manual backup and validate it.
4. Prepare local scaffolding artifacts:

```bash
./scripts/release/prepare-vps-artifacts.sh
```

5. Copy artifacts manually to the VPS using the operator's chosen channel.
6. Install code/build output.
7. Restart the service manually.
8. Run `scripts/service/health.sh` against the public HTTPS URL.
9. Run RC check with `ALPHAWAVE_BASE_URL=https://...`.

## Rollback Outline

1. Stop the service manually.
2. Restore the previous release directory or previous git commit.
3. Restore SQLite only if the release changed data in a bad way. Use the guarded restore command with the exact confirmation phrase; it validates checksum, integrity and schema and creates a pre-restore backup. Restart afterward.
4. Start the service manually.
5. Run health and RC checks.

## Security Notes

- Do not commit real `.env` files.
- Do not serve `.env`, SQLite, backups, logs, reports or diagnostics bundles.
- Do not bind FastAPI to `0.0.0.0` for Phase 1.
- Keep VPN, IP allowlist, or an equivalent external guard as defense in depth for private beta.
- Do not expose `/docs`, `/redoc`, `/openapi.json`, or development endpoints; private mode disables them by construction.
- Keep Telegram send and Trello write smoke opt-ins disabled during normal release checks.
- Diagnostics bundles are support artifacts, not backups.
