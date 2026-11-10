"""Container healthcheck for the ingest worker.

Exit 0 when the worker heartbeat is fresh. Used by Compose so a dead worker is
visible even though it has no HTTP port.
"""

from __future__ import annotations

from datetime import UTC, datetime

from . import jobs
from .settings import IngestSettings


def main() -> int:
    settings = IngestSettings.from_env(role="ingest", require_tokens=False)
    with settings.database.connect() as conn:
        latest = jobs.latest_heartbeat(conn)

    if latest is None:
        print("no worker heartbeat")
        return 1

    age = (datetime.now(UTC) - latest).total_seconds()
    if age > settings.worker_heartbeat_timeout_seconds:
        print(f"worker heartbeat is stale ({age:.0f}s)")
        return 1
    print(f"worker heartbeat is fresh ({age:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
