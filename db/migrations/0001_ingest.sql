-- 0001_ingest.sql
--
-- Migration form of db/init/05_ingest.sql. It is idempotent and can be applied
-- to an existing database without recreating the data volume.
--
-- The migration runner (scripts/migrate.py / transducer_ingest.service.migrate)
-- creates or updates transducer_upload, transducer_ingest, and transducer_read
-- before applying this file, so the grants below always resolve. The runner
-- wraps each file in a transaction; do not put BEGIN/COMMIT here.

CREATE SCHEMA IF NOT EXISTS ingest AUTHORIZATION CURRENT_USER;

CREATE TABLE IF NOT EXISTS ingest.job (
    job_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sha256          char(64) NOT NULL UNIQUE,
    filename        text NOT NULL,
    byte_size       bigint NOT NULL,
    source_client   text NOT NULL,
    source_ip       inet,
    status          text NOT NULL DEFAULT 'queued',
    import_status   text,
    file_id         bigint REFERENCES transducer.import_file (file_id),
    attempt_count   integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz,
    locked_at       timestamptz,
    received_at     timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz,
    error           text,
    CONSTRAINT job_sha256_format CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT job_filename_not_blank CHECK (btrim(filename) <> ''),
    CONSTRAINT job_byte_size_positive CHECK (byte_size > 0),
    CONSTRAINT job_attempt_count_nonnegative CHECK (attempt_count >= 0),
    CONSTRAINT job_status_valid CHECK (
        status IN ('queued', 'processing', 'done', 'failed')
    ),
    CONSTRAINT job_import_status_valid CHECK (
        import_status IS NULL
        OR import_status IN (
            'imported',
            'warning',
            'empty',
            'error',
            'skipped_duplicate',
            'skipped_compensated'
        )
    )
);

CREATE INDEX IF NOT EXISTS job_queue_idx
    ON ingest.job (status, next_attempt_at, job_id);
CREATE INDEX IF NOT EXISTS job_received_idx
    ON ingest.job (received_at DESC);
CREATE INDEX IF NOT EXISTS job_file_idx
    ON ingest.job (file_id);

CREATE TABLE IF NOT EXISTS ingest.worker_heartbeat (
    worker_id text PRIMARY KEY,
    last_seen timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT worker_id_not_blank CHECK (btrim(worker_id) <> '')
);

CREATE OR REPLACE FUNCTION ingest.file_known(p_sha text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, transducer
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM transducer.import_file
        WHERE sha256 = p_sha
    );
$$;

REVOKE ALL ON FUNCTION ingest.file_known(text) FROM PUBLIC;

REVOKE ALL ON SCHEMA ingest FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA ingest FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA ingest FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ingest FROM PUBLIC;

GRANT USAGE ON SCHEMA ingest TO transducer_upload, transducer_ingest;

GRANT SELECT, INSERT, UPDATE ON ingest.job TO transducer_upload;
GRANT SELECT ON ingest.worker_heartbeat TO transducer_upload;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ingest TO transducer_upload;
GRANT EXECUTE ON FUNCTION ingest.file_known(text) TO transducer_upload;

GRANT SELECT, INSERT, UPDATE ON ingest.job TO transducer_ingest;
GRANT SELECT, INSERT, UPDATE ON ingest.worker_heartbeat TO transducer_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ingest TO transducer_ingest;

ALTER DEFAULT PRIVILEGES IN SCHEMA ingest
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA ingest
    REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA ingest
    REVOKE ALL ON FUNCTIONS FROM PUBLIC;
