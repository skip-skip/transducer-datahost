# Ingester

The ingestion package is **not implemented yet**. This directory will contain the Python
code that parses `.xle` exports and loads uncompensated readings into PostgreSQL.

## Planned layout

```
ingest/
  pyproject.toml
  transducer_ingest/
    cli.py          # ingest / --dry-run / report
    parse_xle.py    # XML -> normalized records
    validate.py     # anomaly classification
    load.py         # staging COPY + upsert
    db.py           # connections and transactions
  tests/
    fixtures/       # small sample .xle files
    test_parse.py
```

## Planned commands

```
python -m transducer_ingest.cli ingest --source <folder> [--dry-run] [--since <date>]
python -m transducer_ingest.cli report [--source <folder>]
```

## Design reference

See [`../docs/ingestion.md`](../docs/ingestion.md) for selection rules, validation, and
deduplication behavior.
