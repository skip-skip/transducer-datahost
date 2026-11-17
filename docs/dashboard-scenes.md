# Dashboard scenes redesign

> **Status:** In progress. The state layer (version 2 scenes schema, validation, and v1 → v2 migration) and the
> server-rendered scene shell (scene list, options pane, CSS-grid workspace, scene-aware routes) have landed. Drag,
> resize, snap-to-grid, and batched layout persistence remain. This document records the implementation plan and current
> progress.
> Current shipped behavior is documented in [dashboard.md](dashboard.md). Implementers should keep this document
> updated as decisions are finalized.

## 1. Goal

Replace the single-figure dashboard experience with **scene-based workspaces**:

- **Scenes** are the primary items shown in the left pane (personal/shared). A scene is a flat container of figures
  (a folder-like grouping of figures, not nested scenes).
- The main **figure space becomes a grid workspace** where multiple figures are displayed at once as window-like panels.
- Figures can be **arranged in the grid and snap to occupy grid cells/positions** (drag, reorder, resize as supported).
- A **second, nested options pane** is attached to the right side of the current (left/navigation) pane. It shows
  options for the **currently selected figure or scene** (context-sensitive).

The redesign must preserve existing boundaries: no database schema/role changes, no dashboard state in PostgreSQL, no
write routes on the read API, read-API-token authentication, and the dashboard remains a separate service over the
read-only API.

## 2. Current baseline (to replace/extend)

- FastAPI + Jinja2 + HTMX + Plotly, vanilla JS/CSS, vendored static assets, **no Node/build step**.
- State: `dashboard_state` volume JSON (`state.json`, `version: 2`, `StateStore` with file lock + atomic write).
  Model: `shared.scenes[]` and `personal[label].scenes[]`; each scene has grid settings and figures with
  per-figure layout, plus per-scope UI selection/collapse. The version 1 layout
  (`shared.figures[]`, `personal[label].figures[]`) migrates to a default scene on load.
- UI: three regions. The left pane lists scenes; the nested options pane edits the selected scene or figure; the main
  workspace renders every figure in the selected scene as a panel in a CSS grid using its stored `layout`. Charts load
  per panel through HTMX and render as independent Plotly instances (`app.js`). Drag/resize/snap and batched layout saves
  are not built yet.
- Figure identity/binding: UUID id, `series_id`, optional `location_id` (ADR-027), per-figure config; permissions are
  personal-vs-shared with optional `DASHBOARD_SHARED_EDITORS`.

See [dashboard.md](dashboard.md), `dashboard/transducer_dashboard/{app,figures,state}.py`, templates, and static assets.

## 3. Planned UX model

| Region | Planned behavior |
|---|---|
| **Left pane (scenes)** | Lists **scenes** (replacing the flat figure list). Scope remains personal/shared. Selecting a scene opens its grid workspace. Scene-level actions (create/rename/delete/select) live here or via options. |
| **Grid workspace (center)** | Shows all figures in the selected scene as panels/windows on a grid. Panels can be moved/resized and **snap** to grid positions/sizes. Selecting a panel focuses it. Each panel renders its own chart/data state. |
| **Options pane (nested, right of left pane)** | Context-sensitive: shows **scene options** when a scene (or no figure) is selected, and **figure options** when a figure panel is selected. Collapsible; selection/collapse persisted per user where appropriate. |

### Figure-as-window (expected capabilities)
- Bind data like today: name, location, analyte (`series_id`), optional `location_id`, range/display config.
- Independent chart rendering within the scene (multiple Plotly instances).
- Layout fields persisted per figure instance in the scene: e.g. grid `x`, `y`, `w`, `h` (and min constraints) so the
  workspace restores on reload.

### Scene (expected capabilities)
- Container: id, name, scope/ownership, ordered/positioned figure set, grid/layout settings (column count, row height or
  equivalent), timestamps/creator metadata.
- Flat hierarchy only (no scenes inside scenes).

## 4. Techstack plan

### 4.1 Keep (no change)
- **Service/runtime:** FastAPI (Python), uvicorn, Jinja2, Docker Compose service, Caddy routing at `/dashboard/*`.
- **Data access:** read-only HTTP calls to `read-api` with the user's token (`ReadClient`); no DB credentials/role.
- **Charting:** Plotly (vendored) for timeseries rendering.
- **Persistence medium:** JSON file on `dashboard_state` volume with `filelock`, validation, tmp+fsync+backup+atomic
  replace, single uvicorn worker.
- **Read API/DB:** unchanged in schema/roles; existing read endpoints expected to remain sufficient (see §6).

### 4.2 Change / add
- **State model (v1 → v2):** introduce scenes and per-figure layout; bump `STATE_VERSION` and implement migration and
  validation (see §5). Refactor/replace `figures.py` with scene-aware domain operations.
- **App/routes/templates:** scene-aware navigation and mutations; grid workspace template; options-pane partials/forms;
  endpoints for layout persistence and multi-figure data loading (see §7).
- **Frontend interaction (primary stack addition):** add a **vendored, no-build JavaScript grid/windowing library** to
  provide drag, resize, and snap-to-grid behavior (candidate to evaluate during implementation: e.g. GridStack.js or a
  comparable library; final choice must be license-compatible and vendorable under `static/vendor/`).
- **Client JS/CSS:** evolve `app.js`/`app.css` (or split static modules, still no bundler) for multi-chart lifecycle
  (`Plotly.newPlot`/`react`/`purge`, resize handling), focus/selection, and debounced layout-save requests.
- **Interaction style:** hybrid — HTMX/server-rendered for structure, forms, and CRUD navigation where appropriate;
  client-managed grid state/callbacks for drag/resize/snap with explicit/batched persistence to the backend.

### 4.3 Explicit non-goals for the stack
- No Node/npm build pipeline and no SPA framework rewrite required as part of this redesign (revisit only if a chosen
  dependency cannot be vendored sanely).
- No PostgreSQL tables/roles/migrations and no read-API write endpoints.

## 5. State schema and migration

> The version 2 state layer and the scene-aware routes/shell are implemented. Grid interactions (drag/resize/snap) and
> batched layout persistence are still planned.

### 5.1 Implemented v2 shape
- **Scene:** `id` (UUID), `name`, `position`, `grid` (`cols`, `row_height`), `figures[]`,
  `created_by`/`created_at`/`updated_at`.
- **Figure (scene member):** `id` (UUID), `name`, `series_id`, `location_id` (nullable, ADR-027 semantics), `config`
  (e.g. range/tz/display), `layout` (`x`,`y`,`w`,`h`), timestamps/creator.
- **Scoping:** personal scenes per token label and shared scenes visible to valid read tokens, with edit rules aligned to
  `DASHBOARD_SHARED_EDITORS`.
- **UI state:** selected scene id, selected figure id, pane collapse (`folders_collapsed` for the navigation pane and
  `options_collapsed` for the nested options pane), active scope/view, timezone.

Sketch of the current shape:

```json
{
  "version": 2,
  "shared": {
    "scenes": [
      {
        "id": "...",
        "name": "Default scene",
        "position": 0,
        "grid": {"cols": 12, "row_height": 220},
        "figures": [
          {
            "id": "...",
            "name": "...",
            "series_id": 42,
            "location_id": 7,
            "config": {},
            "layout": {"x": 0, "y": 0, "w": 6, "h": 2},
            "created_by": "...",
            "created_at": "...",
            "updated_at": "..."
          }
        ],
        "created_by": "...",
        "created_at": "...",
        "updated_at": "..."
      }
    ]
  },
  "personal": {
    "<label>": {
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

### 5.2 Migration from v1 (implemented)
- Detect `version: 1` on load; transform to v2.
- **Default mapping:** one `Default scene` per scope/owner contains the previous figures; figures receive sequential,
  non-overlapping grid layouts; the old selected figure maps to the new selected scene and figure (stale selections
  are dropped).
- Preserve ADR-027 semantics: figures with `location_id: null` remain valid/unscoped until edited.
- Write migrated state through the normal atomic path; retain `state.json.bak`. Validate strictly after migration.

### 5.3 Write behavior
- Support **batched layout updates** (single payload for drag/resize end or debounce window) to limit write frequency.
- Maintain one writer (`uvicorn` worker = 1) and existing lock/atomic guarantees.

## 6. Read API impact

- **Expected:** no read API or database changes. Per-figure data continues to use existing endpoints:
  `GET /whoami`, `/locations`, `/locations/{id}/series`, `/series/{id}`, `/series/{id}/points`.
- **Dashboard-side considerations:** parallel fetching per figure in a scene, per-figure error isolation, respect
  `DASHBOARD_MAX_POINTS`/`READ_MAX_WINDOW_DAYS`/`READ_MAX_ROWS`, and avoid thundering-herd loads on scene open (lazy or
  staged chart loading if needed).
- **Optional (only if profiling justifies):** a dashboard aggregation endpoint that collects chart payloads for a scene
  in one request while still proxying the read API per user token — not a read-API contract change.

## 7. Backend and UI implementation slices (prep checklist)

1. **State/domain (done):** `state.py` v2 schema + migration + validation; scene/figure/layout operations in
   `figures.py`, permission checks, ID generation.
2. **Routes/app (done):** scene CRUD/select, figure CRUD within scene, figure/scene options partials, chart partial,
   context-sensitive options resolution. **Pending:** layout save endpoint and batched persistence.
3. **Templates (done):** `index.html` shell (scenes pane, options pane, grid), scene list partial, figure panel
   template, scene-options and figure-options partials; accessible labels and HTMX targets preserved.
4. **Static assets (partial):** multi-chart registry, range toggle, and resize handling in `app.js`; three-pane/grid/
   panel/collapse styling in `app.css`. **Pending:** vendor a grid library with license notice and wire drag/resize/snap.
5. **Settings/config:** only add env vars if truly required (e.g. grid defaults/limits); prefer safe defaults to minimize
   deployment/config churn.
6. **Verification (partial):** unit tests cover migration, operations, routes, and permissions. **Pending:** manual
   browser checks (drag/resize/snap persistence, restart/restore) and performance sanity with representative scene sizes.

## 8. Security, permissions, and ops considerations

- Preserve session cookie properties and security headers; CSP may need adjustment if a vendored library requires it
  (prefer libraries compatible with current self-only/static policy).
- Shared-scene edit rules must be explicit (align with `DASHBOARD_SHARED_EDITORS`); read-only viewers must not mutate
  layout or bindings.
- Layout-save endpoints must validate ownership/scope and input bounds (grid coordinates/sizes, figure ids).
- Backup/restore guidance unchanged in mechanism but state files will grow with multi-figure scenes; document v2 restore
  expectations.

## 9. Decisions to finalize before/during implementation

- **Sharing granularity:** scene as the shareable/editable unit (recommended) vs other models.
- **Grid library choice:** capability, size, license, accessibility, and vendoring practicality.
- **Save UX:** autosave-on-change (debounced) vs explicit save for layout/config.
- **Grid definition:** column count, responsive breakpoints, row height, min panel sizes, behavior on small screens.
- **Scene open behavior:** eager load all charts vs lazy/lazy-on-visible.
- **v1 → v2 default scene mapping:** exact transformation and naming.
- **Figure caps:** maximum figures per scene / concurrent chart renders for performance safety.

## 10. References

- Current behavior and ops: [dashboard.md](dashboard.md), [dashboard/README.md](../dashboard/README.md)
- Decisions: [decisions.md](decisions.md) (ADR-026, ADR-027) and the ADR added for this redesign
- Roadmap: [roadmap.md](roadmap.md)
- Architecture/boundaries: [architecture.md](architecture.md), [network-security.md](network-security.md)
