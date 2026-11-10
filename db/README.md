# Database

Executable SQL for the `transducer` schema, application roles, read views, and grants.
The files in `init/` are mounted at `/docker-entrypoint-initdb.d` and run in filename
order the first time the data volume is initialized.

## Files

| File | Purpose |
|---|---|
| `init/01_roles.sql` | Creates `transducer_ingest` and `transducer_read` from `INGEST_PASSWORD` / `READ_PASSWORD`; sets SCRAM passwords and a pinned `search_path`. |
| `init/02_schema.sql` | `transducer` schema, five tables, constraints, and indexes. |
| `init/03_views.sql` | `v_reading`, `v_reading_utc`, and `v_import`. |
| `init/04_grants.sql` | Least-privilege grants and `PUBLIC` revokes. |
| `tests/verify_phase1.sh` | Phase 1 acceptance checks (roles, SCRAM, grants, views, constraints). |
| `tests/run_phase1.sh` | Runs the acceptance checks inside the Compose `db` service. |

`01_roles.sql` runs first so a missing or empty role password stops initialization
immediately. Each SQL file wraps its work in a transaction so a failure cannot leave a
partially-created schema.

## Roles and access

| Role | Access |
|---|---|
| `transducer_admin` | Bootstrap superuser / object owner. Provisioning only. |
| `transducer_ingest` | `SELECT`, `INSERT`, `UPDATE` on the five base tables; `USAGE`/`SELECT` on sequences. No `DELETE`, `TRUNCATE`, or DDL. |
| `transducer_read` | `SELECT` on `logger`, `location`, `series`, `reading`, and the three views. No direct `import_file` access, no sequences, no writes. |

The views are security-definer (owned by the admin), so `transducer_read` can query
`v_import` (including `error` details) without holding `SELECT` on `import_file`.

## First start

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD, INGEST_PASSWORD, and READ_PASSWORD.
docker compose up -d db
docker compose logs -f db
```

Compose forces SCRAM for host and local connections and uses a TCP healthcheck, so the
socket-only temporary server used during initialization is not reported as ready.

## Verification

The recommended path runs the checks inside the Compose `db` service, so the host
does not need `psql` and `.env` does not need to be sourced:

```bash
docker compose up -d db
db/tests/run_phase1.sh
```

Compose loads `.env` and passes `POSTGRES_PASSWORD`, `INGEST_PASSWORD`, and
`READ_PASSWORD` to the container. `run_phase1.sh` sets `ALLOW_NON_TEST_DB=1`
because the Compose database is normally `transducer`, not a `_test` database.

To run the checks directly against any reachable database:

```bash
ALLOW_NON_TEST_DB=1 \
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=transducer \
POSTGRES_USER=transducer_admin POSTGRES_PASSWORD=... \
INGEST_PASSWORD=... READ_PASSWORD=... \
db/tests/verify_phase1.sh
```

`POSTGRES_PASSWORD` is the admin/provisioning password; the loader uses
`INGEST_PASSWORD` and the API uses `READ_PASSWORD`. The legacy `PGHOST`, `PGPORT`,
`PGDATABASE`, `PGUSER`, and `PGPASSWORD` names still work as fallbacks.

The script refuses database names that do not end in `_test` unless
`ALLOW_NON_TEST_DB=1` is set. It inserts reserved test rows and removes them on exit.

It checks, among other things:

- Both roles exist, can log in, are not superusers, and have SCRAM passwords.
- The ingest role can write base tables and use sequences but cannot create, alter, or
  drop schema objects, delete rows, or truncate tables.
- The read role can read the base tables and all three views but cannot read
  `import_file` directly, write, truncate, or use sequences.
- `v_reading_utc` converts a known local timestamp to the expected UTC instant.
- Duplicate readings, duplicate hashes, invalid statuses, and invalid checks are
  rejected.
- Wrong passwords are rejected and `PUBLIC` has no privileges.

## Re-initialization

Postgres runs the init scripts **only when the data volume is empty**. After the first
start, edit the scripts and re-create the volume:

```bash
docker compose down -v   # destroys the database and its data
docker compose up -d db
```

This is the supported reset path until a migration mechanism exists (see ADR-011 in
[`../docs/decisions.md`](../docs/decisions.md)). Do not use `down -v` on a database that
holds data you need.

## Connecting

Analysts and the API use the read role:

```bash
psql "host=<vm> port=5432 dbname=transducer user=transducer_read"
```

The ingester uses `transducer_ingest`. Only the bootstrap/admin credentials may run DDL.

## Design reference

See [`../docs/data-model.md`](../docs/data-model.md) for the logical model and
[`../docs/network-security.md`](../docs/network-security.md) for role and network rules.
