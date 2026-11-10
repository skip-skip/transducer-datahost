# Ingester

The ingestion package parses Solinst `.xle` exports, reports validation results without a
database, loads uncompensated readings into PostgreSQL, and provides the VM-hosted upload
API, queue worker, manual uploader, and migration runner.

## Layout

```
ingest/
  pyproject.toml
  Dockerfile
  transducer_ingest/
    __init__.py
    parse_xle.py        # XML -> normalized records
    validate.py         # statuses, anomaly classification, folder report
    report.py           # database-free report CLI
    db.py               # role-aware POSTGRES_* / UPLOAD_* / INGEST_* / READ_* config
    load.py             # staging COPY + upsert loader, load_one(), deduplication
    upload.py           # HTTPS uploader (scan, settle, hash, HEAD, POST, wait)
    cli.py              # ingest/upload/report CLI
    service/
      settings.py       # environment-backed service settings
      auth.py           # static bearer-token auth (constant-time compare)
      log.py            # JSON logging setup
      jobs.py           # ingest.job queue SQL
      spool.py          # content-addressed spool
      app.py            # upload FastAPI app
      worker.py         # single-writer queue worker
      healthcheck.py    # worker heartbeat healthcheck
      migrate.py        # role reconciler + db/migrations runner
  tests/
    fixtures/           # small cp1252 and UTF-8 sample .xle files
    conftest.py
    test_parse.py
    test_validate.py
    test_report.py
    test_load.py        # pure loader tests + gated DB integration test
    test_service_settings.py
    test_service_spool.py
    test_service_jobs.py
    test_upload.py
```

## Install

```
cd ingest
python -m pip install -e ".[dev]"
```

Extras:

- `.[dev]` — tests, ruff, mypy, httpx, FastAPI test client.
- `.[service]` — FastAPI, Uvicorn, multipart support, and the service dependencies.
- `.[upload]` — `httpx` and `rich` for the Windows uploader.

## Commands

Database-free report:

```
python -m transducer_ingest.report --source <folder> [--json] [--check] [--all]
```

Database loader (development/direct use):

```
python -m transducer_ingest.cli ingest --source <folder> [--dry-run] [--since <date>] [--json] [--all]
```

Manual HTTPS uploader:

```
python -m transducer_ingest.cli upload --source <folder> --api <url> \
    [--token <token>] [--ca <bundle>] [--settle-seconds N] \
    [--wait|--no-wait] [--timeout S] [--wait-timeout S] [--retries N] \
    [--client-name NAME] [--json] [--all]
```

Service processes (normally started by Compose):

```
python -m transducer_ingest.service.migrate
python -m transducer_ingest.service.worker
uvicorn transducer_ingest.service.app:create_app --factory --host 0.0.0.0 --port 8080
python -m transducer_ingest.service.healthcheck
```

`transducer-ingest` is the installed entry point for the CLI; `transducer-report` is the
database-free report. `--source` is scanned recursively for `.xle` files. `--check` exits
non-zero when the aggregate totals differ from the expected current export. The report
never imports a database driver.

The direct loader reads `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `INGEST_USER`,
`INGEST_PASSWORD`, and `SITE_TIMEZONE` (default `America/New_York`). It connects with the
least-privilege ingest role, not the admin `POSTGRES_USER` / `POSTGRES_PASSWORD`.
`--dry-run` executes the load inside one transaction and rolls it back; `--since` filters
on the header download time.

The uploader reads `INGEST_API_URL`, `INGEST_API_TOKEN`, `INGEST_SETTLE_SECONDS`, and
optionally `INGEST_CA_CERT`. It never reads a database credential. Prefer the PowerShell
wrapper on Windows (`../scripts/upload_xle.ps1`), which adds a mutex and a daily log.

The services read:

| Variable | Used by | Purpose |
|---|---|---|
| `UPLOAD_USER` / `UPLOAD_PASSWORD` | ingest API | Queue-only database role |
| `INGEST_USER` / `INGEST_PASSWORD` | worker, migrate | Domain write role |
| `READ_USER` / `READ_PASSWORD` | read API, migrate | Read-only role |
| `POSTGRES_*` | migrate | Admin connection |
| `INGEST_API_TOKENS` | ingest API | `label:token` pairs |
| `SPOOL_DIR` | API, worker | Spool volume path |
| `MAX_UPLOAD_BYTES` | ingest API | Upload cap (10 MB default) |
| `WORKER_*` | worker | Poll, lock timeout, retries, backoff, heartbeat |
| `SPOOL_*_RETENTION_DAYS` | worker | Processed/failed spool retention |
| `INGEST_DOCS_ENABLED` | ingest API | Interactive docs (off by default) |

Expected current-export totals (measured against `OneDrive_1_11-9-2026`):

| Metric | Expected |
|---|---|
| Compensated files | 46 |
| Uncompensated files | 101 |
| Log records | 173,508 |
| Unique samples `(serial, ts)` | 113,177 |
| Series readings `(serial, channel, ts)` | 233,855 |
| Empty files | 1 |
| Anomalous files | 13 |
| Fully redundant files | 31 |

## Tests

```
python -m pytest -q
```

The full-export test is marked `integration` and is skipped automatically when
`OneDrive_1_11-9-2026/` is not present. The database tests are also marked `integration`
and are skipped unless `TRANSDUCER_TEST_DSN` points at a disposable PostgreSQL database
with the Phase 1 schema and `ingest` migration applied. They roll back or clean up their
rows, but never point them at production.

## Design reference

See [`../docs/ingestion.md`](../docs/ingestion.md) for selection rules, validation,
deduplication, the gateway flow, and the manual upload workflow.
