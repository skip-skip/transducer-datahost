"""Parse Solinst .xle transducer exports into normalized records.

Source files are Windows-1252 XML with no namespace and no usable encoding
declaration. Two layouts are supported: ``DataGrabber V3.001`` (compact) and
``Version 4.7.0`` (indented, extra metadata). The parser reads by tag name, so
indentation and layout-only fields do not matter.

Parsing is deliberately tolerant of metadata problems but strict about data: a
file with an unreadable log record is imported with a warning, while a file
that is not well-formed XML is reported as an error and contributes no
readings. ``raw_log_count`` is retained for error files so the report can still
account for the log records present in a corrupt file.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from defusedxml.ElementTree import fromstring as safe_fromstring

XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)
COMPENSATION_TAG = re.compile(r"<Compensation_info\b", re.IGNORECASE)
RAW_LOG_TAG = re.compile(r"<Log\b")
DATE_FORMAT = "%Y/%m/%d %H:%M:%S"

# Units observed in the current export. Unknown units are imported with a
# warning rather than rejected, because a future logger may report a new unit.
KNOWN_UNITS = frozenset({"m", "ft", "°C", "µS/cm", "kPa"})


class XleError(Exception):
    """Base class for .xle parsing errors."""


class XleDecodeError(XleError):
    """The file could not be decoded as text."""


@dataclass(frozen=True)
class ChannelHeader:
    channel_no: int
    identification: str
    unit: str


@dataclass(frozen=True)
class FileMetadata:
    filename: str
    sha256: str
    created_by: str | None = None
    download_time: datetime | None = None
    serial_number: str | None = None
    instrument_type: str | None = None
    model_number: str | None = None
    firmware: str | None = None
    project_id: str | None = None
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    sample_rate: int | None = None
    start_time: datetime | None = None
    stop_time: datetime | None = None
    num_log: int | None = None
    declared_channels: int | None = None
    channels: tuple[ChannelHeader, ...] = ()
    is_compensated: bool = False
    filename_says_compensated: bool = False


@dataclass(frozen=True)
class Reading:
    channel_no: int
    identification: str
    unit: str
    ts: datetime
    value: float


@dataclass
class ParsedFile:
    metadata: FileMetadata
    readings: list[Reading] = field(default_factory=list)
    log_count: int = 0
    raw_log_count: int = 0
    internal_duplicates: int = 0
    invalid_records: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def decode_xle(data: bytes) -> str:
    """Decode an .xle payload and remove the XML declaration.

    Current exports are UTF-8 without a BOM; older exports are cp1252. A UTF-8
    BOM is honoured explicitly, otherwise UTF-8 is tried first and cp1252 is
    the fallback. UTF-8 is attempted first because cp1252 accepts every byte
    sequence, so a cp1252-first attempt would silently mis-decode UTF-8 files
    (for example ``\\xc2\\xb0`` becoming ``\\u00c2\\u00b0``). The declaration is
    removed because it names an encoding that no longer matches the decoded
    string and would make ElementTree complain.
    """

    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
        encodings: tuple[str, ...] = ("utf-8",)
    else:
        encodings = ("utf-8", "cp1252")

    last_error: UnicodeDecodeError | None = None
    text: str | None = None
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError as exc:
            last_error = exc

    if text is None:
        raise XleDecodeError(f"could not decode as utf-8 or cp1252: {last_error}")
    return XML_DECLARATION.sub("", text, count=1)


def detect_compensation(text: str) -> bool:
    """Return True when the body contains the authoritative compensation block."""

    return COMPENSATION_TAG.search(text) is not None


def filename_says_compensated(filename: str) -> bool:
    """Return True when the filename says ``Compensated`` (advisory only).

    ``uncompensated`` must not match, so the token is matched only when it is
    not preceded by ``un``.
    """

    return re.search(r"(?<!un)compensated", filename, re.IGNORECASE) is not None


def count_raw_logs(text: str) -> int:
    """Count ``<Log`` tags without requiring well-formed XML."""

    return len(RAW_LOG_TAG.findall(text))


def parse_timestamp(
    date_text: str | None,
    time_text: str | None,
    ms_text: str | None,
) -> datetime:
    """Combine the logger date, time, and millisecond fields.

    The result is a naive local timestamp with ``microsecond = ms * 1000`` so
    that two-samples-per-second aquifer-test data is preserved.
    """

    date_text = (date_text or "").strip()
    time_text = (time_text or "").strip()
    ms_text = (ms_text or "0").strip()
    if not date_text or not time_text:
        raise ValueError("missing date or time")

    base = datetime.strptime(f"{date_text} {time_text}", DATE_FORMAT)
    try:
        milliseconds = int(ms_text)
    except ValueError as exc:
        raise ValueError(f"invalid ms value {ms_text!r}") from exc
    if not 0 <= milliseconds <= 999:
        raise ValueError(f"ms value out of range: {milliseconds}")
    return base.replace(microsecond=milliseconds * 1000)


def parse_xle(path: Path) -> ParsedFile:
    """Parse one .xle file.

    Content problems are reported through :attr:`ParsedFile.error` instead of
    being raised, so a folder report can continue past a corrupt file. I/O
    errors are also captured.
    """

    filename = path.name
    filename_compensated = filename_says_compensated(filename)

    try:
        data = path.read_bytes()
    except OSError as exc:
        return ParsedFile(
            metadata=_minimal_metadata(filename, "", False, filename_compensated),
            error=f"I/O error: {exc}",
        )

    sha256 = hashlib.sha256(data).hexdigest()
    try:
        text = decode_xle(data)
    except XleDecodeError as exc:
        return ParsedFile(
            metadata=_minimal_metadata(filename, sha256, False, filename_compensated),
            error=f"decode error: {exc}",
        )

    is_compensated = detect_compensation(text)
    raw_log_count = count_raw_logs(text)
    try:
        root = safe_fromstring(text)
    except Exception as exc:  # defusedxml raises ParseError/DefusedXmlException
        return ParsedFile(
            metadata=_minimal_metadata(
                filename, sha256, is_compensated, filename_compensated
            ),
            raw_log_count=raw_log_count,
            error=f"malformed XML: {exc}",
        )

    metadata = _extract_metadata(
        root, filename, sha256, is_compensated, filename_compensated
    )
    if metadata.download_time is None:
        try:
            metadata = replace(
                metadata,
                download_time=datetime.fromtimestamp(path.stat().st_mtime),
            )
        except OSError:
            pass

    logs = root.findall("Data/Log")
    log_count = len(logs)
    warnings = _metadata_warnings(metadata, log_count)

    error: str | None = None
    if not metadata.serial_number:
        error = "missing <Serial_number>"
    elif not metadata.channels:
        error = "no <ChN_data_header> elements"

    readings: list[Reading] = []
    internal_duplicates = 0
    invalid_records = 0
    if not is_compensated and error is None:
        readings, internal_duplicates, invalid_records = _parse_readings(
            logs, metadata.channels
        )

    if internal_duplicates:
        warnings.append(
            f"{internal_duplicates} duplicate (channel, timestamp) readings; kept last"
        )
    if invalid_records:
        warnings.append(f"{invalid_records} log records were skipped as invalid")

    return ParsedFile(
        metadata=metadata,
        readings=readings,
        log_count=log_count,
        raw_log_count=raw_log_count,
        internal_duplicates=internal_duplicates,
        invalid_records=invalid_records,
        warnings=warnings,
        error=error,
    )


def _parse_readings(
    logs: list,
    channels: tuple[ChannelHeader, ...],
) -> tuple[list[Reading], int, int]:
    reading_map: dict[tuple[int, datetime], Reading] = {}
    internal_duplicates = 0
    invalid_records = 0

    for log in logs:
        try:
            ts = parse_timestamp(
                log.findtext("Date"),
                log.findtext("Time"),
                log.findtext("ms"),
            )
        except ValueError:
            invalid_records += 1
            continue

        for channel in channels:
            value_text = log.findtext(f"ch{channel.channel_no}")
            if value_text is None:
                invalid_records += 1
                continue
            try:
                value = float(value_text)
            except ValueError:
                invalid_records += 1
                continue
            if not math.isfinite(value):
                invalid_records += 1
                continue

            key = (channel.channel_no, ts)
            if key in reading_map:
                internal_duplicates += 1
            reading_map[key] = Reading(
                channel_no=channel.channel_no,
                identification=channel.identification,
                unit=channel.unit,
                ts=ts,
                value=value,
            )

    return list(reading_map.values()), internal_duplicates, invalid_records


def _metadata_warnings(metadata: FileMetadata, log_count: int) -> list[str]:
    warnings: list[str] = []

    if metadata.num_log is not None and metadata.num_log != log_count:
        warnings.append(
            f"<Num_log> is {metadata.num_log} but {log_count} logs were parsed"
        )
    if (
        metadata.start_time
        and metadata.stop_time
        and metadata.stop_time < metadata.start_time
    ):
        warnings.append("<Stop_time> is before <Start_time>")
    if (
        metadata.declared_channels is not None
        and metadata.declared_channels != len(metadata.channels)
    ):
        warnings.append(
            f"<Channel> is {metadata.declared_channels} but "
            f"{len(metadata.channels)} channel headers were found"
        )

    unknown_units = sorted(
        {channel.unit for channel in metadata.channels if channel.unit not in KNOWN_UNITS}
    )
    if unknown_units:
        warnings.append("unknown units: " + ", ".join(unknown_units))

    return warnings


def _extract_metadata(
    root,
    filename: str,
    sha256: str,
    is_compensated: bool,
    filename_compensated: bool,
) -> FileMetadata:
    file_info = root.find("File_info")
    instrument = root.find("Instrument_info")
    header = root.find("Instrument_info_data_header")

    download_time = None
    if file_info is not None:
        date_text = _text(file_info, "Date") or ""
        time_text = _text(file_info, "Time") or ""
        download_time = _parse_datetime(f"{date_text} {time_text}".strip())

    channels = []
    for channel_no in range(1, 10):
        channel_header = root.find(f"Ch{channel_no}_data_header")
        if channel_header is None:
            continue
        channels.append(
            ChannelHeader(
                channel_no=channel_no,
                identification=_text(channel_header, "Identification") or "",
                unit=_text(channel_header, "Unit") or "",
            )
        )

    return FileMetadata(
        filename=filename,
        sha256=sha256,
        created_by=_text(file_info, "Created_by"),
        download_time=download_time,
        serial_number=_text(instrument, "Serial_number"),
        instrument_type=_text(instrument, "Instrument_type"),
        model_number=_text(instrument, "Model_number"),
        firmware=_text(instrument, "Firmware"),
        project_id=_text(header, "Project_ID"),
        location=_text(header, "Location"),
        latitude=_float(_text(header, "Latitude")),
        longitude=_float(_text(header, "Longtitude")),
        sample_rate=_int(_text(header, "Sample_rate")),
        start_time=_parse_datetime(_text(header, "Start_time")),
        stop_time=_parse_datetime(_text(header, "Stop_time")),
        num_log=_int(_text(header, "Num_log")),
        declared_channels=_int(_text(instrument, "Channel")),
        channels=tuple(channels),
        is_compensated=is_compensated,
        filename_says_compensated=filename_compensated,
    )


def _minimal_metadata(
    filename: str,
    sha256: str,
    is_compensated: bool,
    filename_says_compensated: bool,
) -> FileMetadata:
    return FileMetadata(
        filename=filename,
        sha256=sha256,
        is_compensated=is_compensated,
        filename_says_compensated=filename_says_compensated,
    )


def _text(element, path: str) -> str | None:
    if element is None:
        return None
    value = element.findtext(path)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, DATE_FORMAT)
    except ValueError:
        return None
