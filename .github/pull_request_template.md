## Summary

## Validation

- [ ] `backend/.venv/bin/pytest -q`
- [ ] `backend/.venv/bin/pip check`
- [ ] `cd frontend && npm audit --audit-level=high`
- [ ] `cd frontend && npm run lint && npm run build`
- [ ] `./scripts/dev/publication-audit.sh`
- [ ] Browser or Docker smoke when runtime/UI behavior changes

## Safety Checklist

- [ ] No `.env`, SQLite databases, backups, reports, support bundles, or generated build/dependency folders were added.
- [ ] No tokens, API keys, chat IDs, private board IDs, or private card content were added.
- [ ] Test data, screenshots, and examples are synthetic.
- [ ] Trello writes and Telegram sends remain opt-in or explicitly confirmed.
- [ ] New settings or runtime behavior are documented.
