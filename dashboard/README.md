# Transducer dashboard

Panel dashboard (FastAPI + HoloViz Panel + Bokeh) over the existing read-only
API. It has **no database credentials** and **no write routes on the read API**.
The browser authenticates with a read API token, and the dashboard forwards that
token on every data request.

Production URL: `https://<INGEST_HOSTNAME>:8443/dashboard/`.

## State

Layout state is a single JSON file on the Compose volume `dashboard_state`,
mounted at `/var/lib/transducer-dashboard/state.json`. It contains:

- `scenes`: shared scenes visible to every read key. Each scene has a name and a
  list of figures.
- Each figure binds to an optional `location_id` and `series_id`, a range
  (`24h`, `7d`, `30d`), and a grid layout (`x`, `y`, `w`, `h`).
- `ui[<token label>]`: the selected scene, selected figure, and scene-tray
  collapse state for each user.

Scenes are shared between all users; there are no personal scenes. The grid is
12 columns wide with 220 px rows, and a figure is at least 3 columns by 2 rows.

In the browser, click an empty grid cell to add a figure, click its title ribbon
to select it and open the settings tray, and drag the ribbon to move it. Layout
changes are saved immediately to the shared state.

The settings tray edits the title, location, analyte, and range (`24h`, `7d`,
`30d`). Saving verifies the analyte against the read API: an unknown series is
rejected, and a series with no readings at the chosen location is rejected.
Deleting a figure takes two clicks (delete, then confirm).

Every configured figure in the selected scene loads a Bokeh time-series chart.
Charts start from a loading placeholder and are filled in after the page
renders; an unconfigured figure asks for a location and analyte, an empty range
says no data is available, and a read-API failure shows an inline error without
breaking the rest of the scene. Range windows use `SITE_TIMEZONE`, and each
request is capped at `DASHBOARD_MAX_POINTS`.

Writes take a process-wide lock plus a file lock, fsync a temporary file, copy
the previous version to `state.json.bak`, and atomically replace `state.json`.
The container runs one uvicorn worker.

A version 1 state file (personal/shared figures) is archived to
`state.v1.json` and replaced with an empty version 2 document on first load. It
is never published into the shared scenes.

A figure binds to a `series_id` plus an optional `location_id`. The UI presents
**Location** and **Analyte** selectors, which resolve to one series at one
location via `/locations` and `/locations/{id}/series`; chart points are then
scoped to that location. Figures left on "All locations" chart the whole series.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `READ_API_URL` | `http://read-api:8080` | Internal read API base URL |
| `DASHBOARD_SECRET_KEY` | required | Signs the session cookie |
| `DASHBOARD_STATE_DIR` | `/var/lib/transducer-dashboard` | JSON state directory |
| `DASHBOARD_SESSION_MAX_AGE` | `28800` | Session lifetime (seconds) |
| `DASHBOARD_DEFAULT_WINDOW_DAYS` | `7` | Range for new figures (`<=1` → 24h, `<=7` → 7d, otherwise 30d) |
| `DASHBOARD_MAX_POINTS` | `2000` | Chart point cap |
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
previous version. `state.v1.json` holds the archived version 1 document after a
schema upgrade.

## Development

```bash
python -m pip install -e "dashboard[dev]"
python -m pytest dashboard/tests
python -m ruff check dashboard
```

Set `DASHBOARD_SECRET_KEY`, `DASHBOARD_STATE_DIR`, and `DASHBOARD_COOKIE_SECURE=false`
for local runs. The read API must be reachable at `READ_API_URL`.
