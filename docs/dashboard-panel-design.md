# Panel dashboard design

**Status:** Accepted; implementation in progress ([ADR-028](decisions.md#adr-028-use-holoviz-panel-for-the-dashboard-frontend))

This document defines the replacement dashboard frontend. It supersedes the
FastAPI + Jinja2 + HTMX + Plotly UI described in [dashboard.md](dashboard.md)
once the implementation lands.

## 1. Purpose and scope

The dashboard becomes a Python-only Panel application mounted inside the existing
FastAPI service. The MVP deliberately stays small:

- A collapsible left tray lists **scenes**.
- A scene is a named collection of figures placed in a gridded canvas.
- Scenes can be created and deleted.
- **All scenes are shared between all users.** There are no personal scenes.
- Clicking an empty grid cell creates a new figure at the minimum size.
- A figure has a title ribbon. Clicking the ribbon selects the figure; dragging
  the ribbon moves it within the grid.
- Selecting a figure highlights it and opens its settings in a right-hand tray.
- Only time-series figures are implemented.

Non-goals for this MVP:

- Non-time-series figure types (maps, tables, gauges, and so on).
- Personal or private scenes.
- Scene rename.
- Lazy viewport chart loading or request throttling.
- Real-time collaboration or conflict resolution.
- Dashboard state in PostgreSQL.
- React, Node, or a JavaScript build step.

## 2. Architecture

```text
Browser
  |  HTTPS :8443/dashboard/*
  v
Caddy (unchanged; strips /dashboard)
  |
  v
FastAPI service
  |-- /login, /logout, /health
  |-- security headers
  |-- Panel app mounted at "/" via panel.io.fastapi.add_applications
  |       |
  |       | httpx + browser user's read token
  |       v
  |   read-api:8080
  |
  |-- StateStore (JSON + file lock)
          v
      /var/lib/transducer-dashboard/state.json
      (dashboard_state Docker volume)
```

- Panel is mounted in the same process as FastAPI. There is no second service and
  no separate Panel port.
- FastAPI keeps authentication, the health endpoint, the security headers, and
  the `StateStore`/`ReadClient` dependencies.
- Panel owns the dashboard UI and calls the existing read API through
  `ReadClient` using the browser user's token.
- The dashboard still has no database credentials and no write route on the read
  API.
- Panel **>= 1.5** is required for `panel.io.fastapi.add_applications`. The
  development environment was upgraded from 1.4.4 and the mount is verified with
  Panel 1.10 and Bokeh 3.10.

### Mount verification

The mount-stage placeholder confirmed the integration assumptions:

- FastAPI `/login`, `/logout`, and `/health` routes take precedence over the
  Panel application mounted at `/`.
- `pn.state.cookies` exposes the existing signed session cookie to the Panel
  application.
- Security headers wrap Panel responses because the header middleware is added
  after `add_applications`.
- Panel serves its Bokeh and extension JavaScript locally; the page contains no
  CDN references.
- `--root-path /dashboard` preserves the login redirect and cookie path, and the
  relative Panel asset URLs resolve under the prefix.
- The page renders under a policy without `unsafe-eval`: inline scripts and
  styles plus `ws:`/`wss:` connections are the required additions.

## 3. Authentication and session flow

The existing read-token login is preserved.

1. `GET /dashboard/login` renders the FastAPI login form.
2. `POST /dashboard/login` calls `GET /api/whoami` with the submitted token.
3. On success, FastAPI stores `{label, token}` in the existing signed,
   `HttpOnly`, `Secure`, `SameSite=Strict` cookie.
4. The Panel app reads that cookie from `pn.state.cookies` and validates it with
   the same `itsdangerous` serializer and `DASHBOARD_SECRET_KEY`.
5. If the cookie is missing or invalid, the Panel app renders a sign-in link to
   `/login` instead of the workspace. FastAPI may also redirect unauthenticated
   page requests before Panel renders.
6. `POST /dashboard/logout` clears the cookie and redirects to the login page.
7. A `401` from the read API clears the session and returns the user to login.

The token is never rendered into the page, written to logs, or exposed through a
query string. The websocket session is only useful when the cookie is valid
because the app refuses to load data without a session.

### Content Security Policy

Panel and Bokeh require a wider policy than the previous `script-src 'self'`.
The mount-stage policy is:

- `default-src 'self'`
- `script-src 'self' 'unsafe-inline'`
- `style-src 'self' 'unsafe-inline'`
- `img-src 'self' data:`
- `font-src 'self' data:`
- `connect-src 'self' ws: wss:`
- `worker-src 'self' blob:`
- `frame-ancestors 'none'`
- `base-uri 'self'`
- `form-action 'self'`

`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and
`Referrer-Policy: same-origin` stay. The page renders without `unsafe-eval`; if a
future Panel component requires it, that is a security regression and must be
called out in ADR-028 before it is accepted.

Panel resources must be served by the dashboard itself. No CDN request is
acceptable, and the browser network log is part of the verification checklist.

## 4. State model (version 2)

State remains one JSON file on the `dashboard_state` volume.

```json
{
  "version": 2,
  "scenes": [
    {
      "id": "7f3c2a...",
      "name": "Aquifer tests",
      "figures": [
        {
          "id": "b91e4d...",
          "title": "GC-1980 water level",
          "location_id": 7,
          "series_id": 42,
          "range": "7d",
          "layout": { "x": 0, "y": 0, "w": 3, "h": 2 },
          "created_at": "2026-11-17T13:00:00Z",
          "updated_at": "2026-11-17T13:00:00Z"
        }
      ],
      "created_at": "2026-11-17T13:00:00Z",
      "updated_at": "2026-11-17T13:00:00Z"
    }
  ],
  "ui": {
    "analyst-1": {
      "selected_scene_id": "7f3c2a...",
      "selected_figure_id": "b91e4d...",
      "scenes_tray_collapsed": false
    }
  }
}
```

### Invariants

- `version` is `2`.
- `scenes` is a list. Scene IDs and figure IDs are unique non-empty strings.
- Scene and figure names/titles are non-empty strings.
- `location_id` and `series_id` are integers or `null`.
- `range` is one of `24h`, `7d`, `30d`.
- `layout` contains integer `x`, `y`, `w`, `h` with:
  - `x >= 0`, `y >= 0`
  - `w >= 3`, `h >= 2`
  - `x + w <= 12`
- `ui` is an object keyed by token label. Each entry may contain
  `selected_scene_id`, `selected_figure_id`, and `scenes_tray_collapsed`.
- Deleting a scene or figure clears matching selections for every user.

### Grid constants

- 12 columns.
- 220 px row height.
- Minimum figure size: 3 columns x 2 rows.

These are module constants for the MVP. Per-scene grid configuration is a
follow-up.

### Version 1 state

There is no automatic migration. On first load of a version 1 document:

1. Copy `state.json` to `state.v1.json`.
2. Write a fresh version 2 document with no scenes.
3. Leave the archived file on the volume for manual recovery.

This avoids silently publishing personal figures into shared scenes. The
dashboard is not yet verified against production data, so a clean start is
acceptable.

## 5. Domain operations

The domain layer is plain Python and is testable without Panel.

- `create_scene(state, name=None) -> scene`
  - Defaults to the next free `Scene N` name.
- `delete_scene(state, scene_id) -> None`
  - Removes the scene and clears matching UI selections.
- `add_figure(state, scene_id, x, y) -> figure`
  - Creates a minimum-size figure at `(x, y)`.
  - Defaults: title `Untitled figure`, `range = 7d`, no location or series.
- `update_figure(state, scene_id, figure_id, *, title, location_id, series_id,
  range) -> figure`
  - Validates the title and range.
  - The web layer validates that the series exists and that a location/series
    pair has readings together before saving.
- `delete_figure(state, scene_id, figure_id) -> None`
  - Clears matching UI selections.
- `update_layout(state, scene_id, layouts) -> None`
  - Accepts a batch keyed by figure ID.
  - Validates bounds and minimum size.
- `set_ui(state, label, **changes) -> None`
  - Stores selection and tray state per user.

Any valid read token may create or delete scenes and edit figures. There is no
personal/shared split and no editor allowlist in the MVP.

## 6. Panel component tree

```text
Shell (custom pn.Row / pn.Column with self-hosted CSS)
├── Left scene tray (collapsible)
│   ├── "Scenes" header + collapse toggle
│   ├── Scene list (selected scene highlighted)
│   ├── + New scene
│   └── Delete scene (with confirm step)
├── Center scene canvas
│   ├── Scene title + figure count
│   └── SceneGrid (Panel GridStack subclass)
│       └── FigureCard
│           ├── FigureRibbon (Button; drag handle; click to select)
│           └── ChartPane (Bokeh figure or placeholder)
└── Right settings tray
    ├── Empty state: "Select a figure"
    └── Selected figure
        ├── Title
        ├── Location select ("All locations" plus known locations)
        ├── Analyte select (series for the chosen location)
        ├── Range: 24h / 7d / 30d
        ├── Save
        └── Delete figure
```

A custom shell is preferred over a Panel template for the MVP because it keeps
the right-hand settings tray simple and avoids template resource dependencies.

## 7. Grid interaction specification

### SceneGrid

`SceneGrid` subclasses `panel.layout.GridStack` and extends its render script to:

- set the GridStack `handle` option to `.figure-ribbon`;
- set `minW = 3` and `minH = 2`;
- attach a background click listener for empty-cell creation;
- ignore clicks that originate inside a `.grid-stack-item`.

### Empty-cell click

1. The browser computes the clicked cell with GridStack's
   `getCellFromPixel({left, top}, true)`.
2. If the cell is inside an existing item, the event is ignored.
3. The cell is written to a `cell_click` string parameter on the grid and synced
   to Python.
4. A Python watcher creates a minimum-size figure at that cell.
5. If the minimum-size figure does not fit, the watcher finds the first free
   slot. If the scene is full, it shows a notification instead of creating a
   figure.

### Ribbon click versus drag

- The ribbon is a Panel `Button` styled as the figure title bar.
- GridStack uses `.figure-ribbon` as its drag handle.
- A drag stop sets a short-lived flag so the click handler does not also select
  the figure after a move.
- A real click selects the figure, updates the highlight, and populates the
  settings tray.

### Selection highlight

The selected figure card receives an extra CSS class. Selection updates the
existing card in place; it does not rebuild the grid.

### Layout persistence

- Panel's `GridStack.state` updates on `dragstop` and `resizestop`.
- A watcher maps Panel object IDs back to figure IDs and calls
  `update_layout`.
- The initial render write is ignored.
- Layout is saved immediately on stop; no debounce is needed because GridStack
  only publishes state at the end of an interaction.
- A small status line reports `Saved` or the error.

### Scene switching

Selecting another scene builds a new `SceneGrid` for that scene. The previous
grid is discarded. The selected scene is stored in per-user UI state.

## 8. Chart specification

- Bokeh `figure` with a single `line` glyph, a datetime x-axis, and a hover
  tooltip showing the timestamp and value.
- Wrapped in `pn.pane.Bokeh` with `sizing_mode="stretch_both"`.
- Ranges: `24h`, `7d`, `30d`; default `7d`.
- Data comes from `ReadClient.points(...)` with the figure's optional
  `location_id`, the configured site time zone, and `DASHBOARD_MAX_POINTS`.
- Location and analyte resolution reuses the current rules:
  - `GET /locations` fills the location select.
  - `GET /locations/{id}/series` fills the analyte select.
  - "All locations" is allowed and behaves like an un-scoped figure.
- Saving rejects a missing series and a location/series pair with no readings
  together.
- Empty figures show "Choose a location and analyte."
- A failed chart shows an inline error and does not break the scene.
- Charts for every figure in the selected scene are loaded on scene open. Lazy
  viewport loading is a follow-up.

## 9. Concurrency and persistence

- Keep the existing atomic JSON writes, fsync, `.bak` copy, validation, and file
  lock.
- Add a process-wide `threading.RLock` to `StateStore` because Panel callbacks
  may run on multiple threads in one process.
- Run one uvicorn worker.
- Concurrent edits are last-write-wins. Layout saves from two users can
  overwrite each other; this is acceptable for the MVP and documented.
- Backup and restore remain file-copy operations on the volume.

## 10. Security

- No `POSTGRES_*` environment variables in the dashboard container.
- No database role or schema changes.
- No write routes on the read API.
- Read tokens remain the only credential.
- The token is never logged or rendered.
- Keep the signed `HttpOnly`, `Secure`, `SameSite=Strict` cookie.
- Keep the security headers and `frame-ancestors 'none'`.
- The CSP relaxation required by Panel/Bokeh is documented in ADR-028.
- Panel resources are served locally; the verification checklist includes a
  network-log check for external requests.

## 11. Testing and verification

Automated:

- State v2 validation, round-trip, backup/restore, and the v1 archive behavior.
- Scene and figure domain operations, layout bounds, minimum size, and UI
  selection cleanup.
- FastAPI auth, health, security headers, Panel mount, and `root_path`.
- Panel app construction and callbacks with a fake session and read client.
- Chart builder with sample bucketed points.
- Read-client and settings tests remain.

Manual browser checklist:

1. Log in with a valid read token; an invalid token is rejected.
2. Create and delete scenes; a restart preserves the remaining scenes.
3. Click an empty cell; a minimum-size figure appears there.
4. Click a ribbon; the figure highlights and the settings tray opens.
5. Drag a ribbon; the figure moves and the layout survives a reload and a
   container restart.
6. Resize a figure; the minimum size is enforced and the layout persists.
7. Set title, location, analyte, and range; the chart renders for 24h/7d/30d.
8. Delete a figure; the scene updates and the selection clears.
9. Log in with a second token; the same scenes are visible.
10. No `POSTGRES_*` variables in the dashboard container.
11. No external CDN requests; the page works under the final CSP.
12. `state.json` backup and restore work.

The `node --check` step is removed because there is no dashboard JavaScript
asset.

## 12. Implementation slices and acceptance criteria

1. **Docs:** ADR-028, this design doc, roadmap phase, and stack references.
2. **Mount Panel:** upgrade to Panel >= 1.5, mount a placeholder app in FastAPI,
   remove Jinja/HTMX/vendor assets, verify `root_path`, cookie access, CSP, and
   local resources.
3. **State v2 and domain:** schema, validation, v1 archive, scene/figure CRUD,
   layout, tests.
4. **Scene shell:** collapsible scene tray, settings tray, scene create/delete.
5. **Grid canvas:** `SceneGrid`, ribbon drag/click, empty-cell creation,
   selection, layout persistence.
6. **Figure settings and charts:** title/location/analyte/range/delete, Bokeh
   time-series rendering, loading and error states.
7. **Hardening:** cookie validation, CSP, in-process lock, restart/restore
   coverage.
8. **Docs closeout:** rewrite `dashboard.md`, update `dashboard/README.md`, and
   record verification results.

Acceptance criteria:

- The MVP interactions in section 1 work in a browser.
- Scenes are shared between two read tokens.
- Layouts survive reload and container restart.
- A figure cannot be saved with an invalid series or an incompatible
  location/series pair.
- The dashboard container still has no database credentials.
- No database schema, role, or read-API write route is added.
- `state.json` v2 loads and validates; a v1 file is archived, not published.
- The old Jinja/HTMX/Plotly assets and dependencies are removed.
- Python tests, ruff, and mypy pass.

## 13. Risks and open items

- **Panel FastAPI integration:** Panel 1.4.4 lacks `panel.io.fastapi`; the mount
  spike must prove Panel >= 1.5 works with FastAPI, the Caddy `root_path`, and
  the websocket path.
- **Cookie visibility:** `pn.state.cookies` must expose the existing signed
  cookie inside the mounted app. If it does not, authentication needs a small
  redesign before proceeding.
- **GridStack customization:** ribbon-only dragging and empty-cell clicks need a
  `ReactiveHTML` subclass and a JavaScript-to-Python bridge. This is the most
  custom UI work.
- **CSP:** the minimum directives for Bokeh/Panel must be measured, and any
  `unsafe-eval` requirement needs explicit sign-off.
- **Self-hosted resources:** Panel must not fall back to a CDN.
- **Concurrency:** last-write-wins is acceptable for the MVP but should be
  revisited if multiple analysts edit the same scene frequently.
