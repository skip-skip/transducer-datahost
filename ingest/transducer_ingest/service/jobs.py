"""Durable queue operations backed by ``ingest.job``.

The queue uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so a single worker (or a
small number of workers) can claim jobs safely. All functions take an open
psycopg connection; the caller owns the transaction/commit boundary.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

JOB_FIELDS = """
    job_id, sha256, filename, byte_size, source_client, source_ip,
    status, import_status, file_id, attempt_count, next_attempt_at,
    locked_at, received_at, started_at, finished_at, error
"""

# The UPDATE ... FROM form below puts both the target and the CTE in scope,
# so RETURNING must qualify the target columns to avoid an ambiguous reference.
JOB_FIELDS_QUALIFIED = """
    j.job_id, j.sha256, j.filename, j.byte_size, j.source_client, j.source_ip,
    j.status, j.import_status, j.file_id, j.attempt_count, j.next_attempt_at,
    j.locked_at, j.received_at, j.started_at, j.finished_at, j.error
"""


def _job_row(conn: Connection, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def enqueue_job(
    conn: Connection,
    *,
    sha256: str,
    filename: str,
    byte_size: int,
    source_client: str,
    source_ip: str | None,
) -> tuple[dict[str, Any], bool]:
    """Insert a queued job, or return the existing job for this SHA.

    Returns ``(job, created)``. The unique SHA constraint makes concurrent
    uploads of the same bytes idempotent.
    """

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            INSERT INTO ingest.job
                (sha256, filename, byte_size, source_client, source_ip)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sha256) DO NOTHING
            RETURNING {JOB_FIELDS}
            """,
            (sha256, filename, byte_size, source_client, source_ip),
        )
        row = cur.fetchone()
        if row is not None:
            return row, True

        cur.execute(
            f"SELECT {JOB_FIELDS} FROM ingest.job WHERE sha256 = %s",
            (sha256,),
        )
        existing = cur.fetchone()

    if existing is None:  # pragma: no cover - defensive
        raise RuntimeError(f"job for sha256 {sha256} disappeared during enqueue")
    return existing, False


def get_job(conn: Connection, job_id: int) -> dict[str, Any] | None:
    return _job_row(
        conn,
        f"SELECT {JOB_FIELDS} FROM ingest.job WHERE job_id = %s",
        (job_id,),
    )


def get_job_by_sha(conn: Connection, sha256: str) -> dict[str, Any] | None:
    return _job_row(
        conn,
        f"SELECT {JOB_FIELDS} FROM ingest.job WHERE sha256 = %s",
        (sha256,),
    )


def list_jobs(
    conn: Connection,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT {JOB_FIELDS}
            FROM ingest.job
            {where}
            ORDER BY job_id DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params),
        )
        return list(cur.fetchall())


def retry_job(conn: Connection, job_id: int) -> dict[str, Any] | None:
    """Reset a failed job to queued. Returns ``None`` if it was not failed."""

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            UPDATE ingest.job
            SET status = 'queued',
                import_status = NULL,
                error = NULL,
                attempt_count = 0,
                next_attempt_at = NULL,
                locked_at = NULL,
                finished_at = NULL
            WHERE job_id = %s AND status = 'failed'
            RETURNING {JOB_FIELDS}
            """,
            (job_id,),
        )
        return cur.fetchone()


def claim_job(conn: Connection) -> dict[str, Any] | None:
    """Atomically claim the oldest ready job."""

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            WITH candidate AS (
                SELECT job_id
                FROM ingest.job
                WHERE status = 'queued'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                ORDER BY job_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE ingest.job AS j
            SET status = 'processing',
                locked_at = now(),
                started_at = COALESCE(j.started_at, now()),
                attempt_count = j.attempt_count + 1
            FROM candidate AS c
            WHERE j.job_id = c.job_id
            RETURNING {JOB_FIELDS_QUALIFIED}
            """,
            (),
        )
        return cur.fetchone()


def finish_job(
    conn: Connection,
    job_id: int,
    *,
    import_status: str,
    file_id: int | None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE ingest.job
        SET status = 'done',
            import_status = %s,
            file_id = %s,
            error = %s,
            locked_at = NULL,
            finished_at = now()
        WHERE job_id = %s
        """,
        (import_status, file_id, error, job_id),
    )


def fail_job(
    conn: Connection,
    job_id: int,
    *,
    error: str,
    retry: bool,
    next_attempt_at: datetime | None = None,
) -> None:
    if retry:
        conn.execute(
            """
            UPDATE ingest.job
            SET status = 'queued',
                error = %s,
                next_attempt_at = %s,
                locked_at = NULL
            WHERE job_id = %s
            """,
            (error, next_attempt_at, job_id),
        )
        return

    conn.execute(
        """
        UPDATE ingest.job
        SET status = 'failed',
            error = %s,
            locked_at = NULL,
            finished_at = now()
        WHERE job_id = %s
        """,
        (error, job_id),
    )


def heartbeat(conn: Connection, worker_id: str) -> None:
    conn.execute(
        """
        INSERT INTO ingest.worker_heartbeat (worker_id, last_seen)
        VALUES (%s, now())
        ON CONFLICT (worker_id) DO UPDATE SET last_seen = now()
        """,
        (worker_id,),
    )


def latest_heartbeat(conn: Connection) -> datetime | None:
    row = conn.execute(
        "SELECT max(last_seen) FROM ingest.worker_heartbeat"
    ).fetchone()
    return row[0] if row else None


def recover_stale(conn: Connection, timeout_seconds: int) -> int:
    """Requeue jobs left in ``processing`` by a crashed worker."""

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingest.job
            SET status = 'queued',
                locked_at = NULL,
                error = 'recovered stale lock'
            WHERE status = 'processing'
              AND locked_at < now() - make_interval(secs => %s)
            """,
            (timeout_seconds,),
        )
        return cur.rowcount


def file_known(conn: Connection, sha256: str) -> bool:
    row = conn.execute(
        "SELECT ingest.file_known(%s)", (sha256,)
    ).fetchone()
    return bool(row and row[0])


def queued_jobs(conn: Connection) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT job_id, sha256, filename FROM ingest.job "
            "WHERE status IN ('queued', 'processing') ORDER BY job_id"
        )
        return list(cur.fetchall())


def retry_delay(attempt_count: int, base_seconds: int) -> datetime:
    """Exponential backoff for a transient failure."""

    exponent = max(0, attempt_count - 1)
    return datetime.now().astimezone() + timedelta(
        seconds=base_seconds * (2**exponent)
    )
