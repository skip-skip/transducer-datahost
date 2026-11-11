# Data Model

## 1. Entity relationships

```
+-----------+        +-----------+        +-----------+
|  logger   | 1    * |  series   | 1    * |  reading  |
|-----------|--------|-----------|--------|-----------|
| serial PK |--------| series PK |--------| series FK |
+-----------+        | serial FK |        | ts        |
                     | channel   |        | value     |
                     | ident     |        | file FK   |
                     | unit      |        +-----+-----+
                     | data_kind |              |
                     +-----------+              | *
                                                |
+-----------+        +--------------+            |
| location  | 1    * | import_file  | 1         *|
|-----------|--------|--------------|------------+
| loc PK    |--------| file PK      |
| name      |        | sha256       |
| base      |        | serial FK    |
| is_at     |        | location FK  |
| tz        |        | start/stop   |
+-----------+        | status       |
                     +--------------+
```

- `logger` identifies the physical instrument by serial number.
- `location` identifies the monitoring point (and whether it is an aquifer test).
- `series` identifies one channel of one logger in one unit for one data kind.
- `reading` is the time-series fact table.
- `import_file` is both the ingest audit trail and the provenance anchor for readings.
- `ingest.job` is the durable upload/processing queue; it references `import_file` once a
  job completes. It lives in a separate `ingest` schema so the upload role never touches
  domain tables.

## 2. Tables

### `transducer.logger`

| Column | Type | Notes |
|---|---|---|
| `serial_number` | `text` PK | From `<Serial_number>` |
| `instrument_type` | `text` | e.g. `L5_LTC`, `L5_LT` |
| `model_number` | `text` | e.g. `M5/C80` |
| `firmware` | `text` | e.g. `1.006` |
| `project_id` | `text` | From `<Project_ID>` |
| `first_seen` | `timestamptz` | Default `now()` |
| `last_seen` | `timestamptz` | Updated on import |

### `transducer.location`

| Column | Type | Notes |
|---|---|---|
| `location_id` | `integer` identity PK | |
| `name` | `text` UNIQUE | Header `<Location>`, e.g. `SC-2560`, `MCDMW-04_AT` |
| `base_location` | `text` | Name without `_AT`, e.g. `MCDMW-04` |
| `is_aquifer_test` | `boolean` | True when name ends with `_AT` |
| `latitude` | `numeric(9,6)` | From header |
| `longitude` | `numeric(9,6)` | From header (misspelled `Longtitude` in source) |
| `timezone` | `text` | Default `America/New_York`; per-location override |

### `transducer.series`

| Column | Type | Notes |
|---|---|---|
| `series_id` | `bigint` identity PK | |
| `serial_number` | `text` FK → `logger` | |
| `channel_no` | `smallint` | 1, 2, 3 |
| `identification` | `text` | `LEVEL`, `TEMPERATURE`, `CONDUCTIVITY` |
| `unit` | `text` | `m`, `°C`, `µS/cm`, `kPa` |
| `data_kind` | `text` | `uncompensated` or `compensated`; only uncompensated series are loaded in v1 |

Unique key: `(serial_number, channel_no, identification, unit, data_kind)`.

### `transducer.import_file`

| Column | Type | Notes |
|---|---|---|
| `file_id` | `bigint` identity PK | Monotonic processing order |
| `filename` | `text` | Original filename |
| `sha256` | `char(64)` UNIQUE | Content hash for exact-duplicate detection |
| `download_time` | `timestamp(3)` | `<File_info><Date> + <Time>`; ingest priority; falls back to the file mtime when the header is unreadable |
| `serial_number` | `text` FK → `logger` | Nullable when the file cannot be parsed |
| `location_id` | `integer` FK → `location` | From header; nullable when the file cannot be parsed |
| `start_time` | `timestamp(3)` | Header `<Start_time>` |
| `stop_time` | `timestamp(3)` | Header `<Stop_time>` |
| `num_log` | `integer` | Header `<Num_log>` |
| `parsed_log` | `integer` | Actual parsed `<Log>` count |
| `is_compensated` | `boolean` | Presence of `<Compensation_info>` metadata (filename is advisory only) |
| `data_kind` | `text` | `uncompensated`, or `compensated` for skipped compensated files |
| `status` | `text` | `imported`, `warning`, `empty`, `error`, `skipped_duplicate`, `skipped_compensated` |
| `error` | `text` | Validation/parse detail |
| `imported_at` | `timestamptz` | Default `now()` |

`is_compensated` and `data_kind` are kept consistent by a check constraint:
`is_compensated = (data_kind = 'compensated')`. A `skipped_compensated` row must have
`parsed_log = 0` because no readings are loaded for it.

### `transducer.reading`

| Column | Type | Notes |
|---|---|---|
| `series_id` | `bigint` FK → `series` | |
| `ts` | `timestamp(3)` | Naive local logger time, millisecond precision |
| `value` | `double precision` | Raw channel value |
| `file_id` | `bigint` FK → `import_file` | Provenance |

Primary key: `(series_id, ts)`.

## 3. Operational `ingest` schema

The `ingest` schema is intentionally separate from the `transducer` domain schema. It is
owned by the admin role and granted only to `transducer_upload` and `transducer_ingest`;
`transducer_read` has no access.

### `ingest.job`

Durable queue for uploaded files. The upload API inserts a row; the worker claims it with
`SELECT ... FOR UPDATE SKIP LOCKED`.

| Column | Type | Notes |
|---|---|---|
| `job_id` | `bigint` identity PK | |
| `sha256` | `char(64)` UNIQUE | Content hash; one job per file content |
| `filename` | `text` | Sanitized original filename |
| `byte_size` | `bigint` | Bytes written to the spool |
| `source_client` | `text` | Bearer-token label (or `recovered`) |
| `source_ip` | `inet` | Client address, when available |
| `status` | `text` | `queued`, `processing`, `done`, `failed` |
| `import_status` | `text` | Final `import_file.status`, when done |
| `file_id` | `bigint` FK → `import_file` | Set when the loader writes an audit row |
| `attempt_count` | `integer` | Incremented on each claim |
| `next_attempt_at` | `timestamptz` | Backoff gate for retries |
| `locked_at` | `timestamptz` | Claim time; stale locks are requeued |
| `received_at` | `timestamptz` | Default `now()` |
| `started_at` | `timestamptz` | First claim time |
| `finished_at` | `timestamptz` | Terminal time |
| `error` | `text` | Last error, when failed or retrying |

Constraints enforce a lowercase 64-hex `sha256`, a non-blank filename, a positive byte
size, non-negative attempts, and the allowed status values.

### `ingest.worker_heartbeat`

One row per running worker. The worker updates `last_seen` while polling. `/health` and the
Compose healthcheck report `degraded`/unhealthy when the heartbeat is older than
`WORKER_HEARTBEAT_TIMEOUT_SECONDS`.

| Column | Type | Notes |
|---|---|---|
| `worker_id` | `text` PK | Hostname + PID, or `WORKER_ID` |
| `last_seen` | `timestamptz` | Updated on every claim attempt |

### `ingest.file_known(text)`

`SECURITY DEFINER` function returning a boolean: whether the hash already exists in
`transducer.import_file`. It lets `transducer_upload` answer `HEAD /v1/files/{sha}`
without `SELECT` on `import_file` and cannot expose row contents.

## 4. Design notes

### Narrow `reading` instead of a wide table

A wide table (`level`, `temperature`, `conductivity` columns) would need NULLs for
2-channel loggers, separate columns for kPa barometers, and would collide when a future
compensated series shares the same serial/channel. The narrow model handles all of these
with one shape and keeps unit semantics explicit in `series`.

At the current volume the narrow table is roughly 250k–340k rows, which is trivial.

### Naive local time with explicit conversion

The source has no timezone offset. Storing `timestamptz` would require inventing an offset
at ingest and would silently change meaning across DST boundaries. Instead:

- `reading.ts` is `timestamp(3) without time zone`, exactly the logger clock.
- `location.timezone` (or the `SITE_TIMEZONE` default) records how to interpret it.
- The API converts with `ts AT TIME ZONE timezone` when UTC is requested.

Known caveat: during the DST fall-back hour, naive local times are ambiguous. Postgres
resolves them deterministically; this is documented rather than corrected.

### Per-file location attribution

Each file's `<Location>` header is stored on `import_file`. Readings join through `file_id`
for provenance. Filename/header mismatches are recorded in `import_file.error` for review
rather than silently preferred. A future curated deployment table can refine attribution
without changing the reading key.

### `data_kind` future-proofing

Compensated exports reuse the same serial and channels but carry different values. Adding
`data_kind` to `series` and its unique key means a compensated series gets its own
`series_id`, so `(series_id, ts)` never collides with raw data. v1 writes only
`uncompensated`.

## 5. Indexes

| Index | Purpose |
|---|---|
| `reading` PK `(series_id, ts)` | Range scans per series, dedup key |
| `reading` BRIN on `ts` | Broad time-range filtering |
| `reading` btree on `file_id` | Provenance joins and FK maintenance |
| `import_file` UNIQUE on `sha256` | Exact-duplicate detection |
| `import_file` on `(serial_number, download_time)` | Per-logger audit and priority |
| `import_file` on `location_id` | Location joins and FK maintenance |
| `series` UNIQUE on identity | Idempotent series creation |
| `ingest.job` UNIQUE on `sha256` | One job per file content |
| `ingest.job` on `(status, next_attempt_at, job_id)` | Queue claim and backoff |
| `ingest.job` on `received_at DESC` | Recent-job listing |
| `ingest.job` on `file_id` | Join to `import_file` |

## 6. Views

The read role queries curated views instead of `import_file`:

| View | Purpose |
|---|---|
| `transducer.v_reading` | `reading` → `series` → `import_file` → `location`; exposes `series_id`, `serial_number`, `location_id`, `location`, `is_aquifer_test`, `timezone`, `data_kind`, `channel_no`, `identification`, `unit`, `ts`, `value`, `file_id`, `filename`, `download_time`. |
| `transducer.v_reading_utc` | Everything in `v_reading` plus `ts_utc = ts AT TIME ZONE timezone`; local `ts` is retained for comparison. |
| `transducer.v_import` | Audit view with `file_id`, `filename`, `sha256`, `download_time`, `serial_number`, `location_id`, `location`, `is_aquifer_test`, `start_time`, `stop_time`, `num_log`, `parsed_log`, `is_compensated`, `data_kind`, `status`, `error`, `imported_at`. |

The views are owned by the admin role, so the read role does not need direct access to
`import_file` (and does not have it).

## 7. Estimated volume

| Measure | Value |
|---|---|
| Uncompensated files | 101 |
| Log records across files | 173,508 |
| Unique samples `(serial, ts)` | 113,177 |
| Series readings `(serial, channel, ts)` | 233,855 |
| Estimated database size | Well under 100 MB |

## 8. Example queries (illustrative)

Latest level per logger:

```sql
SELECT DISTINCT ON (s.serial_number)
       s.serial_number, r.ts, r.value, s.unit
FROM transducer.reading r
JOIN transducer.series s USING (series_id)
WHERE s.identification = 'LEVEL'
ORDER BY s.serial_number, r.ts DESC;
```

Readings for one logger over a window, in UTC (read role):

```sql
SELECT v.ts_utc, v.identification, v.value, v.unit
FROM transducer.v_reading_utc v
WHERE v.serial_number = '1096042'
  AND v.ts >= '2026-01-01' AND v.ts < '2026-02-01'
ORDER BY v.ts;
```

## 9. Compensated extension (future)

- Ingest compensated files with `data_kind = 'compensated'`.
- Series are created separately by the extended unique key.
- A view can pivot raw and compensated values side by side by timestamp.
- No change to `reading` primary key is required.

## 10. Dashboard state (not SQL)

The dashboard deliberately adds no tables and no database role. Its layout state is a
single JSON document on the `dashboard_state` Docker volume, owned by the dashboard
service:

- `personal[<read-token label>].figures`: private figures.
- `shared.figures`: figures visible to every valid read token.
- `personal[<read-token label>].ui`: selected figure, pane collapse, and active view.

A figure stores `id`, `name`, `series_id`, `position`, `config`, `created_by`,
`created_at`, and `updated_at`. It binds to `transducer.series.series_id` by value only;
there is no foreign key because the dashboard has no database connection. The UI derives
Source and Analyte labels from `/api/series/{id}`.

See [docs/dashboard.md](dashboard.md) for the document shape, atomic-write behavior, and
backup procedure. Migrating this state into PostgreSQL later would not change the
timeseries schema.
