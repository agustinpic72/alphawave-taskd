# Security Policy

## Supported Versions

Security fixes are provided for the latest release on the `main` branch. Older releases may not receive backports.

## Reporting a Vulnerability

Please use GitHub's private vulnerability reporting feature for this repository. If that feature is unavailable, contact the maintainer through the email address listed on the GitHub profile and use the subject `AlphaWave TaskD security report`.

Do not open a public issue for a suspected vulnerability. Do not include live credentials, personal task data, database files, private integration IDs, or unsanitized diagnostics in a report. A minimal synthetic reproduction is preferred.

You should receive an acknowledgement within seven days. Validation and remediation timing depend on severity and reproducibility. No bug-bounty program or payment is offered.

## Security Boundaries

AlphaWave TaskD is local-first:

- SQLite data is stored locally.
- Telegram, Trello, and OpenAI integrations are optional and disabled by default.
- The default backend binds to loopback.
- Non-local deployment modes require application authentication; public mode also requires secure cookies.
- External write actions remain guarded by explicit confirmation.

This project is not a hosted multi-tenant SaaS and does not claim tenant isolation, managed monitoring, uptime, or incident response.

## Operator Responsibilities

- Keep `.env`, database files, backups, logs, and diagnostic bundles private.
- Use a stable encryption key before storing per-user integration credentials.
- Put any non-local deployment behind TLS, a reverse proxy, and appropriate network controls.
- Rotate a credential immediately if it appears in logs, screenshots, issues, or commits.
- Review release notes and back up SQLite before upgrading.

See [docs/security.md](docs/security.md) for implementation-focused security guidance.
