# Transducer dashboard

Server-rendered dashboard (FastAPI + Jinja2 + HTMX + Plotly) over the existing
read-only API. It has **no database credentials** and **no write routes on the
read API**. The browser authenticates with a read API token, and the dashboard
forwards that token on every data request.

Production URL: `https://<INGEST_HOSTNAME>:8443/dashboard/`.

> **Planned redesign:** scenes (flat containers of figures), a multi-figure snapping grid workspace, and a
> context-sensitive options pane are in progress (no DB/read-API boundary changes). The scene list, options pane, and
> grid rendering have landed; drag/resize/snap and batched layout saves remain. See
> [../docs/dashboard-scenes.md](../docs/dashboard-scenes.md). The sections below describe the current implementation.

## State

Layout state is a single JSON file on the Compose volume `dashboard_state`,
mounted at `/var/lib/transducer-dashboard/state.json`. It is **version 2** and
contains:

- `shared.scenes` / `personal[<token label>].scenes`: scenes (flat containers
  of figures) with grid settings and a per-figure layout.
- `personal[<token label>].ui`: selected scene/figure, pane collapse, and view
  for each user, separately for personal and shared.

The dashboard now renders the selected scene's figures as panels in a CSS grid
using each figure's stored `layout`. The left pane lists scenes, and a nested
options pane edits the selected scene or figure. Drag, resize, snap-to-grid, and
batched layout persistence are the remaining Phase 9 work. Version 1 documents
are migrated on first load into a default scene per scope/owner, validated, and
written through the normal atomic path (`state.json.bak` keeps the old file).

Writes take a file lock, fsync a temporary file, copy the previous version to
`state.json.bak`, and atomically replace `state.json`. The container runs one
uvicorn worker.

A figure binds to a `series_id` plus an optional `location_id`. The UI presents
**Location** and **Analyte** selectors, which resolve to one series at one
location via `/locations` and `/locations/{id}/series`; chart points are then
scoped to that location. Figures saved before this binding (or left on "All
locations") chart the whole series.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `READ_API_URL` | `http://read-api:8080` | Internal read API base URL |
| `DASHBOARD_SECRET_KEY` | required | Signs the session cookie |
| `DASHBOARD_STATE_DIR` | `/var/lib/transducer-dashboard` | JSON state directory |
| `DASHBOARD_SESSION_MAX_AGE` | `28800` | Session lifetime (seconds) |
| `DASHBOARD_DEFAULT_WINDOW_DAYS` | `7` | Initial chart range |
| `DASHBOARD_MAX_POINTS` | `2000` | Chart point cap |
| `DASHBOARD_SHARED_EDITORS` | empty | Comma-separated labels allowed to edit shared scenes/figures; empty means any valid read key |
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
previous version. Restoring a version 1 file is supported: it migrates to
version 2 on first load and the pre-migration file is copied to `state.json.bak`.

## Development

```bash
python -m pip install -e "dashboard[dev]"
python -m pytest dashboard/tests
python -m ruff check dashboard
```

Set `DASHBOARD_SECRET_KEY`, `DASHBOARD_STATE_DIR`, and `DASHBOARD_COOKIE_SECURE=false`
for local runs. The read API must be reachable at `READ_API_URL`.
