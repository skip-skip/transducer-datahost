# Roadmap and Acceptance Criteria

The repository is currently at **Phase 3 (loader and deduplication)**. Phases 0–2 are
complete. The Phase 1 SQL and acceptance script are implemented and verified against an
embedded PostgreSQL 16.2, and Phase 3 has been verified against the same embedded server.
Docker/Compose verification on the Linux VM is pending.

## Phase 0 — Documentation and skeleton (complete)

- [x] Architecture summary
- [x] Data model
- [x] Ingestion design
- [x] Network and security design
- [x] Architecture decisions
- [x] Repository directory skeleton and configuration templates
- [x] Git repository initialized (local only; no remote configured)

## Phase 1 — Schema and local database

**Deliverables**

- [x] `db/init/01_roles.sql`: `transducer_ingest`, `transducer_read`, SCRAM passwords.
- [x] `db/init/02_schema.sql`: schema, tables, constraints, indexes.
- [x] `db/init/03_views.sql`: `v_reading`, `v_reading_utc`, `v_import`.
- [x] `db/init/04_grants.sql`: least-privilege grants.
- [x] `db/tests/verify_phase1.sh`: acceptance checks.
- [x] Compose `db` service passes role passwords, forces SCRAM, and uses a TCP healthcheck.
- [ ] `docker compose up -d db` verified healthy on the Linux VM.

**Exit criteria**

- [x] Schema applies cleanly to an empty database (verified on embedded PostgreSQL 16.2).
- [x] Roles authenticate with `scram-sha-256` (verified).
- [x] Read role cannot write; write role cannot alter schema (verified).
- [ ] `docker compose down -v` / re-init cycle verified on the VM.

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
- [x] `ingest/transducer_ingest/db.py` for `PG*` connection configuration.
- [x] `ingest/transducer_ingest/cli.py` with `ingest`, `--dry-run`, `--since`, `--json`,
  `--all`, and `report`.
- [x] `scripts/run_ingest.ps1` for Task Scheduler.

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

## Phase 4 — Infrastructure and network access

**Deliverables**

- Linux VM with Docker Engine and Compose.
- Production `.env` on the VM and ingest host.
- Firewall rules scoped to the internal subnet.
- Nightly `pg_dump` job and a documented restore procedure.

**Exit criteria**

- Ingest host connects on 5432.
- Another internal host can query with the read-only role.
- Connections from outside the allowed subnet are blocked.
- A restore from backup succeeds in a scratch database.

## Phase 5 — Backfill and reconciliation

**Deliverables**

- Full import of the current OneDrive export.
- Reconciliation report by status, logger, and location.
- Review notes for the 13 anomalous files and location mismatches.

**Exit criteria**

- Acceptance totals from Phase 3 confirmed in production.
- No compensated rows.
- Known anomalies documented; none block dashboard use.

## Phase 6 — Read-only API

**Deliverables**

- `api/app.py` with FastAPI and parameterized queries.
- Endpoints: `/health`, `/loggers`, `/locations`, `/series`, `/readings`,
  `/readings/latest`, `/imports`, optional `/series/{id}/summary`.
- Local and UTC time output via `?tz=local|utc`.
- Dockerfile and compose service.

**Exit criteria**

- API uses only the read-only role.
- `tz=local` and `tz=utc` return the same instants with correct offsets.
- Pagination and maximum time-window limits enforced.
- CORS limited to configured dashboard origins.

## Phase 7 — Operations

**Deliverables**

- Scheduled ingest (Task Scheduler) and backups (cron/systemd).
- Monitoring/alerting for ingest failures, stale data, and disk usage.
- Runbook: start/stop, restore, rotate passwords, add a new site/logger.

**Exit criteria**

- 30 consecutive days of successful scheduled ingest.
- Backup restore tested after go-live.
- Runbook reviewed by the operator.

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
| Read role write attempts | Denied |
| Out-of-subnet connections | Blocked |
| API local/UTC consistency | Identical instants |

## Note on Git

The repository is initialized locally with no remote configured. Commits stay local
until a remote is provided. Confirm the OneDrive export folder and `.zip` remain ignored
before each commit.
