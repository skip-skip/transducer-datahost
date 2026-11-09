# Scripts

| Script | Purpose | Runs on |
|---|---|---|
| `run_ingest.ps1` | Invoke the ingester with locking and daily logging | Windows ingest host |
| `backup.sh` | Nightly `pg_dump --format=custom` to a network share | Linux VM |
| `restore.sh` | Restore a dump into a scratch or production database | Linux VM |
| `healthcheck.sh` | Verify DB/API reachability and latest ingest age | Linux VM |

`run_ingest.ps1` is implemented; the shell scripts are still planned.

## `run_ingest.ps1`

```powershell
.\scripts\run_ingest.ps1 -Source "C:\path\to\OneDrive_1_11-9-2026"
```

- Acquires a machine-wide mutex so scheduled runs cannot overlap.
- Appends timestamped output to `logs/ingest-yyyyMMdd.log` (override with `-LogDirectory`).
- Runs `python -m transducer_ingest.cli ingest` against the source folder and exits with its
  code.

## Scheduling

- Ingest: Windows Task Scheduler, e.g. every 6 hours.
- Backup: cron or systemd timer, nightly.
- Restore test: quarterly, manual or scheduled.

## Design reference

See [`../docs/ingestion.md`](../docs/ingestion.md) and
[`../docs/network-security.md`](../docs/network-security.md).
