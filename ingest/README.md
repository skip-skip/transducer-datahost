# Ingester

The ingestion package parses Solinst `.xle` exports and reports validation results without a
database. Database loading is added in Phase 3.

## Layout

```
ingest/
  pyproject.toml
  transducer_ingest/
    __init__.py
    parse_xle.py    # XML -> normalized records
    validate.py     # statuses, anomaly classification, folder report
    report.py       # database-free report CLI
  tests/
    fixtures/       # small cp1252 and UTF-8 sample .xle files
    conftest.py
    test_parse.py
    test_validate.py
    test_report.py
```

## Install

```
cd ingest
python -m pip install -e ".[dev]"
```

## Commands

```
python -m transducer_ingest.report --source <folder> [--json] [--check] [--all]
```

`--source` is scanned recursively for `.xle` files. `--check` exits non-zero when the
aggregate totals differ from the expected current export. The command never imports a
database driver.

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
`OneDrive_1_11-9-2026/` is not present.

## Design reference

See [`../docs/ingestion.md`](../docs/ingestion.md) for selection rules, validation, and
deduplication behavior.
