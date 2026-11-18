# Scripts

| Script | Purpose | Runs on |
|---|---|---|
| `upload_xle.ps1` | Manually upload new `.xle` files to the ingest API | Windows upload host |
| `run_ingest.ps1` | Legacy direct loader with locking and daily logging | Windows host with DB access |
| `migrate.py` | Apply `db/migrations` and update application roles | VM (or any host with admin access) |
| `backup.sh` | Nightly `pg_dump --format=custom` plus dashboard state | Linux VM |
| `restore.sh` | Restore a dump into a scratch or production database, or restore dashboard state | Linux VM |
| `healthcheck.sh` | Verify containers, APIs, heartbeat, jobs, disk, and certificate; optional webhook alert | Linux VM |
| `dev_dashboard.ps1` | Local dashboard server with fake data (fast Panel or prod-like FastAPI) | Windows dev host |
| `_common.sh` | Shared helpers sourced by the shell scripts | — |

All scripts are implemented. The shell scripts are driven by
[`deploy/systemd`](../deploy/systemd) timers and documented in
[docs/operations.md](../docs/operations.md). They read `.env` in the repository
root and accept the same values from the environment, so systemd can override
them through `/etc/transducer/ops.env`.

## `backup.sh`

```bash
scripts/backup.sh
```

Writes a custom-format `pg_dump`, a tar archive of the `dashboard_state`
volume, and a JSON manifest to `BACKUP_DIR` (default `/var/backups/transducer`).
The dump is verified with `pg_restore --list` before it is promoted. Retention
defaults to 30 daily runs and 12 monthly copies; `BACKUP_REMOTE` rsyncs the
directory to a network share. A `flock` prevents overlapping runs.

Not included: database roles (run the `migrate` service), `.env`, TLS material,
and the spool volume. Drain queued jobs before a backup if a restore must finish
them. See [docs/operations.md](../docs/operations.md#3-backups).

## `restore.sh`

```bash
scripts/restore.sh --list BACKUP.dump
scripts/restore.sh --database BACKUP.dump
scripts/restore.sh --database BACKUP.dump --production --force
scripts/restore.sh --dashboard-state BACKUP.tar.gz --force
```

- `--list` prints the archive table of contents.
- `--database` restores into a scratch database (`transducer_restore` by
  default) and prints `import_file` and `reading` counts. It never touches
  production unless `--target` is `POSTGRES_DB` and both `--production` and
  `--force` are supplied.
- A production restore stops the API, worker, read API, and dashboard, restores
  in a single transaction, and starts them again.
- `--dashboard-state` replaces the state volume, restores ownership to uid
  10001, and restarts the dashboard.

Roles are not part of a `pg_dump`; run `docker compose run --rm migrate` on a
fresh cluster before restoring. See
[docs/operations.md](../docs/operations.md#4-restore).

## `healthcheck.sh`

```bash
scripts/healthcheck.sh
```

Checks container health, the ingest/read/dashboard `/health` endpoints, worker
heartbeat age, failed jobs in the last 24 hours, queue backlog, latest ingest
age, disk usage, and certificate expiry. Exit 0 means no failures (warnings are
allowed); exit 1 means at least one failure.

Set `ALERT_WEBHOOK_URL` to a Slack-compatible webhook to receive alerts.
`ALERT_ON_WARNINGS=true` includes warnings. Identical alerts are suppressed for
`ALERT_REPEAT_SECONDS` (default 3600). See
[docs/operations.md](../docs/operations.md#5-monitoring-and-alerting).

## `dev_dashboard.ps1`

```powershell
.\scripts\dev_dashboard.ps1                     # fast Panel loop, port 8081
.\scripts\dev_dashboard.ps1 -Mode prod          # FastAPI + Panel, port 8080
```

- Fast mode serves `dashboard/dev_panel.py` with `panel serve --dev` and reloads
  the browser page on Python changes. There is no login, CSP, or proxy behavior.
- Prod mode serves `dashboard/dev_fastapi.py` with `uvicorn --reload`, so it
  exercises login, cookies, security headers, and the Panel mount. The process
  restarts on changes; refresh the browser.
- Both use fake fixtures by default. In prod mode, sign in with token `dev`.
- State is isolated in `%TEMP%\transducer-dashboard-dev`; `-ResetState` deletes
  only that directory. `-NoSeed` skips the demo scene.
- `-RealApi` uses `READ_API_URL` instead of fixtures. `-Api` overrides the URL
  for one run, and `-CaBundle` sets `SSL_CERT_FILE` for the VM certificate.
- `-RootPath /dashboard` serves prod mode under the production prefix; the dev
  server strips the prefix like Caddy. The fast loop rejects it because it has no
  proxy. `-Port` overrides the default port.

The script restores the environment variables it sets when the server exits.
See [dashboard/README.md](../dashboard/README.md#development) for the full
workflow and the TLS caveat.

## `upload_xle.ps1`

```powershell
.\scripts\upload_xle.ps1 -Source "C:\path\to\OneDrive_1_11-9-2026"
```

- Acquires a machine-wide mutex so uploads cannot overlap.
- Appends timestamped output to `logs/upload-yyyyMMdd.log` (override with
  `-LogDirectory`).
- Scans the source recursively, skips files newer than `INGEST_SETTLE_SECONDS`
  (default 120) or without a trailing `</Body_xle>`, hashes each file, and asks
  the API whether it is already known.
- Uploads with `transducer-ingest upload`, retries transient failures, and
  waits for each job to finish by default. Use `-NoWait` to enqueue only.

Parameters override the environment for that run:

```powershell
.\scripts\upload_xle.ps1 `
    -Source "C:\path\to\export" `
    -Api "https://transducer.internal:8443/ingest" `
    -Ca "C:\certs\rootCA.pem"
```

Set `INGEST_API_TOKEN` in the user environment (or pass `-Token`) so the
uploader never prompts during an unattended run. `INGEST_API_URL` and
`INGEST_CA_CERT` can be set the same way; `-Api` and `-Ca` override them.
The uploader never receives a database password.

## `migrate.py`

```bash
python scripts/migrate.py
```

Applies ordered SQL files from `db/migrations` and creates or updates
`transducer_upload`, `transducer_ingest`, and `transducer_read`. It reads the
admin `POSTGRES_*` variables plus `UPLOAD_PASSWORD`, `INGEST_PASSWORD`, and
`READ_PASSWORD`. Compose runs the same code in the `migrate` service.

## `run_ingest.ps1`

```powershell
.\scripts\run_ingest.ps1 -Source "C:\path\to\OneDrive_1_11-9-2026"
```

- Acquires a machine-wide mutex so scheduled runs cannot overlap.
- Appends timestamped output to `logs/ingest-yyyyMMdd.log` (override with `-LogDirectory`).
- Runs `python -m transducer_ingest.cli ingest` against the source folder and exits with its
  code.

The wrapper inherits its environment from the calling shell. Set the canonical
`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `INGEST_USER`, and
`INGEST_PASSWORD` variables (plus optional `SITE_TIMEZONE`) before running it.

> The VM-hosted design does **not** use `run_ingest.ps1`; uploads go through
> `upload_xle.ps1` and the HTTPS API. Keep the direct loader for development
> against a disposable database only.
