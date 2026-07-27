# Security

AlphaWave TaskD is local-first by default and can optionally connect to Telegram, Trello and OpenAI.

## Defaults

- Backend binds to `127.0.0.1:8711`.
- SQLite lives under `data/`.
- Telegram, Trello and LLM integrations are disabled by default.
- Trello writes are disabled by default.
- Trello writes require explicit confirmation when enabled.
- Default live smoke scripts avoid Telegram sends and Trello writes.
- `private` and `public` modes require authentication and disable dev endpoints and API docs.
- `public` mode requires secure auth cookies.

## Do Not Commit

These artifacts are private or generated and must stay out of git:

- `.env` and `.env.*`, except `.env.example`.
- SQLite databases and SQLite sidecar files.
- Backups.
- Reports, diagnostic bundles and support bundles.
- Logs that may contain private runtime details.
- Virtualenvs, dependency folders and build output.

## Secrets

Instance-level secrets live in `.env`. Per-user Telegram, Trello and OpenAI secrets use the encrypted integration secret store when `ALPHAWAVE_SECRET_ENCRYPTION_KEY` is configured.

Never share:

- Telegram bot tokens.
- Telegram chat IDs or private user IDs.
- Trello API keys or tokens.
- Private board IDs, card IDs or card content.
- OpenAI API keys.
- Diagnostic bundles that have not been reviewed.

If a secret appears in logs, screenshots, issues or commits, rotate it immediately and remove the affected artifact before sharing anything publicly.

## Network

Do not expose `APP_HOST=0.0.0.0` unless the deployment has a deliberate reverse proxy, TLS, firewall and auth configuration. The expected local default is:

```env
APP_HOST=127.0.0.1
APP_PORT=8711
ALPHAWAVE_DEPLOYMENT_MODE=local
```

Use `ALPHAWAVE_DEPLOYMENT_MODE=private` or `public` for any non-local deployment. Those modes fail closed when auth is disabled; `/docs`, `/redoc`, and `/openapi.json` are not registered. Public mode additionally rejects insecure cookies.

## Telegram

Use `TELEGRAM_ALLOWED_USER_ID` for local bot usage. Messages from other users should be ignored by runtime checks.

## Trello

The safety rules for Trello are:

- No destructive card operations by default.
- No unconfirmed writes.
- A pending confirmation is user-owned and its write opt-in, mapping, target and task state are revalidated at execution time.
- No live-write smoke unless explicitly enabled.
- Mappings should use Trello list IDs, not only display names.

## Diagnostics

`/api/system/status`, diagnostic export and support bundles must sanitize secrets. Generated bundles are still private local artifacts and are ignored by git.

## Briefing And Backups

Briefing queries, run history, delivery state and Telegram destinations are user-scoped. A missing destination is a failed delivery and is never recorded as sent; legacy destination fallback is limited to a single-owner instance.

Backups are online SQLite snapshots written atomically with metadata and checksums. Restore accepts only known backup IDs, requires an authenticated admin plus CSRF and the exact confirmation phrase, validates gzip/SQLite integrity and minimum app schema, creates a pre-restore backup, and rolls back a failed replacement. Restart the service after a successful operator-initiated restore.

## Settings

Settings stores runtime behavior such as briefing, reminders, weekend mode, priority scoring, backup preferences and Trello mappings. It must not expose raw tokens or API keys.
