-- 03_views.sql
--
-- Curated read views. They are owned by the bootstrap superuser and use the
-- default security-definer behavior, so the read role can query them without
-- holding SELECT on the underlying import_file table.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------------------
-- v_reading: friendly local-time readings with provenance
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW transducer.v_reading AS
SELECT
    r.series_id,
    s.serial_number,
    f.location_id,
    l.name AS location,
    l.is_aquifer_test,
    l.timezone,
    s.data_kind,
    s.channel_no,
    s.identification,
    s.unit,
    r.ts,
    r.value,
    r.file_id,
    f.filename,
    f.download_time
FROM transducer.reading r
JOIN transducer.series s USING (series_id)
JOIN transducer.import_file f USING (file_id)
LEFT JOIN transducer.location l USING (location_id);

-- ---------------------------------------------------------------------------
-- v_reading_utc: local time plus the same instant in UTC
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW transducer.v_reading_utc AS
SELECT
    v.*,
    v.ts AT TIME ZONE v.timezone AS ts_utc
FROM transducer.v_reading v;

-- ---------------------------------------------------------------------------
-- v_import: audit trail for analysts and the API, including error details
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW transducer.v_import AS
SELECT
    f.file_id,
    f.filename,
    f.sha256,
    f.download_time,
    f.serial_number,
    f.location_id,
    l.name AS location,
    l.is_aquifer_test,
    f.start_time,
    f.stop_time,
    f.num_log,
    f.parsed_log,
    f.is_compensated,
    f.data_kind,
    f.status,
    f.error,
    f.imported_at
FROM transducer.import_file f
LEFT JOIN transducer.location l USING (location_id);

COMMIT;
