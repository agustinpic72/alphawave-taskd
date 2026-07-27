# Changelog

All notable changes to AlphaWave TaskD are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Prepared the repository, documentation, security gates, and automation for public collaboration.

## [0.1.0] - 2026-07-27

### Added

- Local-first task capture, planning, reminders, confirmation queues, backup controls, and diagnostics.
- FastAPI and SQLAlchemy backend with SQLite persistence.
- React, TypeScript, and Vite interface.
- Optional OpenAI-assisted suggestions with deterministic fallback.
- Optional Telegram commands and Trello synchronization with guarded writes.
- Docker Compose packaging, native development workflows, browser smokes, and runtime persistence checks.
- Synthetic demo data and reviewed public screenshots.
- Apache-2.0 license, contribution guidance, security policy, support policy, and asset-provenance record.

### Security

- External integrations are disabled by default.
- Non-local deployment modes fail closed unless authentication requirements are met.
- Sensitive runtime artifacts are ignored and checked by the publication audit.

[Unreleased]: https://github.com/agustinpic72/alphawave-taskd/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/agustinpic72/alphawave-taskd/releases/tag/v0.1.0
