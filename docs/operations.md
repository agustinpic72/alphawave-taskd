# Operations

## Preflight

```bash
./scripts/preflight.sh
./scripts/preflight.sh --strict
```

Chequea repo, Python, frontend build, SQLite, directorios escribibles, Telegram, Trello, LLM, briefing y backups. No imprime secretos.

En modo default, `.env` faltante es `warning`. En `--strict`, `.env` faltante es `error`.

API equivalente:

```bash
curl -sS http://127.0.0.1:8711/api/system/preflight
```

## Smoke

```bash
./scripts/smoke.sh --dry-run
```

Modo real, con backend levantado:

```bash
./scripts/smoke.sh
```

El smoke usa tareas con prefijo `[SMOKE]`, prueba health, tasks, dev simulator, reminders, planning y briefing preview. No hace writes Trello reales.

## Browser Smoke

Con la app local levantada:

```bash
./scripts/dev/browser-smoke.sh
```

Usa `ALPHAWAVE_BASE_URL` si necesitás apuntar a otro puerto. Cubre Settings/System Status, save bar, Trello/Backups no destructivo, completado local y explicación de prioridad. Crea datos con prefijo `[M17 SMOKE]` y los limpia al final. No hace writes Trello, no envía Telegram y no ejecuta restore.

## RC Check Local

Para un release candidate local de dogfooding:

```bash
./scripts/dev/rc-check.sh
```

Orquesta, en orden, estado git, `pytest --durations`, build frontend, health/status sanity, browser smoke, Trello read-only, Telegram live default, smoke API local y verificación de cleanup. El smoke API usa prefijo `[M17D SMOKE]`, completa tareas localmente, cancela reminders, valida prioridad, crea un backup manual local y abre restore-plan sin restaurar.

Variables útiles:

```bash
ALPHAWAVE_BASE_URL=http://127.0.0.1:8711
ALPHAWAVE_RC_RESTART=1
ALPHAWAVE_RC_SKIP_BROWSER=1
ALPHAWAVE_RC_SKIP_TRELLO=1
ALPHAWAVE_RC_SKIP_TELEGRAM=1
ALPHAWAVE_RC_SEND_TELEGRAM=1
ALPHAWAVE_RC_INCLUDE_TRELLO_CARDS=1
```

Por default no envía Telegram, no hace Trello writes, no restaura backups y no toca `.env`. Cada ejecución genera un reporte Markdown local en `reports/`.

Para una beta web/VPS privada, corré el mismo gate contra la URL HTTPS pública, manteniendo Telegram send y Trello write apagados salvo opt-in explícito:

```bash
ALPHAWAVE_BASE_URL=https://taskd.example.com ./scripts/dev/browser-smoke.sh
ALPHAWAVE_BASE_URL=https://taskd.example.com ./scripts/dev/rc-check.sh
```

La arquitectura recomendada de M19A está en [Web/VPS deployment architecture](web-vps-deployment-architecture.md). El camino hosted/mobile está en [Hosted product roadmap](hosted-product-roadmap.md) y [Mobile/Android strategy](mobile-android-strategy.md).

## VPS Private Beta Scaffolding

M19B agrega templates y scripts seguros para preparar una beta privada sin ejecutar deploy real:

```bash
deploy/nginx/alphawave-taskd.nginx.example
deploy/systemd/alphawave-taskd.service.example
deploy/env/alphawave-taskd.env.example
scripts/service/health.sh
scripts/service/status.sh
scripts/service/logs.sh
scripts/service/restart.sh
scripts/release/check-release.sh
scripts/release/prepare-vps-artifacts.sh
```

Health contra local o URL pública:

```bash
./scripts/service/health.sh
ALPHAWAVE_BASE_URL=https://taskd.example.com ./scripts/service/health.sh
```

Status/logs son read-only. Para instalación local `systemd --user`:

```bash
ALPHAWAVE_SYSTEMD_USER=1 ./scripts/service/status.sh
ALPHAWAVE_SYSTEMD_USER=1 ./scripts/service/logs.sh
```

`scripts/service/restart.sh` se niega a reiniciar salvo opt-in explícito:

```bash
./scripts/service/restart.sh
ALPHAWAVE_CONFIRM_RESTART=1 ./scripts/service/restart.sh
```

Release gate completo:

```bash
ALPHAWAVE_BASE_URL=https://taskd.example.com ./scripts/release/check-release.sh
```

Preparación local de artefactos:

```bash
./scripts/release/build-frontend.sh
./scripts/release/prepare-vps-artifacts.sh
```

Runbook: [VPS private beta runbook](vps-private-beta-runbook.md). Proceso de release: [Release process](release-process.md). Readiness: [Production readiness](production-readiness.md).

## Diagnostics Export

Para generar un bundle de soporte local y saneado:

```bash
./scripts/dev/export-diagnostics.sh
```

Escribe un zip en `reports/support-bundles/` con:

- `manifest.json`;
- `diagnostics.json` desde `/api/system/diagnostics`;
- `system-status.json`;
- resumen git sin remotes;
- hasta 3 reportes RC recientes;
- snapshot de docs operativas.

El bundle es read-only respecto de la app: no envía Telegram, no ejecuta Trello, no crea/restaura backups y no modifica Settings. No contiene `.env`, SQLite, backups reales, `node_modules`, `.venv`, tokens, API keys ni credenciales de DB. Es diagnóstico, no backup. M19I agrega secrets cifrados por usuario y link-code Telegram; los diagnósticos deben seguir redacting Trello keys/tokens, Telegram bot tokens, ciphertext, link-code hashes y chat identifiers.

Variables:

```bash
ALPHAWAVE_BASE_URL=http://127.0.0.1:8711
ALPHAWAVE_DIAGNOSTICS_INCLUDE_RC_REPORTS=1
ALPHAWAVE_DIAGNOSTICS_OUTPUT_DIR=reports/support-bundles
```

## Live Manual Checklist

Para cerrar el dogfooding real que no conviene automatizar agresivamente:

```bash
./scripts/dev/live-manual-checklist.sh
```

El asistente corre preflight, smoke Telegram/Trello seguro, export de diagnóstico, cleanup local de `[M17F SMOKE]` y genera:

```text
reports/live-manual-checklist-YYYYMMDD-HHMMSS.md
```

Para marcar el round-trip manual de Telegram desde el cliente real:

```bash
./scripts/dev/live-manual-checklist.sh --interactive
```

El script no envía Telegram ni escribe Trello por default. Opt-ins explícitos:

```bash
ALPHAWAVE_TELEGRAM_SMOKE_SEND=1 ./scripts/dev/telegram-live-smoke.sh
ALPHAWAVE_TRELLO_WRITE_SMOKE=1 ALPHAWAVE_TRELLO_WRITE_SMOKE_BOARD=GAMMA ./scripts/dev/live-manual-checklist.sh --skip-rc
ALPHAWAVE_TRELLO_WRITE_SMOKE=1 ALPHAWAVE_TRELLO_WRITE_SMOKE_BOARD=GAMMA ALPHAWAVE_TRELLO_WRITE_SMOKE_CLEANUP=1 ./scripts/dev/live-manual-checklist.sh --skip-rc
```

La guía completa está en [docs/live-manual-checklist.md](live-manual-checklist.md).

## Trello Read-Only Smoke

Con la app local levantada y credenciales Trello en `.env`:

```bash
./scripts/dev/trello-readonly-smoke.sh
```

El comando lee `/api/settings`, toma los boards dinámicos activos y consulta Trello sólo con GETs. Valida que las credenciales permitan leer, que cada `board_id` exista, que el board no esté cerrado, que los `list_id` configurados pertenezcan al board correcto y que `pending`/`completed` estén mapeados. Los boards desactivados se saltean y `perpetual` desactivado no genera error.

Variables opcionales:

```bash
ALPHAWAVE_BASE_URL=http://127.0.0.1:8711
ALPHAWAVE_TRELLO_SMOKE_INCLUDE_CARDS=0
ALPHAWAVE_TRELLO_SMOKE_MAX_CARDS=20
```

Exit codes:

| Código | Significado |
| --- | --- |
| `0` | OK o sólo warnings. |
| `1` | Error real de acceso/mapping. |
| `2` | La app local no responde. |
| `3` | Trello no está configurado o no hay boards activos. |

El reporte no imprime tokens/API keys. Auto-confirm aparece como estado informativo y no ejecuta acciones.

## Telegram Live Safety Smoke

Default seguro, con la app local levantada:

```bash
./scripts/dev/telegram-live-smoke.sh
```

El comando lee `/api/system/status`, valida configuración local saneada y ejecuta `getMe` contra Telegram con timeout corto. `getMe` valida el token sin enviar mensajes ni tocar webhooks/polling remoto. Si Telegram está desactivado o falta token/allowlist, el smoke termina como `SKIPPED` limpio.

Para enviar un único mensaje real de prueba al chat allowlisted:

```bash
ALPHAWAVE_TELEGRAM_SMOKE_SEND=1 ./scripts/dev/telegram-live-smoke.sh
```

Variables opcionales:

```bash
ALPHAWAVE_BASE_URL=http://127.0.0.1:8711
ALPHAWAVE_TELEGRAM_SMOKE_TIMEOUT=5
ALPHAWAVE_TELEGRAM_SMOKE_MESSAGE="[M17 SMOKE] Telegram live smoke OK"
ALPHAWAVE_TELEGRAM_SMOKE_CHAT_ID=<chat allowlisted>
```

Si hubiera más de un chat allowlisted, el envío requiere `ALPHAWAVE_TELEGRAM_SMOKE_CHAT_ID` y debe coincidir con la allowlist. El reporte no imprime token ni chat id completo.

Exit codes:

| Código | Significado |
| --- | --- |
| `0` | OK, warnings o skip por Telegram no configurado. |
| `1` | Error real de `getMe` o envío opt-in. |
| `2` | La app local no responde. |

## Live Check

```bash
./scripts/live-check.sh --all --dry-run
./scripts/live-check.sh --all
```

Valida integraciones reales de forma no destructiva:

- backend health;
- preflight;
- briefing preview;
- Telegram `getMe`, si se pide;
- Trello auth/boards/listas, si se pide;
- LLM provider, si se pide.

No crea tareas ni cards.

## Live Smoke

```bash
./scripts/live-smoke.sh --dry-run
./scripts/live-smoke.sh
```

Hace una prueba real mínima con confirmación interactiva. Por default no hace Trello write. `--trello-write` no confirma acciones automáticamente; sólo avisa y deja el flujo explícito para UI/Telegram.

## Import Inicial

```bash
./scripts/import-tasks.sh tasks.txt --dry-run
./scripts/import-tasks.sh tasks.txt
```

Importa tareas locales vía API usando la clasificación existente y la extracción determinística de fechas naturales de tasks. No usa Trello.

Ejemplos válidos por línea:

- `comprar cuentas el viernes`
- `mañana comprar café`
- `domingo ver una película`

`el finde` sigue siendo ambiguo y se importa sin deadline.

## Telegram NLU

Telegram entiende frases naturales básicas sin depender del LLM:

- `agregá comprar cuentas el viernes`
- `buenas, que tengo que hacer hoy?`
- `che, que hago ahora?`
- `buenas, dame la todolist`
- `ordenar`
- `ordename la todo list por prioridades`
- `prioriza mi lista`
- `comprar cuentas recordamelo mañana`
- `posponé comprar cuentas hasta el sábado`
- `comprar cuentas deadline viernes`

El parser normaliza tildes y puntuación para detectar intents, pero preserva el texto original de la tarea. Las fechas claras se extraen antes de clasificar scope.

Semántica:

- `deadline` actualiza `due_at` y usa 12:00 si no hay hora.
- `snooze` actualiza `snoozed_until` y usa 09:00 si no hay hora.
- `recordame`/`recordamelo` crea recordatorio; si encuentra una tarea activa parecida, lo asocia y también la oculta hasta ese momento.
- `agregá X el viernes` sigue creando tarea nueva con deadline; `X recordamelo el viernes` busca una tarea existente.
- `agregá X deadline viernes` crea una tarea nueva con deadline; `X deadline viernes` busca una tarea existente y actualiza `due_at`.
- `ordenar` y variantes generan una propuesta de prioridad; se aplica sólo con `confirmar`.

Si hay varias tareas parecidas, Telegram pregunta cuál usar. Si no hay tarea clara para `recordamelo`, no duplica tareas automáticamente: pregunta si crear tarea + recordatorio, crear sólo recordatorio o cancelar.

OpenAI es opcional y se configura por usuario desde Settings. Los comandos ambiguos sólo consultan OpenAI cuando la conexión está validada y habilitada; cualquier error vuelve al parser determinístico. La interpretación asistida sólo acepta intents seguros y no puede ejecutar deletes, renames, moves, confirmaciones, cancelaciones ni writes Trello.

El estado separa configuración, validación y habilitación:

| Estado | Significado |
| --- | --- |
| `disabled` | IA apagada desde Settings. No es warning. |
| `requires_encryption_key` | Falta la Fernet key de la instalación. |
| `requires_api_key` | Falta guardar una API key del usuario. |
| `not_validated` | La key o el modelo todavía no fueron validados. |
| `ready` | Validación estructurada exitosa y uso habilitado. |
| `error` | El último check/intento falló con error saneado. |

`GET /api/integrations/openai/status` es read-only y no ejecuta requests externas. `POST /api/integrations/openai/validate` hace una llamada mínima y estructurada a la Responses API. Las llamadas usan timeout y retries acotados, `store=false`, no habilitan tools y nunca registran prompts o responses completos.

## Task Detail Suggestions

La asistencia individual se abre desde el panel de detalle con `Sugerir detalles`. El botón superior abre una cola bulk para revisar varias tareas incompletas. El flujo siempre es revisar antes de aplicar:

```text
Sugerir detalles -> revisar motivo/confianza -> Aplicar seleccionadas
```

Endpoints:

```bash
GET  /api/tasks/{task_id}/detail-gaps
POST /api/tasks/{task_id}/suggest-details
POST /api/tasks/{task_id}/apply-suggestions
GET  /api/tasks/incomplete-details?limit=25
POST /api/tasks/suggest-details-bulk
POST /api/tasks/apply-suggestions-bulk
```

Campos aplicables: `normalized_title`, `scope`, `priority_label`, `impact_score`, `urgency_score`, `blocking_score`, `effort_bucket`, `estimated_minutes`, `context_bucket`, `due_at` y `notes`. Por default sólo se sugieren/aplican campos vacíos o `unknown`; para sobrescribir un campo existente el caller debe enviar `allow_existing_field_updates=true`.

La IA es opcional. `suggest-details` consulta readiness y sólo usa OpenAI si `safe_to_use=true`; cualquier otro estado cae al fallback heurístico con warning claro. Las respuestas pasan por schemas Pydantic estrictos y los valores se validan antes de aplicar. `due_at` sólo aparece si hay señal de fecha en título/notas o parser existente.

Bulk M18B no persiste cola en DB: las sugerencias viven en estado del frontend hasta aplicar o descartar. `incomplete-details` lista candidatas activas con 2+ campos importantes faltantes. `suggest-details-bulk` procesa máximo 25 ids y la UI usa 10 por defecto. `apply-suggestions-bulk` es atómico por tarea: una falla no revierte las demás, y devuelve errores por task.

M18C agrega edición directa desde el panel de detalle. `PATCH /api/tasks/{task_id}` acepta `notes` y las guarda en `metadata_json.notes`, además de metadata local como prioridad, scores, esfuerzo, minutos, contexto y deadline. Este PATCH no ejecuta escrituras Trello: las acciones remotas siguen pasando por endpoints de propuesta/confirmación o por el flujo explícito de crear card.

M18D agrega procesamiento de Inbox en la UI. La cola usa tareas activas `scope=Inbox`, ordenadas por antigüedad, y cada guardado llama al mismo PATCH local. Cuando el scope sale de `Inbox`, se registra `inbox_processed`. Omitir no persiste cambios; completar es local; descartar mueve a Papelera. Elegir un scope Trello-backed no crea card ni confirmación, sólo habilita el CTA explícito `Crear card Trello`.

M18E refina `Hoy` y `Ahora`: `/api/planning/today` devuelve summary, grupos determinísticos y top recommendations; `/api/planning/now` devuelve siguiente acción, alternativas y warnings. Ambos usan scoring local y nunca crean confirmaciones, reordenan tareas ni escriben en Trello. Telegram responde `que hago hoy`/`que hago ahora` con el mismo criterio, sin depender de IA.

Seguridad:

- no auto-aplica sugerencias;
- no crea cards Trello ni confirmaciones Trello;
- no manda tokens, API keys, DB URL, chat ids, `.env`, paths sensibles ni logs crudos a OpenAI;
- `trello_card_recommendation` es informativa y no se puede aplicar como metadata.

## Backups

Backup inmediato:

```bash
./scripts/backup.sh
```

Los backups quedan en:

```text
data/backups/
```

La retención default es `BACKUP_RETENTION_DAYS=30` y también puede ajustarse desde `Configuración > Backups`.

Contrato actual:

- `Backups automáticos activados` controla sólo backups de startup/automatismos.
- `Crear backup ahora` siempre es manual y puede ejecutarse aunque los automáticos estén desactivados.
- Los backups nuevos tienen metadata sidecar con `source`, tamaño, hash y estado de validación.
- La retención automática limpia backups `automatic` antiguos, nunca borra el backup más reciente y no borra backups `manual` ni backups viejos sin metadata.
- La validación de backup es read-only y ejecuta `PRAGMA quick_check` sobre una copia temporal descomprimida.

API útil:

```bash
curl -sS http://127.0.0.1:8711/api/backups/status
curl -sS http://127.0.0.1:8711/api/backups
curl -sS -X POST http://127.0.0.1:8711/api/backups
curl -sS -X POST http://127.0.0.1:8711/api/backups/<id>/validate
curl -sS -X POST http://127.0.0.1:8711/api/backups/<id>/restore-plan
```

## Configuración

La vista `Configuración` del dock guarda overrides no sensibles en SQLite (`app_settings`, `user_settings`, `user_integrations`) y deja auditoría básica en `settings_audit`. `.env` queda para infraestructura, fallback legacy y la master key `ALPHAWAVE_SECRET_ENCRYPTION_KEY`; los secretos por usuario viven cifrados en `integration_secrets`. Settings gobierna comportamiento runtime sin editar `.env`: briefing, recordatorios, modo fin de semana, prioridad, backups y toggles seguros. `app_settings` mantiene compatibilidad local/single-owner; en modo autenticado, los Settings personales viven en `user_settings` y la frontera Trello/Telegram se resuelve por usuario.

Referencias:

- [`docs/settings-runtime-contract.md`](settings-runtime-contract.md): contrato `.env` vs `app_settings`.
- [`docs/settings-regression-matrix.md`](settings-regression-matrix.md): tests que protegen M15A-M15I.
- [`docs/dogfooding-checklist.md`](dogfooding-checklist.md): smoke manual rápido/completo.

Secciones actuales:

- Trello: mappings por board/estado con `list_id` + `list_name`, scopeados por usuario vía `user_integrations`.
- Integraciones de usuario: link-code Telegram y credenciales Trello manuales cifradas para beta privada.
- Weekend mode: días activos, scopes permitidos y reglas de notificación automática.
- Briefing: hora, cutoff, timezone, startup y límites por sección.
- Recordatorios: horas default, demora de "más tarde" y umbral de agrupación.
- Prioridad: preset y pesos avanzados usados por `Priorizar`, `Hoy` y `Ahora`.
- Backups: enabled y retención.

Para habilitar secrets por usuario, generar una Fernet key y copiarla manualmente a `.env`/secret manager:

```bash
./scripts/admin/generate-secret-key.sh
```

No commitear esa key. Sin `ALPHAWAVE_SECRET_ENCRYPTION_KEY`, el link-code Telegram puede registrar inbound por hash, pero los envíos proactivos y credenciales Trello por usuario quedan bloqueados.

### Modo fin de semana

El modo fin de semana reduce ruido, no cambia la fuente de verdad de las tareas. La activación se calcula con `general.timezone` efectivo y `modes.weekend.active_days`; si está activo, la UI muestra una señal discreta y atenúa scopes fuera de `active_scopes`, pero no oculta tareas ni cambia su orden de forma destructiva.

| Área | Efecto del modo fin de semana |
| --- | --- |
| Visual emphasis | Sí: banner/badge y scopes no activos atenuados. |
| Telegram manual | No bloquea: `/todo`, planning, altas, completado, snooze, sync y briefing manual responden. |
| Telegram automático | Sí, según toggles de `modes.weekend.notifications`. |
| Recordatorios explícitos | Según `reminders_explicit`; si está apagado, quedan pendientes y no se marcan enviados. |
| Briefing automático | Según `briefing_auto` y `briefing_late_startup`; el briefing manual lo saltea. |
| Check-ins/nags | Según `stale_in_progress` y `perpetual_checkins`. |
| Trello sync | No se frena. Es mantenimiento de estado, no ruido al usuario. |
| Backups | No se frenan. |
| Scoring/prioridad | No cambia directamente. |

`/api/system/status` expone `weekend_mode` como read model con `enabled`, `active_today`, timezone, scopes, toggles permitidos/silenciados y resumen. Ese endpoint no dispara envíos, syncs ni backups.

### Prioridad explicable

`Priorizar`, `Hoy` y `Ahora` usan scoring local determinístico, sin IA. Los criterios activos en `Configuración > Prioridad` son exactamente los que el backend implementa:

- `due_date`: vencida, hoy, mañana, esta semana o futura.
- `manual_priority`: prioridad local marcada por el usuario.
- `urgency`, `impact`, `blocking`: scores 1-5.
- `stale_in_progress`: tareas Trello `in_progress` sin movimiento visible por 3+ días.
- `source_priority`: prioridad importada desde labels Trello rojo/naranja/amarillo/verde.
- `effort`: quick wins suben; deep work puede subir si tiene impacto alto o bajar levemente si no muestra urgencia.
- `age`: tareas viejas sin resolver suben gradualmente con cap.

El orden vertical de Settings afecta el multiplicador: arriba pesa más, abajo pesa menos. Desactivar un criterio elimina su contribución. Cada item de planning/propuesta incluye `score`, `priority_band`, factores con contribución y un resumen humano; la UI muestra una versión compacta en el modal de priorización y en el detalle de tarea.

`Configuración > Estado` consulta `/api/system/status`, un read model frecuente y sin writes. No ejecuta el preflight completo porque ese comando puede crear directorios/probar escritura; para checks de instalación seguí usando `scripts/preflight.sh`.

El status separa settings, configuración y actividad runtime por servicio. Devuelve `overall`, `environment` y `services` con datos saneados para DB, Telegram, Trello, Recordatorios, Briefing, Backups e IA. El polling automático sólo lee DB/metadatos y no ejecuta sync Trello, backups, envíos Telegram ni llamadas a OpenAI. Los botones con efectos laterales viven fuera del polling automático, incluido `Validar conexión`.

El diagnóstico copiable desde la UI usa ese mismo JSON saneado: sólo indica presencia/configuración y timestamps, sin tokens ni claves. Trello se reporta por boards dinámicos desde Settings; boards desactivados no generan warning, `perpetual` desactivado no es problema, y faltantes de `pending`/`completed` con `list_id` sí aparecen como advertencia.

`Backups activados` controla automatismos y retención. `Crear backup ahora` es manual y puede ejecutarse aunque los automáticos estén desactivados, siempre que exista SQLite.

Para corregir errores como `No encontré la lista TERMINADAS en ALPHA`, abrir `Configuración > Trello`, ejecutar `Descubrir`, elegir la lista real para `Terminadas`, guardar y luego `Validar listas`. Las escrituras Trello usan `list_id`; el nombre queda como etiqueta humana y puede cambiar sin romper el write.

API útil:

```bash
curl -sS http://127.0.0.1:8711/api/settings
curl -sS http://127.0.0.1:8711/api/system/status
curl -sS -X POST http://127.0.0.1:8711/api/settings/trello/validate
```

## Papelera

La Papelera permite restaurar tareas, eliminar definitivamente tareas individuales, eliminar seleccionadas en bulk y vaciar todo. Borrar desde TODO o Completadas sigue siendo soft-delete; el hard-delete existe sólo dentro de Papelera y pide confirmación clara en UI.

Eliminar definitivamente una task Trello sólo afecta la copia local. No borra ni archiva la card remota; se guarda una tombstone local para evitar que la sync de Trello la recree automáticamente.

## Restore

```bash
./scripts/restore.sh data/backups/alphawave-taskd-YYYYMMDD-HHMMSS.sqlite.gz
```

El restore:

- exige confirmación interactiva;
- detiene el servicio si está corriendo;
- crea un backup de seguridad del estado actual;
- restaura SQLite desde el `.sqlite.gz`.

Para scripts controlados:

```bash
./scripts/restore.sh --yes data/backups/archivo.sqlite.gz
```

La UI muestra `Ver plan de recuperación`, no ejecuta restore automático. El plan explica el backup seleccionado, DB actual, pasos, frase de confirmación y comandos manuales. La restauración automática vía API responde que no está habilitada para evitar reemplazar SQLite mientras la app puede estar escribiendo.

Flujo manual recomendado:

```bash
systemctl --user stop alphawave-taskd
./scripts/restore.sh data/backups/alphawave-taskd-YYYYMMDD-HHMMSS.sqlite.gz
systemctl --user start alphawave-taskd
curl -sS http://127.0.0.1:8711/api/health
```

## Logs

Systemd:

```bash
./scripts/logs.sh
```

Archivo:

```bash
./scripts/logs.sh --file
```

El log principal es:

```text
logs/alphawave-taskd.log
```
