# Ingestion Design

## 1. Source profile

| Attribute | Value |
|---|---|
| File type | Solinst `.xle` XML (`<Body_xle>`) |
| Encoding | Windows-1252 (`°C`, `µS/cm`); numeric values are ASCII |
| Namespace | None |
| Generations | `DataGrabber V3.001` (compact) and `Version 4.7.0` (indented, extra fields) |
| Channel metadata | `<ChN_data_header>` with `<Identification>` and `<Unit>` |
| Data records | `<Log id="N">` with `<Date>`, `<Time>`, `<ms>`, `<ch1>`, `<ch2>`, optional `<ch3>` |
| Long-term interval | 4 hours |
| Aquifer-test interval | 2 Hz (`ms` 0 / 500) |
| Compensation marker | `<Compensation_info>` metadata block in the body (authoritative); filename `compensat` is advisory |

The parser must not assume indentation, field presence, channel count, or a fixed interval.

## 2. File selection

A file is eligible when all of the following are true:

1. Extension is `.xle` (case-insensitive).
2. The body does **not** contain a `<Compensation_info>` block. This metadata block is the
   authoritative compensated-data marker and separates all 46 compensated files from all
   101 uncompensated files in the current export.
3. Content is complete: the final non-whitespace text ends with `</Body_xle>`.
4. The file's last-write time is older than `INGEST_SETTLE_SECONDS` (default 120s), to
   avoid reading a file mid-sync.
5. Its SHA-256 is not already present in `import_file`.

The `.zip` archive is ignored. Subdirectories are scanned recursively.

### Compensation detection

Detection uses file contents, not the filename:

```
is_compensated = body contains <Compensation_info>
```

The filename substring `compensat` (case-insensitive) is used only as a consistency check
and is never used to include or exclude a file. In the current export the two signals agree
on all 147 files: 46 have both, 101 have neither. If they ever disagree, metadata wins and
the discrepancy is recorded in `import_file.error`.

Why not use the numeric values? Compensated `ch1` is the compensated result and is often
near zero or slightly negative, but 36 of 46 compensated files contain no negative value,
and some aquifer-test compensated files reach `ch1` ≈ 28. One uncompensated file has all
`abs(ch1) < 1`. Value-based rules therefore produce both false negatives and false
positives. `Created_by` is also not a discriminator: compensated files use `Version 4.6.3`
or `Version 4.7.0`, and 20 uncompensated files also use `Version 4.7.0`.

## 3. Parse steps

1. Read bytes; decode as Windows-1252 with a UTF-8 fallback for future exports.
2. Parse XML with a hardened parser (`defusedxml`); reject malformed documents.
3. Extract file metadata: `<File_info>` date/time, creator/version.
4. Extract instrument metadata: serial, type, model, firmware, project, channel count.
5. Extract data header: location, latitude, longitude, sample rate, start/stop, num log.
6. Extract channel headers into `(channel_no, identification, unit)`.
7. Extract every `<Log>`: combine `Date`, `Time`, and `ms` into a `timestamp(3)`.
8. Emit normalized records:
   - one `logger` upsert,
   - one `location` upsert,
   - one `series` per channel,
   - one reading per `(series, timestamp)`.

## 4. Validation matrix

| Check | Severity | Action |
|---|---|---|
| XML parses | Fatal | `status = error`, no rows |
| Serial number present | Fatal | `status = error` |
| At least one `<Log>` | Fatal | `status = empty` |
| Date/time/ms parseable | Fatal for that record | Record skipped; file `warning` |
| Channel value numeric | Fatal for that record | Record skipped; file `warning` |
| `<Num_log>` differs from parsed count | Warning | Import; note in `error` |
| `Stop_time < Start_time` | Warning | Import log data; note in `error` |
| Filename/header location mismatch | Warning | Import header location; note in `error` |
| Duplicate `(series, ts)` within one file | Warning | Keep last occurrence; note count |
| Unknown channel count or unit | Warning | Import what is known; note in `error` |
| `<Compensation_info>` present | Skip | Not eligible in v1 |
| Filename/metadata compensation mismatch | Warning | Metadata wins; note in `error` |
| SHA-256 already imported | Skip | `status = skipped_duplicate` |

Rationale: header fields are frequently unreliable in this export (12 files have
`Stop_time < Start_time`), but their log data is still valid. Only reject when the data
itself cannot be read.

## 5. Identity and deduplication

### File level

- `sha256` is unique in `import_file`. Exact copies (`- Copy`, `(2)`, `(3)`, `_jh`,
  `REFERENCE BARO_` prefix) are skipped after the first import.
- 34 of the 101 uncompensated files are fully redundant in the current export.

### Row level

- Reading identity is `(series_id, ts)`, where `ts` includes milliseconds. The `ms` field
  is essential: aquifer-test files contain two samples per second.
- Files are processed in ascending `download_time` (`<File_info>` date/time), then by
  filename as a tiebreaker.
- On conflict, the newer file wins:

```sql
INSERT INTO transducer.reading (series_id, ts, value, file_id)
VALUES (...)
ON CONFLICT (series_id, ts) DO UPDATE
SET value = EXCLUDED.value,
    file_id = EXCLUDED.file_id
WHERE EXCLUDED.file_id > transducer.reading.file_id;
```

Because `file_id` increases with processing order, an older file re-imported later cannot
overwrite newer data. A re-run of the same export inserts zero rows.

## 6. Load strategy

1. Parse the file fully in memory (largest file is ~3.8 MB, so this is safe).
2. Begin a transaction.
3. Upsert `logger`, `location`, and `series`; collect their IDs.
4. `COPY` normalized readings into a temporary staging table.
5. `INSERT ... SELECT ... ON CONFLICT` from staging into `reading`.
6. Insert/update the `import_file` row with counts and status.
7. Commit. On any failure, roll back and record `status = error` in a separate transaction.

## 7. Status model

| Status | Meaning |
|---|---|
| `imported` | Parsed cleanly; rows loaded |
| `warning` | Rows loaded, but a non-fatal anomaly was recorded |
| `empty` | File contained no log records |
| `error` | File could not be parsed or loaded; `error` has details |
| `skipped_duplicate` | SHA-256 already present |

`import_file` is the audit log. The dry-run report and `GET /imports` read from it.

## 8. Dry-run report

`ingest --dry-run` must print, without writing:

- Eligible, skipped, and already-imported file counts.
- Per-file outcome: new rows, overlapping rows, internal duplicates, warnings.
- Aggregate new unique readings and conflicts.
- A list of anomalous files with reasons.
- A comparison against expected backfill totals.

Expected current-export results:

| Metric | Expected |
|---|---|
| Eligible uncompensated files | 101 |
| Fully redundant files | 34 |
| Anomalous files | 13 |
| Empty files | 1 |
| Unique readings | 113,177 |
| Compensated rows | 0 |

## 9. Scheduling

- **v1:** Windows Task Scheduler on the host where OneDrive syncs, running
  `scripts/run_ingest.ps1` (for example, every 6 hours).
- The task runs the CLI against the export folder and logs to a rolling file.
- Overlapping runs are prevented with a mutex/lock file.
- A failed run leaves previously imported data intact and retries the same files next run.

## 10. Failure handling

| Failure | Behavior |
|---|---|
| Database unavailable | Abort run; no partial file commits |
| File locked by OneDrive | Skip; retry next run |
| Partial file | Skip (completeness check); retry next run |
| Malformed XML | Record `error`; continue with other files |
| Duplicate series definition | Reuse existing series |
| Disk full on VM | Ingest fails cleanly; alert via monitoring |

## 11. Backfill procedure

1. Run `ingest --dry-run` and reconcile against the expected table above.
2. Run `ingest` for the full export.
3. Verify `import_file` counts by status and `reading` count.
4. Re-run `ingest`; confirm zero new rows.
5. Review the 13 warning files and the location mismatches.

## 12. Planned CLI contract

```
python -m transducer_ingest.cli ingest --source <folder> [--dry-run] [--since <date>]
python -m transducer_ingest.cli report [--source <folder>]
```

Configuration comes from environment variables (`PGHOST`, `PGPORT`, `PGDATABASE`,
`PGUSER`, `PGPASSWORD`, `SITE_TIMEZONE`, `INGEST_SETTLE_SECONDS`). Passwords are never
accepted as command-line arguments in scheduled runs.

This contract is a design target; the CLI is not implemented in the skeleton.
