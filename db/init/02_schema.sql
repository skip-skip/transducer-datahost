-- 02_schema.sql
--
-- The transducer schema: five tables, constraints, and indexes.
--
-- Timestamps in `reading`, `import_file.download_time`, `start_time`, and
-- `stop_time` are naive logger-local time (see ADR-005). The interpretation
-- zone lives in `location.timezone`.
--
-- The whole file runs in one transaction so a failure cannot leave a
-- partially-created schema behind on a fresh volume.

\set ON_ERROR_STOP on

BEGIN;

CREATE SCHEMA IF NOT EXISTS transducer AUTHORIZATION CURRENT_USER;

-- ---------------------------------------------------------------------------
-- logger: one row per physical instrument
-- ---------------------------------------------------------------------------
CREATE TABLE transducer.logger (
    serial_number   text PRIMARY KEY,
    instrument_type text,
    model_number    text,
    firmware        text,
    project_id      text,
    first_seen      timestamptz NOT NULL DEFAULT now(),
    last_seen       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT logger_serial_not_blank CHECK (btrim(serial_number) <> '')
);

-- ---------------------------------------------------------------------------
-- location: one row per monitoring point, including aquifer-test variants
-- ---------------------------------------------------------------------------
CREATE TABLE transducer.location (
    location_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            text NOT NULL UNIQUE,
    base_location   text NOT NULL,
    is_aquifer_test boolean NOT NULL,
    latitude        numeric(9,6),
    longitude       numeric(9,6),
    timezone        text NOT NULL DEFAULT 'America/New_York',
    CONSTRAINT location_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT location_base_not_blank CHECK (btrim(base_location) <> ''),
    CONSTRAINT location_base_matches_name CHECK (
        base_location = CASE
            WHEN right(name, 3) = '_AT' THEN left(name, length(name) - 3)
            ELSE name
        END
    ),
    CONSTRAINT location_aquifer_flag_matches_name CHECK (
        is_aquifer_test = (right(name, 3) = '_AT')
    ),
    CONSTRAINT location_latitude_range CHECK (
        latitude IS NULL OR (latitude >= -90 AND latitude <= 90)
    ),
    CONSTRAINT location_longitude_range CHECK (
        longitude IS NULL OR (longitude >= -180 AND longitude <= 180)
    )
);

-- ---------------------------------------------------------------------------
-- series: one channel of one logger in one unit for one data kind
-- ---------------------------------------------------------------------------
CREATE TABLE transducer.series (
    series_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    serial_number  text NOT NULL
                   REFERENCES transducer.logger (serial_number)
                   ON UPDATE CASCADE ON DELETE RESTRICT,
    channel_no     smallint NOT NULL,
    identification text NOT NULL,
    unit           text NOT NULL,
    data_kind      text NOT NULL DEFAULT 'uncompensated',
    CONSTRAINT series_channel_positive CHECK (channel_no > 0),
    CONSTRAINT series_identification_not_blank CHECK (btrim(identification) <> ''),
    CONSTRAINT series_unit_not_blank CHECK (btrim(unit) <> ''),
    CONSTRAINT series_data_kind_valid CHECK (data_kind IN ('uncompensated', 'compensated')),
    CONSTRAINT series_identity_unique UNIQUE (serial_number, channel_no, identification, unit, data_kind)
);

-- ---------------------------------------------------------------------------
-- import_file: ingest audit trail and provenance anchor for readings
-- ---------------------------------------------------------------------------
CREATE TABLE transducer.import_file (
    file_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename       text NOT NULL,
    sha256         char(64) NOT NULL UNIQUE,
    download_time  timestamp(3) NOT NULL,
    serial_number  text
                   REFERENCES transducer.logger (serial_number)
                   ON UPDATE CASCADE ON DELETE RESTRICT,
    location_id    integer
                   REFERENCES transducer.location (location_id)
                   ON UPDATE CASCADE ON DELETE RESTRICT,
    start_time     timestamp(3),
    stop_time      timestamp(3),
    num_log        integer,
    parsed_log     integer NOT NULL DEFAULT 0,
    is_compensated boolean NOT NULL DEFAULT false,
    data_kind      text NOT NULL DEFAULT 'uncompensated',
    status         text NOT NULL,
    error          text,
    imported_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT import_file_sha256_format CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT import_file_num_log_nonnegative CHECK (num_log IS NULL OR num_log >= 0),
    CONSTRAINT import_file_parsed_log_nonnegative CHECK (parsed_log >= 0),
    CONSTRAINT import_file_data_kind_valid CHECK (data_kind IN ('uncompensated', 'compensated')),
    CONSTRAINT import_file_status_valid CHECK (
        status IN (
            'imported',
            'warning',
            'empty',
            'error',
            'skipped_duplicate',
            'skipped_compensated'
        )
    ),
    CONSTRAINT import_file_compensation_matches_kind CHECK (
        is_compensated = (data_kind = 'compensated')
    ),
    CONSTRAINT import_file_skipped_compensated_has_no_rows CHECK (
        status <> 'skipped_compensated' OR parsed_log = 0
    )
);

-- ---------------------------------------------------------------------------
-- reading: the time-series fact table
-- ---------------------------------------------------------------------------
CREATE TABLE transducer.reading (
    series_id bigint NOT NULL
              REFERENCES transducer.series (series_id)
              ON UPDATE CASCADE ON DELETE RESTRICT,
    ts        timestamp(3) NOT NULL,
    value     double precision NOT NULL,
    file_id   bigint NOT NULL
              REFERENCES transducer.import_file (file_id)
              ON UPDATE CASCADE ON DELETE RESTRICT,
    PRIMARY KEY (series_id, ts)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
-- The reading primary key covers per-series range scans. BRIN keeps the
-- broad time-range index tiny at this volume; the FK on file_id needs its
-- own index for provenance lookups and referential checks.
CREATE INDEX reading_ts_brin ON transducer.reading USING brin (ts);
CREATE INDEX reading_file_id_idx ON transducer.reading (file_id);

-- Audit/priority lookups per logger. Also serves the serial_number FK.
CREATE INDEX import_file_serial_download_idx
    ON transducer.import_file (serial_number, download_time);
CREATE INDEX import_file_location_idx ON transducer.import_file (location_id);

COMMIT;
