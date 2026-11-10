# Ingester

The ingestion package parses Solinst `.xle` exports, reports validation results without a
database, and loads uncompensated readings into PostgreSQL.

## Layout

```
ingest/
  pyproject.toml
  transducer_ingest/
    __init__.py
    parse_xle.py    # XML -> normalized records
    validate.py     # statuses, anomaly classification, folder report
    report.py       # database-free report CLI
    db.py           # POSTGRES_* / INGEST_* connection configuration
    load.py         # staging COPY + upsert loader and deduplication
    cli.py          # ingest/report CLI
  tests/
    fixtures/       # small cp1252 and UTF-8 sample .xle files
    conftest.py
    test_parse.py
    test_validate.py
    test_report.py
    test_load.py    # pure loader tests + gated DB integration test
```

## Install

```
cd ingest
python -m pip install -e ".[dev]"
```

## Commands

Database-free report:

```
python -m transducer_ingest.report --source <folder> [--json] [--check] [--all]
```

Database loader:

```
python -m transducer_ingest.cli ingest --source <folder> [--dry-run] [--since <date>] [--json] [--all]
```

Both are also available as the installed `transducer-report` and `transducer-ingest`
entry points. `--source` is scanned recursively for `.xle` files. `--check` exits non-zero
when the aggregate totals differ from the expected current export. The report never imports
a database driver.

The loader reads `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `INGEST_USER`,
`INGEST_PASSWORD`, and `SITE_TIMEZONE` (default `America/New_York`). It connects with the
least-privilege ingest role, not the admin `POSTGRES_USER` / `POSTGRES_PASSWORD`.
`--dry-run` executes the load inside one transaction and rolls it back; `--since` filters
on the header download time.

Expected current-export totals (measured against `OneDrive_1_11-9-2026`):

| Metric | Expected |
|---|---|
| Compensated files | 46 |
| Uncompensated files | 101 |
| Log records | 173,508 |
| Unique samples `(serial, ts)` | 113,177 |
| Series readings `(serial, channel, ts)` | 233,855 |
| Empty files | 1 |
| Anomalous files | 13 |
| Fully redundant files | 31 |

## Tests

```
python -m pytest -q
```

The full-export test is marked `integration` and is skipped automatically when
`OneDrive_1_11-9-2026/` is not present. The database test is also marked `integration` and
is skipped unless `TRANSDUCER_TEST_DSN` points at a disposable PostgreSQL database with the
Phase 1 schema applied. It truncates the transducer tables, so never point it at
production.

## Design reference

See [`../docs/ingestion.md`](../docs/ingestion.md) for selection rules, validation, and
deduplication behavior.
