# Contributing

Thank you for considering a contribution to AlphaWave TaskD. The project is an experimental, local-first application, so changes should stay focused, safe, and easy to review.

## Before You Start

- Search existing issues before opening a new one.
- Use a GitHub issue for a significant feature proposal before investing in an implementation.
- Report vulnerabilities privately according to [SECURITY.md](SECURITY.md), not in a public issue.
- Do not submit real task data, credentials, integration identifiers, private taxonomy, or diagnostic bundles.

## Development Setup

The quickest full-stack setup is:

```bash
git clone https://github.com/agustinpic72/alphawave-taskd.git
cd alphawave-taskd
docker compose up --build
```

For native development:

```bash
cp .env.example .env

python3 -m venv backend/.venv
backend/.venv/bin/pip install -e "backend[dev]"

cd frontend
npm ci
cd ..
```

Keep `.env` local. The application and its tests must work without live Telegram, Trello, or OpenAI credentials.

## Validation

Run the checks relevant to your change, and run the complete local gate before requesting a release:

```bash
backend/.venv/bin/pytest -q
cd frontend && npm run lint && npm run build && cd ..
./scripts/dev/test-publication-audit.sh
./scripts/dev/publication-audit.sh
docker compose config
docker compose build
```

For UI or runtime changes, also run the browser and Docker smoke suites documented in [docs/release-process.md](docs/release-process.md). Routine pull requests must not send Telegram messages, write to Trello, or call paid external APIs.

## Pull Requests

- Keep each pull request focused and explain the user-visible effect.
- Add tests for changed behavior.
- Use synthetic test/demo data.
- Update documentation when behavior, configuration, or public claims change.
- Do not commit `.env` files, databases, backups, logs, reports, diagnostic bundles, build output, or dependency folders.
- Preserve confirmation gates for external side effects.
- Confirm `git diff --check` passes.

By submitting a contribution, you agree that it is licensed under the repository's Apache-2.0 license.
