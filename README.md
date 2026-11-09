# Transducer Data Warehouse

Centralized PostgreSQL store for uncompensated time-series transducer exports, with a
read-only API for internal dashboards and analysts.

> **Status: planning / skeleton.**
> No application code, executable schema scripts, or runnable services exist yet.
> This repository currently contains architecture documentation, configuration templates,
> and an empty directory skeleton. See [docs/roadmap.md](docs/roadmap.md) for the build plan.

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
| `db/` | Schema, role, and view scripts (executed on first container start) | Placeholder |
| `ingest/` | Python package that parses `.xle` files and loads PostgreSQL | Placeholder |
| `api/` | Read-only FastAPI service | Placeholder |
| `scripts/` | Scheduled ingest, backup, and restore helpers | Placeholder |
| `docker-compose.yml` | PostgreSQL service skeleton; API/ingest services commented out | Skeleton |
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

Switch to the build agent to begin Phase 1 in [docs/roadmap.md](docs/roadmap.md):
scaffold the schema, parser, loader, and tests.
