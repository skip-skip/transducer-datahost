# Database

Executable SQL is **not implemented yet**. This directory will hold the initialization
scripts mounted into the Postgres container at `/docker-entrypoint-initdb.d`.

## Planned files

| File | Purpose |
|---|---|
| `init/01_schema.sql` | `transducer` schema, tables, constraints, indexes |
| `init/02_roles.sql` | `transducer_ingest`, `transducer_read`, grants |
| `init/03_views.sql` | `v_reading`, optional UTC view |

## Important behavior

Postgres runs `/docker-entrypoint-initdb.d/*.sql` **only when the data volume is empty**.
Changes after first initialization require a migration or a manual apply. A migration tool
(e.g. `dbmate` or plain versioned scripts) should be chosen in Phase 1.

## Design reference

See [`../docs/data-model.md`](../docs/data-model.md) for the full logical model.
