# Public Release Audit

This repository is a sanitized public candidate for AlphaWave TaskD. Private development history, real runtime data, credentials, private taxonomy, and local execution reports are outside the public release boundary.

## Reviewed Surfaces

- Tracked source and configuration.
- Dependency manifests and lockfiles.
- Local-first deployment defaults.
- Authentication and external-write boundaries.
- Generated screenshots and visual-asset provenance.
- Community, support, security, and release documentation.
- Publication, test, browser, and container validation workflows.

## Release Boundary

The application uses SQLite and in-process workers and targets local or deliberately managed single-instance operation. Telegram, Trello, and OpenAI integrations are optional. AlphaWave TaskD is not presented as a hosted multi-tenant SaaS.

## Verification

The authoritative release evidence is the output produced for the exact candidate commit by the steps in [release-process.md](release-process.md). This document intentionally contains no private terms, machine-specific paths, internal milestone notes, or claims about checks that were not run on that commit.

Publication remains blocked until every item in [github-publication-checklist.md](github-publication-checklist.md) is green locally and remotely.
