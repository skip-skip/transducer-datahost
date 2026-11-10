-- 01_roles.sql
--
-- Create the application roles from UPLOAD_PASSWORD, INGEST_PASSWORD, and
-- READ_PASSWORD.
--
-- This script runs first, before the schema exists, so a missing password
-- fails initialization immediately instead of leaving a half-configured
-- database. It runs as the bootstrap superuser (POSTGRES_USER) and uses
-- psql's \getenv so no shell script or executable bit is required.
--
-- The passwords are interpolated with :'variable', which lets psql quote and
-- escape them safely. Passwords are never written to the SQL files.

\set ON_ERROR_STOP on

\getenv upload_password UPLOAD_PASSWORD
\getenv ingest_password INGEST_PASSWORD
\getenv read_password READ_PASSWORD

-- Treat an unset environment variable as empty so the check below produces a
-- clear error rather than an undefined-variable syntax error.
\if :{?upload_password}
\else
  \set upload_password ''
\endif
\if :{?ingest_password}
\else
  \set ingest_password ''
\endif
\if :{?read_password}
\else
  \set read_password ''
\endif

SELECT (
    :'upload_password' = ''
    OR :'ingest_password' = ''
    OR :'read_password' = ''
) AS missing_password \gset

\if :missing_password
  DO $$
  BEGIN
      RAISE EXCEPTION
          'UPLOAD_PASSWORD, INGEST_PASSWORD, and READ_PASSWORD must all be set and non-empty before database initialization';
  END
  $$;
\endif

BEGIN;

SET password_encryption = 'scram-sha-256';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'transducer_upload') THEN
        CREATE ROLE transducer_upload;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'transducer_ingest') THEN
        CREATE ROLE transducer_ingest;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'transducer_read') THEN
        CREATE ROLE transducer_read;
    END IF;
END
$$;

ALTER ROLE transducer_upload
    WITH LOGIN
         NOSUPERUSER
         NOCREATEDB
         NOCREATEROLE
         NOREPLICATION
         NOBYPASSRLS
         PASSWORD :'upload_password';

ALTER ROLE transducer_ingest
    WITH LOGIN
         NOSUPERUSER
         NOCREATEDB
         NOCREATEROLE
         NOREPLICATION
         NOBYPASSRLS
         PASSWORD :'ingest_password';

ALTER ROLE transducer_read
    WITH LOGIN
         NOSUPERUSER
         NOCREATEDB
         NOCREATEROLE
         NOREPLICATION
         NOBYPASSRLS
         PASSWORD :'read_password';

-- Resolve unqualified names in the application schemas, never in public.
-- The upload role only sees the ingest queue; the worker sees domain tables
-- and the queue; readers stay in the transducer schema.
ALTER ROLE transducer_upload SET search_path = pg_catalog, ingest;
ALTER ROLE transducer_ingest SET search_path = pg_catalog, transducer, ingest;
ALTER ROLE transducer_read SET search_path = pg_catalog, transducer;

COMMIT;
