import os
import uuid

import pytest

from transducer_ingest.service import jobs


def _sha() -> str:
    return uuid.uuid4().hex


@pytest.mark.integration
def test_job_lifecycle() -> None:
    dsn = os.environ.get("TRANSDUCER_TEST_DSN")
    if not dsn:
        pytest.skip("TRANSDUCER_TEST_DSN is not set")
    psycopg = pytest.importorskip("psycopg")

    with psycopg.connect(dsn) as conn:
        present = conn.execute(
            "SELECT to_regclass('ingest.job') IS NOT NULL"
        ).fetchone()[0]
        if not present:
            pytest.skip("ingest.job is not present; run the migrations")
        conn.rollback()

        sha = _sha()
        with conn.transaction(force_rollback=True):
            job, created = jobs.enqueue_job(
                conn,
                sha256=sha,
                filename="pytest_job.xle",
                byte_size=10,
                source_client="pytest",
                source_ip="127.0.0.1",
            )
            assert created is True
            assert job["status"] == "queued"

            duplicate, created_again = jobs.enqueue_job(
                conn,
                sha256=sha,
                filename="pytest_job.xle",
                byte_size=10,
                source_client="pytest",
                source_ip="127.0.0.1",
            )
            assert created_again is False
            assert duplicate["job_id"] == job["job_id"]

            # Keep unrelated queued jobs out of the way so this test claims its
            # own row. The whole transaction is rolled back at the end.
            conn.execute(
                "UPDATE ingest.job SET next_attempt_at = now() + interval '1 day' "
                "WHERE job_id <> %s AND status = 'queued'",
                (job["job_id"],),
            )

            claimed = jobs.claim_job(conn)
            assert claimed is not None
            assert claimed["job_id"] == job["job_id"]
            assert claimed["status"] == "processing"
            assert claimed["attempt_count"] == 1

            jobs.finish_job(
                conn,
                job["job_id"],
                import_status="imported",
                file_id=None,
            )
            done = jobs.get_job(conn, job["job_id"])
            assert done is not None
            assert done["status"] == "done"
            assert done["import_status"] == "imported"

            jobs.fail_job(
                conn,
                job["job_id"],
                error="transient",
                retry=True,
            )
            retried = jobs.get_job(conn, job["job_id"])
            assert retried is not None
            assert retried["status"] == "queued"

            jobs.fail_job(
                conn,
                job["job_id"],
                error="terminal",
                retry=False,
            )
            failed = jobs.get_job(conn, job["job_id"])
            assert failed is not None
            assert failed["status"] == "failed"

            reset = jobs.retry_job(conn, job["job_id"])
            assert reset is not None
            assert reset["status"] == "queued"
            assert jobs.retry_job(conn, job["job_id"]) is None

            assert jobs.file_known(conn, _sha()) is False


@pytest.mark.integration
def test_stale_jobs_are_requeued() -> None:
    dsn = os.environ.get("TRANSDUCER_TEST_DSN")
    if not dsn:
        pytest.skip("TRANSDUCER_TEST_DSN is not set")
    psycopg = pytest.importorskip("psycopg")

    with psycopg.connect(dsn) as conn:
        present = conn.execute(
            "SELECT to_regclass('ingest.job') IS NOT NULL"
        ).fetchone()[0]
        if not present:
            pytest.skip("ingest.job is not present; run the migrations")
        conn.rollback()

        with conn.transaction(force_rollback=True):
            job, _created = jobs.enqueue_job(
                conn,
                sha256=_sha(),
                filename="pytest_stale.xle",
                byte_size=10,
                source_client="pytest",
                source_ip=None,
            )
            conn.execute(
                "UPDATE ingest.job SET status = 'processing', "
                "locked_at = now() - interval '1 hour' WHERE job_id = %s",
                (job["job_id"],),
            )

            assert jobs.recover_stale(conn, 60) >= 1

            recovered = jobs.get_job(conn, job["job_id"])
            assert recovered is not None
            assert recovered["status"] == "queued"


@pytest.mark.integration
def test_worker_heartbeat() -> None:
    dsn = os.environ.get("TRANSDUCER_TEST_DSN")
    if not dsn:
        pytest.skip("TRANSDUCER_TEST_DSN is not set")
    psycopg = pytest.importorskip("psycopg")

    with psycopg.connect(dsn) as conn:
        present = conn.execute(
            "SELECT to_regclass('ingest.worker_heartbeat') IS NOT NULL"
        ).fetchone()[0]
        if not present:
            pytest.skip("ingest.worker_heartbeat is not present; run the migrations")
        conn.rollback()

        with conn.transaction(force_rollback=True):
            jobs.heartbeat(conn, "pytest-worker")
            assert jobs.latest_heartbeat(conn) is not None
