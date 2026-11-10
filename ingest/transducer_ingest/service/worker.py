"""Single-writer ingest worker.

The worker claims one ``ingest.job`` at a time, loads the spooled file with the
existing parser/validator/loader, and records the outcome. Parse/validation
errors are terminal and still write an ``import_file`` audit row. Transient
database errors are recorded only in ``ingest.job`` so a retry is possible.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import time
from typing import Any

from ..load import load_one
from ..validate import Status
from . import jobs
from .log import configure as configure_logging
from .settings import IngestSettings
from .spool import Spool

logger = logging.getLogger("transducer.ingest.worker")


class Worker:
    def __init__(self, settings: IngestSettings) -> None:
        self.settings = settings
        self.spool = Spool(settings.spool_dir)
        self.worker_id = os.environ.get("WORKER_ID") or (
            f"{socket.gethostname()}:{os.getpid()}"
        )
        self._stop = False

    def stop(self, *_args: object) -> None:
        self._stop = True

    def run(self) -> None:
        self.spool.ensure()
        self.recover()
        logger.info("worker started", extra={"worker_id": self.worker_id})

        while not self._stop:
            try:
                job = self._claim()
                if job is None:
                    self._sleep(self.settings.worker_poll_seconds)
                    continue
                self.process(job)
            except Exception:  # noqa: BLE001 - keep the worker alive
                logger.exception(
                    "worker loop error", extra={"worker_id": self.worker_id}
                )
                self._sleep(max(1.0, self.settings.worker_poll_seconds))

        logger.info("worker stopped", extra={"worker_id": self.worker_id})

    def recover(self) -> None:
        with self.settings.database.connect() as conn:
            recovered = jobs.recover_stale(
                conn, self.settings.worker_lock_timeout_seconds
            )
            conn.commit()
        if recovered:
            logger.warning(
                "requeued stale jobs",
                extra={"worker_id": self.worker_id, "status": str(recovered)},
            )

        self._reconcile_spool()

        removed_processed = self.spool.cleanup_old(
            self.settings.spool_processed_retention_days, state="processed"
        )
        removed_failed = self.spool.cleanup_old(
            self.settings.spool_failed_retention_days, state="failed"
        )
        if removed_processed or removed_failed:
            logger.info(
                "spool cleanup removed files",
                extra={
                    "worker_id": self.worker_id,
                    "status": f"processed={removed_processed} failed={removed_failed}",
                },
            )

    def _claim(self) -> dict[str, Any] | None:
        with self.settings.database.connect() as conn:
            jobs.heartbeat(conn, self.worker_id)
            job = jobs.claim_job(conn)
            conn.commit()
        return job

    def process(self, job: dict[str, Any]) -> None:
        job_id = int(job["job_id"])
        sha256 = str(job["sha256"])
        path = self.spool.path("queued", sha256)

        if not path.exists():
            self._handle_missing_file(job_id, sha256)
            return

        try:
            with self.settings.database.connect() as conn:
                result = load_one(
                    conn,
                    path,
                    site_timezone=self.settings.site_timezone,
                )

            destination = (
                "failed" if result.status is Status.ERROR else "processed"
            )
            try:
                self.spool.move(sha256, "queued", destination)
            except OSError:
                logger.exception(
                    "could not move spool file",
                    extra={
                        "worker_id": self.worker_id,
                        "job_id": job_id,
                        "sha256": sha256,
                        "status": destination,
                    },
                )

            with self.settings.database.connect() as conn:
                jobs.finish_job(
                    conn,
                    job_id,
                    import_status=result.status.value,
                    file_id=result.file_id,
                    error=result.error,
                )
                conn.commit()

            logger.info(
                "job done",
                extra={
                    "worker_id": self.worker_id,
                    "job_id": job_id,
                    "sha256": sha256,
                    "source_filename": result.filename,
                    "status": result.status.value,
                },
            )
        except Exception as exc:  # noqa: BLE001 - classified and retried below
            attempt_count = int(job.get("attempt_count") or 1)
            retry = attempt_count < self.settings.worker_max_attempts
            next_attempt_at = (
                jobs.retry_delay(
                    attempt_count, self.settings.worker_backoff_seconds
                )
                if retry
                else None
            )
            if not retry:
                self.spool.move(sha256, "queued", "failed")

            with self.settings.database.connect() as conn:
                jobs.fail_job(
                    conn,
                    job_id,
                    error=str(exc)[:4000],
                    retry=retry,
                    next_attempt_at=next_attempt_at,
                )
                conn.commit()

            logger.exception(
                "job failed",
                extra={
                    "worker_id": self.worker_id,
                    "job_id": job_id,
                    "sha256": sha256,
                    "status": "queued" if retry else "failed",
                },
            )

    def _handle_missing_file(self, job_id: int, sha256: str) -> None:
        processed = self.spool.path("processed", sha256)
        failed = self.spool.path("failed", sha256)

        with self.settings.database.connect() as conn:
            if processed.exists():
                jobs.finish_job(
                    conn,
                    job_id,
                    import_status="skipped_duplicate",
                    file_id=None,
                    error="file already present in processed spool",
                )
            else:
                jobs.fail_job(
                    conn,
                    job_id,
                    error=(
                        "file already present in failed spool"
                        if failed.exists()
                        else "file missing from queued spool"
                    ),
                    retry=False,
                )
            conn.commit()

        logger.error(
            "job has no queued file",
            extra={
                "worker_id": self.worker_id,
                "job_id": job_id,
                "sha256": sha256,
                "status": "failed",
            },
        )

    def _reconcile_spool(self) -> None:
        queued = {path.stem for path in self.spool.queued_files()}
        with self.settings.database.connect() as conn:
            tracked = jobs.queued_jobs(conn)
            tracked_shas = {str(row["sha256"]) for row in tracked}

            for row in tracked:
                sha256 = str(row["sha256"])
                if sha256 in queued:
                    continue
                if self.spool.path("processed", sha256).exists():
                    jobs.finish_job(
                        conn,
                        int(row["job_id"]),
                        import_status="skipped_duplicate",
                        file_id=None,
                        error="file already present in processed spool",
                    )
                else:
                    jobs.fail_job(
                        conn,
                        int(row["job_id"]),
                        error="file missing from spool during recovery",
                        retry=False,
                    )

            for sha256 in queued - tracked_shas:
                path = self.spool.path("queued", sha256)
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                job, created = jobs.enqueue_job(
                    conn,
                    sha256=sha256,
                    filename=path.name,
                    byte_size=size,
                    source_client="recovered",
                    source_ip=None,
                )
                if not created and job.get("status") == "done":
                    self.spool.move(sha256, "queued", "processed")
                elif not created and job.get("status") == "failed":
                    self.spool.move(sha256, "queued", "failed")
            conn.commit()

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while not self._stop and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))


def main() -> int:
    configure_logging()
    settings = IngestSettings.from_env(role="ingest", require_tokens=False)
    worker = Worker(settings)
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
