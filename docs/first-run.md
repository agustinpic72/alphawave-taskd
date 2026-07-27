# First Run

Guía para activar `alphawave-taskd` con datos reales sin exponer secretos.

## 1. Crear `.env`

```bash
./scripts/setup-env.sh
```

Editá `.env` manualmente. No lo commitees.

## 2. Telegram

1. Crear bot con BotFather.
2. Pegar `TELEGRAM_BOT_TOKEN` en `.env`.
3. Mandarle un mensaje al bot.
4. Descubrir tu user id:

```bash
./scripts/telegram-whoami.sh --write
```

El script no imprime el token.

## 3. Trello

1. Crear/obtener API key y token de Trello.
2. Pegarlos en `.env`.
3. Descubrir member id, boards y listas:

```bash
./scripts/trello-discover.sh --write
```

El script no imprime key/token y no escribe en Trello.

## 4. Validar config

```bash
./scripts/preflight.sh --strict
```

## 5. Instalar y arrancar servicio

```bash
./scripts/install.sh --start
```

## 6. Validar integraciones live

```bash
./scripts/live-check.sh --all
```

## 7. Probar Telegram

En Telegram:

```text
/todo
agregá domingo ver una película
recuérdame revisar Trello en 2 minutos
briefing
```

## 8. Probar Trello read-only

En Telegram:

```text
sync trello
estado trello
```

O por API:

```bash
curl -sS -X POST http://127.0.0.1:8711/api/trello/sync
```

## 9. Importar tareas iniciales

```bash
./scripts/import-tasks.sh ~/keep-export/todo.txt --dry-run
./scripts/import-tasks.sh ~/keep-export/todo.txt
```

Formato simple:

```text
hacer un trámite
domingo ver una película
DELTA: revisar endpoint de transactions
AW: mejorar prompt de briefing
```

La importación crea tareas locales; no usa Trello.

## 10. Primer día

- Revisar `Hoy`.
- Revisar `Ahora`.
- Ejecutar `briefing` manual si querés probar el formato.
- Dejar que el briefing automático corra a las 13:00 UTC.
