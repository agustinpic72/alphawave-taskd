# Open-Source Readiness

AlphaWave TaskD is prepared as an experimental local-first application. This document describes the public release boundary; it is not a claim that the project is a hosted service.

## Public Scope

- FastAPI and SQLAlchemy backend.
- React, TypeScript, and Vite frontend.
- Local SQLite persistence and in-process workers.
- Optional Telegram, Trello, and OpenAI integrations, disabled by default.
- Docker Compose and native development paths.
- Synthetic demo screenshots.

## Required Release Evidence

- Publication audit and synthetic regression pass.
- Gitleaks passes on the candidate tree and reachable history.
- Backend clean install, dependency check, and full tests pass.
- Frontend clean install, dependency audits, lint, build, and browser smoke pass.
- Compose validation/build and isolated runtime/persistence smoke pass.
- Public documentation, license, support/security policies, and asset provenance are reviewed.
- Remote CI is green on the exact candidate commit while the repository is private.
- The signed-out review checklist passes after publication.

## Explicit Non-Goals for v0.1.0

- Hosted multi-tenant SaaS.
- Tenant isolation guarantees.
- Managed infrastructure, monitoring, backups, uptime, or support.
- Live external integrations in CI.
- Bundled production credentials or real user data.

See [release-process.md](release-process.md) for the executable gate and [github-publication-checklist.md](github-publication-checklist.md) for the final repository review.
