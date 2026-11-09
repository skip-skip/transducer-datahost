# Network and Security

## 1. Topology

```
+-----------------------------+          +-----------------------------+
| Windows host                |          | Linux VM                    |
| - OneDrive sync             |  TCP     | - Docker Engine             |
| - Scheduled ingester        |  5432    | - postgres:16 (db)          |
| - write role only           | -------> | - FastAPI (api)             |
+-----------------------------+          | - pg_dump backups           |
                                         +--------------+--------------+
                                                        | TCP 5432 (read-only)
                                                        |
                                         +--------------+--------------+
                                         | Internal consumers          |
                                         | - analysts (SQL)            |
                                         | - dashboards (HTTP :8000)   |
                                         +-----------------------------+
```

All traffic stays on the internal network. There is no public exposure.

## 2. Firewall rules

| Source | Destination | Port | Purpose | Rule |
|---|---|---|---|---|
| Ingest host | DB VM | 5432 | Writes | Allow from ingest host IP only |
| Dashboard/analyst subnet | DB VM | 5432 | Read-only SQL | Allow from internal subnet |
| Dashboard/analyst subnet | API VM | 8000 | Read-only HTTP | Allow from internal subnet |
| Everything else | DB VM / API VM | 5432 / 8000 | — | Deny |

Implementation: `ufw`/`firewalld` on the VM, plus any upstream network ACL. Publish Docker
ports on the internal interface rather than `0.0.0.0` where possible.

## 3. PostgreSQL roles

| Role | Capabilities | Used by |
|---|---|---|
| `transducer_admin` | Owner; DDL and maintenance | Provisioning only |
| `transducer_ingest` | `SELECT`/`INSERT`/`UPDATE` on the five base tables and `USAGE`/`SELECT` on sequences; no `DELETE`, `TRUNCATE`, or DDL | Ingester |
| `transducer_read` | `SELECT` on `logger`, `location`, `series`, `reading`, and the three read views; no direct `import_file` access, no sequences, no writes | API and analysts |

Rules:

- Application roles never own objects.
- `transducer_read` has no direct access to `import_file`; the curated `v_import` view
  exposes the audit fields, including `error`.
- Passwords are generated, stored in the secret store / `.env` on the host, and rotated.
- No shared superuser credentials in application config.

## 4. Authentication

- `password_encryption = scram-sha-256`; both application roles are created with SCRAM
  passwords by `db/init/01_roles.sql`.
- Compose sets `POSTGRES_HOST_AUTH_METHOD=scram-sha-256` and initializes with
  `--auth-host=scram-sha-256 --auth-local=scram-sha-256`, so the generated `pg_hba.conf`
  contains no `trust` entries.
- `pg_hba.conf` entries are scoped by database, role, and source subnet, for example:

```
# TYPE  DATABASE     USER                ADDRESS           METHOD
host    transducer   transducer_ingest   10.20.30.41/32    scram-sha-256
host    transducer   transducer_read     10.20.0.0/16      scram-sha-256
```

- Reject all other remote connections.
- Enable TLS (`ssl = on`) if traffic crosses untrusted segments; otherwise document the
  internal-only assumption.

## 5. Secrets management

- `.env` is never committed (see `.gitignore`).
- `.env.example` documents required variables with placeholder values.
- Production values live on the VM/host with restrictive file permissions.
- Rotate database passwords on a defined schedule and after staff changes.
- The API uses only the read-only role.

## 6. API security

- Read-only endpoints; no mutation verbs.
- CORS restricted to known dashboard origins (`API_CORS_ORIGINS`).
- Request limits: maximum time window and row count per query; pagination required.
- No SQL string interpolation; parameterized queries only.
- Optional reverse proxy for TLS and centralized access logs.
- Rate limiting at the proxy for untrusted internal clients.

## 7. Backups

- Nightly `pg_dump --format=custom` to a network share.
- Retention: 30 daily, 12 monthly (adjust to policy).
- Backups encrypted at rest by the share/platform.
- Quarterly restore test into a scratch database.
- Document recovery point objective (RPO, ~24h) and recovery time objective (RTO).

## 8. Monitoring and alerting

| Signal | Source | Action |
|---|---|---|
| Ingest failures | `import_file.status = 'error'` | Alert ingest owner |
| No successful ingest in 24h | Scheduler log / heartbeat table | Alert |
| DB disk usage | Host metrics | Alert at 80% |
| Connection failures | Postgres logs | Investigate firewall/credentials |
| API 5xx rate | API logs/proxy | Investigate |

## 9. Hardening checklist

- [ ] Postgres bound to the internal interface; no public port mapping.
- [ ] Firewall default-deny with explicit allow rules.
- [ ] `scram-sha-256` only; no `trust` entries.
- [ ] Separate write/read roles; no application superuser.
- [ ] `.env` excluded from version control.
- [ ] Container images pinned to explicit versions.
- [ ] Automated backups verified by restore.
- [ ] OS and image security updates scheduled.
- [ ] API CORS limited and pagination enforced.
- [ ] Audit trail (`import_file`) retained.
