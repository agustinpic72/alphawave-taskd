# Release Process

This process prepares a reproducible AlphaWave TaskD release without calling live integrations. A release is created only from an exact commit that passes the complete local and remote gates.

## 1. Clean Installation

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e "backend[dev]"
backend/.venv/bin/pip check

cd frontend
npm ci
cd ..
```

Do not add Telegram, Trello, or OpenAI credentials for release validation.

## 2. Quality and Security

```bash
./scripts/dev/test-publication-audit.sh
./scripts/dev/publication-audit.sh
backend/.venv/bin/pytest -q

cd frontend
npm audit
npm audit --omit=dev
npm run lint
npm run build
cd ..

git diff --check
```

Run Gitleaks against both the candidate tree and its reachable history. Configure any real private-taxonomy scan through the ignored local terms file; values must never enter Git, CI, logs, or release reports.

## 3. Browser and Container Gates

```bash
./scripts/dev/browser-smoke.sh
docker compose config
docker compose build
./scripts/dev/docker-smoke.sh
```

The Docker smoke must verify health, restart behavior, ordinary down/up behavior, and named-volume persistence without enabling external integrations.

## 4. Candidate Review

- Confirm `git status` contains only intended source changes.
- Confirm the author and committer use the GitHub noreply address.
- Review the README, license, screenshots, release notes, changelog, security/support policies, and asset provenance.
- Confirm screenshots contain synthetic `[DEMO]` data only.
- Record the candidate commit and tree.

## 5. Private Remote Gate

Push normally to the private canonical repository. Do not force-push or combine unrelated histories. Wait for every required check on the exact candidate commit, then inspect repository rendering and security configuration.

If a required check fails, fix it in a normal follow-up commit and repeat. Do not publish from a different or partially tested commit.

## 6. Publish

After the exact private commit is green:

1. Make the canonical repository public.
2. Verify it in a signed-out browser with [anonymous-review-checklist.md](anonymous-review-checklist.md).
3. Create signed/annotated tag `v0.1.0` as appropriate for the repository policy.
4. Publish `AlphaWave TaskD v0.1.0 — initial open-source release` using [releases/v0.1.0.md](releases/v0.1.0.md).
5. Do not attach unreproducible binaries.

## Stop Conditions

Do not publish if tests, dependency audits, lint, build, browser smoke, container smoke, publication audit, Gitleaks, or remote checks fail; if a public link is broken; or if any secret, private taxonomy, personal data, local path, internal hostname, or private history is present.
