# Dashboard scenes redesign

> **Status:** Implemented. The state layer (version 2 scenes schema, validation, and v1 → v2 migration), the
> server-rendered scene shell (scene list, options pane, grid workspace, scene-aware routes), the GridStack
> drag/resize/snap workspace with debounced batched layout persistence, and the per-scene figure cap have landed.
> Automated coverage now includes app restart and v2 backup restore. Remaining: manual browser and performance
> verification, plus any follow-up decisions captured in §9.
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
  workspace renders every figure in the selected scene as a GridStack panel using its stored `layout`. Panels can be
  dragged by their grip, resized, and snap to grid cells; layout changes are debounced and saved in one batch per scene.
  Charts load per panel through HTMX and render as independent Plotly instances (`app.js`).
- Figure identity/binding: UUID id, `series_id`, optional `location_id` (ADR-027), per-figure config; permissions are
  personal-vs-shared with optional `DASHBOARD_SHARED_EDITORS`.

See [dashboard.md](dashboard.md), `dashboard/transducer_dashboard/{app,figures,state}.py`, templates, and static assets.

## 3. UX model

| Region | Behavior |
|---|---|
| **Left pane (scenes)** | Lists **scenes** (replacing the flat figure list). Scope remains personal/shared. Selecting a scene opens its grid workspace. Scene-level actions (create/rename/delete/select) live here or via options. |
| **Grid workspace (center)** | Shows all figures in the selected scene as panels/windows on a grid. Panels can be moved/resized and **snap** to grid positions/sizes. Selecting a panel focuses it. Each panel renders its own chart/data state. |
| **Options pane (nested, right of left pane)** | Context-sensitive: shows **scene options** when a scene (or no figure) is selected, and **figure options** when a figure panel is selected. Collapsible; selection/collapse persisted per user where appropriate. |

### Figure-as-window (capabilities)
- Bind data like today: name, location, analyte (`series_id`), optional `location_id`, range/display config.
- Independent chart rendering within the scene (multiple Plotly instances).
- Layout fields persisted per figure instance in the scene: e.g. grid `x`, `y`, `w`, `h` (and min constraints) so the
  workspace restores on reload.

### Scene (capabilities)
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
- **Frontend interaction (implemented):** **GridStack.js 14.1.1** (MIT) is vendored under `static/vendor/` with its
  license notice. It provides drag, resize, and snap-to-grid; the dashboard keeps a no-build, no-bundler setup.
- **Client JS/CSS:** evolve `app.js`/`app.css` (or split static modules, still no bundler) for multi-chart lifecycle
  (`Plotly.newPlot`/`react`/`purge`, resize handling), focus/selection, and debounced layout-save requests.
- **Interaction style:** hybrid — HTMX/server-rendered for structure, forms, and CRUD navigation where appropriate;
  client-managed grid state/callbacks for drag/resize/snap with explicit/batched persistence to the backend.

### 4.3 Explicit non-goals for the stack
- No Node/npm build pipeline and no SPA framework rewrite required as part of this redesign (revisit only if a chosen
  dependency cannot be vendored sanely).
- No PostgreSQL tables/roles/migrations and no read-API write endpoints.

## 5. State schema and migration

> The version 2 state layer, scene-aware routes/shell, and batched layout persistence are implemented.

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

### 5.3 Write behavior (implemented)
- Layout changes are debounced client-side (500 ms) and sent as one batch to `POST /scenes/{scope}/{scene_id}/layout`;
  the endpoint validates every figure id and grid bound before writing.
- One writer (`uvicorn` worker = 1) and the existing lock/atomic guarantees remain unchanged.

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
   batched layout save endpoint, context-sensitive options resolution.
3. **Templates (done):** `index.html` shell (scenes pane, options pane, grid), scene list partial, figure panel
   template, scene-options and figure-options partials; accessible labels and HTMX targets preserved.
4. **Static assets (done):** vendored GridStack.js 14.1.1 with license notice; multi-chart registry, range toggle,
   resize handling, and debounced layout saves in `app.js`; three-pane/grid/panel/collapse styling in `app.css`.
5. **Settings/config (done):** added `DASHBOARD_MAX_FIGURES_PER_SCENE` (default 50, positive-value validation) as the
   only new setting; grid defaults stay code-level constants to minimize deployment/config churn.
6. **Verification (partial):** unit tests cover migration, operations, routes, permissions, layout payloads, app restart,
   and v2 backup restore. **Pending:** manual browser checks (drag/resize/snap persistence) and performance sanity with
   representative scene sizes.

## 8. Security, permissions, and ops considerations

- Session cookie properties and security headers are unchanged. The vendored GridStack assets fit the existing
  self-only CSP: scripts/styles are served from `/static`, inline style attributes are already allowed by
  `style-src 'unsafe-inline'`, and the resize-handle SVGs use `data:` images allowed by `img-src`.
- Shared-scene edit rules align with `DASHBOARD_SHARED_EDITORS`; read-only viewers get a disabled grid and cannot mutate
  layout or bindings (enforced again by the layout-save endpoint).
- The layout-save endpoint validates ownership/scope, figure membership, duplicate ids, and grid bounds.
- Backup/restore guidance is unchanged in mechanism; state files grow with multi-figure scenes, and v2 restore
  expectations are documented in [dashboard.md](dashboard.md).

## 9. Decisions

- **Sharing granularity:** the scene is the shareable/editable unit; figures belong to exactly one scene.
- **Grid library:** GridStack.js 14.1.1 (MIT), vendored under `static/vendor/`; drag handle is the panel grip.
- **Save UX:** layout autosaves on drag/resize end, debounced (500 ms) and batched per scene. Binding/config edits keep
  explicit save in the options pane.
- **Grid definition:** scene-level column count (default 12) and row height (default 220 px), minimum panel size 2×1
  cells, vertical compaction (`float: false`), and a horizontal-scroll workspace on small screens.
- **Scene open behavior:** charts load eagerly per panel through HTMX; per-panel errors stay isolated.
- **Figure cap:** each scene is limited to `DASHBOARD_MAX_FIGURES_PER_SCENE` figures (default 50). The domain layer and
  create route enforce the cap; the scene options pane shows `X of N figures` and replaces `+ Add figure` with a limit
  hint when the scene is full.
- **v1 → v2 default scene mapping:** one `Default scene` per scope/owner with non-overlapping layouts (implemented).

## 10. References

- Current behavior and ops: [dashboard.md](dashboard.md), [dashboard/README.md](../dashboard/README.md)
- Decisions: [decisions.md](decisions.md) (ADR-026, ADR-027) and the ADR added for this redesign
- Roadmap: [roadmap.md](roadmap.md)
- Architecture/boundaries: [architecture.md](architecture.md), [network-security.md](network-security.md)
