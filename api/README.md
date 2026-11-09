# API

The read-only FastAPI service is **not implemented yet**. This directory will contain the
HTTP layer that exposes the `transducer` schema to internal dashboards without distributing
database credentials.

## Planned endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness/readiness |
| GET | `/loggers` | Logger inventory |
| GET | `/locations` | Locations and time zones |
| GET | `/series?serial=` | Channel/series metadata |
| GET | `/readings?serial=&channel=&start=&end=&tz=local\|utc` | Time-series data |
| GET | `/readings/latest?serial=` | Most recent reading per series |
| GET | `/imports` | Ingest audit trail |
| GET | `/series/{id}/summary?interval=hour\|day` | Optional aggregation |

## Constraints

- Uses only the `transducer_read` role.
- Parameterized queries; no string interpolation.
- Pagination and maximum time-window limits.
- CORS restricted to `API_CORS_ORIGINS`.

## Design reference

See [`../docs/network-security.md`](../docs/network-security.md) and
[`../docs/data-model.md`](../docs/data-model.md).
