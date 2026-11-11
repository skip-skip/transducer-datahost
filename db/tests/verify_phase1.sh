#!/usr/bin/env bash
#
# verify_phase1.sh -- Phase 1 acceptance checks for the transducer database.
#
# Verifies the schema, roles, grants, views, and SCRAM authentication against
# an initialized database. Safe to run repeatedly: all test rows use reserved
# names and are removed on exit.
#
# Connection settings (admin):
#   POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
#   Legacy PGHOST, PGPORT, PGDATABASE, PGUSER, and PGPASSWORD are accepted as
#   fallbacks so existing environments keep working.
# Role settings:
#   INGEST_USER (default transducer_ingest), INGEST_PASSWORD
#   READ_USER   (default transducer_read),   READ_PASSWORD
# Optional:
#   PSQL_BIN            psql executable (default: psql)
#   ALLOW_NON_TEST_DB   set to 1 to allow a database name not ending in _test
#
# Example (Docker, database "transducer"):
#   ALLOW_NON_TEST_DB=1 POSTGRES_PASSWORD=... INGEST_PASSWORD=... READ_PASSWORD=... \
#       db/tests/verify_phase1.sh

set -euo pipefail

PSQL_BIN=${PSQL_BIN:-psql}
POSTGRES_HOST=${POSTGRES_HOST:-${PGHOST:-127.0.0.1}}
POSTGRES_PORT=${POSTGRES_PORT:-${PGPORT:-5432}}
POSTGRES_DB=${POSTGRES_DB:-${PGDATABASE:-transducer}}
POSTGRES_USER=${POSTGRES_USER:-${PGUSER:-transducer_admin}}
ADMIN_PASSWORD=${POSTGRES_PASSWORD:-${PGPASSWORD:-}}
UPLOAD_USER=${UPLOAD_USER:-transducer_upload}
UPLOAD_PASSWORD=${UPLOAD_PASSWORD:-}
INGEST_USER=${INGEST_USER:-transducer_ingest}
INGEST_PASSWORD=${INGEST_PASSWORD:-}
READ_USER=${READ_USER:-transducer_read}
READ_PASSWORD=${READ_PASSWORD:-}

PASSED=0
FAILED=0

pass() { PASSED=$((PASSED + 1)); printf 'ok   %s\n' "$1"; }
fail() { FAILED=$((FAILED + 1)); printf 'FAIL %s\n' "$1" >&2; }

admin_psql() {
    PGPASSWORD="$ADMIN_PASSWORD" "$PSQL_BIN" -X -q -w -v ON_ERROR_STOP=1 \
        -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"
}

role_psql() {
    local role=$1 password=$2
    shift 2
    PGPASSWORD="$password" "$PSQL_BIN" -X -q -w -v ON_ERROR_STOP=1 \
        -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$role" -d "$POSTGRES_DB" "$@"
}

query_admin() { admin_psql -Atc "$1"; }

check_bool() {
    local desc=$1 expected=$2 sql=$3 actual
    if ! actual=$(query_admin "$sql"); then
        fail "$desc (query failed)"
    elif [ "$actual" = "$expected" ]; then
        pass "$desc"
    else
        fail "$desc (expected $expected, got $actual)"
    fi
}

check_equal() {
    local desc=$1 expected=$2 actual=$3
    if [ "$actual" = "$expected" ]; then
        pass "$desc"
    else
        fail "$desc (expected '$expected', got '$actual')"
    fi
}

expect_success() {
    local desc=$1 role=$2 password=$3 sql=$4 output
    if output=$(role_psql "$role" "$password" -Atc "$sql" 2>&1); then
        pass "$desc"
    else
        fail "$desc ($output)"
    fi
}

expect_failure() {
    local desc=$1 pattern=$2 role=$3 password=$4 sql=$5 output status
    set +e
    output=$(role_psql "$role" "$password" -Atc "$sql" 2>&1)
    status=$?
    set -e
    if [ "$status" -eq 0 ]; then
        fail "$desc (unexpectedly succeeded)"
    elif [ -n "$pattern" ] && ! printf '%s\n' "$output" | grep -Eiq -- "$pattern"; then
        fail "$desc (unexpected error: $output)"
    else
        pass "$desc"
    fi
}

# ---------------------------------------------------------------------------
# Configuration and connectivity
# ---------------------------------------------------------------------------
if ! command -v "$PSQL_BIN" >/dev/null 2>&1; then
    printf 'psql not found: %s\n' "$PSQL_BIN" >&2
    exit 2
fi

if [ -z "$ADMIN_PASSWORD" ]; then
    printf 'POSTGRES_PASSWORD (admin password) must be set\n' >&2
    exit 2
fi
if [ -z "$UPLOAD_PASSWORD" ] || [ -z "$INGEST_PASSWORD" ] || [ -z "$READ_PASSWORD" ]; then
    printf 'UPLOAD_PASSWORD, INGEST_PASSWORD, and READ_PASSWORD must be set\n' >&2
    exit 2
fi

case "$POSTGRES_DB" in
    *_test|*_test_*) ;;
    *)
        if [ "${ALLOW_NON_TEST_DB:-0}" != "1" ]; then
            printf "refusing to run against non-test database '%s' (set ALLOW_NON_TEST_DB=1 to override)\n" \
                "$POSTGRES_DB" >&2
            exit 2
        fi
        ;;
esac

if ! admin_psql -Atc "SELECT 1" >/dev/null 2>&1; then
    printf 'cannot connect as %s to %s at %s:%s\n' \
        "$POSTGRES_USER" "$POSTGRES_DB" "$POSTGRES_HOST" "$POSTGRES_PORT" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Test data (reserved names, cleaned up on exit)
# ---------------------------------------------------------------------------
TEST_SERIAL='__verify_phase1__'
TEST_LOCATION='__verify_phase1__'
TEST_AT_LOCATION='__verify_phase1_AT'
TEST_FILENAME='__verify_phase1.xle'
TEST_SHA='0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
TEST_TS='2026-01-15 12:00:00'
TEST_TS_UTC='2026-01-15 17:00:00'

cleanup() {
    admin_psql -Atc "
        DELETE FROM transducer.reading
        WHERE file_id IN (
            SELECT file_id FROM transducer.import_file
            WHERE filename LIKE '__verify_phase1%'
        );
        DELETE FROM transducer.import_file WHERE filename LIKE '__verify_phase1%';
        DELETE FROM transducer.series WHERE serial_number = '$TEST_SERIAL';
        DELETE FROM transducer.location WHERE name IN ('$TEST_LOCATION', '$TEST_AT_LOCATION');
        DELETE FROM transducer.logger
        WHERE serial_number IN ('$TEST_SERIAL', '__verify_forbidden__', '__verify_read_write__');
    " >/dev/null 2>&1 || true
    admin_psql -Atc "
        DELETE FROM ingest.job
        WHERE sha256 = '$TEST_SHA' OR filename LIKE '__verify_phase1%';
        DELETE FROM ingest.worker_heartbeat
        WHERE worker_id LIKE '__verify_phase1%';
    " >/dev/null 2>&1 || true
}
trap cleanup EXIT

cleanup

printf 'Verifying %s on %s:%s as %s\n\n' "$POSTGRES_DB" "$POSTGRES_HOST" "$POSTGRES_PORT" "$POSTGRES_USER"

# ---------------------------------------------------------------------------
# Roles and password encryption
# ---------------------------------------------------------------------------
ROLE_ATTRS=$(query_admin "
    SELECT string_agg(
        rolname || ':' || rolsuper || ':' || rolcanlogin || ':' || rolcreatedb || ':' ||
        rolcreaterole || ':' || rolreplication || ':' || rolbypassrls,
        ',' ORDER BY rolname)
    FROM pg_roles
    WHERE rolname IN ('$UPLOAD_USER', '$INGEST_USER', '$READ_USER');
")

check_equal "upload role exists and is a plain login role" \
    "$UPLOAD_USER:false:true:false:false:false:false" \
    "$(printf '%s' "$ROLE_ATTRS" | tr ',' '\n' | grep "^$UPLOAD_USER:" || true)"
check_equal "ingest role exists and is a plain login role" \
    "$INGEST_USER:false:true:false:false:false:false" \
    "$(printf '%s' "$ROLE_ATTRS" | tr ',' '\n' | grep "^$INGEST_USER:" || true)"
check_equal "read role exists and is a plain login role" \
    "$READ_USER:false:true:false:false:false:false" \
    "$(printf '%s' "$ROLE_ATTRS" | tr ',' '\n' | grep "^$READ_USER:" || true)"

for role in "$UPLOAD_USER" "$INGEST_USER" "$READ_USER"; do
    PREFIX=$(query_admin "SELECT left(rolpassword, 14) FROM pg_authid WHERE rolname = '$role';")
    check_equal "$role password uses SCRAM-SHA-256" 'SCRAM-SHA-256$' "$PREFIX"
done

UPLOAD_SEARCH_PATH=$(role_psql "$UPLOAD_USER" "$UPLOAD_PASSWORD" -Atc "SHOW search_path;")
INGEST_SEARCH_PATH=$(role_psql "$INGEST_USER" "$INGEST_PASSWORD" -Atc "SHOW search_path;")
READ_SEARCH_PATH=$(role_psql "$READ_USER" "$READ_PASSWORD" -Atc "SHOW search_path;")
check_equal "upload search_path is pg_catalog, ingest" 'pg_catalog, ingest' "$UPLOAD_SEARCH_PATH"
check_equal "ingest search_path is pg_catalog, transducer, ingest" 'pg_catalog, transducer, ingest' "$INGEST_SEARCH_PATH"
check_equal "read search_path is pg_catalog, transducer" 'pg_catalog, transducer' "$READ_SEARCH_PATH"

check_bool "upload cannot create schemas" 'f' \
    "SELECT has_database_privilege('$UPLOAD_USER', current_database(), 'CREATE');"
check_bool "ingest cannot create schemas" 'f' \
    "SELECT has_database_privilege('$INGEST_USER', current_database(), 'CREATE');"
check_bool "read cannot create schemas" 'f' \
    "SELECT has_database_privilege('$READ_USER', current_database(), 'CREATE');"
check_bool "upload is not a member of the admin role" 'f' \
    "SELECT pg_has_role('$UPLOAD_USER', '$POSTGRES_USER', 'MEMBER');"
check_bool "ingest is not a member of the admin role" 'f' \
    "SELECT pg_has_role('$INGEST_USER', '$POSTGRES_USER', 'MEMBER');"
check_bool "read is not a member of the admin role" 'f' \
    "SELECT pg_has_role('$READ_USER', '$POSTGRES_USER', 'MEMBER');"

# ---------------------------------------------------------------------------
# Authentication over TCP with SCRAM
# ---------------------------------------------------------------------------
check_equal "upload authenticates over TCP" "$UPLOAD_USER" \
    "$(role_psql "$UPLOAD_USER" "$UPLOAD_PASSWORD" -Atc "SELECT current_user;")"
check_equal "ingest authenticates over TCP" "$INGEST_USER" \
    "$(role_psql "$INGEST_USER" "$INGEST_PASSWORD" -Atc "SELECT current_user;")"
check_equal "read authenticates over TCP" "$READ_USER" \
    "$(role_psql "$READ_USER" "$READ_PASSWORD" -Atc "SELECT current_user;")"

UPLOAD_TCP=$(role_psql "$UPLOAD_USER" "$UPLOAD_PASSWORD" -Atc "SELECT inet_server_addr() IS NOT NULL;")
INGEST_TCP=$(role_psql "$INGEST_USER" "$INGEST_PASSWORD" -Atc "SELECT inet_server_addr() IS NOT NULL;")
READ_TCP=$(role_psql "$READ_USER" "$READ_PASSWORD" -Atc "SELECT inet_server_addr() IS NOT NULL;")
check_equal "upload connection uses TCP" 't' "$UPLOAD_TCP"
check_equal "ingest connection uses TCP" 't' "$INGEST_TCP"
check_equal "read connection uses TCP" 't' "$READ_TCP"

expect_failure "upload rejects a wrong password" 'password authentication failed' \
    "$UPLOAD_USER" 'definitely-not-the-password' "SELECT 1;"
expect_failure "ingest rejects a wrong password" 'password authentication failed' \
    "$INGEST_USER" 'definitely-not-the-password' "SELECT 1;"
expect_failure "read rejects a wrong password" 'password authentication failed' \
    "$READ_USER" 'definitely-not-the-password' "SELECT 1;"

# ---------------------------------------------------------------------------
# Ingest can write base tables and use sequences
# ---------------------------------------------------------------------------
if ! role_psql "$INGEST_USER" "$INGEST_PASSWORD" -Atc "
        INSERT INTO transducer.logger (serial_number, instrument_type)
        VALUES ('$TEST_SERIAL', 'verify');
    " >/dev/null; then
    fail "ingest can insert a logger"
    printf '\ncannot continue without test data\n' >&2
    exit 1
fi
pass "ingest can insert a logger"

LOCATION_ID=$(role_psql "$INGEST_USER" "$INGEST_PASSWORD" -Atc "
    INSERT INTO transducer.location (name, base_location, is_aquifer_test, timezone)
    VALUES ('$TEST_LOCATION', '$TEST_LOCATION', false, 'America/New_York')
    RETURNING location_id;
")
pass "ingest can insert a location (sequence usable)"

SERIES_ID=$(role_psql "$INGEST_USER" "$INGEST_PASSWORD" -Atc "
    INSERT INTO transducer.series (serial_number, channel_no, identification, unit, data_kind)
    VALUES ('$TEST_SERIAL', 1, 'LEVEL', 'm', 'uncompensated')
    RETURNING series_id;
")
pass "ingest can insert a series (sequence usable)"

FILE_ID=$(role_psql "$INGEST_USER" "$INGEST_PASSWORD" -Atc "
    INSERT INTO transducer.import_file
        (filename, sha256, download_time, serial_number, location_id,
         start_time, stop_time, num_log, parsed_log, is_compensated, data_kind, status)
    VALUES
        ('$TEST_FILENAME', '$TEST_SHA', '2026-01-15 12:00:00', '$TEST_SERIAL', $LOCATION_ID,
         '2026-01-15 12:00:00', '2026-01-15 12:00:00', 1, 1, false, 'uncompensated', 'imported')
    RETURNING file_id;
")
pass "ingest can insert an import_file row"

expect_success "ingest can insert a reading" "$INGEST_USER" "$INGEST_PASSWORD" "
    INSERT INTO transducer.reading (series_id, ts, value, file_id)
    VALUES ($SERIES_ID, '$TEST_TS', 1.25, $FILE_ID);
"

expect_success "ingest can update a reading" "$INGEST_USER" "$INGEST_PASSWORD" "
    UPDATE transducer.reading
    SET value = 2.5
    WHERE series_id = $SERIES_ID AND ts = '$TEST_TS';
"

expect_success "ingest can update an import_file row" "$INGEST_USER" "$INGEST_PASSWORD" "
    UPDATE transducer.import_file
    SET status = 'warning', error = 'verify update'
    WHERE file_id = $FILE_ID;
"

check_equal "ingest update is visible" '2.5' \
    "$(query_admin "SELECT value FROM transducer.reading WHERE series_id = $SERIES_ID AND ts = '$TEST_TS';")"
check_equal "ingest import status update is visible" 'warning|verify update' \
    "$(query_admin "SELECT status || '|' || error FROM transducer.import_file WHERE file_id = $FILE_ID;")"

# ---------------------------------------------------------------------------
# Ingest cannot perform DDL, delete, or truncate
# ---------------------------------------------------------------------------
check_bool "ingest has no CREATE on the schema" 'f' \
    "SELECT has_schema_privilege('$INGEST_USER', 'transducer', 'CREATE');"
check_bool "ingest has no DELETE on reading" 'f' \
    "SELECT has_table_privilege('$INGEST_USER', 'transducer.reading', 'DELETE');"
check_bool "ingest has no TRUNCATE on reading" 'f' \
    "SELECT has_table_privilege('$INGEST_USER', 'transducer.reading', 'TRUNCATE');"
check_bool "ingest owns no objects in the schema" '0' \
    "SELECT count(*) FROM pg_class c
     JOIN pg_namespace n ON n.oid = c.relnamespace
     JOIN pg_roles r ON r.oid = c.relowner
     WHERE n.nspname = 'transducer' AND r.rolname = '$INGEST_USER';"

expect_failure "ingest cannot create a table" 'permission denied' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "BEGIN; CREATE TABLE transducer.__verify_forbidden (id integer); ROLLBACK;"
expect_failure "ingest cannot create a schema" 'permission denied' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "BEGIN; CREATE SCHEMA __verify_forbidden; ROLLBACK;"
expect_failure "ingest cannot alter a table" 'must be owner' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "BEGIN; ALTER TABLE transducer.reading ADD COLUMN __verify_forbidden integer; ROLLBACK;"
expect_failure "ingest cannot drop a table" 'must be owner' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "BEGIN; DROP TABLE transducer.reading; ROLLBACK;"
expect_failure "ingest cannot truncate a table" 'permission denied' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "BEGIN; TRUNCATE transducer.reading; ROLLBACK;"
expect_failure "ingest cannot delete rows" 'permission denied' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "BEGIN; DELETE FROM transducer.reading; ROLLBACK;"

# ---------------------------------------------------------------------------
# Constraints reject bad data
# ---------------------------------------------------------------------------
expect_failure "duplicate (series_id, ts) is rejected" 'duplicate key' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "INSERT INTO transducer.reading (series_id, ts, value, file_id)
     VALUES ($SERIES_ID, '$TEST_TS', 9.9, $FILE_ID);"

expect_failure "duplicate sha256 is rejected" 'duplicate key' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "INSERT INTO transducer.import_file (filename, sha256, download_time, status)
     VALUES ('__verify_phase1_dup.xle', '$TEST_SHA', '2026-01-15 12:00:00', 'imported');"

expect_failure "invalid sha256 is rejected" 'check constraint' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "INSERT INTO transducer.import_file (filename, sha256, download_time, status)
     VALUES ('__verify_phase1_bad_sha.xle', 'not-a-sha', '2026-01-15 12:00:00', 'imported');"

expect_failure "invalid status is rejected" 'check constraint' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "INSERT INTO transducer.import_file (filename, sha256, download_time, status)
     VALUES ('__verify_phase1_bad_status.xle',
             'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
             '2026-01-15 12:00:00', 'bogus');"

expect_failure "invalid data_kind is rejected" 'check constraint' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "INSERT INTO transducer.import_file (filename, sha256, download_time, data_kind, status)
     VALUES ('__verify_phase1_bad_kind.xle',
             'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
             '2026-01-15 12:00:00', 'bogus', 'imported');"

expect_failure "skipped_compensated with parsed rows is rejected" 'check constraint' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "INSERT INTO transducer.import_file
         (filename, sha256, download_time, parsed_log, is_compensated, data_kind, status)
     VALUES ('__verify_phase1_comp.xle',
             'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
             '2026-01-15 12:00:00', 1, true, 'compensated', 'skipped_compensated');"

expect_failure "blank logger serial is rejected" 'check constraint' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "INSERT INTO transducer.logger (serial_number) VALUES ('');"

expect_failure "aquifer-test flag must match the name" 'check constraint' \
    "$INGEST_USER" "$INGEST_PASSWORD" \
    "INSERT INTO transducer.location (name, base_location, is_aquifer_test)
     VALUES ('$TEST_AT_LOCATION', '__verify_phase1', false);"

# ---------------------------------------------------------------------------
# Ingest queue: the upload role can enqueue only; the worker can process
# ---------------------------------------------------------------------------
check_bool "upload has USAGE on ingest" 't' \
    "SELECT has_schema_privilege('$UPLOAD_USER', 'ingest', 'USAGE');"
check_bool "upload has no USAGE on transducer" 'f' \
    "SELECT has_schema_privilege('$UPLOAD_USER', 'transducer', 'USAGE');"
check_bool "read has no USAGE on ingest" 'f' \
    "SELECT has_schema_privilege('$READ_USER', 'ingest', 'USAGE');"
check_bool "ingest has USAGE on ingest" 't' \
    "SELECT has_schema_privilege('$INGEST_USER', 'ingest', 'USAGE');"

check_bool "upload can insert ingest.job" 't' \
    "SELECT has_table_privilege('$UPLOAD_USER', 'ingest.job', 'INSERT');"
check_bool "upload can select ingest.job" 't' \
    "SELECT has_table_privilege('$UPLOAD_USER', 'ingest.job', 'SELECT');"
check_bool "upload can update ingest.job" 't' \
    "SELECT has_table_privilege('$UPLOAD_USER', 'ingest.job', 'UPDATE');"
check_bool "upload cannot delete ingest.job" 'f' \
    "SELECT has_table_privilege('$UPLOAD_USER', 'ingest.job', 'DELETE');"
check_bool "upload has no SELECT on import_file" 'f' \
    "SELECT has_table_privilege('$UPLOAD_USER', 'transducer.import_file', 'SELECT');"
check_bool "upload has no SELECT on reading" 'f' \
    "SELECT has_table_privilege('$UPLOAD_USER', 'transducer.reading', 'SELECT');"

# Schedule the test job in the future so a live ingest worker never claims it.
# claim_job() only picks up rows whose next_attempt_at is null or already due;
# the claim check below updates by job_id and ignores that column. Without
# this, the worker consumes the row, fails it as "file missing from queued
# spool", and the claim check sees no queued job.
if ! JOB_ID=$(role_psql "$UPLOAD_USER" "$UPLOAD_PASSWORD" -Atc "
    INSERT INTO ingest.job
        (sha256, filename, byte_size, source_client, next_attempt_at)
    VALUES ('$TEST_SHA', '__verify_phase1_job.xle', 1234, 'verify',
            now() + interval '1 day')
    RETURNING job_id;
" 2>&1); then
    fail "upload can enqueue a job ($JOB_ID)"
    exit 1
fi
if [ -z "$JOB_ID" ]; then
    fail "upload can enqueue a job (no job_id returned)"
    exit 1
fi
pass "upload can enqueue a job (sequence usable)"

check_equal "upload sees its queued job" 'queued|__verify_phase1_job.xle' \
    "$(role_psql "$UPLOAD_USER" "$UPLOAD_PASSWORD" -Atc "
        SELECT status || '|' || filename FROM ingest.job WHERE job_id = $JOB_ID;")"

expect_failure "upload cannot read import_file directly" 'permission denied' \
    "$UPLOAD_USER" "$UPLOAD_PASSWORD" "SELECT count(*) FROM transducer.import_file;"
expect_failure "upload cannot delete jobs" 'permission denied' \
    "$UPLOAD_USER" "$UPLOAD_PASSWORD" \
    "DELETE FROM ingest.job WHERE job_id = $JOB_ID;"
expect_failure "upload cannot create a table in ingest" 'permission denied' \
    "$UPLOAD_USER" "$UPLOAD_PASSWORD" \
    "BEGIN; CREATE TABLE ingest.__verify_forbidden (id integer); ROLLBACK;"

check_equal "file_known finds an imported hash" 't' \
    "$(role_psql "$UPLOAD_USER" "$UPLOAD_PASSWORD" -Atc "
        SELECT ingest.file_known('$TEST_SHA');")"
check_equal "file_known rejects an unknown hash" 'f' \
    "$(role_psql "$UPLOAD_USER" "$UPLOAD_PASSWORD" -Atc "
        SELECT ingest.file_known(
            'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb');")"

CLAIMED=$(role_psql "$INGEST_USER" "$INGEST_PASSWORD" -Atc "
    UPDATE ingest.job
    SET status = 'processing', locked_at = now(), attempt_count = attempt_count + 1
    WHERE job_id = $JOB_ID AND status = 'queued'
    RETURNING status;
")
check_equal "ingest can claim a queued job" 'processing' "$CLAIMED"

expect_success "ingest can finish a job" "$INGEST_USER" "$INGEST_PASSWORD" "
    UPDATE ingest.job
    SET status = 'done', import_status = 'imported', file_id = $FILE_ID,
        locked_at = NULL, finished_at = now()
    WHERE job_id = $JOB_ID;
"

expect_success "ingest can write a worker heartbeat" "$INGEST_USER" "$INGEST_PASSWORD" "
    INSERT INTO ingest.worker_heartbeat (worker_id) VALUES ('__verify_phase1_worker')
    ON CONFLICT (worker_id) DO UPDATE SET last_seen = now();
"

expect_failure "read cannot select ingest.job" 'permission denied' \
    "$READ_USER" "$READ_PASSWORD" "SELECT count(*) FROM ingest.job;"

# ---------------------------------------------------------------------------
# Read role: allowed reads
# ---------------------------------------------------------------------------
for rel in logger location series reading v_reading v_reading_utc v_import; do
    expect_success "read can select $rel" "$READ_USER" "$READ_PASSWORD" \
        "SELECT count(*) FROM transducer.$rel;"
done

check_equal "v_reading exposes the test reading" '2.5' \
    "$(role_psql "$READ_USER" "$READ_PASSWORD" -Atc "
        SELECT value FROM transducer.v_reading
        WHERE series_id = $SERIES_ID AND ts = '$TEST_TS';")"

check_equal "v_import exposes status and error text" 'warning|verify update' \
    "$(role_psql "$READ_USER" "$READ_PASSWORD" -Atc "
        SELECT status || '|' || error FROM transducer.v_import WHERE file_id = $FILE_ID;")"

check_equal "v_reading_utc converts local time to UTC" "$TEST_TS_UTC" \
    "$(role_psql "$READ_USER" "$READ_PASSWORD" -Atc "
        SELECT to_char(ts_utc AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
        FROM transducer.v_reading_utc
        WHERE series_id = $SERIES_ID AND ts = '$TEST_TS';")"

check_equal "v_reading includes timezone" '1' \
    "$(role_psql "$READ_USER" "$READ_PASSWORD" -Atc "
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema = 'transducer' AND table_name = 'v_reading'
          AND column_name = 'timezone';")"

check_equal "v_reading_utc includes ts and ts_utc" '2' \
    "$(role_psql "$READ_USER" "$READ_PASSWORD" -Atc "
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema = 'transducer' AND table_name = 'v_reading_utc'
          AND column_name IN ('ts', 'ts_utc');")"

check_equal "v_import includes error" '1' \
    "$(role_psql "$READ_USER" "$READ_PASSWORD" -Atc "
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema = 'transducer' AND table_name = 'v_import'
          AND column_name = 'error';")"

# ---------------------------------------------------------------------------
# Read role: denied reads, writes, and DDL
# ---------------------------------------------------------------------------
check_bool "read has USAGE on the schema" 't' \
    "SELECT has_schema_privilege('$READ_USER', 'transducer', 'USAGE');"
check_bool "read has no CREATE on the schema" 'f' \
    "SELECT has_schema_privilege('$READ_USER', 'transducer', 'CREATE');"
check_bool "read has SELECT on reading" 't' \
    "SELECT has_table_privilege('$READ_USER', 'transducer.reading', 'SELECT');"
check_bool "read has no SELECT on import_file" 'f' \
    "SELECT has_table_privilege('$READ_USER', 'transducer.import_file', 'SELECT');"
check_bool "read has no INSERT on reading" 'f' \
    "SELECT has_table_privilege('$READ_USER', 'transducer.reading', 'INSERT');"
check_bool "read has no UPDATE on logger" 'f' \
    "SELECT has_table_privilege('$READ_USER', 'transducer.logger', 'UPDATE');"
check_bool "read has no DELETE on reading" 'f' \
    "SELECT has_table_privilege('$READ_USER', 'transducer.reading', 'DELETE');"
check_bool "read has no TRUNCATE on reading" 'f' \
    "SELECT has_table_privilege('$READ_USER', 'transducer.reading', 'TRUNCATE');"
check_bool "read has no sequence privileges" 'f' \
    "SELECT has_sequence_privilege(
        '$READ_USER',
        (pg_get_serial_sequence('transducer.series', 'series_id'))::regclass,
        'USAGE');"
check_bool "read owns no objects in the schema" '0' \
    "SELECT count(*) FROM pg_class c
     JOIN pg_namespace n ON n.oid = c.relnamespace
     JOIN pg_roles r ON r.oid = c.relowner
     WHERE n.nspname = 'transducer' AND r.rolname = '$READ_USER';"

expect_failure "read cannot select import_file directly" 'permission denied' \
    "$READ_USER" "$READ_PASSWORD" "SELECT count(*) FROM transducer.import_file;"

expect_failure "read cannot insert a logger" 'permission denied' \
    "$READ_USER" "$READ_PASSWORD" \
    "BEGIN; INSERT INTO transducer.logger (serial_number)
     VALUES ('__verify_read_write__'); ROLLBACK;"
expect_failure "read cannot update a logger" 'permission denied' \
    "$READ_USER" "$READ_PASSWORD" \
    "BEGIN; UPDATE transducer.logger SET last_seen = now()
     WHERE serial_number = '$TEST_SERIAL'; ROLLBACK;"
expect_failure "read cannot delete a reading" 'permission denied' \
    "$READ_USER" "$READ_PASSWORD" \
    "BEGIN; DELETE FROM transducer.reading
     WHERE series_id = $SERIES_ID; ROLLBACK;"
expect_failure "read cannot truncate a table" 'permission denied' \
    "$READ_USER" "$READ_PASSWORD" \
    "BEGIN; TRUNCATE transducer.reading; ROLLBACK;"
expect_failure "read cannot create a table" 'permission denied' \
    "$READ_USER" "$READ_PASSWORD" \
    "BEGIN; CREATE TABLE transducer.__verify_forbidden (id integer); ROLLBACK;"

# ---------------------------------------------------------------------------
# PUBLIC has no privileges
# ---------------------------------------------------------------------------
check_bool "PUBLIC has no schema privileges" '0' \
    "SELECT count(*) FROM aclexplode(
        (SELECT nspacl FROM pg_namespace WHERE nspname = 'transducer')) a
     WHERE a.grantee = 0;"
check_bool "PUBLIC has no table or sequence privileges" '0' \
    "SELECT count(*) FROM pg_class c, aclexplode(c.relacl) a
     WHERE c.relnamespace = 'transducer'::regnamespace AND a.grantee = 0;"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf '\n%d passed, %d failed\n' "$PASSED" "$FAILED"
[ "$FAILED" -eq 0 ]
