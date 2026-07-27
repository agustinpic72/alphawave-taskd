# Dogfooding Checklist

Usar después de cambios grandes en Settings/runtime. No requiere push. Evitar datos sensibles en screenshots o logs.

## Smoke Rápido (5 minutos)

- [ ] Iniciar app local y abrir `http://127.0.0.1:8711`.
- [ ] Abrir `Configuración -> Estado`; confirmar que carga diagnóstico y no muestra secretos.
- [ ] Crear una tarea local `Personal`.
- [ ] Crear una tarea con scope Trello-backed, por ejemplo `BETA`, sin card Trello.
- [ ] Completar ambas localmente; la tarea sin card Trello no debe pedir flujo Trello.
- [ ] Click en `Priorizar`; revisar que el modal muestre razones y no aplique hasta confirmar.
- [ ] Abrir detalle de una tarea incompleta; click en `Sugerir detalles`; aplicar sólo algunas sugerencias y confirmar que sólo esas cambiaron.
- [ ] Abrir `Configuración -> Avanzado`; click en `Validar IA`; confirmar estado humano (`Desactivada`, `No verificada`, etc.).
- [ ] Crear backup manual desde `Configuración -> Backups`.
- [ ] Correr Telegram manual si está configurado: `/todo` y `qué hago ahora`.

## Smoke Completo (20 minutos)

- [ ] Revisar `Configuración -> General`; confirmar timezone esperada.
- [ ] Crear reminder con “mañana” y verificar que usa hora default configurada.
- [ ] Cambiar temporalmente un default de recordatorios, guardar y verificar que se refleja.
- [ ] Activar modo fin de semana para el día actual; confirmar badge global y tareas no ocultas.
- [ ] Con `reminders_explicit=true`, crear reminder due soon y confirmar envío si Telegram está activo.
- [ ] Con `reminders_explicit=false`, confirmar que el reminder queda pendiente y no enviado.
- [ ] Probar briefing automático/manual según corresponda; el manual debe responder aunque weekend esté activo.
- [ ] En `Configuración -> Trello`, validar un board existente.
- [ ] Confirmar que board activo tiene `board_id`, `pending.list_id` y `completed.list_id`.
- [ ] Confirmar que `Perpetuas` puede estar desactivado sin warning.
- [ ] Revisar auto-confirm por board; dejarlo apagado salvo intención explícita.
- [ ] Crear backup manual, validarlo y abrir `Ver plan de recuperación`.
- [ ] Confirmar que la UI no ejecuta restore automático destructivo.
- [ ] Copiar diagnóstico de Estado y buscar visualmente que no incluya tokens/API keys/chat IDs.
- [ ] Desactivar cambios temporales de weekend/reminders antes de seguir usando la app.

## Comandos Locales

```bash
backend/.venv/bin/pytest -q backend/tests/test_*m15*.py backend/tests/test_external_integrations_are_isolated_m15i.py
time backend/.venv/bin/pytest -q --durations=50
cd frontend && npm run build
./scripts/dev/rc-check.sh
./scripts/dev/export-diagnostics.sh
./scripts/dev/browser-smoke.sh
./scripts/dev/trello-readonly-smoke.sh
./scripts/dev/telegram-live-smoke.sh
git status --short --branch
```

## RC Check

```bash
./scripts/dev/rc-check.sh
```

Opcionales:

```bash
ALPHAWAVE_RC_RESTART=1 ./scripts/dev/rc-check.sh
ALPHAWAVE_RC_SEND_TELEGRAM=1 ./scripts/dev/rc-check.sh
```

Genera reporte en `reports/`, usa `[M17D SMOKE]`, limpia tareas/reminders al final y no envía Telegram ni hace Trello writes salvo opt-in explícito de Telegram.

## Diagnostics Bundle

```bash
./scripts/dev/export-diagnostics.sh
```

Verificar:

- [ ] Imprime path del zip en `reports/support-bundles/`.
- [ ] El bundle no contiene `.env`, SQLite, backups reales ni tokens.
- [ ] Incluye `manifest.json`, `diagnostics.json`, `system-status.json` y reportes RC recientes.
- [ ] Recordar que no es backup de datos.

## Browser Smoke

```bash
./scripts/dev/browser-smoke.sh
```

Requiere la app local levantada en `http://127.0.0.1:8711` o `ALPHAWAVE_BASE_URL`.
Usa Playwright con Chrome, crea datos con prefijo `[M17 SMOKE]` y los limpia al final.
No hace writes Trello, no envía Telegram y no ejecuta restore.
Si no hay Google Chrome local, instalá Chromium de Playwright con `cd frontend && npx playwright install chromium` y corré `PLAYWRIGHT_BROWSER_CHANNEL=bundled ./scripts/dev/browser-smoke.sh`.

## Trello Read-Only Smoke

```bash
./scripts/dev/trello-readonly-smoke.sh
```

Requiere la app local levantada y credenciales Trello configuradas en `.env`. Lee Settings, valida boards/listas con GETs remotos y reporta errores de mapping sin crear, mover, renombrar ni completar cards. `ALPHAWAVE_TRELLO_SMOKE_INCLUDE_CARDS=1` permite muestrear cards abiertas de forma acotada.

## Telegram Live Smoke

Default seguro, sin enviar mensajes:

```bash
./scripts/dev/telegram-live-smoke.sh
```

Envío real opt-in, máximo un mensaje al chat allowlisted:

```bash
ALPHAWAVE_TELEGRAM_SMOKE_SEND=1 ./scripts/dev/telegram-live-smoke.sh
```

Checklist manual si querés probar comandos reales desde Telegram:

- [ ] Enviar `/todo`.
- [ ] Enviar `qué hago ahora`.
- [ ] Crear `agregá [M17 SMOKE] probar telegram`.
- [ ] Marcarla hecha con el índice mostrado.
- [ ] Confirmar que no quedan datos `[M17 SMOKE]` pendientes.

## M17F Live Manual Checklist

```bash
./scripts/dev/live-manual-checklist.sh
./scripts/dev/live-manual-checklist.sh --interactive
```

Usa prefijo `[M17F SMOKE]`, corre preflight seguro, genera diagnóstico, guía el round-trip manual de Telegram y limpia datos locales al final. Genera reporte en `reports/live-manual-checklist-YYYYMMDD-HHMMSS.md`.

Trello write real sigue apagado salvo opt-in explícito:

```bash
ALPHAWAVE_TRELLO_WRITE_SMOKE=1 ALPHAWAVE_TRELLO_WRITE_SMOKE_BOARD=GAMMA ./scripts/dev/live-manual-checklist.sh --skip-rc
```

Checklist detallada: [Live manual checklist](live-manual-checklist.md).

## M18A Task Detail Suggestions

- [ ] Crear `[M18A SMOKE] programar endpoint de sugerencias` sin metadata.
- [ ] Abrir detalle y click `Sugerir detalles`.
- [ ] Confirmar que aparecen prioridad, scores/esfuerzo/contexto/minutos/notas con motivo y confianza.
- [ ] Aplicar seleccionadas, no todas.
- [ ] Confirmar que sólo los campos seleccionados cambian.
- [ ] Crear `[M18A SMOKE] comprar café mañana`; confirmar que sólo sugiere deadline si el parser detecta fecha.
- [ ] Crear tarea local con scope Trello-backed sin card; confirmar que sólo aparece recomendación informativa, sin crear card ni confirmación Trello.
- [ ] Limpiar tareas `[M18A SMOKE]`.

## M18B Bulk Task Detail Suggestions

- [ ] Crear `[M18B SMOKE] programar reporte semanal`, `[M18B SMOKE] comprar café mañana` y `[M18B SMOKE] revisar PR BETA`.
- [ ] Click en toolbar `Sugerir detalles`.
- [ ] Confirmar candidatos, límite y copy “Revisá antes de aplicar”.
- [ ] Generar sugerencias.
- [ ] Deseleccionar una tarea y/o un campo.
- [ ] Aplicar seleccionadas.
- [ ] Confirmar que sólo las tareas/campos seleccionados cambiaron.
- [ ] Confirmar que no se creó card Trello ni confirmación Trello.
- [ ] Limpiar tareas `[M18B SMOKE]`.

## M18C Task Detail Editing & Completion

- [ ] Crear `[M18C SMOKE] Personal editar detalle` y `[M18C SMOKE] ALPHA editar detalle`.
- [ ] Abrir detalle y confirmar header con título, scope, estado y origen.
- [ ] Editar notas multiline, prioridad, esfuerzo, minutos y contexto desde el panel.
- [ ] Refrescar y confirmar persistencia.
- [ ] Click `Sugerir detalles`, aplicar un campo y confirmar que el editor se actualiza sin cambiar de tarea.
- [ ] Completar la task Personal: debe completarse local directo.
- [ ] Completar la task ALPHA local sin card: debe completarse local directo, sin modal Trello.
- [ ] Confirmar que la task ALPHA local muestra `Sin card Trello` y botón explícito `Crear card Trello` mientras está activa.
- [ ] Limpiar tareas `[M18C SMOKE]`.

## M18D Inbox Processing Workflow

- [ ] Crear `[M18D SMOKE] revisar factura`, `[M18D SMOKE] programar fix BETA` y `[M18D SMOKE] comprar café mañana` en Inbox.
- [ ] Abrir TODO y confirmar card `Inbox` con contador y botón `Procesar Inbox`.
- [ ] Click `Procesar Inbox`.
- [ ] Para la primera, elegir scope `Personal`, editar notas y `Guardar y siguiente`.
- [ ] Para la segunda, elegir scope Trello-backed, confirmar badge/copy `Sin card Trello` y que no crea card automáticamente.
- [ ] Click `Sugerir detalles`, aplicar campos seleccionados y confirmar revisión humana.
- [ ] Omitir una tarea y confirmar que sigue en Inbox.
- [ ] Completar o descartar una tarea desde triage y confirmar que avanza la cola.
- [ ] Limpiar tareas `[M18D SMOKE]`.

## M18E Daily Planning UX

- [ ] Crear `[M18E SMOKE] vence hoy impacto alto`, `[M18E SMOKE] quick admin 15 min`, `[M18E SMOKE] deep work 90 min` y una task Inbox.
- [ ] Abrir `Hoy` y confirmar grupos `Vencidas`, `Para hoy`, `Quick wins`, `Trabajo profundo` e `Inbox por procesar` cuando aplican.
- [ ] Confirmar razones/chips sin `null`, `None`, `undefined` ni `[]`.
- [ ] Abrir `Ahora` y confirmar una siguiente acción concreta.
- [ ] Confirmar alternativas para 15 min, foco, baja energía o procesar Inbox.
- [ ] Probar Telegram manual `que hago ahora` y `que hago hoy`.
- [ ] Confirmar que no se creó confirmación Trello ni se aplicó sort.
- [ ] Limpiar tareas `[M18E SMOKE]`.

## M18F Responsive Dogfooding

- [ ] Desktop 1440x900: abrir TODO, Hoy, Ahora y confirmar que no hay scroll horizontal global.
- [ ] Laptop 1366x768: abrir detalle de task, Settings y Trello; confirmar que dock, save bar y paneles no tapan acciones.
- [ ] Mobile 390x844: abrir TODO, Hoy y Ahora; confirmar cards/chips apilados y sin overflow.
- [ ] Mobile 390x844: abrir una task y confirmar detail sheet con cerrar, completar, sugerir detalles, notas y metadata accesibles.
- [ ] Mobile 390x844: abrir Settings, navegar Estado/Trello/Backups y confirmar nav horizontal local, cards full-width y botones visibles.
- [ ] Mobile 390x844: abrir `Procesar Inbox`; confirmar título, scope, notas y acciones visibles con scroll interno.
- [ ] Mobile 390x844: abrir sugerencias individuales y bulk; confirmar filas apiladas, checkboxes y acciones sticky visibles.
- [ ] Limpiar tareas `[M17 SMOKE]` / `[M18F SMOKE]` si quedaron de pruebas manuales.

## M19A Web/VPS Architecture Plan

- [ ] Revisar [Web/VPS deployment architecture](web-vps-deployment-architecture.md) y confirmar Phase 1: nginx HTTPS, FastAPI localhost, SQLite privada.
- [ ] Confirmar que Phase 1 no promete multiusuario, Android, pagos, anuncios ni VPS real automatizado.
- [ ] Revisar inventario multiusuario y marcar componentes de mayor riesgo antes de diseñar auth.
- [ ] Revisar clasificación API y decidir qué endpoints se vuelven `/api/v1` para mobile.
- [ ] Revisar [Hosted product roadmap](hosted-product-roadmap.md) y confirmar orden de fases.
- [ ] Revisar [Mobile/Android strategy](mobile-android-strategy.md) antes de iniciar app Android.

## M19B VPS Private Beta Scaffolding

- [ ] Revisar `deploy/nginx/alphawave-taskd.nginx.example` y confirmar dominio/certs como placeholders.
- [ ] Revisar `deploy/systemd/alphawave-taskd.service.example` y confirmar `APP_HOST=127.0.0.1`.
- [ ] Revisar `deploy/env/alphawave-taskd.env.example` y confirmar que no contiene secretos reales.
- [ ] Correr `./scripts/service/health.sh` contra local.
- [ ] Correr `ALPHAWAVE_SYSTEMD_USER=1 ./scripts/service/status.sh` si el servicio local existe.
- [ ] Correr `./scripts/service/restart.sh` y confirmar que se niega sin `ALPHAWAVE_CONFIRM_RESTART=1`.
- [ ] Correr `./scripts/release/check-release.sh`.
- [ ] Correr `./scripts/release/prepare-vps-artifacts.sh` y confirmar que excluye `.env`, SQLite, backups y logs.
- [ ] Revisar [VPS private beta runbook](vps-private-beta-runbook.md), [Release process](release-process.md) y [Production readiness](production-readiness.md).
- [ ] Para una beta pública privada real, repetir RC con `ALPHAWAVE_BASE_URL=https://<domain>`.

## M19C Multi-User Readiness Audit

- [ ] Revisar [Multi-user readiness audit](multi-user-readiness-audit.md) y confirmar P0/P1 blockers.
- [ ] Revisar [Multi-user data ownership matrix](multi-user-data-ownership-matrix.md) y marcar entidades que requieren `user_id`.
- [ ] Revisar [Multi-user migration plan](multi-user-migration-plan.md) antes de diseñar auth.
- [ ] Confirmar que M19D no agrega `user_id` sin request user context y tests de aislamiento.
- [ ] Confirmar que cualquier beta VPS pública sigue detrás de VPN/Basic Auth/IP allowlist hasta tener auth real.

## M19D Auth, Request Context and Per-User Settings Design

- [ ] Revisar [Auth, user scope and request context design](auth-user-scope-design.md).
- [ ] Revisar [Auth API boundary](auth-api-boundary.md) y confirmar categorías de endpoints.
- [ ] Revisar [Per-user settings design](per-user-settings-design.md) y confirmar scope de cada setting.
- [ ] Revisar [User isolation test strategy](user-isolation-test-strategy.md) antes de implementar auth.
- [ ] Confirmar que el próximo milestone no implemente mobile tokens antes de proteger web/admin y request context.

## M19E Auth Shell

- [ ] Con auth apagado, correr RC y confirmar que dogfooding local sigue igual.
- [ ] Crear owner en entorno de prueba con `ALPHAWAVE_OWNER_EMAIL`/`ALPHAWAVE_OWNER_PASSWORD` o prompt interactivo.
- [ ] Habilitar `ALPHAWAVE_AUTH_ENABLED=true` en entorno de prueba.
- [ ] Abrir UI, iniciar sesión y confirmar que TODO carga.
- [ ] Confirmar que logout vuelve a login.
- [ ] Confirmar que `/api/health` queda público y `/api/tasks` requiere sesión.
- [ ] Recordar: M19E no aísla datos por usuario; no crear múltiples usuarios reales.

## M19F Core Ownership

- [ ] Correr `backend/.venv/bin/pytest -q backend/tests/test_auth_m19e.py backend/tests/test_core_ownership_m19f.py`.
- [ ] Confirmar que User B no ve ni modifica tasks/reminders/confirmaciones de User A.
- [ ] Confirmar que planning/sugerencias/Inbox sólo usan tareas del usuario actual.
- [ ] Recordar: Settings personales y Trello/Telegram ya tienen ownership foundation; backups y diagnostics siguen instance/admin.

## Limitaciones Conocidas

- El browser smoke cubre regresiones obvias, no reemplaza dogfooding visual completo de Settings, dock, tooltips y scroll.
- El Trello read-only smoke valida acceso y mappings reales, pero no prueba escrituras remotas ni auto-confirm.
- El Telegram live smoke default valida `getMe` y estado local. El round-trip real queda cerrado sólo cuando `live-manual-checklist.sh --interactive` se ejecuta con comandos enviados manualmente desde Telegram.
- OpenAI debe permanecer deshabilitada hasta guardar y validar una API key por usuario.
- Restore automático por API no está habilitado; usar plan + script con servicio detenido.
