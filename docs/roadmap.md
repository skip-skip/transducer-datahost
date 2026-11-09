# Roadmap and Acceptance Criteria

The repository is currently at **Phase 0 (skeleton)**. Nothing below is implemented yet.

## Phase 0 — Documentation and skeleton (current)

- [x] Architecture summary
- [x] Data model
- [x] Ingestion design
- [x] Network and security design
- [x] Architecture decisions
- [x] Repository directory skeleton and configuration templates
- [ ] Git repository initialized (optional; see note at end)

## Phase 1 — Schema and local database

**Deliverables**

- `db/init/01_schema.sql`: schema, tables, constraints, indexes.
- `db/init/02_roles.sql`: `transducer_ingest`, `transducer_read`, grants.
- `db/init/03_views.sql`: `v_reading` and optional UTC view.
- Runnable `docker compose up db` with a healthy Postgres.

**Exit criteria**

- Schema applies cleanly to an empty volume.
- Roles authenticate with `scram-sha-256`.
- Read role cannot write; write role cannot alter schema.

## Phase 2 — Parser and validation

**Deliverables**

- `ingest/transducer_ingest/parse_xle.py`
- `ingest/transducer_ingest/validate.py`
- Unit tests and small committed fixtures (excluding the full export).
- A parser report that runs without a database.

**Exit criteria**

- Parses both `DataGrabber V3.001` and `Version 4.7.0` layouts.
- Handles 2- and 3-channel files and kPa barometers.
- Preserves `ms` precision.
- Detects compensated files by the `<Compensation_info>` metadata block with zero false
  positives; filenames are only a consistency check.
- Reproduces the expected totals: 101 uncompensated files, 173,508 rows, 113,177 unique
  readings.

## Phase 3 — Loader and deduplication

**Deliverables**

- `ingest/transducer_ingest/load.py` with staging `COPY` + upsert.
- `ingest/transducer_ingest/cli.py` with `ingest`, `--dry-run`, and `report`.
- `scripts/run_ingest.ps1` for Task Scheduler.

**Exit criteria**

- Dry run writes nothing and reports expected counts.
- Full backfill inserts 113,177 unique readings and 0 compensated rows.
- Re-running inserts 0 rows.
- 34 fully redundant files contribute zero new readings; 13 warning files recorded; 1 empty file recorded.
- A failed file rolls back without affecting other files.

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
| Unique readings | 113,177 |
| Compensated rows | 0 |
| Fully redundant files (zero new readings) | 34 |
| Warning files | 13 |
| Empty files | 1 |
| Re-run inserts | 0 |
| Read role write attempts | Denied |
| Out-of-subnet connections | Blocked |
| API local/UTC consistency | Identical instants |

## Note on Git

This directory was not a Git repository when the skeleton was created. Initialize with
`git init` when ready, then confirm the OneDrive export folder and `.zip` remain ignored
before the first commit.
