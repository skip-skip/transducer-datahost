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
| `data_kind` | `text` | `uncompensated` (v1); future `compensated` |

Unique key: `(serial_number, channel_no, identification, unit, data_kind)`.

### `transducer.import_file`

| Column | Type | Notes |
|---|---|---|
| `file_id` | `bigint` identity PK | Monotonic processing order |
| `filename` | `text` | Original filename |
| `sha256` | `char(64)` UNIQUE | Content hash for exact-duplicate detection |
| `download_time` | `timestamp` | `<File_info><Date> + <Time>`; ingest priority |
| `serial_number` | `text` FK → `logger` | |
| `location_id` | `integer` FK → `location` | From header |
| `start_time` | `timestamp` | Header `<Start_time>` |
| `stop_time` | `timestamp` | Header `<Stop_time>` |
| `num_log` | `integer` | Header `<Num_log>` |
| `parsed_log` | `integer` | Actual parsed `<Log>` count |
| `is_compensated` | `boolean` | Presence of `<Compensation_info>` metadata (filename is advisory only) |
| `data_kind` | `text` | `uncompensated` in v1 |
| `status` | `text` | `imported`, `warning`, `empty`, `error`, `skipped_duplicate` |
| `error` | `text` | Validation/parse detail |
| `imported_at` | `timestamptz` | Default `now()` |

### `transducer.reading`

| Column | Type | Notes |
|---|---|---|
| `series_id` | `bigint` FK → `series` | |
| `ts` | `timestamp(3)` | Naive local logger time, millisecond precision |
| `value` | `double precision` | Raw channel value |
| `file_id` | `bigint` FK → `import_file` | Provenance |

Primary key: `(series_id, ts)`.

## 3. Design notes

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

## 4. Indexes

| Index | Purpose |
|---|---|
| `reading` PK `(series_id, ts)` | Range scans per series, dedup key |
| `reading` BRIN on `ts` | Broad time-range filtering |
| `import_file` UNIQUE on `sha256` | Exact-duplicate detection |
| `import_file` on `(serial_number, download_time)` | Per-logger audit and priority |
| `series` UNIQUE on identity | Idempotent series creation |

## 5. Views

Planned `transducer.v_reading` joins `reading` → `series` → `logger` → `import_file` →
`location` and exposes friendly columns:

```
serial_number, location, is_aquifer_test, data_kind,
channel_no, identification, unit, ts, value,
file_id, filename, download_time
```

A future `transducer.v_reading_utc` can expose `ts AT TIME ZONE timezone AS ts_utc`.

## 6. Estimated volume

| Measure | Value |
|---|---|
| Uncompensated files | 101 |
| Parsed rows across files | 173,508 |
| Unique readings | 113,177 |
| Estimated `reading` rows (3 channels) | ~250k–340k |
| Estimated database size | Well under 100 MB |

## 7. Example queries (illustrative)

Latest level per logger:

```sql
SELECT DISTINCT ON (s.serial_number)
       s.serial_number, r.ts, r.value, s.unit
FROM transducer.reading r
JOIN transducer.series s USING (series_id)
WHERE s.identification = 'LEVEL'
ORDER BY s.serial_number, r.ts DESC;
```

Readings for one logger over a window, in UTC:

```sql
SELECT r.ts AT TIME ZONE l.timezone AS ts_utc, s.identification, r.value, s.unit
FROM transducer.reading r
JOIN transducer.series s USING (series_id)
JOIN transducer.import_file f USING (file_id)
JOIN transducer.location l USING (location_id)
WHERE s.serial_number = '1096042'
  AND r.ts >= '2026-01-01' AND r.ts < '2026-02-01'
ORDER BY r.ts;
```

## 8. Compensated extension (future)

- Ingest compensated files with `data_kind = 'compensated'`.
- Series are created separately by the extended unique key.
- A view can pivot raw and compensated values side by side by timestamp.
- No change to `reading` primary key is required.
