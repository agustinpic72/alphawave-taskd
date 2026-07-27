# Docker Soak Checklist

Use this checklist for a release candidate after the isolated loopback smoke passes and before wider private-beta access. Record timestamps, revision, host platform, Docker/Compose versions, and operator initials with the release evidence.

## Entry Gates

- [ ] CI labels `Linux / Compose build` and `Linux / Docker runtime and persistence` are green.
- [ ] `./scripts/dev/docker-smoke.sh` passes on the target host or its matching supported platform.
- [ ] A current backup has been created, copied off the Docker host, and its checksum recorded.
- [ ] The resolved `docker compose config` contains the intended deployment mode, port, volumes, and no unexpected published API port.
- [ ] The local gateway listens on `127.0.0.1:8711`; port `8080` remains internal to nginx and FastAPI is not host-published.
- [ ] Record whether volumes use the portable `local-driver-managed` default or the optional `external-bind-backed` mode.
- [ ] For `external-bind-backed`, verify both private device paths are absolute, empty for initial migration, on the intended mounted filesystem, and pass write, lock, and atomic-rename probes.
- [ ] Inspect both Docker volumes and confirm the exact intended driver/options/device; treat any wrong-device or option mismatch as a hard stop.
- [ ] The migration baseline records the resolved Compose hash, source and destination SHA-256, exhaustive SQLite verification, and captured systemd ownership/mode metadata.
- [ ] The transactional rollback plan and dry run pass with explicit candidate volume names and a private staging directory on adequate storage.
- [ ] Integration credentials are test or least-privilege credentials; write/send gates remain off unless that behavior is explicitly under test.

## Cutover Checks

- [ ] Record the previous and candidate revisions and the exact cutover time.
- [ ] Stop the previous stack with `docker compose stop` or `docker compose down`; never use `-v` during cutover.
- [ ] Confirm the quiesced source and seeded destination match exactly by SHA-256 and exhaustive table/schema verification before starting the candidate API.
- [ ] Start the candidate, then confirm `docker compose ps`, `/healthz`, and `/api/health` are healthy.
- [ ] Confirm authentication and deployment-mode behavior from a loopback client and, when applicable, the private ingress.
- [ ] Create, edit, complete, restore, and permanently delete only clearly tagged test tasks.
- [ ] Restart `api`, then perform Compose down/up and verify the tagged persistence task remains.
- [ ] Create a post-cutover backup and copy it off-host.

## Soak Window

Run for 14 consecutive calendar days. A rollback, data-integrity failure, duplicated integration effect, or failed upgrade resets the soak clock.

- [ ] Check container health, restart counts, disk/volume usage, and recent logs daily.
- [ ] Rehearse an API restart on days 3 and 11 and a backed-up rebuild/upgrade on days 4 and 9.
- [ ] Validate SQLite integrity and backup metadata at baseline, after each upgrade, and on day 14.
- [ ] Confirm normal task and settings workflows after each observation point.
- [ ] Confirm backup creation and retention without growth in unexpected paths.
- [ ] Confirm the API remains unpublished and the web gateway is reachable only through the intended interface/ingress.
- [ ] Confirm logs and diagnostics contain no secrets, cookies, tokens, private task content, or host paths requiring redaction.
- [ ] Confirm Docker daemon autostart, `restart: unless-stopped`, the persistent external mount, and the masked legacy app service after a controlled reboot.
- [ ] For enabled integrations, confirm ownership, rate behavior, and read/write gates using tagged test records only.

## Stop And Roll Back

Roll back immediately for missing or corrupt data, repeated container restarts, persistent unhealthy state, auth bypass, cross-user disclosure, secret leakage, or unintended Telegram/Trello writes.

- [ ] Preserve logs and `docker compose ps --all` output before stopping the candidate.
- [ ] Stop with `docker compose stop` or `docker compose down` without `-v`; retain both named volumes.
- [ ] Run the transactional rollback plan/dry run before execution and confirm its exclusive lock, staging space, target database, and expected service identity.
- [ ] Confirm rollback exports and validates the Docker database, preserves the previous systemd database, and reapplies the captured systemd UID, GID, and modes.
- [ ] If the replacement service fails health or data verification, confirm automatic recovery restores the preserved systemd database; keep Docker stopped and retain every transaction artifact.
- [ ] Restore the previous revision and volumes, or use the documented verified backup procedure when data restoration is required.
- [ ] Re-run loopback health and persistence checks after rollback.
- [ ] Record the failure, impact, evidence location, and follow-up owner; do not resume the soak until the cause is understood.

## Exit

- [ ] The full soak window completed without a rollback condition.
- [ ] Final health, restart count, volume usage, backup, and integration checks are recorded.
- [ ] Temporary tagged tasks, test integration artifacts, and credentials are removed.
- [ ] The release owner signs off before access is broadened.
