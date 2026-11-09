# Transducer Data Warehouse

Centralized PostgreSQL store for uncompensated time-series transducer exports, with a
read-only API for internal dashboards and analysts.

> **Status: Phase 1 (schema and local database).**
> The `transducer` schema, roles, read views, grants, and Phase 1 acceptance test are
> implemented and verified against PostgreSQL 16.2. The parser, loader, and API are not
> implemented yet, and Docker/Compose verification on the Linux VM is pending. See
> [docs/roadmap.md](docs/roadmap.md) for the build plan.

## Why this exists

Transducer exports currently arrive as individual Solinst `.xle` XML files (one file per
logger download). Each download contains the logger's full memory, so successive files
overlap heavily and are frequently duplicated (`- Copy`, `(2)`, `_jh`, `REFERENCE BARO_`).
This project provides a single, deduplicated, queryable store on the internal network.

## Scope (v1)

In scope:

- Ingest **uncompensated** `.xle` exports from the OneDrive export folder.
- Preserve native sample intervals (4-hour long-term monitoring, 2 Hz aquifer tests).
- Deduplicate overlapping downloads and repeated files.
- Record per-file provenance and validation status.
- Serve read-only data to internal consumers via PostgreSQL roles and a FastAPI service.
- Return timestamps as local time or UTC on request.

Out of scope for v1:

- Compensated data (excluded from ingestion, but the schema is designed to accept it later).
- Barometric compensation calculations.
- A dashboard UI.
- Public internet exposure or SSO.

## Architecture at a glance

```
 OneDrive export folder (Windows)
            |
            |  scheduled ingest (Python CLI)
            v
 +--------------------------+          +-----------------------------+
 | Ingester (Windows host)  |  :5432   | PostgreSQL 16 (Linux VM)    |
 | parse / validate / upsert| -------> | schema: transducer          |
 +--------------------------+          +--------------+--------------+
                                                      ^
                                                      | read-only role :5432
                                        +-------------+--------------+
                                        | FastAPI read-only service  |
                                        | internal network :8000     |
                                        +-------------+--------------+
                                                      ^
                                                      | HTTP(S)
                                        +-------------+--------------+
                                        | Dashboards / analysts      |
                                        +----------------------------+
```

## Repository map

| Path | Purpose | Status |
|---|---|---|
| `docs/` | Architecture, data model, ingestion, network/security, decisions, roadmap | Written |
| `db/init/` | Ordered SQL that creates roles, schema, views, and grants on first start | Implemented |
| `db/tests/` | Phase 1 acceptance checks | Implemented |
| `ingest/` | Python package that parses `.xle` files and loads PostgreSQL | Placeholder |
| `api/` | Read-only FastAPI service | Placeholder |
| `scripts/` | Scheduled ingest, backup, and restore helpers | Placeholder |
| `docker-compose.yml` | Runnable `db` service; API/ingest services commented out | Partial |
| `.env.example` | Environment template | Template |

## Documentation

- [Architecture summary](docs/architecture.md)
- [Data model](docs/data-model.md)
- [Ingestion design](docs/ingestion.md)
- [Network and security](docs/network-security.md)
- [Architecture decisions](docs/decisions.md)
- [Roadmap and acceptance criteria](docs/roadmap.md)

## Source data

The current export is at:

```
OneDrive_1_11-9-2026/
```

It is intentionally **not** committed (see `.gitignore`). It contains 147 `.xle` files:
101 uncompensated and 46 compensated. Compensated files are identified by the
`<Compensation_info>` metadata block in the file body; filenames are only a consistency
check. The sibling `.zip` is the same content zipped and is also ignored.

## Next step

Verify `docker compose up -d db` on the Linux VM, then start Phase 2 (parser and
validation) per [docs/roadmap.md](docs/roadmap.md).
