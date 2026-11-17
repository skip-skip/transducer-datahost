# Operations runbook

This is the day-to-day runbook for the VM-hosted stack: backups, restores,
monitoring, routine changes, and the manual upload cadence. The security model
is in [network-security.md](network-security.md); the ingest flow is in
[ingestion.md](ingestion.md); the dashboard is in [dashboard.md](dashboard.md).

The scripts and units described here are implemented. The Phase 7 exit criteria
in [roadmap.md](roadmap.md#phase-7--operations) remain open until the operator
has run a restore, seen a real alert, and rehearsed certificate rotation on the
production VM.

## 1. Host layout

| Path | Purpose |
|---|---|
| `/opt/transducer_dash` | Git checkout, `.env`, `certs/` |
| `/etc/transducer/ops.env` | Optional overrides for the systemd units |
| `/var/backups/transducer` | Dumps, dashboard state archives, manifests |
| `/var/lib/transducer-healthcheck` | Alert cooldown state |

The operational user (`transducer` in the examples) must be able to run
`docker` and read the checkout. Docker socket access is root-equivalent; grant
it only to this service account.

```bash
sudo useradd --system --home /opt/transducer_dash \
  --shell /usr/sbin/nologin transducer
sudo usermod -aG docker transducer
sudo chown -R transducer:transducer /opt/transducer_dash
sudo chmod 0640 /opt/transducer_dash/.env
sudo chgrp transducer /opt/transducer_dash/.env
sudo chmod 0600 /opt/transducer_dash/certs/server.key
```

The `psql` commands in this runbook connect over the container's local socket,
which uses SCRAM. They prompt for the admin password; use the value of
`POSTGRES_PASSWORD` from `.env`. The backup, restore, and healthcheck scripts
pass it automatically.

## 2. Install the timers

```bash
sudo install -d -m 0755 /etc/transducer
sudo install -d -o transducer -g transducer -m 0700 /var/backups/transducer
sudo install -d -o transducer -g transducer -m 0750 /var/lib/transducer-healthcheck
sudo install -m 0644 deploy/systemd/*.service deploy/systemd/*.timer \
  /etc/systemd/system/
sudo install -m 0600 -o root -g root \
  deploy/systemd/transducer-ops.env.example /etc/transducer/ops.env
sudo systemctl daemon-reload
sudo systemctl enable --now transducer-backup.timer transducer-healthcheck.timer
systemctl list-timers 'transducer-*'
```

Edit `/etc/transducer/ops.env` to set `BACKUP_REMOTE` and
`ALERT_WEBHOOK_URL`. `POSTGRES_*` are read from the repository `.env` unless
they are also present in `ops.env` or the unit environment.

Run each unit once by hand before trusting the timers:

```bash
sudo systemctl start transducer-backup.service
sudo systemctl start transducer-healthcheck.service
journalctl -u transducer-backup -n 40 --no-pager
journalctl -u transducer-healthcheck -n 40 --no-pager
```

## 3. Backups

`scripts/backup.sh` writes three files per run:

| File | Contents |
|---|---|
| `<database>-<UTC timestamp>.dump` | `pg_dump --format=custom` of the whole database |
| `dashboard-state-<UTC timestamp>.tar.gz` | The `dashboard_state` volume (all state files) |
| `backup-<UTC timestamp>.json` | Manifest: timestamp, database, file names, git commit |

`latest.dump`, `latest-dashboard-state.tar.gz`, and `latest.json` are symlinks
to the newest run. The first backup of each month is also copied to `monthly/`.

Run manually:

```bash
scripts/backup.sh
```

What is **not** in the backup:

- Database roles and passwords. They live in `db/init`, `db/migrations`, and
  `.env`; run the `migrate` service when restoring onto a fresh cluster.
- The spool volume. It holds uploaded files for queued jobs. Drain the queue
  before a backup if a restore must be able to finish in-flight jobs:

  ```bash
  docker compose exec -T db psql -U transducer_admin -d transducer -At -c \
    "SELECT count(*) FROM ingest.job WHERE status IN ('queued','processing');"
  ```

  A restored database without the spool reconciles queued jobs as failed; the
  files can be uploaded again.
- `.env` and TLS material. Back them up through your secrets process, not this
  script.

Retention defaults to 30 daily runs and 12 monthly copies. Override with
`BACKUP_RETENTION_DAYS` and `BACKUP_MONTHLY_RETENTION`. Set `BACKUP_REMOTE` to
an rsync target (for example a mounted share) to copy the directory after every
successful run. The script verifies the dump with `pg_restore --list` before
promoting it.

Targets: RPO 24 hours with the nightly timer, RTO about one hour for a database
restore on the same VM.

## 4. Restore

### List a dump

```bash
scripts/restore.sh --list /var/backups/transducer/latest.dump
```

### Restore drill into a scratch database

This never touches production. It drops and recreates `transducer_restore`,
restores the dump, and prints `import_file` and `reading` counts.

```bash
scripts/restore.sh --database /var/backups/transducer/latest.dump
```

Verify the result before dropping the scratch database:

```bash
docker compose exec -T db psql -U transducer_admin -d transducer_restore -c \
  "SELECT status, count(*) FROM transducer.import_file GROUP BY status ORDER BY status;"
docker compose exec -T db psql -U transducer_admin -d transducer_restore -c \
  "SELECT count(*) AS readings, min(ts), max(ts) FROM transducer.reading;"
docker compose exec -T db psql -U transducer_admin -d transducer_restore -c \
  "SELECT status, count(*) FROM ingest.job GROUP BY status ORDER BY status;"
```

Run this drill at least quarterly and after any schema or loader change. Drop
the scratch database when finished:

```bash
docker compose exec -T db psql -U transducer_admin -d postgres -c \
  "DROP DATABASE transducer_restore WITH (FORCE);"
```

### Restore over production

Only after the scratch drill succeeds. The script stops the API, worker, read
API, and dashboard, restores inside a single transaction, prints counts, and
starts the services again. Caddy stays up and returns errors during the window.

```bash
scripts/restore.sh --database /var/backups/transducer/latest.dump \
  --production --force
scripts/healthcheck.sh
```

If the target cluster is new, create the roles first:

```bash
docker compose run --rm migrate
```

Roles are not part of a `pg_dump`, and grants in the dump reference them.

### Restore the dashboard state

This replaces the whole `dashboard_state` volume and requires `--force`:

```bash
scripts/restore.sh --dashboard-state \
  /var/backups/transducer/latest-dashboard-state.tar.gz --force
```

The archive must contain `state.json`. The script stops the dashboard, replaces
the volume contents, restores ownership to uid 10001, and starts the dashboard.
A legacy `state.json` is archived and replaced on first load; see
[dashboard.md](dashboard.md#state-load-failures).

## 5. Monitoring and alerting

The healthcheck covers the signals in
[network-security.md](network-security.md#8-monitoring-and-alerting):

| Signal | Check | Result |
|---|---|---|
| Container down or unhealthy | `docker inspect` for db, ingest API, worker, read API, dashboard, Caddy | Fail |
| HTTP health | `/health` inside ingest API, read API, dashboard | Fail on non-200 |
| Worker stopped | `ingest.worker_heartbeat` age vs `WORKER_HEARTBEAT_TIMEOUT_SECONDS` | Fail |
| Ingest failures | `ingest.job.status = 'failed'` in the last 24 hours | Fail |
| Ingest backlog | Oldest `queued`/`processing` job age | Warn at 1 h, fail at 6 h |
| Latest ingest | `max(transducer.import_file.imported_at)` age | Warn after 168 h (manual cadence) |
| Disk usage | Docker root and backup directory | Warn at 80%, fail at 90% |
| Certificate expiry | `certs/server.crt` via `openssl x509 -checkend` | Warn inside 30 days, fail inside 14 days |

Run it manually:

```bash
scripts/healthcheck.sh
```

Exit code 0 means no failures (warnings may be present); 1 means at least one
failure. The systemd timer runs it every five minutes.

### Webhook alerts

Set `ALERT_WEBHOOK_URL` in `/etc/transducer/ops.env` to a Slack-compatible
incoming webhook. The payload is `{"text": "..."}`. Failures alert by default;
set `ALERT_ON_WARNINGS=true` to include warnings. Identical alerts are
suppressed for `ALERT_REPEAT_SECONDS` (default 3600) using
`HEALTHCHECK_STATE_DIR`, so a persistent problem pages hourly rather than every
five minutes.

Test delivery without waiting for a real failure:

```bash
ALERT_ON_WARNINGS=true ALERT_WEBHOOK_URL='https://hooks.example.invalid/services/...' \
  scripts/healthcheck.sh
```

Useful commands:

```bash
systemctl list-timers 'transducer-*'
journalctl -u transducer-healthcheck -n 100 --no-pager
journalctl -u transducer-backup -n 100 --no-pager
docker compose ps
docker compose logs --since 30m ingest-worker
```

## 6. Runbook

### Start, stop, status, logs

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f --tail 100 ingest-worker
docker compose stop            # keeps containers and volumes
docker compose down            # keeps named volumes
docker compose down -v         # DESTROYS pgdata, spool, and dashboard state
```

Never use `down -v` unless you intend to delete the database.

### Upgrade the application

```bash
git pull --ff-only
docker compose build
docker compose up -d
scripts/healthcheck.sh
```

The `migrate` service applies ordered SQL before the APIs start. Check its log
if a service does not come up:

```bash
docker compose logs migrate
```

### Rotate database passwords

The container's `POSTGRES_PASSWORD` only applies on first initialization.
Rotate the role in PostgreSQL, then update `.env` so clients and the migrate
service use the new value:

```bash
docker compose exec -T db psql -U transducer_admin -d transducer -c \
  "ALTER ROLE transducer_ingest WITH PASSWORD 'new-secret';"
# edit INGEST_PASSWORD in .env
docker compose run --rm migrate
docker compose up -d ingest-worker
```

Repeat for `transducer_upload` and `transducer_read`, restarting the services
that use them. For the admin role, use the current admin password to run the
`ALTER ROLE`, update `POSTGRES_PASSWORD`, and recreate the `migrate` container
on its next run. Verify with `scripts/healthcheck.sh`.

### Rotate API tokens

1. Edit `INGEST_API_TOKENS` or `READ_API_TOKENS` in `.env`.
2. Recreate the services that read them:

   ```bash
   docker compose up -d ingest-api read-api dashboard
   ```

3. Update `INGEST_API_TOKEN` in the upload host's environment.
4. Confirm an old token is rejected and a new one works.

Dashboard sessions are signed cookies and survive read-token rotation until
`DASHBOARD_SESSION_MAX_AGE` expires. To revoke every session immediately,
generate a new `DASHBOARD_SECRET_KEY`, update `.env`, and recreate the
dashboard:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
docker compose up -d dashboard
```

### Add a site or logger

There is no separate site/logger registration step. The loader upserts
`logger`, `location`, and `series` from each `.xle` file's metadata. To add one:

1. Upload a complete export from the new logger with `upload_xle.ps1`.
2. Confirm the job finished and the metadata was created:

   ```bash
   docker compose exec -T db psql -U transducer_admin -d transducer -c \
     "SELECT serial, model FROM transducer.logger ORDER BY logger_id DESC LIMIT 5;"
   docker compose exec -T db psql -U transducer_admin -d transducer -c \
     "SELECT name FROM transducer.location ORDER BY location_id DESC LIMIT 5;"
   docker compose exec -T db psql -U transducer_admin -d transducer -c \
     "SELECT series_id, channel, unit FROM transducer.series ORDER BY series_id DESC LIMIT 10;"
   ```

3. Refresh the dashboard; new locations and analytes appear in the settings
   tray for figures in a shared scene.

If the filename and header location disagree, the import succeeds with a
warning and the header location wins. Review the `import_file.error` text.

### Rotate the TLS certificate

1. Put the new `server.crt` (with chain) and `server.key` in `certs/`.
2. Keep the key at mode 0600.
3. Reload Caddy without dropping connections:

   ```bash
   docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
   ```

4. Verify the served certificate and a health endpoint:

   ```bash
   openssl s_client -connect transducer.internal:8443 -servername transducer.internal \
     </dev/null 2>/dev/null | openssl x509 -noout -dates -subject
   curl -fsS --cacert "$(mkcert -CAROOT)/rootCA.pem" \
     https://transducer.internal:8443/ingest/health
   ```

5. `scripts/healthcheck.sh` should stop warning about expiry.

With an internal CA, substitute its root bundle for the mkcert path in the
`curl` commands.

Rehearse this on the production VM before the current certificate expires.

## 7. Upload cadence

Uploads are manual in v1. Recommended cadence:

- Upload after each logger download batch, and at least **weekly** even when no
  new data is expected, so a broken token or certificate is noticed early.
- Use one consistent day and keep the OneDrive export folder until the jobs are
  verified.
- Never upload while OneDrive is still syncing the folder. The uploader skips
  files newer than `INGEST_SETTLE_SECONDS` (default 120) and files without a
  trailing `</Body_xle>`.

From the Windows upload host:

```powershell
$env:INGEST_API_TOKEN = "<token from INGEST_API_TOKENS>"
.\scripts\upload_xle.ps1 -Source "C:\path\to\OneDrive_export"
```

The wrapper waits for each job by default. Afterwards, verify on the VM:

```bash
docker compose exec -T db psql -U transducer_admin -d transducer -c \
  "SELECT status, import_status, count(*) FROM ingest.job
   WHERE received_at > now() - interval '2 days'
   GROUP BY status, import_status ORDER BY status;"
```

Retry a failed job after fixing the cause:

```bash
curl --cacert "$(mkcert -CAROOT)/rootCA.pem" \
  -H "Authorization: Bearer $INGEST_API_TOKEN" \
  -X POST https://transducer.internal:8443/ingest/v1/uploads/<job_id>/retry
```

`scripts/healthcheck.sh` reports failed jobs in the last 24 hours and a growing
backlog, so a missed or broken upload surfaces without checking by hand.

## 8. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `transducer-dashboard` unhealthy, log shows `state has unknown keys` | Legacy state file | See [dashboard.md](dashboard.md#state-load-failures); current release archives it automatically |
| Dashboard login loops or cookies are rejected | `DASHBOARD_SECRET_KEY` changed or `DASHBOARD_COOKIE_SECURE` wrong for plain HTTP | Recreate the dashboard; use HTTPS except for local development |
| `pg_isready` fails after a restart | Database still initializing or wrong admin password | `docker compose logs db`; check `.env` |
| Ingest API returns 503 on `/health` | Database unavailable or worker heartbeat stale | Check `db` and `ingest-worker` logs; run the healthcheck |
| Uploads return 401 | Token rotated or missing on the upload host | Update `INGEST_API_TOKEN`; confirm it is in `INGEST_API_TOKENS` |
| Uploads return TLS errors | Certificate name mismatch or untrusted CA | Pass `-Ca` to the uploader or trust the internal CA |
| Jobs stay `queued` | Worker stopped, lock timeout, or spool unavailable | `docker compose logs ingest-worker`; check `ingest.worker_heartbeat` |
| Disk warning at 80% | Backups, Docker images, or database growth | Prune old images and backups; expand the volume |
| Backup fails with "another backup is already running" | Stale lock from a crashed run | Confirm no backup process is running; remove `BACKUP_DIR/.backup.lock` |
| Restore refuses to overwrite production | Missing `--production --force` | Run the scratch drill first, then use both flags |

## 9. Phase 7 checklist

Deliverables are implemented. Tick these after the production verification:

- [ ] `scripts/backup.sh` runs from the systemd timer and lands on the share.
- [ ] A restore drill into `transducer_restore` matches production counts.
- [ ] A restore over production has been performed or simulated on a copy.
- [ ] Worker heartbeat and failed-job alerts reach the operator.
- [ ] Certificate rotation has been rehearsed.
- [ ] The operator has reviewed this runbook.
