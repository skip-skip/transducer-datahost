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
`<Compensation_info>` metadata block in the file body. The filename `compensat` substring
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
