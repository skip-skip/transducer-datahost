# Scripts

| Script | Purpose | Runs on |
|---|---|---|
| `upload_xle.ps1` | Manually upload new `.xle` files to the ingest API | Windows upload host |
| `run_ingest.ps1` | Legacy direct loader with locking and daily logging | Windows host with DB access |
| `migrate.py` | Apply `db/migrations` and update application roles | VM (or any host with admin access) |
| `backup.sh` | Nightly `pg_dump --format=custom` to a network share | Linux VM |
| `restore.sh` | Restore a dump into a scratch or production database | Linux VM |
| `healthcheck.sh` | Verify DB/API reachability and latest ingest age | Linux VM |

`upload_xle.ps1` and `migrate.py` are implemented. `run_ingest.ps1` remains for
a direct database connection during development. The shell scripts are still
planned.

## `upload_xle.ps1`

```powershell
.\scripts\upload_xle.ps1 -Source "C:\path\to\OneDrive_1_11-9-2026"
```

- Acquires a machine-wide mutex so uploads cannot overlap.
- Appends timestamped output to `logs/upload-yyyyMMdd.log` (override with
  `-LogDirectory`).
- Scans the source recursively, skips files newer than `INGEST_SETTLE_SECONDS`
  (default 120) or without a trailing `</Body_xle>`, hashes each file, and asks
  the API whether it is already known.
- Uploads with `transducer-ingest upload`, retries transient failures, and
  waits for each job to finish by default. Use `-NoWait` to enqueue only.

Parameters override the environment for that run:

```powershell
.\scripts\upload_xle.ps1 `
    -Source "C:\path\to\export" `
    -Api "https://transducer.internal:8443/ingest" `
    -Ca "C:\certs\rootCA.pem"
```

Set `INGEST_API_TOKEN` in the user environment (or pass `-Token`) so the
uploader never prompts during an unattended run. `INGEST_API_URL` and
`INGEST_CA_CERT` can be set the same way; `-Api` and `-Ca` override them.
The uploader never receives a database password.

## `migrate.py`

```bash
python scripts/migrate.py
```

Applies ordered SQL files from `db/migrations` and creates or updates
`transducer_upload`, `transducer_ingest`, and `transducer_read`. It reads the
admin `POSTGRES_*` variables plus `UPLOAD_PASSWORD`, `INGEST_PASSWORD`, and
`READ_PASSWORD`. Compose runs the same code in the `migrate` service.

## `run_ingest.ps1`

```powershell
.\scripts\run_ingest.ps1 -Source "C:\path\to\OneDrive_1_11-9-2026"
```

- Acquires a machine-wide mutex so scheduled runs cannot overlap.
- Appends timestamped output to `logs/ingest-yyyyMMdd.log` (override with `-LogDirectory`).
- Runs `python -m transducer_ingest.cli ingest` against the source folder and exits with its
  code.

The wrapper inherits its environment from the calling shell. Set the canonical
`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `INGEST_USER`, and `INGEST_PASSWORD`
variables (plus optional `SITE_TIMEZONE`) before running it.

> The VM-hosted design does **not** use `run_ingest.ps1`; uploads go through
> `upload_xle.ps1` and the HTTPS API. Keep the direct loader for development
> against a disposable database only.
