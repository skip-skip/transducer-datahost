"""Validation and aggregation for parsed .xle files.

This module contains no database code. It turns :class:`ParsedFile` objects
into per-file statuses and a folder-level :class:`Report` that can be rendered
or checked against the expected export totals.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from .parse_xle import ParsedFile, parse_xle

DATE_SUFFIX = re.compile(
    r"[_-]\d{4}[_-]\d{2}[_-]\d{2}[_-]\d{2}[_-]?\d{2}[_-]?\d{2}$"
)
COPY_SUFFIX = re.compile(r" \(\d+\)$")
JH_SUFFIX = re.compile(r"_jh$", re.IGNORECASE)
COMPENSATED_SUFFIX = re.compile(r"compensated$", re.IGNORECASE)
REFERENCE_PREFIX = re.compile(r"^REFERENCE BARO_", re.IGNORECASE)
LOCATION_WARNING_PREFIX = "filename location "


class Status(StrEnum):
    IMPORTED = "imported"
    WARNING = "warning"
    EMPTY = "empty"
    ERROR = "error"
    SKIPPED_COMPENSATED = "skipped_compensated"
    SKIPPED_DUPLICATE = "skipped_duplicate"


# Measured against OneDrive_1_11-9-2026 on 2026-11-09. ``log_records`` counts
# <Log> entries, ``unique_samples`` counts distinct (serial, ts), and
# ``series_readings`` counts distinct (serial, channel, ts) -- the rows Phase 3
# will insert into transducer.reading.
EXPECTED: dict[str, int] = {
    "compensated_files": 46,
    "uncompensated_files": 101,
    "log_records": 173_508,
    "unique_samples": 113_177,
    "series_readings": 233_855,
    "empty_files": 1,
    "anomaly_files": 13,
    "redundant_files": 31,
}


@dataclass
class FileReport:
    filename: str
    status: Status
    is_compensated: bool
    serial_number: str | None
    location: str | None
    download_time: datetime | None
    log_count: int
    raw_log_count: int
    reading_count: int
    internal_duplicates: int
    invalid_records: int
    warnings: list[str]
    error: str | None
    sha256: str


def is_anomaly(file_report: FileReport) -> bool:
    """Return True for header/data anomalies, excluding location-only mismatches.

    A filename/header location mismatch is recorded as a warning but reported
    in its own category, matching the documented export totals (12
    ``Stop_time < Start_time`` files plus one malformed file).
    """

    if file_report.error is not None:
        return True
    return any(
        not warning.startswith(LOCATION_WARNING_PREFIX)
        for warning in file_report.warnings
    )


def has_location_mismatch(file_report: FileReport) -> bool:
    """Return True when a file has a filename/header location warning."""

    return any(
        warning.startswith(LOCATION_WARNING_PREFIX)
        for warning in file_report.warnings
    )


@dataclass
class Totals:
    files_total: int
    compensated_files: int
    uncompensated_files: int
    parsed_files: int
    error_files: int
    empty_files: int
    log_records: int
    parsed_log_records: int
    raw_log_records: int
    unique_samples: int
    series_readings: int
    redundant_files: int
    anomaly_files: int
    internal_duplicates: int
    invalid_records: int


@dataclass
class Report:
    source: str
    files: list[FileReport]
    totals: Totals
    redundant_files: list[str]
    expected: dict[str, int]

    def measured(self) -> dict[str, int]:
        return {
            "compensated_files": self.totals.compensated_files,
            "uncompensated_files": self.totals.uncompensated_files,
            "log_records": self.totals.log_records,
            "unique_samples": self.totals.unique_samples,
            "series_readings": self.totals.series_readings,
            "empty_files": self.totals.empty_files,
            "anomaly_files": self.totals.anomaly_files,
            "redundant_files": self.totals.redundant_files,
        }

    def mismatches(self) -> list[tuple[str, int, int]]:
        measured = self.measured()
        return [
            (metric, measured[metric], expected)
            for metric, expected in self.expected.items()
            if measured.get(metric) != expected
        ]

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "totals": asdict(self.totals),
            "expected": dict(self.expected),
            "mismatches": [
                {"metric": metric, "measured": measured, "expected": expected}
                for metric, measured, expected in self.mismatches()
            ],
            "redundant_files": list(self.redundant_files),
            "files": [_file_to_dict(file_report) for file_report in self.files],
        }


def validate_file(parsed: ParsedFile) -> tuple[Status, list[str], str | None]:
    """Classify one parsed file and return ``(status, warnings, error)``."""

    metadata = parsed.metadata
    warnings = list(parsed.warnings)

    if metadata.is_compensated:
        if not metadata.filename_says_compensated:
            warnings.append(
                "filename does not contain 'compensat' but the body has "
                "<Compensation_info>; metadata wins"
            )
        return Status.SKIPPED_COMPENSATED, warnings, parsed.error

    if metadata.filename_says_compensated:
        warnings.append(
            "filename contains 'compensat' but the body has no "
            "<Compensation_info>; metadata wins"
        )

    if metadata.location:
        filename_location = _filename_location(metadata.filename)
        if (
            filename_location
            and filename_location.casefold() != metadata.location.casefold()
        ):
            warnings.append(
                f"filename location {filename_location!r} does not match header "
                f"location {metadata.location!r}"
            )

    if parsed.error:
        return Status.ERROR, warnings, parsed.error

    blank_channels = [
        channel.channel_no
        for channel in metadata.channels
        if not channel.identification.strip() or not channel.unit.strip()
    ]
    if blank_channels:
        channels = ", ".join(str(number) for number in blank_channels)
        return (
            Status.ERROR,
            warnings,
            f"blank identification or unit on channel(s) {channels}",
        )

    if parsed.log_count == 0:
        return Status.EMPTY, warnings, None
    if warnings:
        return Status.WARNING, warnings, None
    return Status.IMPORTED, warnings, None


def build_report(source: Path, paths: Iterable[Path]) -> Report:
    """Parse and validate every path, then aggregate the folder report."""

    parsed_files = [(path, parse_xle(path)) for path in paths]
    file_reports: list[FileReport] = []

    for _path, parsed in parsed_files:
        status, warnings, error = validate_file(parsed)
        metadata = parsed.metadata
        file_reports.append(
            FileReport(
                filename=metadata.filename,
                status=status,
                is_compensated=metadata.is_compensated,
                serial_number=metadata.serial_number,
                location=metadata.location,
                download_time=metadata.download_time,
                log_count=parsed.log_count,
                raw_log_count=parsed.raw_log_count,
                reading_count=len(parsed.readings),
                internal_duplicates=parsed.internal_duplicates,
                invalid_records=parsed.invalid_records,
                warnings=warnings,
                error=error,
                sha256=metadata.sha256,
            )
        )

    order = sorted(
        range(len(file_reports)),
        key=lambda index: (
            file_reports[index].download_time is None,
            file_reports[index].download_time or datetime.min,
            file_reports[index].filename,
        ),
    )
    file_reports = [file_reports[index] for index in order]
    parsed_files = [parsed_files[index] for index in order]

    redundant_files = _find_redundant(file_reports, parsed_files)
    totals = _aggregate(file_reports, parsed_files, redundant_files)

    return Report(
        source=str(source),
        files=file_reports,
        totals=totals,
        redundant_files=redundant_files,
        expected=dict(EXPECTED),
    )


def _find_redundant(
    file_reports: list[FileReport],
    parsed_files: list[tuple[Path, ParsedFile]],
) -> list[str]:
    seen: set[tuple[str, int, datetime]] = set()
    redundant: list[str] = []

    for report, (_path, parsed) in zip(file_reports, parsed_files, strict=True):
        if report.is_compensated or report.error or not parsed.readings:
            continue
        serial = report.serial_number
        if serial is None:
            continue

        keys = {
            (serial, reading.channel_no, reading.ts) for reading in parsed.readings
        }
        if keys <= seen:
            redundant.append(report.filename)
        else:
            seen |= keys

    return redundant


def _aggregate(
    file_reports: list[FileReport],
    parsed_files: list[tuple[Path, ParsedFile]],
    redundant_files: list[str],
) -> Totals:
    compensated = [report for report in file_reports if report.is_compensated]
    uncompensated = [report for report in file_reports if not report.is_compensated]
    parsed_ok = [report for report in uncompensated if report.error is None]
    errors = [report for report in uncompensated if report.error is not None]
    empty = [report for report in parsed_ok if report.log_count == 0]
    anomalies = [
        report for report in uncompensated if is_anomaly(report)
    ]

    parsed_log_records = sum(report.log_count for report in parsed_ok)
    raw_log_records = sum(report.raw_log_count for report in errors)

    unique_samples: set[tuple[str | None, datetime]] = set()
    series_readings: set[tuple[str | None, int, datetime]] = set()
    for report, (_path, parsed) in zip(file_reports, parsed_files, strict=True):
        if report.is_compensated or report.error:
            continue
        serial = report.serial_number
        for reading in parsed.readings:
            unique_samples.add((serial, reading.ts))
            series_readings.add((serial, reading.channel_no, reading.ts))

    return Totals(
        files_total=len(file_reports),
        compensated_files=len(compensated),
        uncompensated_files=len(uncompensated),
        parsed_files=len(parsed_ok),
        error_files=len(errors),
        empty_files=len(empty),
        log_records=parsed_log_records + raw_log_records,
        parsed_log_records=parsed_log_records,
        raw_log_records=raw_log_records,
        unique_samples=len(unique_samples),
        series_readings=len(series_readings),
        redundant_files=len(redundant_files),
        anomaly_files=len(anomalies),
        internal_duplicates=sum(
            report.internal_duplicates for report in uncompensated
        ),
        invalid_records=sum(report.invalid_records for report in uncompensated),
    )


def _filename_location(filename: str) -> str | None:
    """Best-effort location token from names such as ``<serial>_<location>_<date>``.

    Only names that follow the export convention are inspected: a leading
    numeric serial and a trailing date/time suffix are required. Anything else
    (for example a test fixture named ``v3_3ch_uncompensated.xle``) returns
    ``None`` so that it cannot produce a spurious location warning.
    """

    stem = filename.rsplit(".", 1)[0]
    stem = REFERENCE_PREFIX.sub("", stem)
    stem = COPY_SUFFIX.sub("", stem)
    stem = JH_SUFFIX.sub("", stem)
    stem = COMPENSATED_SUFFIX.sub("", stem)

    date_match = DATE_SUFFIX.search(stem)
    if date_match is None:
        return None
    stem = stem[: date_match.start()]

    match = re.match(r"^\d+_(.+)$", stem)
    if match is None:
        return None
    return match.group(1).strip() or None


def _file_to_dict(file_report: FileReport) -> dict:
    return {
        "filename": file_report.filename,
        "status": file_report.status.value,
        "is_compensated": file_report.is_compensated,
        "serial_number": file_report.serial_number,
        "location": file_report.location,
        "download_time": (
            file_report.download_time.isoformat()
            if file_report.download_time is not None
            else None
        ),
        "log_count": file_report.log_count,
        "raw_log_count": file_report.raw_log_count,
        "reading_count": file_report.reading_count,
        "internal_duplicates": file_report.internal_duplicates,
        "invalid_records": file_report.invalid_records,
        "warnings": list(file_report.warnings),
        "error": file_report.error,
        "sha256": file_report.sha256,
    }
