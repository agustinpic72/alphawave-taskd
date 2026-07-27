# Deployment Ubuntu

M8 usa `systemd --user`. No necesita root para instalar el servicio.

## Instalar

```bash
cd alphawave-taskd
./scripts/install.sh
```

El install:

- crea `backend/.venv` si falta;
- instala backend con extras de dev;
- corre `npm install` y `npm run build`;
- crea `.env` desde `.env.example` si falta;
- crea `data/`, `data/backups/` y `logs/`;
- genera `~/.config/systemd/user/alphawave-taskd.service`;
- ejecuta `systemctl --user daemon-reload`;
- ejecuta `systemctl --user enable alphawave-taskd`.

No arranca automáticamente salvo:

```bash
./scripts/install.sh --start
```

## Operar

```bash
./scripts/start.sh
./scripts/status.sh
./scripts/logs.sh
./scripts/restart.sh
./scripts/stop.sh
```

Equivalentes:

```bash
systemctl --user status alphawave-taskd
journalctl --user -u alphawave-taskd -f
```

## Linger opcional

Por ahora el servicio arranca cuando inicia la sesión de usuario Ubuntu. Si más adelante querés que sobreviva logout/reboot sin sesión gráfica, evaluar:

```bash
loginctl enable-linger "$USER"
```

M8 no lo activa automáticamente.

## Validación

```bash
./scripts/preflight.sh
./scripts/smoke.sh --dry-run
curl -sS http://127.0.0.1:8711/api/system/health
```

## Troubleshooting

- Puerto ocupado: `ss -ltnp | grep 8711`.
- Service failed: `./scripts/status.sh`.
- Logs systemd: `./scripts/logs.sh`.
- Logs archivo: `./scripts/logs.sh --file`.
- Config incompleta: `./scripts/preflight.sh`.
