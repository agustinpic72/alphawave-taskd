# Screenshots Plan

Public screenshots must use demo data only. Do not capture screenshots from a real local dogfooding database.

## Generate Demo-Safe Assets

```bash
./scripts/dev/generate-demo-screenshots.sh
```

The script:

- Starts AlphaWave TaskD against a temporary SQLite database.
- Disables auth, Telegram, Trello, LLM, reminders, briefing, check-ins and backups.
- Seeds `[DEMO]` tasks using only `Project Alpha` through `Project Delta` and aliases `ALPHA` through `DELTA`.
- Writes screenshots to `docs/assets/screenshots/`.

Expected outputs:

- `todo.png`
- `today.png`
- `now.png`
- `task-detail.png`

## Manual Review Before Commit

Before committing generated images, visually confirm that they do not show:

- Real task titles.
- Telegram tokens, chat IDs or private user IDs.
- Trello tokens, board IDs, card IDs or private card content.
- Private URLs, hostnames, local paths or support bundle names.
- Real diagnostics or backup identifiers.

Settings and diagnostics screenshots are intentionally excluded for now because they can display local backup identifiers or instance metadata even with a temporary demo database.
