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
- `ui[<token label>]`: the selected scene, selected figure, and scenes drawer
  collapse state for each user. The settings drawer collapse state and
  unpublished figure drafts are per session and are not stored.

Scenes are shared between all users; there are no personal scenes. The grid is
12 columns wide with 220 px rows, and a figure is at least 3 columns by 2 rows.

In the browser, use the bottom-right speed dial to pick a canvas tool. **Add**
paints a figure footprint on empty cells; **Arrange** (the default) moves and
resizes figures; **Lock** freezes the layout. Click a figure's title ribbon to
select it, and drag the ribbon or its lower-right corner while arranging. The
tool choice is per session and resets to Arrange on reload. Layout changes are
saved immediately to the shared state. The settings drawer opens only from its
tab; selecting or creating a figure updates its contents without changing
whether it is open.

The header banner shows the brand on the left, the selected scene name and figure
count in the center, and the signed-in user with Sign out on the right. The
canvas below it is just the grid.

The two rail drawers (scenes and figure settings) are full-height ribbons on
the left edge. Scenes is the leftmost tab and figure settings sits immediately
to its right. Each title is a vertical 44 px tab; opening a drawer reveals a
340 px body to the right whose content scrolls within the viewport. The drawers
stay in flow left-to-right, so opening scenes pushes the settings drawer and the
canvas right, and opening settings pushes the canvas. Both drawers can be open
at once, and both span the full height below the header.

The settings drawer edits the title, location, analyte, and range (`24h`, `7d`,
`30d`). Edits apply to a session-local draft first: the form and that figure's
chart preview the change, and the figure card gets a yellow border (orange while
selected) to show it is unpublished. The drawer button reads `Publish` and is
enabled while the figure has changes; it reads `Published` and is disabled when
the figure matches the shared state. Reloading the page discards drafts.

Publishing writes only the changed settings and verifies the result against the
read API: an unknown series is rejected, and a series with no readings at the
chosen location is rejected. If another session changed the same settings after
the draft started, publishing opens a modal that offers **Overwrite** or **Revert
to server state**; closing the modal keeps the draft. Changes to other settings
are merged because the publish only writes the fields that were edited. Layout
drags and resizes still save immediately and are not part of a draft. Deleting a
figure takes two clicks (delete, then confirm).

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

Install the package and test tools once:

```powershell
python -m pip install -e "dashboard[dev]"
```

Two local loops use fake fixtures and a temp state directory, so neither needs
Docker, PostgreSQL, or the VM read API.

Fast loop — Panel only, no login, CSP, or root-path proxy:

```powershell
.\scripts\dev_dashboard.ps1
# http://127.0.0.1:8081/
```

`panel serve --dev` reloads the session and the browser page when a Python
module changes. Layout, card, chart, and CSS edits are visible in about a
second. The stylesheets live in Python strings, so they reload with the module.

Prod-like loop — FastAPI + Panel, real login, cookies, CSP, and mount:

```powershell
.\scripts\dev_dashboard.ps1 -Mode prod
# http://127.0.0.1:8080/ — sign in with token "dev"
```

`uvicorn --reload` restarts the process, so the Panel session drops and the
browser needs a refresh. Use this loop for authentication, security headers,
`root_path`, and integration changes.

To exercise the production URL prefix in the prod-like loop:

```powershell
.\scripts\dev_dashboard.ps1 -Mode prod -RootPath /dashboard
# http://127.0.0.1:8080/dashboard/
```

The dev server strips the prefix the way Caddy does, so login redirects, cookie
paths, static assets, and the Panel websocket all keep the prefix. The fast
Panel loop has no proxy and rejects `-RootPath`.

Real read API instead of fixtures:

```powershell
$env:READ_API_URL = "https://172.19.68.64:8443/api"
.\scripts\dev_dashboard.ps1 -Mode prod -RealApi -CaBundle C:\certs\server.crt
```

`-CaBundle` sets `SSL_CERT_FILE` for the VM's self-signed certificate. The URL
host must match the certificate's subject or SAN; if it does not, use the
hostname in `READ_API_URL`.

Options:

| Option | Purpose |
|---|---|
| `-Mode fast\|prod` | Panel-only loop (default) or FastAPI loop |
| `-Port` | Override 8081 (fast) or 8080 (prod) |
| `-RootPath /dashboard` | Prod mode only: serve under the production prefix |
| `-ResetState` | Delete only `%TEMP%\transducer-dashboard-dev` |
| `-NoSeed` | Start empty instead of creating the demo scene |
| `-RealApi` | Use `READ_API_URL` instead of fake data |
| `-Api URL` | Set `READ_API_URL` for this run |
| `-CaBundle PATH` | Set `SSL_CERT_FILE` for this run |

Dev state is written to `%TEMP%\transducer-dashboard-dev`, never to the
production volume. `state.json` there is independent of the Compose volume, so
production scenes are not visible unless you copy the file.

Tests and lint:

```powershell
python -m pytest dashboard/tests
python -m ruff check dashboard
python -m mypy dashboard/transducer_dashboard
```
