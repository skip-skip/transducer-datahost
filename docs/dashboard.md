# Dashboard

The dashboard is a separate service that reads the existing read-only API. It
adds no database tables, no database role, and no write routes to the API. The
UI is a HoloViz Panel application mounted inside the FastAPI process
([ADR-028](decisions.md#adr-028-use-holoviz-panel-for-the-dashboard-frontend));
the interaction and state design is in
[dashboard-panel-design.md](dashboard-panel-design.md).

Production URL: `https://<INGEST_HOSTNAME>:8443/dashboard/`.

## 1. Topology

```text
Browser
  |  HTTPS :8443/dashboard/*
  v
Caddy (existing TLS front door)
  |  reverse_proxy dashboard:8080 (prefix stripped)
  v
dashboard service (one uvicorn worker)
  |-- FastAPI: login, logout, health, security headers
  |-- Panel app mounted at /
  |
  | httpx + user's token          | JSON state
  v                               v
read-api:8080                 /var/lib/transducer-dashboard/state.json
(read role, existing)         (dashboard_state volume)
```

Panel and Bokeh assets are served locally by the dashboard process. There is no
Node build and no external CDN request. The dashboard container has no
`POSTGRES_*` environment variables; every data request carries the browser
user's read API token.

## 2. Authentication

The read API keys are the only credential.

1. `GET /dashboard/login` shows a token form.
2. `POST /dashboard/login` calls `GET /api/whoami` with the submitted token.
3. On `200`, the service stores `{label, token}` in a signed session cookie and
   redirects to the Panel application.
4. The Panel application reads the same cookie from `pn.state.cookies` and
   forwards the token on every read API call.
5. A missing or badly signed cookie shows the sign-in view instead of the
   workspace.

Cookie properties:

- `HttpOnly`, `Secure` (configurable for local HTTP only), `SameSite=Strict`.
- Signed with `DASHBOARD_SECRET_KEY`; changing the key invalidates all sessions.
- Lifetime from `DASHBOARD_SESSION_MAX_AGE` (default 8 hours).
- Path is the app root (`/dashboard` in production).
- The token and `Authorization` header are never logged or rendered.

If a read token is revoked at the read API, panel requests fail and the affected
chart or settings action shows an inline error; sign out and sign in again with
a valid token. The session cookie itself is not revalidated on every request.

The service sends `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: same-origin`, and a self-only Content-Security-Policy. There are
two policies. The Panel document at `/` and its `/static/` assets need
`'unsafe-eval'` and `blob:`:

```text
default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:;
style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:;
connect-src 'self' ws: wss:; worker-src 'self' blob:; frame-ancestors 'none';
base-uri 'self'; form-action 'self'; object-src 'none'
```

`/login`, `/logout`, `/health`, and any route added later keep the strict policy,
which omits `'unsafe-eval'` and `blob:` from `script-src`. The relaxed policy is
selected by route path (`/` and `/static/`), with `root_path` stripped first because
uvicorn prepends `--root-path` to `scope["path"]`:

```text
default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; font-src 'self' data:; connect-src 'self' ws: wss:;
worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self';
object-src 'none'
```

The relaxation is required by the UI itself, not by configuration: Panel's
`GridStack` layout is a `ReactiveHTML` component whose scripts Panel compiles with
`new Function`, and the bundled `es-module-shims` loads Bokeh's ES modules from
`blob:` URLs. ADR-028 records the sign-off and the compensating controls.

With devtools open the console still reports blocked requests for
`cdn.jsdelivr.net/*.map`. Those are source-map fetches triggered by
`//# sourceMappingURL=` comments inside the locally served `gridstack-all.js` and
`es-module-shims.min.js`; no code is loaded from the CDN and rendering is
unaffected. Do not add `cdn.jsdelivr.net` to `connect-src`.

## 3. State

State is one JSON file on the `dashboard_state` volume. It is deliberately not
in PostgreSQL.

```json
{
  "version": 2,
  "scenes": [
    {
      "id": "6b1e0c...",
      "name": "Water levels",
      "figures": [
        {
          "id": "5f0c8e...",
          "title": "GC-1980 water level",
          "location_id": 7,
          "series_id": 42,
          "range": "7d",
          "layout": {"x": 0, "y": 0, "w": 3, "h": 2},
          "created_at": "2026-11-11T13:00:00Z",
          "updated_at": "2026-11-11T13:00:00Z"
        }
      ],
      "created_at": "2026-11-11T13:00:00Z",
      "updated_at": "2026-11-11T13:00:00Z"
    }
  ],
  "ui": {
    "analyst-1": {
      "selected_scene_id": "6b1e0c...",
      "selected_figure_id": "5f0c8e...",
      "scenes_tray_collapsed": false
    }
  }
}
```

Rules:

- Scenes are shared between every valid read token; there are no personal
  scenes and no editor allowlist in the MVP.
- A figure binds to a `series_id` and an optional `location_id`. Location and
  analyte are UI selectors that resolve to one series at one location, so
  multi-channel loggers, unit changes, and loggers that moved between wells stay
  unambiguous.
- `location_id` is null for an un-scoped figure: it charts the whole series
  across every location, and the location selector shows "All locations".
- `series_id` is null until a figure is configured; such a figure shows a
  prompt instead of a chart.
- `range` is one of `24h`, `7d`, `30d`. New figures start at the range implied
  by `DASHBOARD_DEFAULT_WINDOW_DAYS`.
- The selected scene, selected figure, and scenes drawer collapse state are stored
  per token label under `ui`. The settings drawer collapse state and unpublished
  figure drafts are session-only and are not persisted.
- Figure and scene IDs are UUIDs because there is no database identity column.

A version 1 state file (personal/shared figures) is archived to
`state.v1.json` and replaced with an empty version 2 document on first load. It
is never published into the shared scenes.

An early version 2 draft stored top-level `shared` and `personal` keys instead
of `scenes` and `ui`. Such a file is archived to `state.v2-legacy.json` and
replaced with an empty version 2 document on first load, so a container that
was left on the abandoned schema still starts. Any scenes in the archive can be
recreated by hand; they are not imported automatically.

Write safety:

- A process-wide `threading.RLock` plus `filelock` around every read-modify-write.
- Write to a temporary file, `fsync`, copy the current file to `state.json.bak`,
  then `os.replace` the temporary file into place.
- Validate the document on load and before every write. A corrupt file stops the
  service with a clear error instead of silently discarding state.
- One uvicorn worker. A publish writes only the settings the user changed. If
  another session changed one of those settings after the draft started, the
  publish is rejected with a conflict modal instead of silently overwriting it;
  changes to settings the draft does not touch are merged.
- Directory mode `0700`, file mode `0600`.

## 4. UI

A single header banner spans the top of the page in three regions: the brand
`Transducer dashboard` on the left, the selected scene name and its figure count
(`Demo · 2 figures`, or `No scene selected`) in the center, and
`Signed in as <user>` with Sign out on the right. The canvas below the banner is
just the grid; the scene title and count are not repeated there.

Left rail (two independent full-height ribbon drawers):

The rail is a row of vertical tabs docked to the left edge of the page. Scenes
is the leftmost tab and figure settings is immediately to its right. Each title
reads bottom-to-top, and its arrow points right when the drawer is closed and
down when it is open. Opening a drawer reveals a 340 px body to the right; the
tab and the body both span the full height of the viewport below the header, and
the body scrolls when its content is taller than the screen. Drawers stay in
flow left-to-right, so opening scenes pushes the settings drawer and the canvas
to the right, and opening settings pushes the canvas. Both drawers can be open
at once.

Scenes drawer:

- Lists every shared scene; the selected scene is highlighted.
- `+ New scene` creates and selects a scene.
- `Delete scene` takes two clicks (delete, then confirm) and removes its
  figures.
- Its collapse state persists per token label.

Figure settings drawer:

- `Title`, `Location` (including "All locations"), `Analyte`, and a
  `24h`/`7d`/`30d` range.
- Locations come from `GET /locations`; analytes come from
  `GET /locations/{id}/series`, or `GET /series` for an un-scoped figure. Option
  labels include the logger serial because a location can host more than one
  instrument over time.
- Edits are held in a session-local draft. The form and that figure's chart show
  the draft immediately, and the figure card gets a yellow border (orange while
  selected) to mark it unpublished. The button reads `Publish` and is enabled
  while changes exist; it reads `Published` and is disabled when the figure
  matches the shared state. Reloading the page discards drafts.
- `Publish` writes only the changed settings and rejects a blank title, a missing
  analyte, a series that does not exist, and a location/series pair with no
  readings together. If another session changed the same settings after the
  draft started, a modal offers **Overwrite** or **Revert to server state**;
  closing it keeps the draft.
- `Delete figure` takes two clicks (delete, then confirm).
- Its collapse state is per browser session and resets on reload. Selecting or
  creating a figure updates the drawer contents without changing its open state;
  only the drawer tab toggles it.

Scene canvas (right of the drawers):

- A fixed 12-column, 6-row grid with 220 px rows; the smallest figure is a
  single cell. The grid scales in width with the browser but never changes its
  row count, height, or card density when figures are created.
- The canvas is light gray with 1 px grid lines on the cell boundaries, so
  empty cells read as a working grid behind the white figure cards.
- A **Lock** / **Unlock** button fixed to the bottom-right overlays the canvas.
  The lock is session-local: it is not saved and resets to unlocked on reload.
  The button stays visible but is disabled while no scene is selected.
- While unlocked, the canvas is editable: figures move and resize, and painting
  an empty rectangle creates a figure. Hovering an empty cell shows a one-cell
  preview; click to create a single-cell figure, or drag to grow the highlight
  toward the cursor. The span stops at the first occupied cell. Releasing
  creates a figure with exactly that span and selects it. A drag that starts on
  a figure moves or selects it instead.
- After release, the painted cells immediately show a card-shaped “Creating
  figure…” placeholder while the backend persists the figure. It is replaced by
  the real card as soon as the figure arrives, without rebuilding the rest of
  the shell.
- **Lock** disables moving, resizing, painting, and figure selection.
- Each figure has a title ribbon and a chart. Click the ribbon to select and
  highlight the figure; drag the ribbon to move it; drag the lower-right corner
  to resize. Layouts persist immediately and survive reloads and restarts.
  Figures stay inside the fixed canvas; when it fills up, creating another
  figure reports that the scene has no room instead of growing the grid.

Charts:

- Bokeh line charts for every configured figure in the selected scene, loaded
  when the scene opens. Cards show a loading placeholder while the requests run.
- Data comes from `GET /series/{id}/points` with the figure's optional
  `location_id`, the site time zone, and `DASHBOARD_MAX_POINTS`. Hover shows the
  site-local timestamp and value.
- An unconfigured figure asks for a location and analyte; an empty range says no
  data is available; a read API failure shows an inline error without breaking
  the rest of the scene.

## 5. Read API additions

All additions are read-only and require no schema change:

| Endpoint | Purpose |
|---|---|
| `GET /whoami` | Returns the token label as `{"label": "..."}` |
| `GET /series/{id}` | One series with reading count and first/last timestamps |
| `GET /locations/{id}/series` | Series with readings recorded at one location, with location-scoped counts (404 when the location is unknown) |
| `GET /series/{id}/points?start=&end=&points=&tz=&location_id=` | Server-side time buckets (`ts`, `ts_utc`, `value`, `min_value`, `max_value`, `n`) bounded by `points`; the optional `location_id` restricts buckets to readings recorded at that location |

`/points` uses `date_bin` with a width chosen so the inclusive window returns at
most `points` rows. The existing `READ_MAX_WINDOW_DAYS` and `READ_MAX_ROWS`
limits still apply.

Location scoping matters because loggers move between wells: `series` carries no
location, and a reading's location comes from its `import_file`. Both new
queries filter through `v_reading`/`v_reading_utc`, so the read role needs no
new table grants and an un-scoped request keeps returning the whole series.

## 6. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `READ_API_URL` | `http://read-api:8080` | Internal read API base URL |
| `DASHBOARD_SECRET_KEY` | required | Signs the session cookie; at least 16 characters |
| `DASHBOARD_STATE_DIR` | `/var/lib/transducer-dashboard` | State directory |
| `DASHBOARD_SESSION_MAX_AGE` | `28800` | Session lifetime (seconds) |
| `DASHBOARD_DEFAULT_WINDOW_DAYS` | `7` | Range for new figures (`<=1` gives `24h`, `<=7` gives `7d`, otherwise `30d`) |
| `DASHBOARD_MAX_POINTS` | `2000` | Chart point cap per figure |
| `DASHBOARD_COOKIE_SECURE` | `true` | `false` only for local HTTP development |
| `DASHBOARD_REQUEST_TIMEOUT` | `15` | Read API timeout (seconds) |
| `SITE_TIMEZONE` | `America/New_York` | Range calculation and hover time zone |

## 7. Operations

Start or update the service:

```bash
docker compose up -d --build read-api dashboard caddy
docker compose restart caddy      # after any Caddyfile change
docker compose ps
```

`docker compose up -d` recreates a container only when its image or service
definition changed. The Caddyfile is bind-mounted into the caddy container and
read by the caddy process only at start, so after editing it a restart (or
`docker compose up -d --force-recreate caddy`) is required; a rebuild alone
never reloads it.

Open `https://<INGEST_HOSTNAME>:8443/dashboard/` and sign in with a read token.
The bare `/dashboard` without a trailing slash is redirected to `/dashboard/`
by a `redir` in `caddy/Caddyfile`.

### TLS termination

Caddy terminates TLS and proxies to the dashboard over plain HTTP. Without
further configuration uvicorn would see `scheme=http`, and the absolute URLs the
app generates (redirects, static assets, Panel websocket) would be `http://` —
browsers block those as mixed content, so styles and scripts do not load and
the login form cannot submit. The dashboard therefore runs uvicorn with
`--proxy-headers --forwarded-allow-ips "*"`, so Caddy's
`X-Forwarded-Proto: https` sets the scheme and every generated URL is
`https://`.

The wildcard allow-list is safe because the dashboard's port is reachable only
from the internal Compose network, and Caddy overwrites `X-Forwarded-Proto` on
every request it proxies, so a browser cannot spoof the header through the
edge.

Back up the state file:

```bash
docker compose exec dashboard cat /var/lib/transducer-dashboard/state.json \
  > dashboard-state-$(date +%F).json
```

The operations backup script archives the whole volume instead, alongside the
database: `scripts/backup.sh` (see [operations.md](operations.md#3-backups)).

Restore:

1. `docker compose stop dashboard`.
2. Replace `/var/lib/transducer-dashboard/state.json` on the volume.
3. Ensure ownership by uid 10001 (`appuser`).
4. `docker compose start dashboard`.

`scripts/restore.sh --dashboard-state BACKUP.tar.gz --force` automates these
steps and restores ownership. `state.json.bak` holds the previous version. Atomic replace makes a live `cat`
safe. A restored version 1 file is archived and replaced as described in
section 3.

### State load failures

`StateError: state has unknown keys: personal, shared` means the volume holds
the abandoned intermediate version 2 document. The current release archives it
to `state.v2-legacy.json` and starts with an empty dashboard. If the image is
older than that fix, stop the dashboard and move the file aside first:

```bash
docker compose stop dashboard
docker compose run --rm --entrypoint sh dashboard -c \
  'cd /var/lib/transducer-dashboard && mv state.json state.json.v2legacy && mv state.json.bak state.json.bak.v2legacy'
docker compose up -d dashboard
```

Any other `StateError` means the file is genuinely malformed. Inspect
`state.json.bak` and restore it as above rather than deleting the volume.

### Local development

The dashboard can run on a workstation without Docker, PostgreSQL, or the VM
read API:

```powershell
.\scripts\dev_dashboard.ps1                     # fast Panel loop, fake data
.\scripts\dev_dashboard.ps1 -Mode prod          # FastAPI + Panel, fake data
```

The fast loop serves `dashboard/dev_panel.py` with `panel serve --dev` on
`http://127.0.0.1:8081/`. It reloads the browser page when a Python module
changes, but it has no login, CSP, or root-path proxy. The prod-like loop runs
`uvicorn --reload` on `http://127.0.0.1:8080/`, so it exercises login, cookies,
security headers, and the Panel mount; sign in with the fake token `dev`.
`-RealApi` uses `READ_API_URL` instead of fixtures, and `-CaBundle` sets
`SSL_CERT_FILE` for the VM's self-signed certificate. `-Mode prod -RootPath
/dashboard` serves under the production prefix and strips it the way Caddy does;
the fast loop has no proxy and rejects `-RootPath`.

Dev state is written to `%TEMP%\transducer-dashboard-dev`, never to the
production volume. `-ResetState` deletes only that directory; `-NoSeed` starts
with an empty dashboard instead of the demo scene. See
[`../dashboard/README.md`](../dashboard/README.md#development) for all options.

## 8. Verification

Automated coverage in `dashboard/tests`:

- State v2 validation, round-trip, backup, v1 and legacy-v2 archive,
  concurrent mutations.
- Scene and figure operations, layout bounds, minimum size, selection cleanup.
- FastAPI auth, health, security headers, the per-route CSP, Panel mount,
  self-hosted resources, and the absence of external CDN URLs.
- Panel shell, grid, layout lock, canvas painting, settings-drawer callbacks,
  and chart builder/windowing.
- Read-client and settings behavior.
- Dev fixtures, the demo seed, and the prod-like login path.

Latest local run: `244 passed`; `ruff check dashboard` and
`mypy dashboard/transducer_dashboard` clean. Startup/restart scripts verified
grid layout persistence, the lock toggle, the paint bridge, settings
publish/reload, and Bokeh chart rendering into a document.

Manual browser checklist:

1. Log in with a valid read token; an invalid token is rejected.
2. Create and delete scenes; a container restart preserves the remaining scenes.
3. While unlocked, drag a ribbon to move a figure and drag its corner to resize;
   the layout survives a reload and a restart.
4. A plain click on an empty cell creates a 1 x 1 figure. Hover to see the
   one-cell preview, then drag to paint a larger footprint and release; the
   painted cells instantly show a “Creating figure…” card that is replaced by
   the real figure with that exact span, selected. Painting stops at an existing
   figure, and a drag that starts on a figure moves or selects it. The grid
   keeps the same size, row count, and card density before and after the figure
   appears.
5. Click Lock; figures no longer move, resize, or respond to selection, and
   painting is unavailable. Click Unlock to edit again. Reload; the canvas
   starts unlocked.
6. Collapse each drawer independently. The scenes drawer stays collapsed after a
   reload; the settings drawer resets to open.
7. Click a ribbon; the figure highlights and the settings drawer keeps its
   current open/closed state.
8. Set title, location, analyte, and range without publishing; the figure card
   gets a yellow border (orange while selected), the chart previews the draft,
   and the button reads `Publish`. Reload; the draft is gone and the card is
   unmarked.
9. Publish; the button changes to `Published`, the border clears, the chart
   renders for 24h/7d/30d, and the change survives a reload.
10. In a second session, edit the same setting and publish, then publish the first
    session's draft; the conflict modal appears. Overwrite keeps the first
    session's value; Revert restores the second session's value.
11. Delete a figure; the scene updates, the selection clears, and any draft for
    it is dropped.
12. Log in with a second token; the same scenes are visible.
13. Confirm no `POSTGRES_*` variables in the dashboard container.
14. Confirm no external network requests in the browser network log. The
    `cdn.jsdelivr.net/*.map` messages that appear with devtools open are blocked
    source-map fetches from local bundle comments, not code loads.
15. Confirm the console has no `EvalError` and no blocked `blob:` script, and that
    `/login` still returns the strict CSP without `'unsafe-eval'`.
16. Test backup and restore of the volume.

## 9. Boundaries

- No changes to `transducer.logger`, `location`, `series`, `reading`,
  `import_file`, or `ingest.job`.
- No dashboard state in PostgreSQL.
- No write routes on the read API.
- No new user/password system; read API keys remain the credential.
- No personal scenes or editor allowlist in the MVP; scenes are shared.
- One uvicorn worker; concurrent layout edits are last-write-wins.
