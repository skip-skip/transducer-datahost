# Roadmap and Acceptance Criteria

The repository is currently at **Phase 4 (VM deployment) pending verification**. Phases 0–3
and the application services are implemented and unit-tested locally. The Phase 1 SQL has
been verified against an embedded PostgreSQL 16.2; Docker/Compose verification on the
Linux VM, the TLS certificate, and the production backfill remain.

## Phase 0 — Documentation and skeleton (complete)

- [x] Architecture summary
- [x] Data model
- [x] Ingestion design
- [x] Network and security design
- [x] Architecture decisions
- [x] Repository directory skeleton and configuration templates
- [x] Git repository initialized with a GitHub remote

## Phase 1 — Schema and local database (complete locally)

**Deliverables**

- [x] `db/init/01_roles.sql`: `transducer_upload`, `transducer_ingest`, `transducer_read`,
  SCRAM passwords, pinned `search_path`.
- [x] `db/init/02_schema.sql`: schema, tables, constraints, indexes.
- [x] `db/init/03_views.sql`: `v_reading`, `v_reading_utc`, `v_import`.
- [x] `db/init/04_grants.sql`: least-privilege grants.
- [x] `db/init/05_ingest.sql`: `ingest.job`, `ingest.worker_heartbeat`,
  `ingest.file_known()`.
- [x] `db/migrations/0001_ingest.sql`: idempotent upgrade for existing databases.
- [x] `db/tests/verify_phase1.sh`: acceptance checks for all roles and the queue.
- [x] `db/tests/run_phase1.sh`: runs the acceptance checks inside the Compose `db` service.
- [x] Compose `db` service passes role passwords, forces SCRAM, uses a TCP healthcheck, and
  publishes no port.
- [ ] `docker compose up -d --build` verified healthy on the Linux VM.

**Exit criteria**

- [x] Schema applies cleanly to an empty database (verified on embedded PostgreSQL 16.2).
- [x] Roles authenticate with `scram-sha-256` (verified).
- [x] Read role cannot write; write role cannot alter schema (verified).
- [x] Upload role is confined to `ingest.job` (verified).
- [ ] Re-init and migration paths verified on the VM.

## Phase 2 — Parser and validation (complete)

**Deliverables**

- [x] `ingest/transducer_ingest/parse_xle.py`
- [x] `ingest/transducer_ingest/validate.py`
- [x] Unit tests and small committed fixtures (excluding the full export).
- [x] A parser report that runs without a database.

**Exit criteria**

- [x] Parses both `DataGrabber V3.001` and `Version 4.7.0` layouts.
- [x] Handles 2- and 3-channel files and kPa barometers.
- [x] Preserves `ms` precision.
- [x] Detects compensated files by the `<Compensation_info>` metadata block with zero false
  positives; filenames are only a consistency check.
- [x] Reproduces the expected totals: 101 uncompensated files, 173,508 log records, 113,177
  unique samples, 233,855 series readings, 1 empty file, 13 anomalies, 31 fully redundant
  files.

## Phase 3 — Loader and deduplication (complete)

**Deliverables**

- [x] `ingest/transducer_ingest/load.py` with staging `COPY` + upsert.
- [x] `ingest/transducer_ingest/db.py` for role-aware connection configuration.
- [x] `ingest/transducer_ingest/cli.py` with `ingest`, `upload`, `report`, `--dry-run`,
  `--since`, `--json`, and `--all`.
- [x] `scripts/run_ingest.ps1` retained for direct development loads.

**Exit criteria**

- [x] Dry run writes nothing and reports expected counts.
- [x] Full backfill inserts 233,855 `reading` rows covering 113,177 unique samples and 0
  compensated rows.
- [x] Re-running inserts 0 rows.
- [x] 31 fully redundant files contribute zero new readings; 13 anomalous files recorded; 1
  empty file recorded.
- [x] A failed file rolls back without affecting other files.
- [x] 29 byte-identical files are skipped at the file level; 118 `import_file` audit rows are
  written on a fresh full load (45 of them `skipped_compensated`).

## Phase 4 — VM-hosted ingest gateway (implemented; VM verification pending)

**Deliverables**

- [x] `ingest/transducer_ingest/service/`: settings, bearer auth, spool, jobs, upload API,
  worker, healthcheck, and migration runner.
- [x] `ingest/transducer_ingest/upload.py`: HTTPS uploader with settle/completeness checks,
  hashing, `HEAD`, retries, and optional wait.
- [x] `scripts/upload_xle.ps1`: manual Windows wrapper with a mutex and daily log.
- [x] `ingest/Dockerfile` for the API, worker, and migrate service.
- [x] `caddy/` TLS reverse proxy and `certs/` instructions.
- [x] Compose stack: `db`, `migrate`, `ingest-api`, `ingest-worker`, `read-api`, `caddy`;
  only Caddy publishes a port.
- [x] Postgres-backed queue with `FOR UPDATE SKIP LOCKED`, stale-lock recovery, retries, and
  a heartbeat.
- [x] Unit tests for settings, auth, spool, uploader, queue helpers, and the API.
- [ ] Caddy starts with a valid certificate on the VM.
- [ ] Upload → queue → worker → database flow verified end-to-end on the VM.
- [ ] Worker crash/restart recovery verified on the VM.

**Exit criteria**

- [ ] `docker compose up -d --build` brings the stack up healthy.
- [ ] `db/tests/run_phase1.sh` passes inside the Compose project.
- [ ] `curl` to `/api/loggers` without a token returns 401; with a read token returns data.
- [ ] `POST /ingest/v1/uploads` without a token returns 401; with an ingest token enqueues a
  job.
- [ ] PostgreSQL is unreachable from the host (`psql -h <vm> -p 5432` fails).

## Phase 5 — Backfill and reconciliation

**Deliverables**

- Full upload and import of the current OneDrive export.
- Reconciliation report by status, logger, and location.
- Review notes for the 13 anomalous files and location mismatches.

**Exit criteria**

- Acceptance totals from Phase 3 confirmed in production.
- No compensated rows.
- Known anomalies documented; none block dashboard use.
- Re-running the uploader uploads zero new files.

## Phase 6 — Read-only API (implemented; VM verification pending)

**Deliverables**

- [x] `api/transducer_api/`: settings, read-role connection, bearer auth, and app.
- [x] Endpoints: `/health`, `/loggers`, `/locations`, `/series`, `/readings`,
  `/readings/latest`, `/imports`, `/series/{id}/summary`.
- [x] Local and UTC time output via `?tz=local|utc`.
- [x] Dockerfile and Compose service.
- [x] Router-level bearer authentication before any database connection.
- [ ] Verified against production data on the VM.

**Exit criteria**

- [ ] API uses only the read-only role.
- [ ] `tz=local` and `tz=utc` return the same instants with correct offsets.
- [ ] Pagination and maximum time-window limits enforced.
- [ ] CORS limited to configured dashboard origins.
- [ ] Unauthenticated requests cannot open a database connection.

## Phase 7 — Operations

**Deliverables**

- Backups (cron/systemd) and restore procedure.
- Monitoring/alerting for ingest failures, stale worker heartbeat, and disk usage.
- Runbook: start/stop, restore, rotate passwords and tokens, add a new site/logger.
- Manual upload cadence documented for the operator.

**Exit criteria**

- Backup restore tested after go-live.
- Worker heartbeat and failed-job alerts verified.
- Runbook reviewed by the operator.
- Certificate rotation rehearsed.

## Global acceptance criteria (v1)

| Criterion | Target |
|---|---|
| Uncompensated files recorded | 101 |
| Unique samples `(serial, ts)` | 113,177 |
| Reading rows `(serial, channel, ts)` | 233,855 |
| Compensated rows | 0 |
| Fully redundant files (zero new readings) | 31 |
| Anomalous files | 13 |
| Empty files | 1 |
| Re-run inserts | 0 |
| Upload role domain-table access | Denied |
| Read role write attempts | Denied |
| Unauthenticated API requests | 401 |
| PostgreSQL reachable from the LAN | No |
| API local/UTC consistency | Identical instants |

## Note on Git

The repository is initialized locally with a GitHub remote (`transducer-datahost`). Commits
stay local until pushed. Confirm the OneDrive export folder, `.env`, and TLS material remain
ignored before each commit.
