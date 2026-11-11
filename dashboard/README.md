# Transducer dashboard

Server-rendered dashboard (FastAPI + Jinja2 + HTMX + Plotly) over the existing
read-only API. It has **no database credentials** and **no write routes on the
read API**. The browser authenticates with a read API token, and the dashboard
forwards that token on every data request.

Production URL: `https://<INGEST_HOSTNAME>:8443/dashboard/`.

## State

Layout state is a single JSON file on the Compose volume `dashboard_state`,
mounted at `/var/lib/transducer-dashboard/state.json`. It contains:

- `personal[<token label>].figures`: private figures for each read key.
- `shared.figures`: figures visible to every read key.
- `personal[<token label>].ui`: selected figure, folder-pane collapse, and view
  for each user, separately for personal and shared.

Writes take a file lock, fsync a temporary file, copy the previous version to
`state.json.bak`, and atomically replace `state.json`. The container runs one
uvicorn worker.

A figure binds to a `series_id`. The UI presents **Source** and **Analyte**
selectors, which resolve to one series via `/loggers` and `/series`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `READ_API_URL` | `http://read-api:8080` | Internal read API base URL |
| `DASHBOARD_SECRET_KEY` | required | Signs the session cookie |
| `DASHBOARD_STATE_DIR` | `/var/lib/transducer-dashboard` | JSON state directory |
| `DASHBOARD_SESSION_MAX_AGE` | `28800` | Session lifetime (seconds) |
| `DASHBOARD_DEFAULT_WINDOW_DAYS` | `7` | Initial chart range |
| `DASHBOARD_MAX_POINTS` | `2000` | Chart point cap |
| `DASHBOARD_SHARED_EDITORS` | empty | Comma-separated labels allowed to edit shared figures; empty means any valid read key |
| `DASHBOARD_COOKIE_SECURE` | `true` | Set `false` only for plain-HTTP local development |
| `SITE_TIMEZONE` | `America/New_York` | Range calculation time zone |

## Operations

Back up state while the service is running (atomic replace makes a read safe):

```bash
docker compose exec dashboard cat /var/lib/transducer-dashboard/state.json \
  > dashboard-state-$(date +%F).json
```

Restore by stopping the dashboard, replacing `state.json`, keeping ownership for
the container user (uid 10001), and starting it again. `state.json.bak` holds the
previous version.

## Development

```bash
python -m pip install -e "dashboard[dev]"
python -m pytest dashboard/tests
python -m ruff check dashboard
```

Set `DASHBOARD_SECRET_KEY`, `DASHBOARD_STATE_DIR`, and `DASHBOARD_COOKIE_SECURE=false`
for local runs. The read API must be reachable at `READ_API_URL`.
