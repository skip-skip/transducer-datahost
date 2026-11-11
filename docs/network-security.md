# Network and Security

## 1. Topology

```
+-----------------------------+          +--------------------------------------+
| Windows upload host         |          | Linux VM                             |
| - OneDrive sync             |  HTTPS   | - Docker Engine                      |
| - uploader CLI              |  8443    | - Caddy (TLS, only published port)   |
| - ingest bearer token only  | -------> | - ingest-api  (upload role)          |
+-----------------------------+          | - ingest-worker (ingest role)        |
                                         | - read-api    (read role)            |
                                         | - dashboard   (no DB role)           |
                                         | - postgres:16 (no published port)    |
                                         | - pg_dump backups                    |
                                         +-------------------+------------------+
                                                             | HTTPS 8443
                                                             |
                                         +-------------------+------------------+
                                         | Internal consumers                   |
                                         | - analysts (read API or dashboard)   |
                                         | - browser dashboard (read token)     |
                                         +--------------------------------------+
```

All traffic stays on the internal network. There is no public exposure. Only Caddy
publishes a host port; PostgreSQL and the application services are reachable only on the
internal Compose network `backend` (`internal: true`). Caddy also joins a non-internal
`edge` network, used by no other service, because Docker does not publish host ports for a
container attached exclusively to internal networks (moby/moby #44986).

## 2. Firewall rules

| Source | Destination | Port | Purpose | Rule |
|---|---|---|---|---|
| Windows upload host | VM | 8443 | HTTPS uploads | Allow from upload host IP only |
| Dashboard/analyst subnet | VM | 8443 | Read-only API and dashboard | Allow from internal subnet |
| Everything else | VM | 8443 | — | Deny |
| Any | VM | 5432 | PostgreSQL | Deny (no published port) |

Implementation: `ufw`/`firewalld` on the VM, plus any upstream network ACL. Bind Caddy to
the internal interface rather than `0.0.0.0` where possible. Never add a Compose `ports:`
entry for `db`, `ingest-api`, `ingest-worker`, `read-api`, or `dashboard`.

## 3. PostgreSQL roles

| Role | Capabilities | Used by |
|---|---|---|
| `transducer_admin` | Owner; DDL and maintenance | Provisioning and the `migrate` service only |
| `transducer_upload` | `SELECT`/`INSERT`/`UPDATE` on `ingest.job`, `SELECT` on `ingest.worker_heartbeat`, `EXECUTE` on `ingest.file_known()`, `USAGE`/`SELECT` on `ingest` sequences; no domain tables, no `DELETE`, no DDL | Ingest API |
| `transducer_ingest` | `SELECT`/`INSERT`/`UPDATE` on the five domain base tables and the `ingest` queue/heartbeat; `USAGE`/`SELECT` on sequences; no `DELETE`, `TRUNCATE`, or DDL | Ingest worker/loader |
| `transducer_read` | `SELECT` on `logger`, `location`, `series`, `reading`, and the three read views; no direct `import_file` access, no `ingest` access, no sequences, no writes | Read API |

The dashboard service has **no database role and no database credentials**. It calls the
read API with the browser user's read token and stores its own JSON state on the
`dashboard_state` volume.

Rules:

- Application roles never own objects.
- `transducer_read` has no direct access to `import_file`; the curated `v_import` view
  exposes the audit fields, including `error`.
- `transducer_upload` cannot read domain tables. `ingest.file_known()` is a
  `SECURITY DEFINER` function that returns only a boolean, so the upload API can answer
  `HEAD /v1/files/{sha}` without `SELECT` on `import_file`.
- Passwords are generated, stored in the secret store / `.env` on the VM, and rotated.
- No shared superuser credentials in application config.
- Role `search_path` is pinned: upload sees `pg_catalog, ingest`; worker sees
  `pg_catalog, transducer, ingest`; read sees `pg_catalog, transducer`.

## 4. Authentication

### Database

- `password_encryption = scram-sha-256`; all application roles are created with SCRAM
  passwords by `db/init/01_roles.sql` and refreshed by the migrate runner.
- Compose sets `POSTGRES_HOST_AUTH_METHOD=scram-sha-256` and initializes with
  `--auth-host=scram-sha-256 --auth-local=scram-sha-256`, so the generated `pg_hba.conf`
  contains no `trust` entries.
- Because PostgreSQL is not published, only containers on `backend` can connect. If a
  direct connection is ever needed for maintenance, use
  `docker compose exec db psql ...`, not a published port.

### API

- TLS is mandatory. Caddy terminates HTTPS on 8443 with a certificate for
  `INGEST_HOSTNAME` (see `certs/README.md`). The listener is a catch-all and sets
  `default_sni`, so clients that connect by IP literal (which send no SNI) still complete
  the handshake; the certificate must include the IP for verification.
- Both APIs use static bearer tokens configured as comma-separated `label:token` pairs:
  - `INGEST_API_TOKENS` for the upload API.
  - `READ_API_TOKENS` for the read API.
- Tokens are compared with `secrets.compare_digest`, and every configured entry is
  compared so response time does not reveal which label matched.
- The token label is recorded on `ingest.job.source_client` and in the API logs for audit.
- The read API applies authentication at the router level, before opening a database
  connection.
- The dashboard authenticates the browser with the same read tokens:
  - Login calls `GET /api/whoami`; success stores `{label, token}` in a signed,
    `HttpOnly`, `Secure`, `SameSite=Strict` cookie.
  - Every dashboard data request forwards the token to the read API.
  - A read API `401` clears the session and redirects to login.
  - `DASHBOARD_SECRET_KEY` signs the cookie; rotating it invalidates sessions. The
    cookie is signed, not encrypted, so it must remain `HttpOnly` and TLS-only.
  - The dashboard sets `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
    `Referrer-Policy: same-origin`, and a self-only CSP.
- `/health` is unauthenticated so Compose and the proxy can probe liveness. It exposes no
  data.

## 5. Secrets management

- `.env` is never committed (see `.gitignore`).
- `.env.example` documents required variables with placeholder values.
- `certs/*.crt`, `certs/*.key`, and CA material are ignored by Git.
- Production values live on the VM with restrictive file permissions.
- Rotate database passwords and API tokens on a defined schedule and after staff changes.
  Regenerate `INGEST_API_TOKENS` / `READ_API_TOKENS` per client so a single token can be
  revoked without affecting others.
- The Windows upload host holds only `INGEST_API_TOKEN`; it has no database credential.
- The read API uses only the read-only role; the upload API uses only the queue role.

## 6. API security

- Read API has read-only endpoints; no mutation verbs.
- Ingest API accepts only `.xle` uploads, caps body size at `MAX_UPLOAD_BYTES` (10 MB by
  default), and requires the `</Body_xle>` completeness marker.
- CORS restricted to known dashboard origins (`API_CORS_ORIGINS`); empty by default.
- The dashboard is server-rendered; the browser never receives a read token in
  JavaScript and no CORS exception is needed for it.
- Dashboard mutations are same-origin HTML form posts protected by the `SameSite=Strict`
  session cookie. Figure state is scoped by the token label.
- Read request limits: maximum time window and row count per query; pagination required.
- No SQL string interpolation; parameterized queries only.
- Caddy is the only public listener and logs requests as JSON.
- Rate limiting can be added at the proxy for untrusted internal clients.

## 7. Backups

- Nightly `pg_dump --format=custom` to a network share.
- Dashboard state is a JSON file, not a database table. Back it up separately with
  `docker compose exec dashboard cat /var/lib/transducer-dashboard/state.json`.
- Retention: 30 daily, 12 monthly (adjust to policy).
- Backups encrypted at rest by the share/platform.
- Quarterly restore test into a scratch database.
- Document recovery point objective (RPO, ~24h) and recovery time objective (RTO).

## 8. Monitoring and alerting

| Signal | Source | Action |
|---|---|---|
| Ingest failures | `ingest.job.status = 'failed'` or `import_file.status = 'error'` | Alert ingest owner |
| Worker stopped | `ingest.worker_heartbeat` older than `WORKER_HEARTBEAT_TIMEOUT_SECONDS` | Alert; Compose healthcheck marks the worker unhealthy |
| Upload rejected | Ingest API 4xx/5xx logs | Investigate token, certificate, or file |
| DB disk usage | Host metrics | Alert at 80% |
| Connection failures | Postgres logs | Investigate credentials |
| API 5xx rate | API logs/proxy | Investigate |
| Certificate expiry | Caddy/client errors | Rotate per `certs/README.md` |

## 9. Hardening checklist

- [ ] Only Caddy publishes a port (8443).
- [ ] PostgreSQL has no published port and `backend` is `internal: true`.
- [ ] Caddy's `edge` network is not `internal` (Docker will not publish ports otherwise).
- [ ] Firewall default-deny with explicit allow rules.
- [ ] `scram-sha-256` only; no `trust` entries.
- [ ] Separate upload/worker/read roles; no application superuser.
- [ ] TLS certificate valid for `INGEST_HOSTNAME`; clients trust the CA.
- [ ] Distinct bearer tokens per upload client and per analyst.
- [ ] Dashboard container has no `POSTGRES_*` variables or database credentials.
- [ ] `DASHBOARD_SECRET_KEY` is set and the session cookie is `Secure`.
- [ ] Dashboard state volume is included in backups and restore tests.
- [ ] `.env` and TLS material excluded from version control.
- [ ] Container images pinned to explicit versions.
- [ ] Automated backups verified by restore.
- [ ] OS and image security updates scheduled.
- [ ] API CORS limited and pagination enforced.
- [ ] Audit trail (`import_file`, `ingest.job`) retained.
