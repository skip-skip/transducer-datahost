# Dashboard

The dashboard is a separate service that reads the existing read-only API. It
adds no database tables, no database role, and no write routes to the API.

Production URL: `https://<INGEST_HOSTNAME>:8443/dashboard/`.

> **Scenes redesign:** this document describes the current dashboard. Its state is `state.json` `version: 2`, which
> stores scenes (flat containers of figures) with per-figure grid layouts. The scene list, nested options pane, grid
> workspace with drag/resize/snap, and batched layout persistence have shipped. Preparation, target state shape,
> migration, and techstack details are in [dashboard-scenes.md](dashboard-scenes.md). Boundaries remain the same (no DB
> schema/roles, no dashboard state in PostgreSQL, no read-API writes, read-API-token auth).

## 1. Topology

```text
Browser
  |  HTTPS :8443/dashboard/*
  v
Caddy (existing TLS front door)
  |  reverse_proxy dashboard:8080 (prefix stripped)
  v
dashboard service (FastAPI + Jinja2 + HTMX + Plotly)
  |                         |
  | httpx + user's token    | JSON read/write
  v                         v
read-api:8080          /var/lib/transducer-dashboard/state.json
(read role, existing)  (dashboard_state Docker volume)
```

The dashboard container has no `POSTGRES_*` environment variables. Every data
request carries the browser user's read API token. The read API remains the only
component with database credentials.

## 2. Authentication

The read API keys are the only credential.

1. `GET /dashboard/login` shows a token form.
2. `POST /dashboard/login` calls `GET /api/whoami` with the submitted token.
3. On `200`, the service stores `{label, token}` in a signed session cookie.
4. Every page and partial uses that token for read API calls.
5. A `401` from the read API clears the session and redirects to login.

Cookie properties:

- `HttpOnly`, `Secure` (configurable for local HTTP only), `SameSite=Strict`.
- Signed with `DASHBOARD_SECRET_KEY`; changing the key invalidates all sessions.
- Lifetime from `DASHBOARD_SESSION_MAX_AGE` (default 8 hours).
- Path is the app root (`/dashboard` in production).
- The token and `Authorization` header are never logged.

The service also sends `X-Frame-Options: DENY`, `X-Content-Type-Options:
nosniff`, `Referrer-Policy: same-origin`, and a self-only Content-Security-Policy.

## 3. State

State is one JSON file on the `dashboard_state` volume. It is deliberately not
in PostgreSQL. Version 2 organizes figures into **scenes**: flat containers with
grid settings and a layout for each figure. The UI lists scenes, edits the
selected scene or figure in the options pane, and renders the selected scene's
figures as panels in a grid workspace. Dragging or resizing a panel updates its
stored layout through a debounced batched save.

```json
{
  "version": 2,
  "shared": {
    "scenes": [
      {
        "id": "0f3c9a...",
        "name": "Default scene",
        "position": 0,
        "grid": { "cols": 12, "row_height": 220 },
        "figures": [
          {
            "id": "5f0c8e...",
            "name": "GC-1980 water level",
            "series_id": 42,
            "location_id": 7,
            "config": {},
            "layout": { "x": 0, "y": 0, "w": 6, "h": 2 },
            "created_by": "analyst-1",
            "created_at": "2026-11-11T13:00:00Z",
            "updated_at": "2026-11-11T13:00:00Z"
          }
        ],
        "created_by": "analyst-1",
        "created_at": "2026-11-11T13:00:00Z",
        "updated_at": "2026-11-11T13:00:00Z"
      }
    ]
  },
  "personal": {
    "analyst-1": {
      "scenes": [],
      "ui": {
        "view": "personal",
        "personal": {
          "selected_scene_id": null,
          "selected_figure_id": null,
          "folders_collapsed": false,
          "options_collapsed": false
        },
        "shared": {
          "selected_scene_id": null,
          "selected_figure_id": null,
          "folders_collapsed": false,
          "options_collapsed": false
        },
        "tz": "local"
      }
    }
  }
}
```

Rules:

- A **scene** is a flat container of figures (no nested scenes). It carries a
  name, display position, grid settings (`cols`, `row_height`), and figures.
- A **figure** belongs to exactly one scene and keeps a grid `layout`
  (`x`, `y`, `w`, `h`) that must fit inside the scene's `cols`.
- A figure binds to a `series_id` and, since location-based selection, an
  optional `location_id`. Location and analyte are UI selectors that resolve
  to one series at one location, so multi-channel loggers, unit changes, and
  loggers that moved between wells stay unambiguous.
- `location_id` is null for figures saved before location scoping and for
  deliberately un-scoped ones: they chart the whole series across every
  location, and the location selector shows "All locations" until re-saved.
- Personal scenes are visible only to the matching token label.
- Shared scenes are visible to every valid read token. `DASHBOARD_SHARED_EDITORS`
  optionally restricts who may edit them; empty means any valid read key.
- Selected scene, selected figure, pane collapse, and view are stored per user.
- Scene and figure IDs are UUIDs because there is no database identity column.

Migration from version 1:

- A version 1 document is migrated on first load: every scope/owner gets one
  `Default scene` containing its previous figures, figures get sequential
  non-overlapping layouts, and the old selected figure maps to the new selected
  scene plus figure.
- The migrated document is validated, written through the normal atomic path,
  and the previous `state.json` is kept as `state.json.bak`. Subsequent loads
  see version 2 and are unchanged.

Write safety:

- `filelock` around every read-modify-write.
- Write to `state.json.tmp`, `fsync`, copy the current file to `state.json.bak`,
  then `os.replace` the temporary file into place.
- Validate the document on load and before every write. A corrupt file stops the
  service with a clear error instead of silently discarding state.
- One uvicorn worker.
- Directory mode `0700`, file mode `0600`.

## 4. UI

The dashboard has three regions:

Scenes pane (left):

- Collapsible, scope tabs `My scenes` and `Shared`.
- Each row is a scene with its figure count; the selected scene is highlighted.
- `+ New scene` creates and selects a scene in the active scope.
- Pane collapse and scene/figure selection are saved per user.

Options pane (nested right of the scenes pane):

- With a scene selected and no figure: scene name, grid columns, row height,
  `+ Add figure`, and delete scene.
- With a figure selected: name, location, analyte, save, delete, and a read-only
  layout summary.
- `DASHBOARD_SHARED_EDITORS` rules apply; read-only shared views disable or hide
  mutations.
- Pane collapse is saved per user.

Grid workspace (main):

- Uses the scene's `cols` and `row_height` to place figures in their stored
  `layout` cells.
- Each figure is a panel with a drag grip, title/select control, range buttons,
  and its own chart. Panels load charts independently through HTMX, so one
  failing figure does not block the rest.
- Panels drag by their grip, resize from the lower-right handle, and snap to grid
  cells (GridStack.js 14.1.1, vendored under `static/vendor/` with its MIT
  license). The grid compacts vertically.
- Layout changes autosave after a 500 ms debounce as one batched request to
  `POST /scenes/{scope}/{scene_id}/layout`; the status line above the grid reports
  success or failure. Read-only shared views disable drag and resize.
- Range buttons (`24h`, `7d`, `30d`) refresh only that panel; charts use
  server-side bucketing.
- Multiple Plotly charts render independently on one page.
- The workspace scrolls horizontally on narrow screens instead of reflowing
  panels, so stored grid positions stay meaningful.

A new figure starts with a default name, no series, and the next free grid slot.
Saving rejects a series that does not exist and a location/series pair with no
readings together, so a figure cannot silently render an empty chart. A
location with no series reports it in the analyte selector.

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
| `DASHBOARD_DEFAULT_WINDOW_DAYS` | `7` | Initial chart range |
| `DASHBOARD_MAX_POINTS` | `2000` | Chart point cap |
| `DASHBOARD_SHARED_EDITORS` | empty | Labels allowed to edit shared figures |
| `DASHBOARD_COOKIE_SECURE` | `true` | `false` only for local HTTP development |
| `DASHBOARD_REQUEST_TIMEOUT` | `15` | Read API timeout (seconds) |
| `SITE_TIMEZONE` | `America/New_York` | Range calculation time zone |

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
further configuration uvicorn would see `scheme=http`, and the absolute URLs
the app generates (form actions, static assets, redirects) would be `http://`
— browsers block those as mixed content, so styles and scripts do not load and
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

Restore:

1. `docker compose stop dashboard`.
2. Replace `/var/lib/transducer-dashboard/state.json` on the volume.
3. Ensure ownership by uid 10001 (`appuser`).
4. `docker compose start dashboard`.

`state.json.bak` holds the previous version. Atomic replace makes a live `cat`
safe.

## 8. Verification

1. Log in with a valid read token; an invalid token is rejected.
2. Create personal and shared scenes; rename/delete scenes, add figures, rename
   figures, change location/analyte, change range, and delete figures. Pick a
   well whose logger also served another location and confirm the chart excludes
   the other deployments.
3. Drag a panel by its grip and resize it from the lower-right handle; reload and
   confirm the layout was saved. Restart the dashboard container; scenes,
   figures, layouts, and selected state survive.
4. Log in with a second token: shared scenes are visible, the first user's
   personal scenes are not, and read-only shared scenes cannot be dragged or
   resized.
5. `docker compose exec dashboard env` shows no `POSTGRES_*` variables.
6. Rotate a read token; the old session returns to login on the next API call.
7. Confirm a 30-day high-frequency chart returns at most `DASHBOARD_MAX_POINTS`
   points.
8. Test backup and restore of the volume.
9. Behind the TLS front door, the login page's form action and asset URLs are
   `https://`; the browser reports no mixed content.

## 9. Boundaries

- No changes to `transducer.logger`, `location`, `series`, `reading`,
  `import_file`, or `ingest.job`.
- No dashboard state in PostgreSQL.
- No write routes on the read API.
- No new user/password system; read API keys remain the credential.
- Scenes are flat containers of figures; scenes do not nest.
