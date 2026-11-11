# Dashboard

The dashboard is a separate service that reads the existing read-only API. It
adds no database tables, no database role, and no write routes to the API.

Production URL: `https://<INGEST_HOSTNAME>:8443/dashboard/`.

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
in PostgreSQL.

```json
{
  "version": 1,
  "shared": {
    "figures": [
      {
        "id": "5f0c8e...",
        "name": "GC-1980 water level",
        "series_id": 42,
        "position": 0,
        "config": {},
        "created_by": "analyst-1",
        "created_at": "2026-11-11T13:00:00Z",
        "updated_at": "2026-11-11T13:00:00Z"
      }
    ]
  },
  "personal": {
    "analyst-1": {
      "figures": [],
      "ui": {
        "view": "personal",
        "personal": { "selected_figure_id": null, "folders_collapsed": false },
        "shared":   { "selected_figure_id": null, "folders_collapsed": false },
        "tz": "local"
      }
    }
  }
}
```

Rules:

- A figure binds to a `series_id`. Source and analyte are UI selectors that
  resolve to one series, so multi-channel loggers and unit changes stay
  unambiguous.
- Personal figures are visible only to the matching token label.
- Shared figures are visible to every valid read token. `DASHBOARD_SHARED_EDITORS`
  optionally restricts who may edit them; empty means any valid read key.
- Selected figure and pane collapse are stored per user and per view.
- Figure IDs are UUIDs because there is no database identity column.

Write safety:

- `filelock` around every read-modify-write.
- Write to `state.json.tmp`, `fsync`, copy the current file to `state.json.bak`,
  then `os.replace` the temporary file into place.
- Validate the document on load and before every write. A corrupt file stops the
  service with a clear error instead of silently discarding state.
- One uvicorn worker.
- Directory mode `0700`, file mode `0600`.

## 4. UI

Left pane:

- Collapsible, single-level folder list. Each row is one figure.
- `My figures` and `Shared` tabs.
- `+ New figure` creates a draft in the active tab.
- The active figure is highlighted; the pane collapse and selection are saved.

Main view:

- `Name`: used in the folder list.
- `Source`: logger serial from `GET /api/loggers`.
- `Analyte`: series from `GET /api/series?serial=...`; the value is `series_id`.
- Range buttons: `24h`, `7d`, `30d`; charts use server-side bucketing.
- `Save` and `Delete`.

A new figure starts as a draft with a default name and no series. If a source has
no series, the analyte selector reports it.

## 5. Read API additions

All additions are read-only and require no schema change:

| Endpoint | Purpose |
|---|---|
| `GET /whoami` | Returns the token label as `{"label": "..."}` |
| `GET /series/{id}` | One series with reading count and first/last timestamps |
| `GET /series/{id}/points?start=&end=&points=&tz=` | Server-side time buckets (`ts`, `ts_utc`, `value`, `min_value`, `max_value`, `n`) bounded by `points` |

`/points` uses `date_bin` with a width chosen so the inclusive window returns at
most `points` rows. The existing `READ_MAX_WINDOW_DAYS` and `READ_MAX_ROWS`
limits still apply.

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
docker compose ps
```

Open `https://<INGEST_HOSTNAME>:8443/dashboard/` and sign in with a read token.

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
2. Create a personal figure, a shared figure, rename, change source/analyte,
   change range, and delete.
3. Restart the dashboard container; figures and selected state survive.
4. Log in with a second token: shared figures are visible, the first user's
   personal figures are not.
5. `docker compose exec dashboard env` shows no `POSTGRES_*` variables.
6. Rotate a read token; the old session returns to login on the next API call.
7. Confirm a 30-day high-frequency chart returns at most `DASHBOARD_MAX_POINTS`
   points.
8. Test backup and restore of the volume.

## 9. Boundaries

- No changes to `transducer.logger`, `location`, `series`, `reading`,
  `import_file`, or `ingest.job`.
- No dashboard state in PostgreSQL.
- No write routes on the read API.
- No new user/password system; read API keys remain the credential.
- Folders are single-level.
