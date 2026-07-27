# Settings Runtime Contract

`alphawave-taskd` usa tres capas de configuración efectiva:

- `.env`: infraestructura, secretos y valores sensibles del proceso local.
- `app_settings`: defaults de instancia y configuración global editable desde la UI.
- `user_settings`: preferencias personales por usuario autenticado.

M19G agrega el contrato runtime de Settings por usuario para preferencias personales. El inventario de migración hosted está en [Web/VPS deployment architecture](web-vps-deployment-architecture.md#multi-user-readiness-inventory) y la auditoría detallada M19C está en [Multi-user readiness audit](multi-user-readiness-audit.md).

## `.env` vs `app_settings`

`.env` conserva:

- `DATABASE_URL`, paths locales y directorios de infraestructura.
- Tokens de instancia para Telegram/Trello y la master key de cifrado.
- Host/port del servicio.
- Defaults sensibles o propios de la instancia.

`user_settings` conserva preferencias personales:

- Recordatorios: enabled, horarios default, snooze y agrupación.
- Briefing: enabled, schedule, cutoff, timezone y contenido.
- Weekend mode: días, scopes visuales y toggles de ruido automático.
- Prioridad: preset, criterios visibles y orden/peso.

`app_settings` conserva defaults de instancia y áreas globales:

- General timezone como fallback de instancia.
- Backups: enabled y retención de automatismos.
- Trello: legacy single-owner defaults and instance-env credential readiness. Per-user board mappings live in `user_integrations` as of M19H, with `app_settings.trello` mirrored for owner compatibility.
- OpenAI: configuración y API key cifrada por usuario en `user_integrations` e `integration_secrets`.

## Future Multi-User Split

M19C recommends splitting current Settings into three future scopes:

- User settings: timezone, reminders, briefing preferences, weekend/vacation mode, priority criteria and personal planning defaults.
- User integration settings: Trello accounts/boards/workflow states, Telegram linked chat, OpenAI model and enablement.
- Instance/admin settings: host/port, DB URL, backup storage, release/runtime diagnostics, platform bot token, provider secrets and deployment knobs.

Do not add per-user settings as isolated one-off keys. The next design pass should define request user context, ownership tests and migration from current global rows to an initial owner.

M19D recommends separate tables for `user_settings`, `instance_settings` and integration settings instead of nullable ownership in one settings table. `user_settings`, `user_integrations` and encrypted per-user integration secrets now cover the current user boundary. Details: [Per-user settings design](per-user-settings-design.md).

## Runtime Matrix

| Sección | Fuente de verdad | Runtime que la consume | Requiere reinicio | Tests |
| --- | --- | --- | --- | --- |
| General timezone | `user_settings.general.timezone` -> `app_settings.general.timezone` -> `.env` | Weekend mode, fechas locales, briefing/reminders según worker | No para API; workers leen en cada tick relevante | `backend/tests/test_user_settings_m19g.py` |
| Recordatorios | `user_settings.reminders` -> `app_settings.reminders` | `reminder_worker`, parser de acciones Telegram | No | `backend/tests/test_user_settings_m19g.py` |
| Briefing | `user_settings.briefing` -> `app_settings.briefing` | Generación/envío manual y helpers runtime con `user_id` | No | `backend/tests/test_user_settings_m19g.py` |
| Weekend mode | `user_settings.modes.weekend` -> `app_settings.modes.weekend` | UI visual, reminder worker, briefing worker | No | `backend/tests/test_user_settings_m19g.py`, `backend/tests/test_weekend_mode_m15g.py` |
| Prioridad | `user_settings.priority` -> `app_settings.priority` | `Priorizar`, `Hoy`, `Ahora`, explicación de score | No | `backend/tests/test_user_settings_m19g.py`, `backend/tests/test_priority_scoring_m15e.py` |
| Trello boards | `user_integrations.trello.config_json` -> legacy `app_settings.trello.boards` for single owner + secretos `.env` | Trello sync, actions, status, UI mappings | No | `backend/tests/test_user_integrations_m19h.py`, `backend/tests/test_trello_dynamic_boards_m15b.py` |
| Trello writes | `app_settings.advanced.editable.trello_write_enabled` + secretos `.env` | Confirmaciones y acciones Trello | No | `backend/tests/test_settings_m12b.py::test_patch_trello_write_enabled_persists_effective_setting` |
| Auto-confirm Trello | Global advanced + override por board | `trello_actions.maybe_auto_confirm` | No | `backend/tests/test_trello_dynamic_boards_m15b.py::test_board_auto_confirm_overrides_global_default` |
| Backups | `app_settings.backups` + path `.env` | Startup/manual backups, retención, status | No | `backend/tests/test_backups_m15f.py` |
| OpenAI | `user_integrations.openai` + API key cifrada en `integration_secrets` | Clasificación opcional, fallback Telegram, sugerencias | No | `backend/tests/test_llm_m15h.py` |
| Estado sistema | Read-only de DB/settings/metadatos locales | Settings -> Estado, diagnóstico copiable | No | `backend/tests/test_settings_runtime_m15a.py::test_system_status_endpoint_is_read_only` |

## Rules

- `.env` nunca se edita desde la UI.
- Settings nunca devuelve tokens, API keys ni chat IDs; los secretos entrantes se cifran inmediatamente.
- Un override explícito `false` en `user_settings` gana sobre defaults de instancia y `.env`.
- Eliminar/resetear una sección personal borra sólo el override del usuario actual.
- Un PATCH completo enruta secciones personales a `user_settings` y secciones globales a `app_settings`.
- `list_id` manda para Trello writes; `list_name` es etiqueta humana y fallback inicial.
- Boards Trello se desactivan/desconectan, no se borran desde UI.
- Non-owner users do not inherit legacy global Trello mappings.
- Restore automático destructivo no está habilitado; la UI muestra plan y comandos manuales.
- OpenAI sólo está lista cuando la key y el modelo fueron validados y el usuario la habilitó.
