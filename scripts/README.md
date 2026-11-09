# Scripts

Operational scripts are **not implemented yet**. Planned contents:

| Script | Purpose | Runs on |
|---|---|---|
| `run_ingest.ps1` | Invoke the ingester with locking and logging | Windows ingest host |
| `backup.sh` | Nightly `pg_dump --format=custom` to a network share | Linux VM |
| `restore.sh` | Restore a dump into a scratch or production database | Linux VM |
| `healthcheck.sh` | Verify DB/API reachability and latest ingest age | Linux VM |

## Scheduling

- Ingest: Windows Task Scheduler, e.g. every 6 hours.
- Backup: cron or systemd timer, nightly.
- Restore test: quarterly, manual or scheduled.

## Design reference

See [`../docs/ingestion.md`](../docs/ingestion.md) and
[`../docs/network-security.md`](../docs/network-security.md).
