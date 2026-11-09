# Architecture Summary

## 1. Purpose

Provide a single, deduplicated, network-accessible database for uncompensated transducer
logger exports, and a read-only interface for internal dashboards and analysts.

The source of truth remains the OneDrive export folder. The database is a derived,
queryable copy with provenance back to the originating `.xle` file.

**Current status:** Phase 1 (schema, roles, views, and grants) is implemented in
`db/init` and verified against PostgreSQL 16.2. Docker/Compose verification on the Linux
VM is pending. See [docs/roadmap.md](roadmap.md).

## 2. Scope

### In scope (v1)

- Ingest all uncompensated `.xle` files from the export folder.
- Preserve native timestamps and sample intervals.
- Deduplicate repeated files and overlapping logger downloads.
- Track every imported file and its validation status.
- Expose data through PostgreSQL (read-only role) and a FastAPI read-only service.
- Support local-time and UTC representations of timestamps.

### Out of scope (v1)

- Compensated exports and barometric compensation math.
- Dashboard user interface.
- Write access for dashboards.
- Public internet exposure, SSO, or per-user authorization.
- Real-time streaming; ingestion is batch/scheduled.

## 3. System context

```
+-----------------------------+
| OneDrive export folder      |
| (Windows host, .xle files)  |
+--------------+--------------+
               |
               | file discovery + parse
               v
+-----------------------------+          TCP 5432          +-----------------------------+
| Ingester                    | ------------------------> | PostgreSQL 16               |
| Python CLI                  |   write role              | Linux VM, Docker            |
| - parse .xle                |                           | schema: transducer          |
| - validate                  |                           |                             |
| - deduplicate               |                           |                             |
| - upsert readings           |                           |                             |
+-----------------------------+                           +--------------+--------------+
                                                                        ^
                                                                        | TCP 5432
                                                                        | read-only role
                                                          +-------------+--------------+
                                                          | FastAPI read-only service  |
                                                          | Linux VM, Docker, :8000    |
                                                          +-------------+--------------+
                                                                        ^
                                                                        | HTTP(S)
                                                          +-------------+--------------+
                                                          | Dashboards / analysts      |
                                                          | internal network           |
                                                          +----------------------------+
```

## 4. Components

| Component | Responsibility | Runtime |
|---|---|---|
| Source export | Authoritative `.xle` files delivered via OneDrive | Windows host |
| Ingester | Discover, parse, validate, deduplicate, and upsert uncompensated data; record file provenance | Windows host (where OneDrive syncs), scheduled |
| PostgreSQL | Durable store, deduplication keys, query surface, audit trail | Linux VM, Docker |
| FastAPI service | Read-only REST access with local/UTC time handling; hides DB credentials from dashboards | Linux VM, Docker |
| Consumers | Analysts (SQL via read-only role) and dashboards (HTTP API) | Internal network |

## 5. Data flow

1. A new `.xle` export lands in the OneDrive folder.
2. The scheduled ingester scans for files that are complete and not yet imported.
3. Each file is parsed into logger metadata, channel metadata, and timestamped readings.
4. Validation classifies the file as imported, warning, empty, error, or
   skipped_compensated (compensated files are audited but contribute no readings).
5. The `import_file` audit row is inserted first, then readings are staged and upserted
   into `transducer.reading`, keyed by `(series_id, ts)`. Overlapping downloads are
   resolved in favor of the newest download.
6. The file's final parsed count and status are recorded in `transducer.import_file`.
7. Consumers read through the API or the read-only Postgres role.

## 6. Deployment topology

### Linux VM

- Docker Engine + Compose.
- `db` container: PostgreSQL 16 with a named volume; `db/init` creates the schema,
  roles, views, and grants on first start. Authentication is SCRAM-only.
- `api` container: FastAPI, read-only DB role.
- Firewall exposes 5432 and 8000 only to the internal subnet.
- Nightly `pg_dump` to a network share.

### Windows host

- OneDrive syncs the export folder.
- The ingester runs on a schedule (Task Scheduler) and connects to the VM over the LAN.
- This avoids mounting OneDrive on Linux and keeps the writer close to the source.

### Why the ingester is separate from the VM

The source lives in OneDrive on Windows. Running the writer where the files already are
removes the need for a OneDrive-on-Linux bridge. The VM only needs to accept database
connections from the ingest host.

## 7. Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Database | PostgreSQL 16 | Free, robust, strong concurrency, easy network access, good time-series support without extensions |
| Packaging | Docker Compose | Reproducible deployment on the VM |
| Ingester | Python 3.11+ | Good XML support, simple scheduling, easy testing |
| XML parsing | `defusedxml` / `lxml` | Safe parsing of external files |
| DB driver | `psycopg` 3 | Fast bulk `COPY`, modern API |
| API | FastAPI | Typed endpoints, OpenAPI docs, easy read-only surface |
| Time zone handling | Postgres `AT TIME ZONE` | Correct local/UTC conversion at query time |
| Scheduling | Windows Task Scheduler (ingest), cron/systemd (backup) | Uses existing platform tooling |

No TimescaleDB extension is required at the current data volume (~113k unique readings).
A BRIN index on `ts` is sufficient. TimescaleDB can be added later without schema changes.

## 8. Source data profile

| Attribute | Observation |
|---|---|
| File count | 147 `.xle` files: 101 uncompensated, 46 compensated |
| Format | XML (`<Body_xle>`), no namespace, Windows-1252 encoding |
| Generations | `DataGrabber V3.001` (compact XML) and `Version 4.7.0` (indented, extra fields) |
| Channels | `LEVEL`, `TEMPERATURE`, optional `CONDUCTIVITY`; barometers report level in kPa |
| Long-term interval | 4 hours |
| Aquifer-test (`_AT_`) interval | 2 Hz (`ms` = 0 / 500) |
| Uncompensated rows | 173,508 |
| Unique rows `(serial, timestamp, ms)` | 113,177 |
| Fully redundant files | 34 |
| Anomalous files | 13 files with header/data anomalies (12 `Stop_time < Start_time`, one of which also has zero logs; 1 `Num_log` mismatch) |
| Compensated marker | `<Compensation_info>` metadata block in the body (authoritative); filename `compensat` is a consistency check only |

## 9. Design principles

1. **Idempotent ingestion.** Re-running the ingester must not create duplicates.
2. **Provenance.** Every reading points to the file that produced it.
3. **Native resolution.** Never resample during ingest; aggregation belongs in views/queries.
4. **Narrow series model.** One row per logger/channel/unit keeps 2- and 3-channel loggers,
   unit changes, and future compensated series unambiguous.
5. **Write/read separation.** Only the ingester writes; consumers use a read-only role or API.
6. **Compensated-ready, not compensated-enabled.** `data_kind` allows a future compensated
   series to coexist with raw data without key collisions.

## 10. Non-goals

- No compensation calculations, barometric correction, or data editing.
- No UI in this repository.
- No public endpoints.
- No streaming or event-driven ingest.

## 11. Assumptions and constraints

- The OneDrive export folder is the authoritative source.
- Files carry no timezone offset; timestamps are logger local time.
- All sites share one timezone unless a per-location override is added.
- The database is reachable only from the internal network.
- A Linux VM with Docker is available.
- Data volume is small enough for a single Postgres instance.

## 12. Known risks

| Risk | Mitigation |
|---|---|
| Partial OneDrive sync | Only import files whose content ends with `</Body_xle>`; honor a settle-time delay |
| File locked during download | Retry with backoff; skip and retry next run |
| Filename/header location mismatch | Store the header location and flag mismatches in `import_file` |
| Logger moved mid-file | Header location may mislabel older rows; flag location changes per serial for review |
| DST ambiguity in naive local time | Document the fall-back hour; conversion follows Postgres rules |
| Source format drift | Parser is tolerant of both known generations; validation surfaces unknown shapes |
