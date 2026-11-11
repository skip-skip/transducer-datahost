# Architecture Summary

## 1. Purpose

Provide a single, deduplicated, network-accessible database for uncompensated transducer
logger exports, and a read-only interface for internal dashboards and analysts.

The source of truth remains the OneDrive export folder. The database is a derived,
queryable copy with provenance back to the originating `.xle` file.

**Current status:** the schema, parser, loader, VM-hosted ingest gateway, and read API are
implemented. Unit tests pass locally; Docker/Compose verification and the production
backfill are pending on the Linux VM. See [docs/roadmap.md](roadmap.md).

## 2. Scope

### In scope (v1)

- Accept all uncompensated `.xle` files from the export folder via manual HTTPS upload.
- Parse, validate, deduplicate, and load on the VM next to PostgreSQL.
- Preserve native timestamps and sample intervals.
- Deduplicate repeated files and overlapping logger downloads.
- Track every imported file and its validation status.
- Expose data through a bearer-token read-only API.
- Support local-time and UTC representations of timestamps.

### Out of scope (v1)

- Compensated exports and barometric compensation math.
- Dashboard user interface.
- Write access for dashboards.
- Public internet exposure, SSO, or per-user authorization.
- Real-time streaming; ingestion is batch and manually triggered.
- Direct SQL access for analysts.
- Scheduled/automated uploads.

## 3. System context

```
+-----------------------------+
| OneDrive export folder      |
| (Windows host, .xle files)  |
+--------------+--------------+
               |
               | HTTPS + bearer token
               | (manual uploader CLI)
               v
+-----------------------------+          +-----------------------------+
| Caddy TLS front door        |          | PostgreSQL 16               |
| Linux VM, :8443             |          | Linux VM, Docker            |
| /ingest/* -> ingest API     |          | schemas: transducer, ingest |
| /api/*    -> read API       |          | no published port           |
+------+----------------------+          +--------------+--------------+
       |                                                ^
       | internal Compose network                       |
       v                                                |
+-----------------------------+   enqueue / claim / load |
| ingest API + single worker  |-------------------------+
| upload role (queue only)    |    ingest role (domain + queue)
| worker role (domain+queue)  |
+-----------------------------+

+-----------------------------+          +-----------------------------+
| read API (read role)        |--------->| Dashboards / analysts       |
| Linux VM, internal network  |  HTTPS   | internal network            |
+-----------------------------+          +-----------------------------+
```

All inbound traffic enters through Caddy on port 8443. PostgreSQL and both application
containers are on an internal Compose network with no published ports and no route to the
host or the internet. Caddy joins that network and a separate non-internal `edge` network,
used by no other service, because Docker does not publish host ports for a container
attached only to internal networks.

## 4. Components

| Component | Responsibility | Runtime |
|---|---|---|
| Source export | Authoritative `.xle` files delivered via OneDrive | Windows host |
| Uploader CLI | Scan, settle-check, hash, `HEAD`, and `POST` files; poll jobs | Windows host |
| Caddy | Terminate TLS and route `/ingest/*` and `/api/*` | Linux VM, Docker |
| Ingest API | Authenticate uploads, stream to spool, enqueue `ingest.job` | Linux VM, Docker |
| Ingest worker | Claim jobs, parse/validate/load, update job and spool | Linux VM, Docker |
| Migrate | Create/update roles and apply `db/migrations` | Linux VM, Docker (one-shot) |
| PostgreSQL | Durable store, deduplication keys, queue, audit trail | Linux VM, Docker |
| Read API | Read-only REST access with local/UTC time handling | Linux VM, Docker |
| Consumers | Dashboards and analysts using the read API | Internal network |

## 5. Data flow

1. A new `.xle` export lands in the OneDrive folder on Windows.
2. An operator runs the uploader, which scans for files that are complete, past the settle
   window, and not already known by SHA-256.
3. The uploader sends `POST /ingest/v1/uploads` with a bearer token. The ingest API
   streams the body into a spool volume, enforces the size limit and the
   `</Body_xle>` completeness marker, and inserts a row in `ingest.job`.
4. The single worker claims a queued job with `SELECT ... FOR UPDATE SKIP LOCKED`, reads
   the spooled file, and runs the existing parser/validator/loader.
5. Each file is parsed into logger metadata, channel metadata, and timestamped readings.
6. Validation classifies the file as imported, warning, empty, error, or
   skipped_compensated (compensated files are audited but contribute no readings).
7. The `import_file` audit row is inserted first, then readings are staged and upserted
   into `transducer.reading`, keyed by `(series_id, ts)`. Overlapping downloads are
   resolved in favor of the newest download.
8. The job records the final status and `file_id`; the spool file moves to
   `processed/` or `failed/`.
9. Consumers read through the read API with their own bearer token.

## 6. Deployment topology

### Linux VM

- Docker Engine + Compose.
- `db`: PostgreSQL 16 with a named volume. `db/init` creates the schema, roles, views,
  grants, and `ingest` queue on first start. Authentication is SCRAM-only. No published
  port.
- `migrate`: one-shot runner that creates/updates application roles and applies
  `db/migrations`. It runs on every `up` and is idempotent.
- `ingest-api`: FastAPI upload endpoint using `transducer_upload`; reads and writes only
  `ingest.job` and `ingest.worker_heartbeat`.
- `ingest-worker`: single-writer queue worker using `transducer_ingest`; reuses
  `parse_xle`, `validate`, and `load`.
- `read-api`: FastAPI read-only service using `transducer_read`.
- `caddy`: TLS terminator and path router. The only service with a published port, and the
  only member of the non-internal `edge` network.
- Named volumes: `pgdata`, `spool`, `caddy_data`, `caddy_config`.
- Internal Compose network `backend` with `internal: true`, plus the non-internal `edge`
  network for Caddy's published port.
- Nightly `pg_dump` to a network share (planned).

### Windows host

- OneDrive syncs the export folder.
- The operator runs `scripts/upload_xle.ps1` manually.
- The host holds only the ingest API URL and bearer token, never a database password.

### Why ingestion moved onto the VM

Running the parser and loader on the VM keeps large reads, parsing, and bulk `COPY` on the
same host as PostgreSQL. The Windows host only streams files over HTTPS. This also means
the database is never published to the LAN and analysts never connect to PostgreSQL
directly.

## 7. Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Database | PostgreSQL 16 | Free, robust, strong concurrency, easy network access, good time-series support without extensions |
| Packaging | Docker Compose | Reproducible deployment on the VM |
| Ingest API | FastAPI + Uvicorn | Typed endpoints, multipart uploads, easy auth dependency |
| Queue | `ingest.job` table | Durable, transactional, no extra broker; `SKIP LOCKED` gives safe claiming |
| Worker | Single Python process | Preserves the proven single-writer loader; simple failure recovery |
| TLS | Caddy | Automatic HTTPS plumbing, simple reverse proxy, only published port |
| Uploader | Python + `httpx` | Reuses hashing/scanning code; HTTPS and retries |
| XML parsing | `defusedxml` / `lxml` | Safe parsing of external files |
| DB driver | `psycopg` 3 | Fast bulk `COPY`, modern API |
| Read API | FastAPI | Typed endpoints, OpenAPI docs, easy read-only surface |
| Time zone handling | Postgres `AT TIME ZONE` | Correct local/UTC conversion at query time |
| Migrations | Small SQL runner | Versioned, checksummed, no heavy dependency |

No TimescaleDB extension is required at the current data volume (~113k unique samples,
233,855 reading rows). A BRIN index on `ts` is sufficient. TimescaleDB can be added later
without schema changes.

## 8. Source data profile

| Attribute | Observation |
|---|---|
| File count | 147 `.xle` files: 101 uncompensated, 46 compensated |
| Format | XML (`<Body_xle>`), no namespace, UTF-8 (current export; cp1252 fallback) |
| Generations | `DataGrabber V3.001` (compact XML) and `Version 4.7.0` (indented, extra fields) |
| Channels | `LEVEL`, `TEMPERATURE`, optional `CONDUCTIVITY`; barometers report level in kPa |
| Long-term interval | 4 hours |
| Aquifer-test (`_AT_`) interval | 2 Hz (`ms` = 0 / 500) |
| Uncompensated rows | 173,508 |
| Unique samples `(serial, ts)` | 113,177 |
| Series readings `(serial, channel, timestamp, ms)` | 233,855 |
| Fully redundant files | 31 (ordered by `download_time`, then filename) |
| Anomalous files | 13 files with header/data anomalies (12 `Stop_time < Start_time`, one of which also has zero logs; 1 malformed XML file) |
| Location mismatches | 2 files (reported separately from anomalies) |
| Compensated marker | `<Compensation_info>` metadata block in the body (authoritative); filename `Compensated` is a consistency check only |

## 9. Design principles

1. **Idempotent ingestion.** Re-running the uploader or worker must not create duplicates.
2. **Provenance.** Every reading points to the file that produced it.
3. **Native resolution.** Never resample during ingest; aggregation belongs in views/queries.
4. **Narrow series model.** One row per logger/channel/unit keeps 2- and 3-channel loggers,
   unit changes, and future compensated series unambiguous.
5. **Write/read separation.** Only the worker writes domain data; consumers use the read
   API with a read-only token.
6. **Compensated-ready, not compensated-enabled.** `data_kind` allows a future compensated
   series to coexist with raw data without key collisions.
7. **Least privilege by role.** Upload, worker, and read roles have disjoint grants.
8. **One front door.** Only Caddy is reachable; everything else stays on an internal
   network. Caddy's published port lives on a non-internal `edge` network that no other
   service joins.

## 10. Non-goals

- No compensation calculations, barometric correction, or data editing.
- No UI in this repository.
- No public endpoints.
- No streaming or event-driven ingest.
- No direct SQL or published PostgreSQL port for analysts.

## 11. Assumptions and constraints

- The OneDrive export folder is the authoritative source.
- Files carry no timezone offset; timestamps are logger local time.
- All sites share one timezone unless a per-location override is added.
- The VM is reachable only from the internal network.
- A Linux VM with Docker is available.
- Data volume is small enough for a single Postgres instance.
- TLS certificates are issued by an internal CA or mkcert and trusted by clients.

## 12. Known risks

| Risk | Mitigation |
|---|---|
| Partial OneDrive sync | Uploader only sends files whose content ends with `</Body_xle>` and honors a settle-time delay |
| File locked during upload | Uploader retries transport errors; skip and retry next run |
| Filename/header location mismatch | Store the header location and flag mismatches in `import_file` |
| Logger moved mid-file | Header location may mislabel older rows; flag location changes per serial for review |
| DST ambiguity in naive local time | Document the fall-back hour; conversion follows Postgres rules |
| Source format drift | Parser is tolerant of both known generations; validation surfaces unknown shapes |
| Worker dies mid-job | Stale locks are requeued after `WORKER_LOCK_TIMEOUT_SECONDS`; spool reconciliation handles orphans |
| Token leakage | Per-client ingest tokens, per-analyst read tokens, TLS-only, rotation documented |
| Certificate expiry | Internal CA/mkcert; monitor and rotate per `certs/README.md` |
