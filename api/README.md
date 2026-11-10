# Read API

Read-only FastAPI service over the `transducer_read` role. It exposes the `transducer`
schema to internal dashboards and analysts without distributing database credentials and
without publishing PostgreSQL.

## Endpoints

Base URL in production: `https://<INGEST_HOSTNAME>:8443/api`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness/readiness (unauthenticated) |
| GET | `/loggers` | Logger inventory with series and latest reading |
| GET | `/locations` | Locations and time zones |
| GET | `/series?serial=&identification=&unit=&data_kind=` | Channel/series metadata |
| GET | `/readings?start=&end=&serial=&channel=&identification=&unit=&location=&series_id=&data_kind=&tz=local\|utc` | Time-series data |
| GET | `/readings/latest?serial=&channel=&data_kind=` | Most recent reading per series |
| GET | `/imports?status=&serial=&location=&start=&end=` | Ingest audit trail |
| GET | `/series/{id}/summary?start=&end=&interval=hour\|day&tz=local\|utc` | Aggregation |

`start` and `end` are required for `/readings` and `/series/{id}/summary`. All endpoints
except `/health` require `Authorization: Bearer <token>`.

## Example

```bash
curl -H "Authorization: Bearer $READ_API_TOKEN" \
  "https://transducer.internal:8443/api/loggers"
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `READ_USER` / `READ_PASSWORD` | `transducer_read` / required | Database role |
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` | `db` / `5432` / `transducer` | Connection |
| `READ_API_TOKENS` | required | Comma-separated `label:token` pairs |
| `READ_MAX_ROWS` | `10000` | Maximum page size |
| `READ_MAX_WINDOW_DAYS` | `31` | Maximum `start`..`end` span |
| `API_CORS_ORIGINS` | empty | Comma-separated allowed browser origins |
| `API_DOCS_ENABLED` | `false` | Serve `/docs` and `/openapi.json` |
| `SITE_TIMEZONE` | `America/New_York` | Default interpretation zone |

## Security and constraints

- Uses only the `transducer_read` role; no write or DDL capability.
- Bearer authentication is applied at the router level, before any database connection.
- Parameterized queries; no string interpolation.
- Pagination and maximum time-window limits enforced.
- CORS is empty by default; set `API_CORS_ORIGINS` for browser dashboards.
- Interactive docs are off by default on the internal VM.
- PostgreSQL is not published; the only entry point is Caddy on 8443.

## Run locally

```bash
cd api
python -m pip install -e ".[dev]"
READ_API_TOKENS=local:token READ_PASSWORD=... POSTGRES_HOST=127.0.0.1 \
  uvicorn transducer_api.app:create_app --factory --reload --port 8080
```

Compose uses the same factory command on port 8080 behind Caddy.

## Tests

```bash
python -m pytest -q
```

## Design reference

See [`../docs/network-security.md`](../docs/network-security.md) and
[`../docs/data-model.md`](../docs/data-model.md).
