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

## ADR-002: Host the database and ingestion on a dedicated Linux VM

**Status:** Accepted (amended by ADR-019)

**Context:** The database must be reachable on the internal network and independent of any
individual workstation. Parsing and loading large files is cheaper next to the database
than across the LAN.

**Decision:** Provision a dedicated Linux VM for PostgreSQL, the ingest gateway, the read
API, and backups. The Windows host keeps the OneDrive folder and runs only the HTTPS
uploader.

**Consequences:**

- Stable address and uptime; workstation reboots do not affect consumers.
- PostgreSQL is never published; all access goes through Caddy and the APIs.
- Requires VM provisioning, patching, certificate management, and firewall changes by IT.
- The Windows host no longer needs a database credential.

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

**Status:** Accepted (amended by ADR-023)

**Context:** Dashboards should not hold database credentials, and consumers need local/UTC
time options. Analysts should not connect to PostgreSQL directly.

**Decision:** Provide a read-only FastAPI service over the `transducer_read` role, exposing
logger/location/series/readings/imports endpoints. All consumers use HTTP with a bearer
token; PostgreSQL is not published.

**Consequences:**

- Credentials stay server-side; CORS and pagination are centralized.
- API becomes another service to deploy, monitor, and back up.
- Analysts no longer get ad hoc SQL; new questions are answered by extending the API.
- A separate ingest API shares the same TLS front door but has its own tokens and role.

---

## ADR-009: Run the ingester on the Windows host

**Status:** Superseded by ADR-019

**Context:** The authoritative source is a OneDrive folder on Windows. Running Python on
Linux would require syncing or mounting OneDrive.

**Decision (historical):** Run the Python ingester on the Windows host where OneDrive
syncs, connecting to Postgres on the VM over the LAN. Schedule with Task Scheduler.

**Consequences:**

- No OneDrive-on-Linux bridge required.
- The writer depends on the Windows host being powered on for scheduled runs.
- Only the write role's credentials live on the Windows host.

This was replaced because it required publishing PostgreSQL and placed the write credential
on a workstation. ADR-019 keeps OneDrive on Windows but moves parsing/loading to the VM.

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

**Status:** Superseded by ADR-022

**Context:** The database is new and has no production data. The Postgres Docker image
runs `/docker-entrypoint-initdb.d/*.sql` only when the data volume is empty. A migration
tool adds process and dependencies before there is data worth preserving.

**Decision (historical):** Use ordered init scripts (`01_roles.sql`, `02_schema.sql`,
`03_views.sql`, `04_grants.sql`) for Phase 1. Re-initialize with `docker compose down -v`
while the data is disposable. Adopt a versioned migration tool before loading production
data or making a schema change that must preserve data.

**Consequences:**

- Simple, reproducible first boot with no migration bookkeeping.
- `down -v` destroys the database; it is only acceptable before production data exists.
- Future schema changes require a real migration path; this ADR was superseded before
  production data was loaded.

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

---

## ADR-019: Move the parser and loader onto the VM behind an upload API

**Status:** Accepted (Phase 4)

**Context:** The Windows ingester required a published PostgreSQL port and put the write
credential on a workstation. It also pulled each file over the LAN only to parse it and
push rows back. The source folder must stay on Windows because OneDrive syncs there.

**Decision:** Keep OneDrive on the Windows host and add a small HTTPS uploader. The upload
API, queue worker, parser, loader, and PostgreSQL all run on the VM. The uploader sends
files with a bearer token and never receives a database credential. Uploads are manual in
v1; Task Scheduler is not used.

**Consequences:**

- PostgreSQL is not published; the VM exposes only Caddy on 8443.
- Parsing and bulk `COPY` stay on the same host as the database.
- The Windows host holds only an ingest API URL and token.
- Uploads depend on an operator running the script (accepted for v1).
- The ingest API and worker are additional services to monitor.

---

## ADR-020: TLS plus static bearer tokens, no mTLS

**Status:** Accepted (Phase 4)

**Context:** The upload and read APIs need authentication over the internal network without
requiring client certificates on every Windows workstation.

**Decision:** Caddy terminates TLS with a certificate for `INGEST_HOSTNAME`. Both APIs
require static bearer tokens configured as comma-separated `label:token` pairs. The token
label is recorded for audit. Tokens are compared with `secrets.compare_digest`.

**Consequences:**

- Simple client setup: one token per upload client or analyst, revocable independently.
- TLS is mandatory; there is no plaintext listener.
- Tokens are shared secrets, so distribution and rotation need care.
- mTLS remains an option later without changing the API surface.

---

## ADR-021: Use `ingest.job` as a Postgres-backed queue

**Status:** Accepted (Phase 4)

**Context:** Uploads arrive faster than the single writer should process them, and a worker
crash must not lose an accepted file. Adding a broker (Redis, RabbitMQ) increases
operational surface.

**Decision:** Store jobs in `ingest.job` and claim them with
`SELECT ... FOR UPDATE SKIP LOCKED`. Keep the spool file under a content-addressed name and
reconcile spool and queue on worker start. Record retries with `attempt_count`,
`next_attempt_at`, and `locked_at`; requeue stale locks after a timeout.

**Consequences:**

- No extra infrastructure; jobs and domain writes share PostgreSQL durability.
- A single worker preserves the proven single-writer loader.
- Queue state is queryable for operations and the uploader's wait loop.
- High throughput would eventually need multiple workers or a broker; not a v1 concern.

---

## ADR-022: Versioned SQL migrations plus a role reconciler

**Status:** Accepted (Phase 4)

**Context:** `db/init` only runs on an empty volume, but the new `ingest` schema and roles
must also reach an existing database without `down -v`.

**Decision:** Add `db/migrations/*.sql` applied by
`transducer_ingest.service.migrate` in filename order. Each file runs in its own
transaction and is recorded with a SHA-256 checksum in `public.schema_migrations`. The same
runner creates or updates `transducer_upload`, `transducer_ingest`, and `transducer_read`
from the environment. Compose runs it as a one-shot `migrate` service on every `up`.

**Consequences:**

- Existing volumes upgrade in place; editing an applied migration is rejected.
- Role passwords can be rotated by rerunning `migrate` with new values.
- The runner needs the admin password; only the one-shot service and operator use it.
- `db/init` and `db/migrations` must be kept consistent for fresh installs and upgrades.

---

## ADR-023: Separate ingest and read APIs with distinct tokens

**Status:** Accepted (Phase 4)

**Context:** Upload clients and analysts have different trust levels and capabilities.
Sharing one API or token list would let an upload client read data and complicate audit.

**Decision:** Run two FastAPI services. The ingest API uses `transducer_upload` and
`INGEST_API_TOKENS`; the read API uses `transducer_read` and `READ_API_TOKENS`. Caddy
routes `/ingest/*` and `/api/*` to the respective services. Analysts use only the read API;
PostgreSQL is not published.

**Consequences:**

- Capabilities and tokens are separated; a leaked upload token cannot read data.
- Audit labels are meaningful per service.
- Two services to deploy and monitor, but they share one TLS front door.
- New analyst questions require extending the read API rather than ad hoc SQL.

---

## ADR-024: Publish Caddy through a non-internal edge network

**Status:** Accepted (Phase 4)

**Context:** Docker does not publish host ports for a container attached only to networks
declared `internal: true` (moby/moby #44986). With Caddy attached only to `backend`,
`HostConfig.PortBindings` was populated but `NetworkSettings.Ports` stayed empty, so 8443
was never bound on the VM and every client saw `connection refused`. The local acceptance
tests did not catch this because they never exercised the published port.

**Decision:** Keep `backend` internal and add a second, non-internal `edge` network that
only Caddy joins. Caddy reaches `ingest-api` and `read-api` over `backend` and publishes
8443 from `edge`. No other service joins `edge` or publishes a port.

**Consequences:**

- `docker compose port caddy 8443` reports `0.0.0.0:8443` and remote clients can connect.
- PostgreSQL and the application services remain isolated on `backend`.
- Caddy has an outbound route through `edge`; it needs no internet access, so host
  firewall rules can still restrict egress if required.
- `edge` must stay non-internal: setting `internal: true` on it silently disables port
  publishing again.

---

## ADR-025: Serve a catch-all HTTPS listener with default SNI

**Status:** Accepted (Phase 4)

**Context:** The site address was `https://{$INGEST_HOSTNAME}:8443`, which adds a Host
matcher and binds certificate selection to that name. Clients that connect to a bare IP
literal do not send SNI (RFC 6066 forbids IP addresses in `server_name`), so Caddy could
not select a certificate and aborted the handshake with `tlsv1 alert internal error`.
Operators naturally test with `curl https://<vm-ip>:8443/ingest/health`, which made a
working gateway look broken.

**Decision:** Use a catch-all `https://:8443` listener with the manual certificate, and set
`default_sni {$INGEST_HOSTNAME}` so no-SNI clients receive that certificate. Routing stays
path-based (`/ingest/*`, `/api/*`); no Host matcher is applied.

**Consequences:**

- Both `https://transducer.internal:8443` and `https://192.168.4.89:8443` work.
- The certificate must include any IP clients use for verification; `curl -k` works
  regardless.
- Host-based access control is not provided by Caddy. Authorization remains the bearer
  token on every API endpoint except `/health`.
- `default_sni` only applies when the client sends no SNI; a client that sends an
  unexpected SNI still fails unless the certificate covers it.

---

## ADR-026: Keep dashboard state as JSON on a volume

**Status:** Accepted (dashboard v1)

**Context:** The dashboard needs to persist figures and UI state, but the existing
timeseries schema must not change and the read role must stay read-only. The options were a
new `dashboard` schema with a dedicated role, new tables granted to `transducer_read`, or a
file on a Docker volume. The operator chose no database schema changes.

**Decision:** Add a separate `dashboard` service with no database credentials. It stores
figures and UI state in `state.json` on the `dashboard_state` volume, protected by a file
lock, written with an fsync and atomic replace, and validated on every load and write.
Figures are scoped as personal (per read-token label) or shared. Authentication reuses the
read API tokens; the browser gets a signed, `HttpOnly`, `Secure`, `SameSite=Strict` cookie.
The service adds only read-only endpoints to the API: `/whoami`, `/series/{id}`, and
`/series/{id}/points`.

**Consequences:**

- No new tables, roles, grants, or migrations; the domain schema and read role are
  untouched.
- The dashboard has no SQL access and cannot read the database if the read API is down.
- State is not SQL-queryable and must be backed up separately from `pg_dump`. The JSON
  shape maps directly to future tables if that changes.
- The session cookie is signed but not encrypted, so it relies on `HttpOnly` and TLS. A
  stolen cookie grants the same access as the underlying read token.
- One uvicorn worker keeps the read-modify-write model simple; the file lock is still used.
- `DASHBOARD_SHARED_EDITORS` optionally restricts shared edits; otherwise any valid read
  token may edit shared figures.

---

## ADR-027: Select dashboard figures by location, not logger

**Status:** Accepted

**Context:** Figures were reached by picking a logger serial ("Source") and then one of
its series. In this dataset loggers move between wells — one serial appears at `MCMW-04`,
`MCDMW-04`, and `MCDMW-04_AT` — and `series` carries no location: a reading's location
comes from its `import_file`. A series-level pull therefore mixed readings recorded at
several locations into one chart.

**Decision:** Bind figures to `(location_id, series_id)` with strict scoping:

- The analyte list for a location shows only series that have readings recorded there
  (`GET /locations/{id}/series`, a new read-only endpoint over `v_reading`).
- Chart points accept an optional `location_id` filter (`/series/{id}/points`), applied
  through `v_reading_utc` so the read role needs no new table grants.
- Saving rejects a series that does not exist and a location/series pair with no
  readings together.
- `location_id` is optional in dashboard state (version stays 1): figures saved before
  this change, or deliberately left un-scoped, chart the whole series, and the location
  selector shows "All locations" until re-saved.
- Analyte option labels include the logger serial, because a location can host more
  than one instrument over time.

**Consequences:**

- Charts answer "what happened at this well" without cross-deployment contamination.
- Two small read-only additions to the API; no schema, role, or write-path changes.
- Legacy figures keep rendering but mix locations until re-bound — acceptable as a
  transition, and re-saving each figure fixes it.
- A location with one logger still shows the serial on every analyte label, which is
  the cost of disambiguation.
- Selecting "All locations" remains possible and behaves like a legacy figure; it is
  offered for continuity rather than recommended for new figures.
