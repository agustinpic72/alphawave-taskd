# Docker

Docker Compose is the recommended way to run AlphaWave TaskD. It builds the React application, serves it through nginx, and keeps FastAPI on an internal Docker network.

```text
browser -> web:8080 -> React
                  -> /api/* -> api:8711 -> SQLite
```

Only the `web` service is published on the host. SQLite data and backups live in separate named volumes.

## Requirements

- Linux: Docker Engine and the Docker Compose plugin.
- macOS: Docker Desktop.
- Windows: Docker Desktop with the WSL2 backend recommended. Run the commands from a WSL2 shell for the most consistent file and permission behavior.

Platform truth labels used by this project:

- **CI-tested:** Linux (`ubuntu-latest`) validates Compose build plus isolated runtime and named-volume persistence.
- **Supported, manual:** macOS Docker Desktop and Windows Docker Desktop with WSL2 follow this guide but are not exercised by hosted CI.
- **Not supported:** Windows containers and direct internet exposure of the default local stack.

Verify the installation:

```bash
docker version
docker compose version
```

## Start

Clone the repository URL shown by GitHub, enter the checkout, and start the stack:

```bash
git clone https://github.com/agustinpic72/alphawave-taskd.git
cd alphawave-taskd
docker compose up --build
```

Open <http://localhost:8711>. The default configuration is local-only and starts without Telegram, Trello, LLM credentials, or application authentication.

For background operation:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f
```

Press `Ctrl+C` to stop attached containers. Use `docker compose down` to stop and remove the containers and network while retaining application data.

## Configuration

Compose has safe defaults, so no environment file is required. To customize the port or runtime settings:

```bash
cp .env.docker.example .env.docker
docker compose --env-file .env.docker up -d --build
```

`.env.docker` is ignored by Git. Do not commit it. The most common override is:

```text
ALPHAWAVE_WEB_PORT=8711
```

Choose another host port when `8080` is occupied:

```bash
ALPHAWAVE_WEB_PORT=8090 docker compose up -d --build
```

Then open `http://localhost:8090`. The API is intentionally not published separately; use `/api` through the web gateway.

## Authentication

Authentication remains disabled in the local defaults. To enable it, set these values in `.env.docker`:

```text
ALPHAWAVE_AUTH_ENABLED=true
```

After starting the stack, create the initial owner inside the API container:

```bash
docker compose exec api python /app/scripts/admin/create_owner.py
```

The command prompts for the owner email and password. For non-interactive automation, pass `ALPHAWAVE_OWNER_EMAIL` and `ALPHAWAVE_OWNER_PASSWORD` with `docker compose exec -e`; avoid placing passwords in shell history.

`private` and `public` deployment modes fail closed unless authentication is enabled. `public` mode also requires secure cookies and an HTTPS reverse proxy. The default Compose stack is intended for local use; review the private-beta deployment runbook before exposing it beyond the host.

## Data And Backups

Compose uses two named volumes:

- `alphawave_data`: the SQLite database at `/app/data/alphawave-taskd.sqlite`.
- `alphawave_backups`: backup archives under `/app/data/backups`.

The portable default is `local-driver-managed`: Docker creates and manages both
volumes in its configured data root. This mode works across the supported Docker
Desktop and Linux environments and does not accept host device paths.

Operators who need the named volumes to live on a specific mounted filesystem can
opt in to `external-bind-backed`. Keep these machine-specific values in an ignored
`.env.docker` or another private operator environment file:

```text
ALPHAWAVE_VOLUME_MODE=external-bind-backed
ALPHAWAVE_DATA_VOLUME=alphawave_data_external
ALPHAWAVE_BACKUPS_VOLUME=alphawave_backups_external
ALPHAWAVE_DATA_VOLUME_DEVICE=/mnt/your-data/alphawave/data
ALPHAWAVE_BACKUPS_VOLUME_DEVICE=/mnt/your-data/alphawave/backups
```

`ALPHAWAVE_VOLUME_MODE`, `ALPHAWAVE_DATA_VOLUME_DEVICE`, and
`ALPHAWAVE_BACKUPS_VOLUME_DEVICE` are migration-tool settings; Compose itself uses
the resolved volume names. Do not add host-specific paths to tracked environment
examples. The tooling requires absolute device paths, rejects symlink traversal,
probes write, lock, and atomic-rename behavior, and requires empty targets for an
initial migration. After creating a bind-backed volume, it verifies that Docker
reports the `local` driver with `type=none`, `o=bind`, and the exact expected
device. An existing volume with a different driver, option, or device fails closed;
the tooling does not silently reuse or replace it.

Inspect the resolved names with:

```bash
docker compose config --volumes
docker volume ls
```

Container recreation, `docker compose restart`, and ordinary `docker compose down` preserve both volumes. To request a backup from the CLI and inspect the resulting files:

```bash
docker compose exec api python -m app.cli backup
docker compose exec api find /app/data/backups -maxdepth 1 -type f -print
```

The application Settings and System Status views expose the same backup controls. To keep an independent copy outside Docker, note the generated filename and copy it to a private host directory:

```bash
docker compose cp api:/app/data/backups/<backup-file> ./local-backups/<backup-file>
```

`local-backups/` is only an example destination and must remain untracked. Store exported backups securely because they contain application data.

The following command is destructive:

```bash
docker compose down -v
```

It permanently deletes the stack's SQLite database and backup volumes. Use it only when intentionally resetting the installation.

This warning also applies to externally bind-backed named volumes. Never use
`docker compose down -v` for migration, upgrade, rollback, troubleshooting, or
soak operations. Use `docker compose stop` or `docker compose down` without `-v`;
retain the named volumes until the data and rollback evidence have been verified.

## Systemd-To-Docker Migration

The administrative migration tooling supports both volume modes. Start with a
read-only plan and dry run, using explicit volume names so the candidate does not
collide with an existing stack:

```bash
scripts/admin/migrate-systemd-runtime-to-docker.sh --plan \
  --data-volume alphawave_data_candidate \
  --backup-volume alphawave_backups_candidate

scripts/admin/migrate-systemd-runtime-to-docker.sh --dry-run \
  --data-volume alphawave_data_candidate \
  --backup-volume alphawave_backups_candidate
```

For an external filesystem, add the opt-in mode and both private device paths:

```bash
scripts/admin/migrate-systemd-runtime-to-docker.sh --dry-run \
  --volume-mode external-bind-backed \
  --data-volume alphawave_data_candidate \
  --backup-volume alphawave_backups_candidate \
  --data-volume-device /mnt/your-data/alphawave/data \
  --backup-volume-device /mnt/your-data/alphawave/backups
```

Execution requires the script's explicit confirmation gate in addition to
`--execute`. Review the plan, generated baseline location, storage gate, resolved
Compose configuration, target mount, volume inspection, and integration gates
before authorizing it. Do not execute migration while another process can write to
the source database.

After quiescing systemd, migration captures the source database and its systemd
ownership/mode metadata, seeds the selected volumes, and verifies the destination
before starting the API. Verification includes exact SHA-256 comparison, SQLite
integrity and foreign-key checks, every user table and row count, schema/version
metadata, indexes, triggers, and a normalized schema digest. A mismatch aborts the
cutover and leaves evidence for investigation. The manifest records metadata and
hashes, not row content or host database paths.

## Transactional Rollback

Inspect rollback before execution:

```bash
scripts/admin/rollback-docker-to-systemd.sh --plan \
  --data-volume alphawave_data_candidate \
  --backup-volume alphawave_backups_candidate

scripts/admin/rollback-docker-to-systemd.sh --dry-run \
  --data-volume alphawave_data_candidate \
  --backup-volume alphawave_backups_candidate
```

The rollback takes an exclusive lock, stops Compose without deleting volumes,
exports a consistent Docker SQLite candidate, validates it, and preserves the
previous systemd database in a private transaction directory. It reapplies the
captured systemd UID, GID, and modes and installs the candidate through a durable
same-directory atomic replacement. Success requires systemd health plus matching
database hash and counts.

For `external-bind-backed`, pass the same mode, volume names, and device paths
used by migration, preferably through the same ignored operator environment.
Rollback rejects a volume whose inspected device or options no longer match.

If installation, startup, health, or verification fails after replacement begins,
the script makes one automatic recovery attempt: it restores the preserved
systemd database with its original ownership/mode and verifies systemd health.
Docker remains stopped and all transaction copies remain available for operator
inspection. Do not remove the Docker volumes or rollback transaction directory
until recovery has been independently verified.

## Operations

Status and health:

```bash
docker compose ps
curl --fail http://localhost:${ALPHAWAVE_WEB_PORT:-8711}/api/health
```

Logs:

```bash
docker compose logs -f
docker compose logs --tail=200 api
docker compose logs --tail=200 web
```

Restart containers without deleting data:

```bash
docker compose restart
docker compose restart api
```

Stop while preserving data:

```bash
docker compose down
```

Upgrade a checkout:

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
docker compose ps
```

Review release notes and take a backup before upgrading. Do not use `down -v` during an upgrade.

## Development And Smoke Tests

Validate the Compose definition and build both images:

```bash
docker compose config
docker compose build
```

The isolated Docker smoke uses a unique project name, explicit image tags, an alternate host port, and explicit temporary volume names. It does not use the normal stack images or database:

```bash
./scripts/dev/docker-smoke.sh
```

Override its port when needed:

```bash
ALPHAWAVE_DOCKER_SMOKE_PORT=18081 ./scripts/dev/docker-smoke.sh
```

For parallel or reproducible runs, provide a unique identifier. Volume names and image tags may also be set explicitly:

```bash
ALPHAWAVE_DOCKER_SMOKE_ID=worktree-a \
ALPHAWAVE_DOCKER_SMOKE_DATA_VOLUME=alphawave_smoke_a_data \
ALPHAWAVE_DOCKER_SMOKE_BACKUPS_VOLUME=alphawave_smoke_a_backups \
ALPHAWAVE_DOCKER_SMOKE_API_IMAGE=alphawave-taskd-api:smoke-a \
ALPHAWAVE_DOCKER_SMOKE_WEB_IMAGE=alphawave-taskd-web:smoke-a \
./scripts/dev/docker-smoke.sh
```

The smoke builds and starts the stack, verifies the frontend and API over loopback, creates a `[DOCKER SMOKE]` task, tests persistence across API restart and Compose down/up, and removes its containers, volumes, and tagged images afterward. Telegram, Trello, and LLM integrations remain disabled.

## Promotion Path

1. **Loopback:** run `docker-smoke.sh` with integrations disabled and confirm gateway, API, and persistence checks pass on `127.0.0.1`.
2. **Integration:** in a disposable private environment, enable one external integration at a time with test credentials. Keep write/send gates disabled until read-only status and redaction checks pass.
3. **Cutover:** take and export a verified backup, record the deployed revision and resolved Compose configuration, use the migration plan and dry run, stop the old stack without `-v`, deploy, then verify health, login, task reads/writes, and backup creation.
4. **Soak:** follow the [Docker soak checklist](docker-soak-checklist.md) before broadening access. Roll back on data loss, repeated restarts, unhealthy services, auth bypass, secret leakage, or unexpected external writes.

## Troubleshooting

Render the final Compose model before debugging environment overrides:

```bash
docker compose --env-file .env.docker config
```

Check health and recent logs:

```bash
docker compose ps
docker compose logs --tail=200 api web
```

Rebuild after dependency or Dockerfile changes:

```bash
docker compose build --no-cache
docker compose up -d
```

On Linux, do not run the application containers as root to work around host permission issues. Runtime state belongs in the named volumes, not bind-mounted repository directories.
