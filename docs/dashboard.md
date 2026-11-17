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
`Referrer-Policy: same-origin`, and a self-only Content-Security-Policy:

```text
default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; font-src 'self' data:; connect-src 'self' ws: wss:;
worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

No `unsafe-eval` is required.

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
- The selected scene, selected figure, and scene-tray collapse state are stored
  per token label under `ui`.
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
- One uvicorn worker; concurrent edits are last-write-wins.
- Directory mode `0700`, file mode `0600`.

## 4. UI

Scene tray (left, collapsible):

- Lists every shared scene; the selected scene is highlighted.
- `+ New scene` creates and selects a scene.
- `Delete scene` takes two clicks (delete, then confirm) and removes its
  figures.

Scene canvas (center):

- A 12-column grid with 220 px rows; a figure is at least 3 columns by 2 rows.
- Clicking an empty cell creates a minimum-size figure at that cell. If the cell
  cannot fit one, the first free slot is used; a full scene reports instead of
  overlapping figures.
- Each figure has a title ribbon and a chart. Click the ribbon to select and
  highlight the figure; drag the ribbon to move it; drag the lower-right corner
  to resize. Layouts persist immediately and survive reloads and restarts.

Settings tray (right):

- `Title`, `Location` (including "All locations"), `Analyte`, and a
  `24h`/`7d`/`30d` range.
- Locations come from `GET /locations`; analytes come from
  `GET /locations/{id}/series`, or `GET /series` for an un-scoped figure. Option
  labels include the logger serial because a location can host more than one
  instrument over time.
- `Save` rejects a blank title, a missing analyte, a series that does not exist,
  and a location/series pair with no readings together.
- `Delete figure` takes two clicks (delete, then confirm).

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

## 8. Verification

Automated coverage in `dashboard/tests`:

- State v2 validation, round-trip, backup, v1 and legacy-v2 archive,
  concurrent mutations.
- Scene and figure operations, layout bounds, minimum size, selection cleanup.
- FastAPI auth, health, security headers, Panel mount, self-hosted resources,
  and the absence of external CDN URLs.
- Panel shell, grid, settings-tray callbacks, and chart builder/windowing.
- Read-client and settings behavior.

Latest local run: `161 passed`; `ruff check dashboard` and
`mypy dashboard/transducer_dashboard` clean. Startup/restart scripts verified
grid layout persistence, the empty-cell bridge, settings save/reload, and
Bokeh chart rendering into a document.

Manual browser checklist:

1. Log in with a valid read token; an invalid token is rejected.
2. Create and delete scenes; a container restart preserves the remaining scenes.
3. Click an empty cell; a minimum-size figure appears there.
4. Click a ribbon; the figure highlights and the settings tray opens.
5. Drag and resize a figure; the layout survives a reload and a restart.
6. Set title, location, analyte, and range; the chart renders for 24h/7d/30d.
7. Delete a figure; the scene updates and the selection clears.
8. Log in with a second token; the same scenes are visible.
9. Confirm no `POSTGRES_*` variables in the dashboard container.
10. Confirm no external network requests in the browser network log.
11. Test backup and restore of the volume.

## 9. Boundaries

- No changes to `transducer.logger`, `location`, `series`, `reading`,
  `import_file`, or `ingest.job`.
- No dashboard state in PostgreSQL.
- No write routes on the read API.
- No new user/password system; read API keys remain the credential.
- No personal scenes or editor allowlist in the MVP; scenes are shared.
- One uvicorn worker; concurrent layout edits are last-write-wins.
