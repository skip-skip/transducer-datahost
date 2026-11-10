# Architecture Decisions

Short ADR-style records for decisions made during planning. Each records context, decision,
and consequences.

---

## ADR-001: Use PostgreSQL 16

**Status:** Accepted

**Context:** The dataset is small (~113k unique readings now), but needs concurrent read
access from internal dashboards and analysts, durable storage, and straightforward backup.

**Decision:** Use PostgreSQL 16, deployed in Docker Compose.

**Consequences:**

- Free and well understood; strong concurrency and SQL.
- No dependency on proprietary licensing or Windows-only tooling.
- A TimescaleDB extension is not required at current volume; a BRIN index suffices.
- Requires a Linux VM and Docker operations capability.

---

## ADR-002: Host the database on a new Linux VM

**Status:** Accepted

**Context:** The database must be reachable on the internal network and independent of any
individual workstation.

**Decision:** Provision a dedicated Linux VM for Postgres, the API, and backups.

**Consequences:**

- Stable address and uptime; workstation reboots do not affect consumers.
- The ingester still runs on the Windows host where OneDrive syncs.
- Requires VM provisioning, patching, and firewall changes by IT.

---

## ADR-003: Store native sample intervals

**Status:** Accepted

**Context:** Long-term logger files are 4-hourly; aquifer-test files are 2 Hz. There is no
hourly data in the export.

**Decision:** Store timestamps at native resolution in `timestamp(3)`, including the `ms`
field. Do not resample during ingest.

**Consequences:**

- No loss of aquifer-test detail.
- Dashboards can aggregate to hourly/daily in views or queries.
- Timestamp precision must include milliseconds to avoid collapsing 2 Hz samples.

---

## ADR-004: Ingest all uncompensated files, exclude compensated

**Status:** Accepted

**Context:** The export contains 101 uncompensated and 46 compensated files. Compensated
files share serials/channels but contain different values and a `<Compensation_info>`
metadata block. Filenames also contain `Compensated`, but naming is a convention, not a
guarantee.

**Decision:** v1 ingests only uncompensated files, detected by the presence of the
`<Compensation_info>` metadata block in the file body. The filename `Compensated` token
is used only as a consistency check and never to include or exclude a file. If the filename
and metadata disagree, metadata wins and the discrepancy is recorded.

**Consequences:**

- Detection does not depend on filenames, so renamed exports are handled correctly.
- The metadata block separates all 46 compensated files from all 101 uncompensated files
  in the current export, with zero mismatches.
- Numeric values are not used: they are not a reliable signal (36 of 46 compensated files
  have no negative value; one uncompensated file has all `abs(ch1) < 1`).
- Schema includes `data_kind` so compensated data can be added later without key changes.

---

## ADR-005: Store timestamps as naive local time

**Status:** Accepted

**Context:** `.xle` files contain no timezone offset. Storing `timestamptz` would require
inventing an offset and could silently shift data across DST boundaries.

**Decision:** Store `reading.ts` as `timestamp(3) without time zone`, exactly the logger
clock. Record the interpretation zone in `location.timezone` (default `SITE_TIMEZONE`,
`America/New_York`). The API converts to UTC on request.

**Consequences:**

- No invented offsets at ingest; raw data is faithful.
- The API must apply `AT TIME ZONE` for UTC output.
- The DST fall-back hour is ambiguous; documented rather than corrected.
- Consumers must specify or understand the zone for cross-site comparisons.

---

## ADR-006: Attribute location per file header

**Status:** Accepted

**Context:** Filenames and `<Location>` headers sometimes disagree, and the same serial can
appear at multiple locations (aquifer tests, re-deployments).

**Decision:** Store the header `<Location>` on `import_file`; readings inherit location via
provenance. Record filename/header mismatches and per-serial location changes as warnings
for review.

**Consequences:**

- Simple and faithful to each download.
- A file spanning a logger move may mislabel older rows; surfaced for review.
- A curated deployment table can be added later without changing the reading key.

---

## ADR-007: Newest download wins on overlap

**Status:** Accepted

**Context:** Each `.xle` download contains the logger's full memory, so files overlap
heavily. Values for the same timestamp can occasionally differ between downloads.

**Decision:** Process files oldest-to-newest by `download_time`; upsert readings with
`ON CONFLICT (series_id, ts) DO UPDATE` when the incoming `file_id` is newer.

**Consequences:**

- The most recent download is authoritative.
- Re-running the ingester is idempotent and inserts no duplicates.
- Provenance always points to the winning file.

---

## ADR-008: Read-only FastAPI service

**Status:** Accepted

**Context:** Dashboards should not hold database credentials, and consumers need local/UTC
time options.

**Decision:** Provide a read-only FastAPI service over the `transducer_read` role, exposing
logger/location/series/readings/imports endpoints. Dashboards use HTTP; analysts may use
SQL with the read-only role.

**Consequences:**

- Credentials stay server-side; CORS and pagination are centralized.
- API becomes another service to deploy, monitor, and back up.
- Postgres remains directly available to analysts for ad hoc work.

---

## ADR-009: Run the ingester on the Windows host

**Status:** Accepted

**Context:** The authoritative source is a OneDrive folder on Windows. Running Python on
Linux would require syncing or mounting OneDrive.

**Decision:** Run the Python ingester on the Windows host where OneDrive syncs, connecting
to Postgres on the VM over the LAN. Schedule with Task Scheduler.

**Consequences:**

- No OneDrive-on-Linux bridge required.
- The writer depends on the Windows host being powered on for scheduled runs.
- Only the write role's credentials live on the Windows host.

---

## ADR-010: Narrow series/reading schema

**Status:** Accepted

**Context:** Loggers have 2 or 3 channels, level units vary (`m` vs `kPa`), and future
compensated series will reuse serial/channel identities.

**Decision:** Model `series(serial, channel, identification, unit, data_kind)` and
`reading(series_id, ts, value, file_id)` rather than a wide per-timestamp table.

**Consequences:**

- Uniform handling of missing channels and unit differences.
- Compensated series get distinct `series_id` values without key collisions.
- Slightly more rows and joins; negligible at this volume.

---

## ADR-011: Initialize with ordered SQL scripts and defer migrations

**Status:** Accepted (Phase 1)

**Context:** The database is new and has no production data. The Postgres Docker image
runs `/docker-entrypoint-initdb.d/*.sql` only when the data volume is empty. A migration
tool adds process and dependencies before there is data worth preserving.

**Decision:** Use four ordered init scripts (`01_roles.sql`, `02_schema.sql`,
`03_views.sql`, `04_grants.sql`) for Phase 1. Re-initialize with
`docker compose down -v` while the data is disposable. Adopt a versioned migration tool
before loading production data or making a schema change that must preserve data.

**Consequences:**

- Simple, reproducible first boot with no migration bookkeeping.
- `down -v` destroys the database; it is only acceptable before production data exists.
- Future schema changes require a real migration path; this ADR will be superseded.

---

## ADR-012: Curated read views

**Status:** Accepted

**Context:** The read role needs friendly joins and audit/status information (including
parse errors), but should not read `import_file` directly or depend on the base-table
shape.

**Decision:** Expose `v_reading`, `v_reading_utc`, and `v_import` as security-definer
views owned by the admin role. Grant `transducer_read` `SELECT` on the views and on
`logger`, `location`, `series`, and `reading`; deny direct `import_file` access.

**Consequences:**

- The read surface is explicit and stable; the API and analysts share it.
- `v_import` exposes `error` details without granting table access.
- Views run with owner privileges, so their definitions must stay under review and be
  re-granted deliberately when columns are added.

---

## ADR-013: Decode .xle files as UTF-8 first, cp1252 fallback

**Status:** Accepted (Phase 2)

**Context:** The current export is UTF-8 without a BOM, while older files and the committed
test fixtures use Windows-1252. cp1252 accepts every byte sequence, so trying it first
silently mis-decodes UTF-8 files: the UTF-8 bytes `c2 b0` for `°` become `Â°`. This made
every channel unit appear unknown and inflated the anomaly count.

**Decision:** Honor a UTF-8 BOM when present; otherwise attempt UTF-8 first and fall back to
cp1252 only when UTF-8 decoding fails. Strip the XML declaration before parsing because it
names an encoding that no longer matches the decoded string.

**Consequences:**

- Both the current UTF-8 export and older cp1252 files parse with correct units.
- A cp1252 file whose bytes happen to form valid UTF-8 could be mis-decoded, but no such
  file exists in the export and the risk is accepted.
- Unknown units are still imported with a warning rather than rejected.

---

## ADR-014: Report metrics and redundancy ordering

**Status:** Accepted (Phase 2)

**Context:** "Rows", "readings", and "samples" were used loosely, and the documented
redundant-file count (34) did not match the implementation.

**Decision:** Define the report metrics explicitly:

- **Log records:** count of `<Log>` entries, including the raw `<Log` count from malformed
  files (1,820 for the corrupt `_jh` copy).
- **Unique samples:** distinct `(serial, ts)` pairs.
- **Series readings:** distinct `(serial, channel, ts)` triples, which become Phase 3
  `reading` rows.

Files are ordered by `download_time` then filename, and a file is fully redundant when all
of its `(serial, channel, ts)` keys were already seen in earlier files. Location-only
mismatches are reported separately and are not counted as anomalies.

**Consequences:**

- The current export reconciles exactly: 173,508 log records, 113,177 unique samples,
  233,855 series readings, 13 anomalies, 31 fully redundant files, 1 empty file.
- The malformed file's raw logs are still accounted for even though it contributes no rows.
- Metric definitions are stable for Phase 3 acceptance tests.

---

## ADR-015: Database-free parser report

**Status:** Accepted (Phase 2)

**Context:** Parsing and validation need to be verified before PostgreSQL is available, and
the report must not depend on the loader.

**Decision:** Provide `python -m transducer_ingest.report` with `--source`, `--json`,
`--check`, and `--all`. It imports only `parse_xle` and `validate`; no database driver is
imported. `--check` exits non-zero when the measured totals differ from the expected export.

**Consequences:**

- Parsing can be tested on the ingest host before the VM is reachable.
- The same `validate.py` aggregation will feed the Phase 3 dry-run report.
- A test asserts that importing the report module does not import `psycopg`.

---

## ADR-016: Duplicate files are report-only

**Status:** Accepted (Phase 3)

**Context:** The export contains byte-identical copies (`- Copy`, `(2)`, `_jh`, and a
`REFERENCE BARO_` duplicate). `import_file.sha256` is unique, so a second audit row cannot be
written for an identical file. Two kinds of redundancy exist: exact file duplicates and
files whose readings were already seen in earlier downloads.

**Decision:** A duplicate `sha256` is counted and shown in the run summary but writes no new
`import_file` row. Redundancy is reported using the same `(serial, channel, ts)` subset rule
as the database-free report, so the loader reports 29 duplicate files and 31 fully redundant
files on the current export. A file that is both is counted in both metrics.

**Consequences:**

- `import_file` stays a one-row-per-content audit log (118 rows on a fresh full load).
- The loader and Phase 2 report agree on the 31 redundant files.
- Re-running the full export inserts zero rows and reports 147 duplicates.

---

## ADR-017: Database integration tests are opt-in

**Status:** Accepted (Phase 3)

**Context:** The loader must be exercised against real PostgreSQL, but CI and developer
machines do not always have a database. The database test truncates the transducer tables,
so it must never run against production.

**Decision:** Mark database tests `integration` and skip them unless `TRANSDUCER_TEST_DSN`
is set. The DSN must point at a disposable database with the Phase 1 schema applied. Pure
loader helpers (`parse_since`, `derive_location`, `prepare_files`) are tested without a
database.

**Consequences:**

- `pytest` passes with no database configured.
- The full SQL path is verified against an embedded PostgreSQL server during development.
- Pointing `TRANSDUCER_TEST_DSN` at production would destroy data; the variable name and
  README warning make the risk explicit.

## ADR-018: Use canonical `POSTGRES_*` environment variables

**Status:** Accepted (Phase 3)

**Context:** The Compose database reads `POSTGRES_PASSWORD`, but the acceptance script and
loader originally used libpq's `PG*` names. Operators therefore had to set two different
password variables, and `PGPASSWORD` was not actually read by the Postgres container.

**Decision:** Make `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD` the canonical admin/provisioning settings. Role-specific variables stay
separate: the loader reads `INGEST_USER` / `INGEST_PASSWORD`, and the API reads `READ_USER`
/ `READ_PASSWORD`. `PGPASSWORD` remains only an internal detail passed to `psql` child
processes. The acceptance script still accepts the legacy `PG*` names as fallbacks.

**Consequences:**

- Operators set one password variable per role and do not need to know libpq internals.
- The loader never needs the admin password, preserving least privilege.
- Tools that connect through libpq directly (for example, an ad hoc `psql`) must still use
  `PGPASSWORD` or a connection string; this is a client requirement, not a repository one.
- `db/tests/run_phase1.sh` runs the acceptance checks inside the Compose database service,
  so the host does not need `psql` or a sourced `.env`.
