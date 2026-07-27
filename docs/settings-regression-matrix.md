# Settings Regression Matrix

Esta matriz mapea los contratos cerrados en M15A-M15H a tests reales. Para una pasada enfocada:

```bash
backend/.venv/bin/pytest -q backend/tests/test_*m15*.py backend/tests/test_external_integrations_are_isolated_m15i.py
```

La validación completa sigue siendo:

```bash
time backend/.venv/bin/pytest -q --durations=50
cd frontend && npm run build
```

| Área | Regresión protegida | Test/archivo |
| --- | --- | --- |
| M15A Settings runtime | `.env` defaults no pisan overrides runtime en SQLite | `backend/tests/test_settings_runtime_contract_m15i.py::test_app_settings_overrides_env_defaults_for_runtime_behavior` |
| M15A explicit false | Distinguir override faltante vs `false` explícito | `backend/tests/test_settings_runtime_contract_m15i.py::test_missing_override_vs_explicit_false_are_distinguishable` |
| M15A reminders | `reminders.enabled=false` no envía due reminders | `backend/tests/test_settings_runtime_m15a.py::test_reminders_enabled_false_skips_due_reminders` |
| M15A briefing | `briefing.enabled=false` saltea scheduler | `backend/tests/test_settings_runtime_m15a.py::test_briefing_enabled_setting_controls_next_tick_and_cutoff_validation` |
| M15A status | `/api/system/status` es read-only | `backend/tests/test_settings_runtime_m15a.py::test_system_status_endpoint_is_read_only` |
| M15B dynamic boards | Board nuevo requiere `board_id` y rechaza duplicados | `backend/tests/test_trello_dynamic_boards_m15b.py::test_create_trello_board_requires_board_id_and_rejects_duplicates` |
| M15B board disable | Desactivar preserva configuración; no se borra | `backend/tests/test_trello_dynamic_boards_m15b.py::test_patch_board_rejects_alias_rename_and_disable_preserves_config` |
| M15B list IDs | Validación persiste `list_id` y status | `backend/tests/test_trello_dynamic_boards_m15b.py::test_validate_board_persists_list_ids_and_validation_status` |
| M15B sync | Sync usa board dinámico y `list_id` | `backend/tests/test_trello_dynamic_boards_m15b.py::test_dynamic_board_sync_uses_settings_board_and_list_id` |
| M15B auto-confirm | Override por board gana sobre global | `backend/tests/test_trello_dynamic_boards_m15b.py::test_board_auto_confirm_overrides_global_default` |
| M15C status observability | Status no expone secretos ni dispara requests de OpenAI | `backend/tests/test_settings_runtime_m15a.py::test_system_status_read_only_no_secrets_and_openai_not_configured` |
| M15C status Trello | Board disabled no warning; faltantes generan warning claro | `backend/tests/test_settings_runtime_m15a.py::test_system_status_trello_dynamic_board_warnings` |
| M15D UX/status | Frontend compila Settings y Status actuales | `cd frontend && npm run build` |
| M15E priority | Criterios visibles están implementados | `backend/tests/test_priority_scoring_m15e.py::test_visible_priority_settings_are_implemented` |
| M15E disabled criterion | Criterio desactivado aporta cero | `backend/tests/test_priority_scoring_m15e.py::test_disabled_criterion_has_no_contribution` |
| M15E explainability | Summary/factors/warnings existen | `backend/tests/test_priority_scoring_m15e.py::test_explainability_includes_summary_factors_and_warnings` |
| M15F manual backups | Backup manual funciona aunque automático esté apagado | `backend/tests/test_backups_m15f.py::test_manual_backup_allowed_when_automatic_disabled_and_returns_metadata` |
| M15F retention | Retención conserva manuales/desconocidos y más reciente | `backend/tests/test_backups_m15f.py::test_retention_preserves_manual_unknown_and_newest_backup` |
| M15F restore-plan | Restore-plan es read-only y restore automático devuelve 409 | `backend/tests/test_backups_m15f.py::test_restore_plan_is_read_only_and_returns_confirmation_phrase`, `backend/tests/test_backups_m15f.py::test_backup_api_endpoints` |
| M15G weekend timezone | `active_today` usa timezone efectivo | `backend/tests/test_weekend_mode_m15g.py::test_weekend_mode_state_uses_effective_timezone_and_days` |
| M15G reminders | Reminders explícitos silenciados quedan pending | `backend/tests/test_weekend_mode_m15g.py::test_weekend_explicit_reminders_can_be_muted_without_marking_sent` |
| M15G manual Telegram | Telegram manual responde durante weekend active | `backend/tests/test_weekend_mode_m15g.py::test_weekend_telegram_manual_commands_still_work` |
| M15G status | System status expone weekend read-only | `backend/tests/test_weekend_mode_m15g.py::test_weekend_status_is_read_only_and_exposes_runtime_state` |
| M15H OpenAI secrets | API key cifrada, redactada, aislada por usuario y revocable | `backend/tests/test_llm_m15h.py::test_credentials_are_encrypted_redacted_user_scoped_and_revocable` |
| M15H validate | Validación exitosa habilita el uso y conserva estado al desactivar | `backend/tests/test_llm_m15h.py::test_validation_success_allows_enable_and_disable_preserves_validation` |
| M15H structured output | Schema estricto y errores controlados | `backend/tests/test_llm_m15h.py::test_invalid_structured_output_is_controlled` |
| M15H privacy | Responses API usa `store=false`, sin tools ni persistencia de conversación | `backend/tests/test_llm_m15h.py::test_structured_request_uses_official_privacy_and_retry_contract` |
| M15I isolation | Tests no activan Telegram, Trello ni OpenAI reales por defecto | `backend/tests/test_external_integrations_are_isolated_m15i.py` |
| M15I no-secrets | Status sanea tokens, API keys, chat IDs y DB credentials fake | `backend/tests/test_settings_runtime_contract_m15i.py::test_system_status_payload_redacts_settings_contract_secrets` |

## Known Manual Coverage

- Settings stale/refresh UI state is primarily protected by `frontend npm run build` plus manual dogfooding.
- Browser-level scroll-spy, dock placement and tooltip alignment do not have a frontend test framework in this repo.
- El live test de OpenAI requiere opt-in explícito y nunca corre en pytest.
