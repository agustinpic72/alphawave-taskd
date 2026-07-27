# Deploy Templates

These files are scaffolding for a future VPS private beta. They are examples, not live host configuration.

Files:

- `nginx/alphawave-taskd.nginx.example`: nginx HTTPS reverse proxy and static frontend template.
- `systemd/alphawave-taskd.service.example`: VPS system service template for the FastAPI backend.
- `env/alphawave-taskd.env.example`: server `.env` template with placeholders only.

Rules:

- Do not copy secrets into the repo.
- Do not expose FastAPI directly on the public internet.
- Keep `APP_HOST=127.0.0.1` for Phase 1.
- Protect the private beta with VPN, Basic Auth, IP allowlist, or equivalent unless you explicitly accept single-owner app-auth risk.
- Use `ALPHAWAVE_BASE_URL=https://<domain> ./scripts/dev/rc-check.sh` as the final gate after manual VPS setup.

See:

- [VPS private beta runbook](../docs/vps-private-beta-runbook.md)
- [Release process](../docs/release-process.md)
- [Production readiness](../docs/production-readiness.md)
