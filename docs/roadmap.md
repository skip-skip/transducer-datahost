# Roadmap and Acceptance Criteria

The repository is currently at **Phase 4 (VM deployment) pending verification**. Phases 0–3
and the application services are implemented and unit-tested locally, and the dashboard
service (Phase 8) is implemented with unit tests. The Phase 1 SQL has
been verified against an embedded PostgreSQL 16.2; Docker/Compose verification on the
Linux VM, the TLS certificate, and the production backfill remain.

## Phase 0 — Documentation and skeleton (complete)

- [x] Architecture summary
- [x] Data model
- [x] Ingestion design
- [x] Network and security design
- [x] Architecture decisions
- [x] Repository directory skeleton and configuration templates
- [x] Git repository initialized with a GitHub remote

## Phase 1 — Schema and local database (complete locally)

**Deliverables**

- [x] `db/init/01_roles.sql`: `transducer_upload`, `transducer_ingest`, `transducer_read`,
  SCRAM passwords, pinned `search_path`.
- [x] `db/init/02_schema.sql`: schema, tables, constraints, indexes.
- [x] `db/init/03_views.sql`: `v_reading`, `v_reading_utc`, `v_import`.
- [x] `db/init/04_grants.sql`: least-privilege grants.
- [x] `db/init/05_ingest.sql`: `ingest.job`, `ingest.worker_heartbeat`,
  `ingest.file_known()`.
- [x] `db/migrations/0001_ingest.sql`: idempotent upgrade for existing databases.
- [x] `db/tests/verify_phase1.sh`: acceptance checks for all roles and the queue.
- [x] `db/tests/run_phase1.sh`: runs the acceptance checks inside the Compose `db` service.
- [x] Compose `db` service passes role passwords, forces SCRAM, uses a TCP healthcheck, and
  publishes no port.
- [ ] `docker compose up -d --build` verified healthy on the Linux VM.

**Exit criteria**

- [x] Schema applies cleanly to an empty database (verified on embedded PostgreSQL 16.2).
- [x] Roles authenticate with `scram-sha-256` (verified).
- [x] Read role cannot write; write role cannot alter schema (verified).
- [x] Upload role is confined to `ingest.job` (verified).
- [ ] Re-init and migration paths verified on the VM.

## Phase 2 — Parser and validation (complete)

**Deliverables**

- [x] `ingest/transducer_ingest/parse_xle.py`
- [x] `ingest/transducer_ingest/validate.py`
- [x] Unit tests and small committed fixtures (excluding the full export).
- [x] A parser report that runs without a database.

**Exit criteria**

- [x] Parses both `DataGrabber V3.001` and `Version 4.7.0` layouts.
- [x] Handles 2- and 3-channel files and kPa barometers.
- [x] Preserves `ms` precision.
- [x] Detects compensated files by the `<Compensation_info>` metadata block with zero false
  positives; filenames are only a consistency check.
- [x] Reproduces the expected totals: 101 uncompensated files, 173,508 log records, 113,177
  unique samples, 233,855 series readings, 1 empty file, 13 anomalies, 31 fully redundant
  files.

## Phase 3 — Loader and deduplication (complete)

**Deliverables**

- [x] `ingest/transducer_ingest/load.py` with staging `COPY` + upsert.
- [x] `ingest/transducer_ingest/db.py` for role-aware connection configuration.
- [x] `ingest/transducer_ingest/cli.py` with `ingest`, `upload`, `report`, `--dry-run`,
  `--since`, `--json`, and `--all`.
- [x] `scripts/run_ingest.ps1` retained for direct development loads.

**Exit criteria**

- [x] Dry run writes nothing and reports expected counts.
- [x] Full backfill inserts 233,855 `reading` rows covering 113,177 unique samples and 0
  compensated rows.
- [x] Re-running inserts 0 rows.
- [x] 31 fully redundant files contribute zero new readings; 13 anomalous files recorded; 1
  empty file recorded.
- [x] A failed file rolls back without affecting other files.
- [x] 29 byte-identical files are skipped at the file level; 118 `import_file` audit rows are
  written on a fresh full load (45 of them `skipped_compensated`).

## Phase 4 — VM-hosted ingest gateway (implemented; VM verification pending)

**Deliverables**

- [x] `ingest/transducer_ingest/service/`: settings, bearer auth, spool, jobs, upload API,
  worker, healthcheck, and migration runner.
- [x] `ingest/transducer_ingest/upload.py`: HTTPS uploader with settle/completeness checks,
  hashing, `HEAD`, retries, and optional wait.
- [x] `scripts/upload_xle.ps1`: manual Windows wrapper with a mutex and daily log.
- [x] `ingest/Dockerfile` for the API, worker, and migrate service.
- [x] `caddy/` TLS reverse proxy and `certs/` instructions.
- [x] Compose stack: `db`, `migrate`, `ingest-api`, `ingest-worker`, `read-api`, `caddy`;
  only Caddy publishes a port.
- [x] Postgres-backed queue with `FOR UPDATE SKIP LOCKED`, stale-lock recovery, retries, and
  a heartbeat.
- [x] Unit tests for settings, auth, spool, uploader, queue helpers, and the API.
- [ ] Caddy starts with a valid certificate on the VM.
- [ ] Upload → queue → worker → database flow verified end-to-end on the VM.
- [ ] Worker crash/restart recovery verified on the VM.

**Exit criteria**

- [ ] `docker compose up -d --build` brings the stack up healthy.
- [ ] `db/tests/run_phase1.sh` passes inside the Compose project.
- [ ] `curl` to `/api/loggers` without a token returns 401; with a read token returns data.
- [ ] `POST /ingest/v1/uploads` without a token returns 401; with an ingest token enqueues a
  job.
- [ ] PostgreSQL is unreachable from the host (`psql -h <vm> -p 5432` fails).

## Phase 5 — Backfill and reconciliation

**Deliverables**

- Full upload and import of the current OneDrive export.
- Reconciliation report by status, logger, and location.
- Review notes for the 13 anomalous files and location mismatches.

**Exit criteria**

- Acceptance totals from Phase 3 confirmed in production.
- No compensated rows.
- Known anomalies documented; none block dashboard use.
- Re-running the uploader uploads zero new files.

## Phase 6 — Read-only API (implemented; VM verification pending)

**Deliverables**

- [x] `api/transducer_api/`: settings, read-role connection, bearer auth, and app.
- [x] Endpoints: `/health`, `/loggers`, `/locations`, `/series`, `/readings`,
  `/readings/latest`, `/imports`, `/series/{id}/summary`.
- [x] Local and UTC time output via `?tz=local|utc`.
- [x] Dockerfile and Compose service.
- [x] Router-level bearer authentication before any database connection.
- [ ] Verified against production data on the VM.

**Exit criteria**

- [ ] API uses only the read-only role.
- [ ] `tz=local` and `tz=utc` return the same instants with correct offsets.
- [ ] Pagination and maximum time-window limits enforced.
- [ ] CORS limited to configured dashboard origins.
- [ ] Unauthenticated requests cannot open a database connection.

## Phase 7 — Operations

**Deliverables**

- [x] Backups (cron/systemd) and restore procedure: `scripts/backup.sh`,
  `scripts/restore.sh`, and `deploy/systemd/transducer-backup.*`.
- [x] Monitoring/alerting for ingest failures, stale worker heartbeat, and disk usage:
  `scripts/healthcheck.sh` with an optional webhook, run by
  `deploy/systemd/transducer-healthcheck.*`.
- [x] Runbook: start/stop, restore, rotate passwords and tokens, add a new site/logger:
  [operations.md](operations.md).
- [x] Manual upload cadence documented for the operator:
  [operations.md](operations.md#7-upload-cadence).

**Exit criteria**

- [ ] Backup restore tested after go-live.
- [ ] Worker heartbeat and failed-job alerts verified.
- [ ] Runbook reviewed by the operator.
- [ ] Certificate rotation rehearsed.

The scripts and units are implemented; the exit criteria require the production
VM. See the Phase 7 checklist in [operations.md](operations.md#9-phase-7-checklist).

## Phase 8 — Dashboard v1 (superseded by Phase 9)

**Deliverables**

- [x] `dashboard/transducer_dashboard/`: settings, signed session auth, JSON state store,
  figure operations, read API client, and the FastAPI app.
- [x] Templates and static assets (HTMX and Plotly vendored) for the collapsible folder
  pane, figure editor (name, location, analyte, range), and Plotly timeseries chart.
- [x] Personal figures per read-token label plus shared figures with an optional editor
  allowlist.
- [x] Location-based figure selection (ADR-027): figures bind
  `(location_id, series_id)`, analyte lists are scoped to series with readings at
  the location, and charts pull location-scoped points. Figures saved before the
  change stay un-scoped ("all locations") until re-saved.
- [x] Read API additions: `/whoami`, `/series/{id}`, `/series/{id}/points` (server-side
  bucketing, optional `location_id` scope), `/locations/{id}/series`.
- [x] Dockerfile, Compose service, `dashboard_state` volume, and Caddy `/dashboard/*`
  route.
- [x] Unit tests for settings, state, figures, read client, and app routes.
- [ ] Verified in the browser against production data on the VM.

**Exit criteria**

- [ ] Login with a read token; invalid tokens are rejected and revoked tokens return to
  login.
- [ ] Personal and shared figures are isolated as designed. Phase 9 replaces this model
  with shared scenes and removes the editor allowlist.
- [ ] Creating, renaming, re-binding, and deleting figures works and survives a container
  restart.
- [ ] Charts render for 24h/7d/30d, including high-frequency aquifer-test series within
  `DASHBOARD_MAX_POINTS`.
- [ ] The dashboard container has no `POSTGRES_*` environment variables.
- [ ] `state.json` backup and restore tested.

## Phase 9 — Panel dashboard rework

The Jinja2/HTMX/Plotly dashboard UI is replaced by a HoloViz Panel application mounted
inside the existing FastAPI service ([ADR-028](decisions.md#adr-028-use-holoviz-panel-for-the-dashboard-frontend)).
The design is in [dashboard-panel-design.md](dashboard-panel-design.md).

**Deliverables**

- [x] `panel>=1.5` dependency; the Panel app is mounted in FastAPI with
  `panel.io.fastapi.add_applications`.
- [x] Jinja templates, HTMX, Plotly, vendored GridStack assets, and the `node --check`
  verification step are removed. The image stays Python-only.
- [x] State version 2: shared scenes, each a collection of gridded figures. A version 1
  `state.json` is archived as `state.v1.json` and replaced with an empty v2 document;
  the abandoned intermediate v2 draft (`shared`/`personal`) is archived as
  `state.v2-legacy.json`.
- [x] Scene domain operations: create and delete scenes; per-user selected scene and
  scenes drawer collapse state.
- [x] Left ribbon rail with full-height vertical tabs stacked left-to-right; the scenes
  drawer is leftmost and opens to the right, pushing the settings drawer and the canvas.
  It lists all shared scenes, with create and confirmed delete.
- [x] Gridded scene canvas (12 columns, 220 px rows) with a one-cell minimum figure size
  and a bottom-right lock toggle. While unlocked, figures move and resize, and painting a
  free footprint creates a figure with that span (a plain click makes a 1 x 1 figure).
  Lock freezes moving, resizing, painting, and selection. The lock is session-local and
  resets to unlocked on reload.
- [x] Figure title ribbon: click selects and highlights the figure and populates the
  settings drawer; drag moves the figure. Layout persists across reloads and restarts.
- [x] Settings drawer with a vertical tab that opens to the right and pushes the canvas:
  title, location (including "All locations"), analyte, range (24h/7d/30d), publish, and
  confirmed delete. Edits stay in a session-local draft, the card gets a yellow/orange
  unpublished border, and publishing detects a conflicting server change and offers
  overwrite or revert. Its collapse state is per session; only its tab toggles it.
- [x] Bokeh time-series charts for every figure in the selected scene, with loading and
  inline error states.
- [x] Panel cookie validation against the existing signed session cookie, the minimum
  Content Security Policy for Bokeh/Panel, and self-hosted Panel resources.
- [x] Tests for state v2, scene/figure operations, and the Panel mount.
- [x] Tests for the Panel scene-shell, grid (layout lock and canvas painting), and
  settings-drawer callbacks.
- [x] Tests for the chart builder.

**Exit criteria**

- [x] Login with a read token works; an invalid or missing session shows the sign-in path.
- [x] Create and delete scenes; a container restart preserves the remaining scenes.
- [x] Painting creates a figure with the painted span; painting stops at occupied cells and
  a conflicting span reports instead of overlapping figures.
- [ ] Clicking a ribbon selects the figure; dragging moves it; the layout survives reload
  and restart. (Selection and layout persistence are automated; dragging needs the
  browser checklist.)
- [x] Figures cannot be published with an invalid series or an incompatible
  location/series pair.
- [x] Charts render for 24h/7d/30d within `DASHBOARD_MAX_POINTS`.
- [x] Two read tokens see the same scenes.
- [x] The dashboard container has no `POSTGRES_*` environment variables, and no database
  schema, role, or read-API write route is added.
- [ ] No external CDN requests; the page works under the final CSP. (Asset URLs, the
  per-route CSP header, and the `unsafe-eval`/`blob:` allowances are automated; the
  in-browser load and grid interaction need the browser checklist.)
- [x] `state.json` backup and restore tested, including the v1 archive.
- [x] Python tests, ruff, and mypy pass.

The two unchecked items are the manual browser checks in
[dashboard.md](dashboard.md#8-verification).

## Global acceptance criteria (v1)

| Criterion | Target |
|---|---|
| Uncompensated files recorded | 101 |
| Unique samples `(serial, ts)` | 113,177 |
| Reading rows `(serial, channel, ts)` | 233,855 |
| Compensated rows | 0 |
| Fully redundant files (zero new readings) | 31 |
| Anomalous files | 13 |
| Empty files | 1 |
| Re-run inserts | 0 |
| Upload role domain-table access | Denied |
| Read role write attempts | Denied |
| Unauthenticated API requests | 401 |
| PostgreSQL reachable from the LAN | No |
| API local/UTC consistency | Identical instants |

## Note on Git

The repository is initialized locally with a GitHub remote (`transducer-datahost`). Commits
stay local until pushed. Confirm the OneDrive export folder, `.env`, and TLS material remain
ignored before each commit.
