# Transducer Data Warehouse

Centralized PostgreSQL store for uncompensated time-series transducer exports, with a
TLS-protected upload gateway and a separate read-only API for internal dashboards and
analysts.

> **Status: Phase 4 (VM deployment) pending verification.**
> The schema, roles, views, grants, `.xle` parser/report, PostgreSQL loader, ingest
> gateway (upload API + queue worker), and read API are implemented. Unit tests pass and
> the Compose topology is defined. What remains is bringing the stack up on the Linux VM,
> generating the TLS certificate, and running the acceptance checks. See
> [docs/roadmap.md](docs/roadmap.md).

## Why this exists

Transducer exports currently arrive as individual Solinst `.xle` XML files (one file per
logger download). Each download contains the logger's full memory, so successive files
overlap heavily and are frequently duplicated (`- Copy`, `(2)`, `_jh`, `REFERENCE BARO_`).
This project provides a single, deduplicated, queryable store on the internal network.

## Scope (v1)

In scope:

- Accept **uncompensated** `.xle` exports uploaded manually from the Windows workstation.
- Parse, validate, deduplicate, and load on the same VM as PostgreSQL.
- Preserve native sample intervals (4-hour long-term monitoring, 2 Hz aquifer tests).
- Record per-file provenance and validation status.
- Serve read-only data to internal consumers through a bearer-token HTTP API.
- Return timestamps as local time or UTC on request.

Out of scope for v1:

- Compensated data (excluded from ingestion, but the schema is designed to accept it later).
- Barometric compensation calculations.
- A dashboard UI.
- Public internet exposure or SSO.
- Automated/scheduled uploads (uploads are manual in v1).
- Direct analyst SQL access to PostgreSQL.

## Architecture at a glance

Only Caddy publishes a port. PostgreSQL, the ingest API, the worker, and the read API stay
on an internal Compose network with no route to the host or the internet. Caddy joins a
second, non-internal `edge` network (no other service does) because Docker will not publish
host ports for a container attached only to internal networks.

```
 OneDrive export folder (Windows)
            |
            |  transducer-ingest upload (HTTPS + bearer token)
            v
 +------------------------------+
 | Caddy :8443 (TLS)            |
 |  /ingest/*  /api/*           |
 +----+---------------------+---+
      |                     |
      v                     v
 +----------+         +------------+
 | ingest   |         | read API   |
 | API      |         | (read role)|
 +----+-----+         +-----+------+
      |  enqueue              | read
      v                       v
 +-------------------------------+
 | PostgreSQL 16 (internal only) |
 | transducer + ingest schemas   |
 +-------------------------------+
      ^
      | claim / load (single worker)
 +----+-------------------+
 | ingest worker          |
 | parse / validate / load|
 +------------------------+
```

The parser and loader run on the VM, next to the database. The Windows workstation only
needs HTTPS and an upload token; it never receives a database credential.

## Repository map

| Path | Purpose | Status |
|---|---|---|
| `docs/` | Architecture, data model, ingestion, network/security, decisions, roadmap | Written |
| `db/init/` | Ordered SQL that creates roles, schemas, views, grants, and the ingest queue on first start | Implemented |
| `db/migrations/` | Versioned SQL applied to existing databases by the migrate runner | Implemented |
| `db/tests/` | Phase 1 acceptance checks | Implemented |
| `ingest/` | `.xle` parser/loader plus the upload API, queue worker, uploader CLI, and migration runner | Implemented |
| `api/` | Read-only FastAPI service | Implemented |
| `caddy/` | TLS reverse proxy configuration and image | Implemented |
| `certs/` | Local TLS certificate instructions (certificates not committed) | Instructions |
| `scripts/` | Manual upload, migration, and (planned) backup/restore helpers | Partial |
| `docker-compose.yml` | Full stack: db, migrate, ingest-api, ingest-worker, read-api, caddy | Implemented |
| `.env.example` | Environment template | Template |

## Quick start (on the Linux VM)

```bash
cp .env.example .env
# Fill in role passwords, INGEST_API_TOKENS, and READ_API_TOKENS.

# Put a certificate for INGEST_HOSTNAME in certs/ (see certs/README.md).

docker compose up -d --build
db/tests/run_phase1.sh
```

Then, from the Windows upload host:

```powershell
$env:INGEST_API_TOKEN = "<token from INGEST_API_TOKENS>"
.\scripts\upload_xle.ps1 -Source "C:\path\to\OneDrive_1_11-9-2026"
```

See [docs/ingestion.md](docs/ingestion.md) for the full upload flow and
[docs/network-security.md](docs/network-security.md) for the security model.

## Source data

The current export is at:

```
OneDrive_1_11-9-2026/
```

It is intentionally **not** committed (see `.gitignore`). It contains 147 `.xle` files:
101 uncompensated and 46 compensated. Compensated files are identified by the
`<CompensationInfo>`/`<Compensation_info>` metadata block in the file body; filenames are
only a consistency check. The sibling `.zip` is the same content zipped and is also
ignored.

## Configuration

Copy `.env.example` to `.env`. The main groups are:

- **PostgreSQL:** `POSTGRES_DB`, `POSTGRES_USER`/`POSTGRES_PASSWORD` (admin, provisioning
  only), and the three application roles:
  - `UPLOAD_USER`/`UPLOAD_PASSWORD` — upload API; `ingest.job` only.
  - `INGEST_USER`/`INGEST_PASSWORD` — worker/loader; domain tables plus the queue.
  - `READ_USER`/`READ_PASSWORD` — read API; read-only.
- **TLS:** `INGEST_HOSTNAME` (must match the certificate) and `CADDY_HTTPS_PORT`.
- **Tokens:** `INGEST_API_TOKENS` and `READ_API_TOKENS` as comma-separated
  `label:token` pairs. The upload host also sets `INGEST_API_TOKEN` and
  `INGEST_API_URL`.
- **Limits:** `MAX_UPLOAD_BYTES`, `READ_MAX_ROWS`, `READ_MAX_WINDOW_DAYS`, worker
  retry/heartbeat settings, and spool retention.

The database passwords are only read on first initialization. Changing them in `.env`
later does not rotate them; use `ALTER ROLE ... PASSWORD` or rerun the `migrate` service
with new values.

Run the Phase 1 checks inside the Compose project:

```bash
docker compose up -d db
db/tests/run_phase1.sh
```

## Documentation

- [Architecture summary](docs/architecture.md)
- [Data model](docs/data-model.md)
- [Ingestion design](docs/ingestion.md)
- [Network and security](docs/network-security.md)
- [Architecture decisions](docs/decisions.md)
- [Roadmap and acceptance criteria](docs/roadmap.md)
