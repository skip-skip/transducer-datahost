# Panel dashboard design

**Status:** Accepted; implementation in progress ([ADR-028](decisions.md#adr-028-use-holoviz-panel-for-the-dashboard-frontend))

This document defines the replacement dashboard frontend. It supersedes the
FastAPI + Jinja2 + HTMX + Plotly UI described in [dashboard.md](dashboard.md)
once the implementation lands.

## 1. Purpose and scope

The dashboard becomes a Python-only Panel application mounted inside the existing
FastAPI service. The MVP deliberately stays small:

- A left rail of full-height ribbon drawers holds two cards, **scenes** and the
  selected figure's **settings**. Scenes is leftmost; each body opens to the
  right and pushes the next drawer and the canvas right.
- A scene is a named collection of figures placed in a gridded canvas.
- Scenes can be created and deleted.
- **All scenes are shared between all users.** There are no personal scenes.
- Clicking an empty grid cell creates a new figure at the minimum size.
- A figure has a title ribbon. Clicking the ribbon selects the figure; dragging
  the ribbon moves it within the grid.
- Selecting a figure highlights it and populates the settings drawer. The
  drawer opens only from its tab.
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
- The mount-stage placeholder rendered under a policy without `unsafe-eval`. That
  measurement did not survive contact with the real UI: the GridStack layout needs
  `'unsafe-eval'` and `blob:` (see [Content Security Policy](#content-security-policy)).

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

Panel and Bokeh require a wider policy than the previous `script-src 'self'`. Two
policies are served. The Panel document and its static assets get:

- `default-src 'self'`
- `script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:`
- `style-src 'self' 'unsafe-inline'`
- `img-src 'self' data:`
- `font-src 'self' data:`
- `connect-src 'self' ws: wss:`
- `worker-src 'self' blob:`
- `frame-ancestors 'none'`
- `base-uri 'self'`
- `form-action 'self'`
- `object-src 'none'`

`/login`, `/logout`, `/health`, and every route added later keep the strict policy,
identical except that `script-src` is `'self' 'unsafe-inline'`. The middleware
allowlists the relaxed policy by route path (`/` and `/static/`), stripping the
`root_path` first because uvicorn prepends `--root-path` to `scope["path"]` even
though Caddy already removed the prefix. A new route therefore cannot inherit
`'unsafe-eval'` by accident.

`'unsafe-eval'` and `blob:` are not optional configuration. `pn.layout.GridStack` is
a `ReactiveHTML` subclass, and Panel implements `ReactiveHTML` scripts by compiling
them with `new Function`, which only `'unsafe-eval'` permits. Bokeh's `CustomJS`
does the same. Bokeh 3 ships ES modules and Panel bundles `es-module-shims`, which
executes them from `blob:` URLs, hence `blob:` in `script-src`.

The mount-stage measurement claimed no `unsafe-eval` was needed; it was taken
against a placeholder that never instantiated `ReactiveHTML`. The accepted policy is
recorded in ADR-028 with the compensating controls: every HTML pane escapes
untrusted text, `frame-ancestors 'none'`, `base-uri 'self'`, `object-src 'none'`,
`nosniff`, and no external origins in any directive. Any future component that
widens the policy further needs another ADR amendment.

`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and
`Referrer-Policy: same-origin` stay.

Panel resources must be served by the dashboard itself. No CDN request is
acceptable, and the browser network log is part of the verification checklist. With
devtools open, `cdn.jsdelivr.net/*.map` requests are reported as blocked; those are
source-map fetches prompted by `//# sourceMappingURL=` comments inside the locally
served bundles, not code loads. They are expected and must not be "fixed" by adding
the CDN to `connect-src`.

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
  - Stores selection and scenes drawer state per user.

Any valid read token may create or delete scenes and edit figures. There is no
personal/shared split and no editor allowlist in the MVP.

## 6. Panel component tree

```text
Shell (custom pn.Row / pn.Column with self-hosted CSS)
├── Header banner (three-region grid)
│   ├── Left: "Transducer dashboard"
│   ├── Center: scene name · figure count ("No scene selected" when none)
│   └── Right: "Signed in as <user>" and Sign out
├── Left ribbon rail (44 px vertical tabs, two independent right-opening drawers,
│   stacked left-to-right and spanning the viewport height)
│   ├── Scenes drawer (leftmost; collapse persists per token label)
│   │   ├── Scene list (selected scene highlighted)
│   │   ├── + New scene
│   │   └── Delete scene (with confirm step)
│   └── Figure settings drawer (collapse is per session)
│       ├── Empty state: "Select a figure"
│       └── Selected figure
│           ├── Title
│           ├── Location select ("All locations" plus known locations)
│           ├── Analyte select (series for the chosen location)
│           ├── Range: 24h / 7d / 30d
│           ├── Publish (disabled and labeled "Published" when synced)
│           └── Delete figure
└── Center scene canvas
    └── SceneGrid (Panel GridStack subclass)
        └── FigureCard
            ├── FigureRibbon (Button; drag handle; click to select)
            └── ChartPane (Bokeh figure or placeholder)
└── Speed dial (fixed bottom-right; Add / Arrange / Lock)
```

The header is a single `pn.pane.HTML` using a CSS grid (`1fr auto 1fr`) so the
brand, scene summary, and user block stay left, center, and right without
absolute positioning. The canvas below it is just the grid: scene title and
figure count live only in the banner.

A persistent `pn.Modal` is appended to the shell and opened only when a publish
conflicts with another session's change. It offers **Overwrite** and **Revert to
server state**.

A custom shell is preferred over a Panel template for the MVP because it keeps
the rail simple and avoids template resource dependencies. Two independent
`pn.Card`s are used instead of one `pn.Accordion` so the scenes and settings
collapse states stay independent; selecting a figure populates the settings
drawer without changing its open state.

Panel 1.10's only built-in drawer is `pn.ui.Drawer`, a Material UI component
re-exported from `panel-material-ui`. Its `docked` + `inline` mode does not fit
this rail: the dock tab is a 24 x 48 px chevron-only control with no title, a
closed drawer collapses to 0 px of layout width and positions its tab at
`right: -24px` (so two closed drawers overlap), and `dock_icon` is ignored in
inline mode. Using it would also switch the session to the Material design and
raise the dependency floor from Panel 1.5 to 1.10. A hybrid using
`variant="persistent"` for the body with custom tabs was considered too, but it
still pulls in the Material dependency and design for no geometry gain. Each
card is therefore restyled as a full-height ribbon. The rail is a `pn.Row` of
cards docked to the left edge of a full-height flex column: scenes is leftmost
and figure settings sits immediately to its right. Each card's shadow root is a
flex row with a 44 px vertical tab and a 340 px body that opens to the right;
the tab and body stretch to the full height of the rail below the header, and
the body scrolls when its content is taller than the viewport. The tab title is
a custom header pane with `writing-mode: vertical-rl` so it reads bottom-to-top;
Panel's built-in chevron points right while the drawer is closed and down while
it is open. Because the drawers stay in flow left-to-right, opening scenes
pushes the settings drawer and the canvas right, and opening settings pushes the
canvas. The rail gets inline `flex: 0 0 auto`, `width: max-content`, and
`height: 100%`; the canvas uses `sizing_mode="stretch_both"`. A rail stylesheet
overrides Panel's computed `min-width: 300px` so both closed tabs measure 88 px.
Inline styles are required for the rail and canvas because they live inside
nested Bokeh shadow roots, where shell stylesheets do not reach.

## 7. Grid interaction specification

### SceneGrid

`SceneGrid` subclasses `panel.layout.GridStack` and extends its render script to:

- set the GridStack `handle` option to `.figure-ribbon`;
- set `minW = 3` and `minH = 2`;
- toggle moving and resizing from the canvas tool mode;
- paint a free rectangle in Add mode and report it to Python;
- publish the measured cell width and height as CSS variables for the grid
  background.

The grid canvas is light gray (`#f7f8fa`) with 1 px cell lines (`#e2e6ec`).
The rule targets the inner `.grid-stack` element and sizes the background with
two CSS variables, `--td-cell-width` and `--td-cell-height`. The render script
sets them from `gridstack.cellWidth()` and `gridstack.getCellHeight()` and
refreshes them from a `ResizeObserver` and after each layout, so the lines follow
the cell size GridStack actually snaps to rather than a fixed guess. The rule
lives in `GRID_CSS` because the canvas is inside the grid component's shadow
root, where the shell stylesheet cannot reach.

### Tool modes

The speed dial in the bottom-right corner selects one of three canvas tools. The
mode is session-local: it is not written to the state file and resets to
**Arrange** on reload.

- **Add** paints a new figure footprint on empty cells.
- **Arrange** (default) moves and resizes figures; it does not create figures.
- **Lock** freezes the layout: no moving, resizing, or painting.

`SceneGrid.tool_mode` is a `param.ObjectSelector`. Its script calls
`gridstack.enableMove()` and `enableResize()` only for Arrange and toggles the
`td-mode-add` / `td-mode-lock` classes that set the cursor and suppress
selection. The shell keeps the authoritative mode and passes it to each new grid
after a refresh, so rebuilding the canvas does not drop the selection.

The dial is a fixed `pn.Column` with one button per mode and a round toggle. It
is always rendered but disabled while no scene is selected. The toast moved left
to `right: 8.75rem` so it clears the dial's 8 rem option column when the dial is
open.

### Add-mode painting

1. `pointerdown` on empty canvas converts the pointer position to a cell with
   GridStack's `getCellFromPixel({left, top}, true)`.
2. The anchor cell must fit the 3 x 2 minimum and that rectangle must be free.
   Otherwise the gesture is ignored.
3. A `.td-paint` overlay shows the footprint. It is absolutely positioned from
   the measured cell width and height, sits behind the figure cards, and has
   `pointer-events: none`.
4. While dragging, the rectangle grows toward the cursor one row or column at a
   time. It stops at the first occupied cell, so a span never overlaps a figure
   or leaves the grid. Growing left or up moves the origin with it.
5. `pointerup` writes the span to the `figure_span` string parameter as JSON.
   `pointercancel` and leaving the canvas clear the overlay.
6. A Python watcher clears the parameter and calls
   `DashboardShell.create_figure_span`, which runs `scenes.add_figure_at` inside
   one `StateStore.mutate`. The new figure keeps the exact painted layout, is
   selected, and is persisted immediately.
7. `add_figure_at` validates the bounds, minimum size, and occupancy. A painted
   span never falls back to a free slot; a conflicting write reports instead of
   creating a figure.

Hovering without pressing shows the minimum 3 x 2 preview, or nothing when the
anchor cannot fit.

### Ribbon click versus drag

- The ribbon is a Panel `Button` styled as the figure title bar.
- GridStack uses `.figure-ribbon` as its drag handle.
- Clicking selects the figure, updates the highlight, and populates the settings
  drawer. Dragging moves it; both gestures start on the same handle.
- Moving and resizing are only enabled in Arrange mode.

### Selection highlight

The selected figure card receives an extra CSS class. Selection updates the
existing card in place; it does not rebuild the grid.

### Draft and publish

- Settings edits (title, location, analyte, range) update a session-local draft
  keyed by figure ID. The draft holds only the fields the user changed.
- The settings form and the figure chart render from the stored figure with the
  draft overlaid. No state file write happens on edit.
- A figure with a draft gets `figure-unpublished` (yellow border) and, when also
  selected, `figure-unpublished figure-selected` (orange border). The two flags
  are tracked separately so a selection change cannot drop the unpublished class.
- Reverting a field to its stored value removes that override. An empty draft is
  deleted and the button returns to `Published`.
- `Publish` validates the effective figure (title, analyte, location/series pair),
  then writes only the overridden fields inside one `StateStore.mutate`.
  Location and analyte are written together when either is overridden.
- The baseline settings signature is recorded when the draft starts. Inside the
  mutation, only the fields being written are compared with the baseline; a
  difference raises `PublishConflict`, writes nothing, and opens the modal.
  A server change to a field the draft does not write is merged.
- Overwrite reloads state, re-validates against the current figure, and
  force-writes the draft. Revert drops the draft, reloads state, and refreshes.
  Closing the modal keeps the draft. Selecting another figure or scene closes
  the modal and clears the pending target.
- Drafts survive scene switches within the session and are discarded on reload.
  Deleting a figure or scene prunes its drafts.
- Layout drag/resize is not part of a draft; it continues to save immediately.

### Layout persistence

- Panel's `GridStack.state` updates on `dragstop` and `resizestop`.
- A watcher maps Panel object IDs back to figure IDs and calls
  `update_layout`.
- The initial render write is ignored.
- Layout is saved immediately on stop; no debounce is needed because GridStack
  only publishes state at the end of an interaction. Saving is silent.
- Errors and confirmations appear as a fixed toast in the bottom-right corner
  that fades out after 3 seconds. The toast is out of the layout flow, so it
  never moves the canvas.

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
- Publishing rejects a missing series and a location/series pair with no readings
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
- Concurrent edits are last-write-wins for layout saves and for settings the
  draft does not touch. A publish whose overridden fields changed since the draft
  started raises a conflict and opens the overwrite/revert modal instead of
  overwriting silently.
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
3. In **Arrange**, click an empty cell: nothing is created. Drag a ribbon to
   move a figure and drag its corner to resize; the layout survives a reload and
   a container restart.
4. Open the dial and choose **Add**. Hover empty cells to see the 3 x 2 preview;
   drag to paint a larger footprint; release. The figure appears with exactly
   that span. Painting stops at an existing figure, and a drag that starts on a
   figure does nothing.
5. Choose **Lock**: figures no longer move or resize and Add-mode painting is
   unavailable. Reload; the dial is back on **Arrange**.
6. Click a ribbon; the figure highlights and the settings drawer keeps its
   current open/closed state.
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
4. **Scene shell:** left ribbon rail with full-height vertical scenes and settings
   tabs whose bodies open to the right and push the next drawer, scene
   create/delete.
5. **Grid canvas:** `SceneGrid`, ribbon drag/click, selection, layout
   persistence, the Add/Arrange/Lock speed dial, and Add-mode painting.
6. **Figure settings and charts:** title/location/analyte/range/delete, the
   session-local draft and publish/conflict flow, Bokeh time-series rendering,
   loading and error states.
7. **Hardening:** cookie validation, CSP, in-process lock, restart/restore
   coverage.
8. **Docs closeout:** rewrite `dashboard.md`, update `dashboard/README.md`, and
   record verification results.

Acceptance criteria:

- The MVP interactions in section 1 work in a browser.
- Scenes are shared between two read tokens.
- Layouts survive reload and container restart.
- A figure cannot be published with an invalid series or an incompatible
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
- **GridStack customization:** ribbon-only dragging, the tool-mode switch, and
  Add-mode painting need a `ReactiveHTML` subclass and JavaScript-to-Python
  bridges. This is the most custom UI work. Painting is pointer-based, so the
  manual browser checklist covers it; headless tests assert the script contents
  and the Python side of the bridge.
- **CSP:** resolved. The GridStack `ReactiveHTML` layout and the ESM shim require
  `'unsafe-eval'` and `blob:` in `script-src`; the strict policy is kept on the
  non-Panel routes. See ADR-028 for the sign-off and compensating controls.
- **Self-hosted resources:** Panel must not fall back to a CDN.
- **Concurrency:** last-write-wins is acceptable for the MVP but should be
  revisited if multiple analysts edit the same scene frequently.
