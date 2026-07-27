# Live Manual Checklist

Checklist guiada para cerrar dogfooding real de Telegram y Trello sin automatizar acciones peligrosas.

Prefijo obligatorio para cualquier dato de prueba:

```text
[M17F SMOKE]
```

## Preflight

- [ ] App local levantada en `http://127.0.0.1:8711` o `ALPHAWAVE_BASE_URL`.
- [ ] `./scripts/dev/rc-check.sh` pasa o deja reporte con causa conocida.
- [ ] `./scripts/dev/telegram-live-smoke.sh` valida `getMe` sin enviar mensajes.
- [ ] `./scripts/dev/trello-readonly-smoke.sh` valida boards/listas sólo con GETs.
- [ ] `./scripts/dev/export-diagnostics.sh` genera bundle saneado.

Comando asistido:

```bash
./scripts/dev/live-manual-checklist.sh
```

Modo de cierre manual interactivo:

```bash
./scripts/dev/live-manual-checklist.sh --interactive
```

## Telegram Manual Round-Trip

Enviar desde Telegram, no desde scripts:

- [ ] `/todo`
  - Esperado: responde lista actual o estado vacío.
- [ ] `qué hago ahora`
  - Esperado: responde recomendación o fallback local aunque la IA no esté verificada.
- [ ] `agrega [M17F SMOKE] probar telegram manual`
  - Esperado: crea una task local activa con ese prefijo.
- [ ] Completar la task desde Telegram con el índice mostrado o desde UI/API si el índice no es estable.
  - Esperado: no quedan tasks activas `[M17F SMOKE]`.

Si Weekend Mode está activo, no desactivarlo automáticamente. El bot debe seguir respondiendo comandos manuales.

## Telegram Send Smoke Opt-In

Default seguro:

```bash
./scripts/dev/telegram-live-smoke.sh
```

Envío real de máximo un mensaje al chat allowlisted:

```bash
ALPHAWAVE_TELEGRAM_SMOKE_SEND=1 ./scripts/dev/telegram-live-smoke.sh
```

Registrar en el reporte si se ejecutó o si quedó skipped.

## Trello Write Smoke Opcional

Default seguro: no Trello writes.

Write real opt-in:

```bash
ALPHAWAVE_TRELLO_WRITE_SMOKE=1 \
ALPHAWAVE_TRELLO_WRITE_SMOKE_BOARD=GAMMA \
./scripts/dev/live-manual-checklist.sh --skip-rc
```

Cleanup remoto opt-in, también write:

```bash
ALPHAWAVE_TRELLO_WRITE_SMOKE=1 \
ALPHAWAVE_TRELLO_WRITE_SMOKE_BOARD=GAMMA \
ALPHAWAVE_TRELLO_WRITE_SMOKE_CLEANUP=1 \
./scripts/dev/live-manual-checklist.sh --skip-rc
```

Reglas:

- El board alias debe ser explícito.
- La card debe llamarse `[M17F SMOKE] Trello write smoke`.
- La creación usa `pending.list_id`, no el nombre visible.
- Si no se activa cleanup, archivar/borrar manualmente la card y guardar link/id saneado.
- No activar auto-confirm automáticamente.

## Auto-Confirm Por Board

Checklist manual:

- [ ] Abrir `Configuración -> Trello`.
- [ ] Revisar el board elegido.
- [ ] Confirmar que auto-confirm está off/on según intención.
- [ ] Si se activa, debe requerir la fricción visible “Entiendo el riesgo”.
- [ ] Guardar.
- [ ] Dejarlo en el estado original al terminar.

## Cleanup

Local automático del asistente:

- tasks `[M17F SMOKE]` activas/completadas/eliminadas;
- reminders pendientes `[M17F SMOKE]`;
- confirmaciones pendientes que contengan `[M17F SMOKE]`.

Cleanup manual:

- mensajes Telegram enviados no se borran desde el script;
- cards Trello creadas sin `ALPHAWAVE_TRELLO_WRITE_SMOKE_CLEANUP=1` deben archivarse o borrarse manualmente.

## Pass/Fail

Pass:

- preflight read-only OK;
- Telegram manual responde `/todo`, planning local/fallback, create y complete;
- cleanup local queda en cero;
- no secretos en reportes;
- Trello write queda skipped o ejecutado con opt-in y cleanup documentado.

Fail:

- Telegram no responde aunque `getMe` esté OK;
- Weekend Mode bloquea comandos manuales;
- crear/completar por Telegram falla;
- completar local dispara flujo Trello para tasks sin card;
- smoke deja datos `[M17F SMOKE]` activos;
- Trello write usa nombres de listas en vez de `list_id`;
- reporte expone tokens, API keys o chat ids completos.

## Evidencia

Guardar:

- reporte `reports/live-manual-checklist-YYYYMMDD-HHMMSS.md`;
- diagnóstico `reports/support-bundles/alphawave-diagnostics-*.zip`;
- screenshots de Telegram/Trello sólo si no muestran tokens, chat ids completos ni datos privados.
