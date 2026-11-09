"""Load validated .xle files into PostgreSQL.

The loader reuses the Phase 2 parser and validator, then writes in a fixed
order: logger, location, audit row, series, and readings. Each file is its own
transaction so a bad file cannot roll back the rest of the run. Readings are
copied into a temporary staging table and upserted with a newest-download-wins
conflict rule.

A dry run executes the same path inside one transaction and rolls it back at
the end, so the reported new/overlapping counts are exact without leaving any
persistent changes behind.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from .parse_xle import ParsedFile, parse_xle
from .validate import Status, validate_file

if TYPE_CHECKING:  # pragma: no cover - imported only for type checking
    from psycopg import Connection

DEFAULT_SITE_TIMEZONE = "America/New_York"
COORDINATE_SCALE = Decimal("0.000001")


class _DryRunRollback(Exception):
    """Sentinel used to roll back the single dry-run transaction."""


@dataclass(frozen=True)
class PreparedFile:
    path: Path
    parsed: ParsedFile
    status: Status
    warnings: tuple[str, ...]
    error: str | None
    download_time: datetime


@dataclass
class FileLoadResult:
    filename: str
    status: Status
    log_count: int
    reading_count: int
    new_rows: int
    overlapping_rows: int
    duplicate_file: bool
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class LoadSummary:
    dry_run: bool
    files_total: int
    compensated_files: int
    uncompensated_files: int
    imported_files: int
    warning_files: int
    empty_files: int
    error_files: int
    duplicate_files: int
    redundant_files: int
    new_readings: int
    overlapping_readings: int
    unique_samples: int
    results: list[FileLoadResult] = field(default_factory=list)


def parse_since(value: str | None) -> datetime | None:
    """Parse a ``--since`` value into a naive local datetime.

    Accepts ``YYYY-MM-DD``, ``YYYY/MM/DD``, the same forms with a time, and
    ISO-8601. ``--since`` filters on the header download time.
    """

    if value is None or not value.strip():
        return None

    text = value.strip()
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid --since value {value!r}") from exc


def derive_location(name: str) -> tuple[str, bool]:
    """Return ``(base_location, is_aquifer_test)`` for a header location."""

    trimmed = name.strip()
    if trimmed.endswith("_AT"):
        return trimmed[:-3], True
    return trimmed, False


def prepare_files(
    paths: Iterable[Path],
    *,
    since: datetime | None = None,
) -> list[PreparedFile]:
    """Parse, validate, filter, and order files for loading.

    Ordering matches the report: ascending download time, then filename. A file
    with no header download time uses its modification time.
    """

    prepared: list[PreparedFile] = []
    for path in paths:
        parsed = parse_xle(path)
        status, warnings, error = validate_file(parsed)
        download_time = _effective_download_time(path, parsed)
        if since is not None and download_time < since:
            continue
        prepared.append(
            PreparedFile(
                path=path,
                parsed=parsed,
                status=status,
                warnings=tuple(warnings),
                error=error,
                download_time=download_time,
            )
        )

    prepared.sort(
        key=lambda item: (item.download_time, item.parsed.metadata.filename)
    )
    return prepared


def load_export(
    conn: Connection,
    prepared: Sequence[PreparedFile],
    *,
    dry_run: bool = False,
    site_timezone: str = DEFAULT_SITE_TIMEZONE,
) -> LoadSummary:
    """Load prepared files and return per-file and aggregate results."""

    results: list[FileLoadResult] = []

    if dry_run:
        try:
            with conn.transaction():
                for item in prepared:
                    try:
                        with conn.transaction():
                            results.append(_load_file(conn, item, site_timezone))
                    except Exception as exc:  # noqa: BLE001 - report and continue
                        results.append(_failure_result(item, exc))
                raise _DryRunRollback
        except _DryRunRollback:
            pass
    else:
        for item in prepared:
            try:
                with conn.transaction():
                    results.append(_load_file(conn, item, site_timezone))
            except Exception as exc:  # noqa: BLE001 - report and continue
                results.append(_failure_result(item, exc))
                _record_failure(conn, item, exc, site_timezone)

    return _summarize(results, prepared, dry_run)


def _load_file(
    conn: Connection,
    item: PreparedFile,
    site_timezone: str,
) -> FileLoadResult:
    metadata = item.parsed.metadata
    parsed = item.parsed

    with conn.cursor() as cur:
        cur.execute(
            "SELECT file_id FROM transducer.import_file WHERE sha256 = %s",
            (metadata.sha256,),
        )
        if cur.fetchone() is not None:
            return FileLoadResult(
                filename=metadata.filename,
                status=Status.SKIPPED_DUPLICATE,
                log_count=parsed.log_count,
                reading_count=len(parsed.readings),
                new_rows=0,
                overlapping_rows=0,
                duplicate_file=True,
                warnings=list(item.warnings),
            )

        if metadata.serial_number:
            _upsert_logger(cur, metadata)
        location_id = (
            _upsert_location(cur, metadata, site_timezone)
            if metadata.location
            else None
        )

        file_id = _insert_import_file(cur, item, location_id)

        if item.status in (
            Status.SKIPPED_COMPENSATED,
            Status.ERROR,
            Status.EMPTY,
        ):
            return FileLoadResult(
                filename=metadata.filename,
                status=item.status,
                log_count=parsed.log_count,
                reading_count=len(parsed.readings),
                new_rows=0,
                overlapping_rows=0,
                duplicate_file=False,
                warnings=list(item.warnings),
                error=item.error,
            )

        series_ids = _upsert_series(cur, metadata)
        rows = [
            (series_ids[reading.channel_no], reading.ts, reading.value)
            for reading in parsed.readings
            if reading.channel_no in series_ids
        ]

        new_rows = 0
        overlapping_rows = 0
        if rows:
            _stage_readings(cur, rows)
            cur.execute(
                "SELECT count(*) FROM staging_reading s "
                "JOIN transducer.reading r USING (series_id, ts)"
            )
            overlapping_rows = _fetch_int(cur, "overlapping reading count")
            cur.execute(
                "INSERT INTO transducer.reading (series_id, ts, value, file_id) "
                "SELECT series_id, ts, value, %s FROM staging_reading "
                "ON CONFLICT (series_id, ts) DO UPDATE "
                "SET value = EXCLUDED.value, file_id = EXCLUDED.file_id "
                "WHERE EXCLUDED.file_id > transducer.reading.file_id",
                (file_id,),
            )
            new_rows = len(rows) - overlapping_rows

        _update_import_file(
            cur,
            file_id,
            parsed_log=parsed.log_count,
            error=_audit_error(item),
        )

    return FileLoadResult(
        filename=metadata.filename,
        status=item.status,
        log_count=parsed.log_count,
        reading_count=len(parsed.readings),
        new_rows=new_rows,
        overlapping_rows=overlapping_rows,
        duplicate_file=False,
        warnings=list(item.warnings),
        error=item.error,
    )


def _upsert_logger(cur, metadata) -> None:
    cur.execute(
        """
        INSERT INTO transducer.logger
            (serial_number, instrument_type, model_number, firmware,
             project_id, last_seen)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (serial_number) DO UPDATE SET
            instrument_type = COALESCE(
                EXCLUDED.instrument_type, transducer.logger.instrument_type
            ),
            model_number = COALESCE(
                EXCLUDED.model_number, transducer.logger.model_number
            ),
            firmware = COALESCE(EXCLUDED.firmware, transducer.logger.firmware),
            project_id = COALESCE(
                EXCLUDED.project_id, transducer.logger.project_id
            ),
            last_seen = now()
        """,
        (
            metadata.serial_number,
            metadata.instrument_type,
            metadata.model_number,
            metadata.firmware,
            metadata.project_id,
        ),
    )


def _upsert_location(cur, metadata, site_timezone: str) -> int:
    name = metadata.location.strip()
    base_location, is_aquifer_test = derive_location(name)
    if not base_location:
        raise ValueError(f"invalid location name {name!r}")

    cur.execute(
        """
        INSERT INTO transducer.location
            (name, base_location, is_aquifer_test, latitude, longitude, timezone)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            latitude = COALESCE(EXCLUDED.latitude, transducer.location.latitude),
            longitude = COALESCE(
                EXCLUDED.longitude, transducer.location.longitude
            )
        RETURNING location_id
        """,
        (
            name,
            base_location,
            is_aquifer_test,
            _coordinate(metadata.latitude),
            _coordinate(metadata.longitude),
            site_timezone,
        ),
    )
    return _fetch_int(cur, f"location id for {name!r}")


def _upsert_series(cur, metadata) -> dict[int, int]:
    if not metadata.serial_number:
        raise ValueError("cannot create series without a serial number")

    series_ids: dict[int, int] = {}
    for channel in metadata.channels:
        cur.execute(
            """
            INSERT INTO transducer.series
                (serial_number, channel_no, identification, unit, data_kind)
            VALUES (%s, %s, %s, %s, 'uncompensated')
            ON CONFLICT
                (serial_number, channel_no, identification, unit, data_kind)
            DO UPDATE SET identification = EXCLUDED.identification
            RETURNING series_id
            """,
            (
                metadata.serial_number,
                channel.channel_no,
                channel.identification,
                channel.unit,
            ),
        )
        series_ids[channel.channel_no] = _fetch_int(
            cur, f"series id for channel {channel.channel_no}"
        )
    return series_ids


def _insert_import_file(cur, item: PreparedFile, location_id: int | None) -> int:
    metadata = item.parsed.metadata
    cur.execute(
        """
        INSERT INTO transducer.import_file
            (filename, sha256, download_time, serial_number, location_id,
             start_time, stop_time, num_log, parsed_log, is_compensated,
             data_kind, status, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s)
        ON CONFLICT (sha256) DO NOTHING
        RETURNING file_id
        """,
        (
            metadata.filename,
            metadata.sha256,
            item.download_time,
            metadata.serial_number,
            location_id,
            metadata.start_time,
            metadata.stop_time,
            metadata.num_log,
            metadata.is_compensated,
            "compensated" if metadata.is_compensated else "uncompensated",
            item.status.value,
            _audit_error(item),
        ),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"duplicate sha256 for {metadata.filename}")
    return row[0]


def _update_import_file(cur, file_id: int, *, parsed_log: int, error: str | None) -> None:
    cur.execute(
        "UPDATE transducer.import_file "
        "SET parsed_log = %s, error = %s WHERE file_id = %s",
        (parsed_log, error, file_id),
    )


def _stage_readings(
    cur,
    rows: Sequence[tuple[int, datetime, float]],
) -> None:
    cur.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS staging_reading (
            series_id bigint NOT NULL,
            ts timestamp(3) NOT NULL,
            value double precision NOT NULL
        ) ON COMMIT DROP
        """
    )
    cur.execute("TRUNCATE staging_reading")
    with cur.copy("COPY staging_reading (series_id, ts, value) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def _record_failure(
    conn: Connection,
    item: PreparedFile,
    exc: Exception,
    site_timezone: str,
) -> None:
    """Best-effort audit row for an unexpected database failure."""

    try:
        with conn.transaction():
            with conn.cursor() as cur:
                metadata = item.parsed.metadata
                if metadata.serial_number:
                    _upsert_logger(cur, metadata)
                location_id = (
                    _upsert_location(cur, metadata, site_timezone)
                    if metadata.location
                    else None
                )
                _insert_import_file(cur, item, location_id)
                cur.execute(
                    "UPDATE transducer.import_file SET status = %s, error = %s "
                    "WHERE sha256 = %s",
                    (
                        Status.ERROR.value,
                        f"database error: {exc}",
                        metadata.sha256,
                    ),
                )
    except Exception:  # noqa: BLE001 - never mask the original failure
        pass


def _failure_result(item: PreparedFile, exc: Exception) -> FileLoadResult:
    return FileLoadResult(
        filename=item.parsed.metadata.filename,
        status=Status.ERROR,
        log_count=item.parsed.log_count,
        reading_count=len(item.parsed.readings),
        new_rows=0,
        overlapping_rows=0,
        duplicate_file=False,
        warnings=list(item.warnings),
        error=f"database error: {exc}",
    )


def _summarize(
    results: Sequence[FileLoadResult],
    prepared: Sequence[PreparedFile],
    dry_run: bool,
) -> LoadSummary:
    compensated = sum(
        1 for item in prepared if item.parsed.metadata.is_compensated
    )

    unique_samples: set[tuple[str | None, datetime]] = set()
    for item in prepared:
        if item.status in (Status.SKIPPED_COMPENSATED, Status.ERROR):
            continue
        serial = item.parsed.metadata.serial_number
        for reading in item.parsed.readings:
            unique_samples.add((serial, reading.ts))

    return LoadSummary(
        dry_run=dry_run,
        files_total=len(prepared),
        compensated_files=compensated,
        uncompensated_files=len(prepared) - compensated,
        imported_files=sum(
            1 for result in results if result.status is Status.IMPORTED
        ),
        warning_files=sum(
            1 for result in results if result.status is Status.WARNING
        ),
        empty_files=sum(1 for result in results if result.status is Status.EMPTY),
        error_files=sum(1 for result in results if result.status is Status.ERROR),
        duplicate_files=sum(1 for result in results if result.duplicate_file),
        redundant_files=_redundant_file_count(prepared),
        new_readings=sum(result.new_rows for result in results),
        overlapping_readings=sum(
            result.overlapping_rows for result in results
        ),
        unique_samples=len(unique_samples),
        results=list(results),
    )


def _audit_error(item: PreparedFile) -> str | None:
    if item.error:
        return item.error
    if item.warnings:
        return "; ".join(item.warnings)
    return None


def _redundant_file_count(prepared: Sequence[PreparedFile]) -> int:
    """Count uncompensated files whose readings were all seen earlier.

    This mirrors the report's definition, so a fresh load and the database-free
    report agree on the expected 31 fully redundant files.
    """

    seen: set[tuple[str | None, int, datetime]] = set()
    redundant = 0
    for item in prepared:
        metadata = item.parsed.metadata
        if metadata.is_compensated or item.status is Status.ERROR:
            continue
        if not item.parsed.readings:
            continue
        serial = metadata.serial_number
        keys = {
            (serial, reading.channel_no, reading.ts)
            for reading in item.parsed.readings
        }
        if keys <= seen:
            redundant += 1
        else:
            seen |= keys
    return redundant


def _fetch_int(cur, what: str) -> int:
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"expected a row for {what}")
    return row[0]


def _effective_download_time(path: Path, parsed: ParsedFile) -> datetime:
    if parsed.metadata.download_time is not None:
        return parsed.metadata.download_time
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return datetime.now()


def _coordinate(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(COORDINATE_SCALE)
