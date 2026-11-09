-- 04_grants.sql
--
-- Least-privilege grants.
--
--   transducer_ingest: read/write the base tables, use their sequences, no DDL.
--   transducer_read:   read the base tables and the curated views, no writes,
--                      no direct import_file access, no sequences.
--
-- Views are security-definer by default (owned by the bootstrap superuser), so
-- transducer_read can use v_import without SELECT on import_file.

\set ON_ERROR_STOP on

BEGIN;

-- No implicit access for PUBLIC. PostgreSQL grants no table/sequence privileges
-- to PUBLIC by default, but the schema and function defaults deserve an
-- explicit revoke so the intent is recorded and survives future changes.
REVOKE ALL ON SCHEMA transducer FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA transducer FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA transducer FROM PUBLIC;

GRANT USAGE ON SCHEMA transducer TO transducer_ingest, transducer_read;

-- Ingest: base tables only. Views are not writable and are not granted.
GRANT SELECT, INSERT, UPDATE ON
    transducer.logger,
    transducer.location,
    transducer.series,
    transducer.import_file,
    transducer.reading
TO transducer_ingest;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA transducer TO transducer_ingest;

-- Read: base tables (except import_file) plus the curated views.
GRANT SELECT ON
    transducer.logger,
    transducer.location,
    transducer.series,
    transducer.reading
TO transducer_read;

GRANT SELECT ON
    transducer.v_reading,
    transducer.v_reading_utc,
    transducer.v_import
TO transducer_read;

-- Future objects in this schema stay private until granted explicitly.
ALTER DEFAULT PRIVILEGES IN SCHEMA transducer
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA transducer
    REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA transducer
    REVOKE ALL ON FUNCTIONS FROM PUBLIC;

COMMIT;
