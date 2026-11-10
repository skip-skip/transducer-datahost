# Database

Executable SQL for the `transducer` and `ingest` schemas, application roles, read views,
grants, and the upload queue. The files in `init/` are mounted at
`/docker-entrypoint-initdb.d` and run in filename order the first time the data volume is
initialized. `migrations/` upgrades an existing volume.

## Files

| File | Purpose |
|---|---|
| `init/01_roles.sql` | Creates `transducer_upload`, `transducer_ingest`, and `transducer_read` from `UPLOAD_PASSWORD` / `INGEST_PASSWORD` / `READ_PASSWORD`; sets SCRAM passwords and pinned `search_path`s. |
| `init/02_schema.sql` | `transducer` schema, five tables, constraints, and indexes. |
| `init/03_views.sql` | `v_reading`, `v_reading_utc`, and `v_import`. |
| `init/04_grants.sql` | Least-privilege grants and `PUBLIC` revokes for `transducer`. |
| `init/05_ingest.sql` | `ingest` schema, `ingest.job`, `ingest.worker_heartbeat`, `ingest.file_known()`, indexes, and grants. |
| `migrations/0001_ingest.sql` | Idempotent upgrade that adds the `ingest` schema to an existing database. |
| `tests/verify_phase1.sh` | Acceptance checks (roles, SCRAM, grants, views, queue boundaries, constraints). |
| `tests/run_phase1.sh` | Runs the acceptance checks inside the Compose `db` service. |

`01_roles.sql` runs first so a missing or empty role password stops initialization
immediately. Each init file wraps its work in a transaction so a failure cannot leave a
partially-created schema.

## Roles and access

| Role | Access |
|---|---|
| `transducer_admin` | Bootstrap superuser / object owner. Provisioning and migrations only. |
| `transducer_upload` | `SELECT`/`INSERT`/`UPDATE` on `ingest.job`, `SELECT` on `ingest.worker_heartbeat`, `EXECUTE` on `ingest.file_known()`, `USAGE`/`SELECT` on `ingest` sequences. No domain tables, no `DELETE`, no DDL. |
| `transducer_ingest` | `SELECT`, `INSERT`, `UPDATE` on the five domain base tables and the `ingest` queue/heartbeat; `USAGE`/`SELECT` on sequences. No `DELETE`, `TRUNCATE`, or DDL. |
| `transducer_read` | `SELECT` on `logger`, `location`, `series`, `reading`, and the three views. No direct `import_file` access, no `ingest` access, no sequences, no writes. |

The views are security-definer (owned by the admin), so `transducer_read` can query
`v_import` (including `error` details) without holding `SELECT` on `import_file`.
`ingest.file_known()` is also security-definer but returns only a boolean, so the upload
role can check for an existing hash without reading domain tables.

## First start

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD, UPLOAD_PASSWORD, INGEST_PASSWORD, and READ_PASSWORD.
docker compose up -d --build
docker compose logs -f db
```

Compose forces SCRAM for host and local connections and uses a TCP healthcheck, so the
socket-only temporary server used during initialization is not reported as ready.
PostgreSQL has no published port; reach it with `docker compose exec db psql ...`.

## Verification

The recommended path runs the checks inside the Compose `db` service, so the host does not
need `psql` and `.env` does not need to be sourced:

```bash
docker compose up -d db
db/tests/run_phase1.sh
```

Compose loads `.env` and passes `POSTGRES_PASSWORD`, `UPLOAD_PASSWORD`,
`INGEST_PASSWORD`, and `READ_PASSWORD` to the container. `run_phase1.sh` sets
`ALLOW_NON_TEST_DB=1` because the Compose database is normally `transducer`, not a
`_test` database.

To run the checks directly against any reachable database:

```bash
ALLOW_NON_TEST_DB=1 \
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=transducer \
POSTGRES_USER=transducer_admin POSTGRES_PASSWORD=... \
UPLOAD_PASSWORD=... INGEST_PASSWORD=... READ_PASSWORD=... \
db/tests/verify_phase1.sh
```

The script refuses database names that do not end in `_test` unless `ALLOW_NON_TEST_DB=1`
is set. It inserts reserved test rows and removes them on exit.

It checks, among other things:

- All three application roles exist, can log in, are not superusers, and have SCRAM
  passwords.
- The upload role can enqueue and read `ingest.job` but cannot delete jobs, read domain
  tables, or create objects.
- The ingest role can write base tables and the queue but cannot create, alter, or drop
  schema objects, delete rows, or truncate tables.
- The read role can read the base tables and all three views but cannot read
  `import_file` or `ingest.job` directly, write, truncate, or use sequences.
- `v_reading_utc` converts a known local timestamp to the expected UTC instant.
- Duplicate readings, duplicate hashes, invalid statuses, and invalid checks are
  rejected.
- Wrong passwords are rejected and `PUBLIC` has no privileges.

## Migrations

`db/init` runs only when the data volume is empty. For an existing database, run the
migration service:

```bash
docker compose run --rm migrate
# or, from a host with admin access:
python scripts/migrate.py
```

The runner creates or updates the three application roles from the environment, records
applied files in `public.schema_migrations` with a checksum, and applies each
`db/migrations/*.sql` file in its own transaction. Editing an applied migration is
rejected; create a new numbered file instead.

Compose runs `migrate` automatically on `docker compose up` and gates the application
services on its successful completion.

## Re-initialization

Postgres runs the init scripts **only when the data volume is empty**. After the first
start, use migrations. If the data is disposable, a full reset is still possible:

```bash
docker compose down -v   # destroys the database and its data
docker compose up -d --build
```

Do not use `down -v` on a database that holds data you need.

## Connecting

There is no host-published port. Use the container for maintenance:

```bash
docker compose exec db psql -U transducer_admin -d transducer
```

The upload API uses `transducer_upload`, the worker uses `transducer_ingest`, and the read
API uses `transducer_read`. Only the bootstrap/admin credentials may run DDL.

## Design reference

See [`../docs/data-model.md`](../docs/data-model.md) for the logical model and
[`../docs/network-security.md`](../docs/network-security.md) for role and network rules.
