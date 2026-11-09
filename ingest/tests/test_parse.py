from pathlib import Path

import pytest

from transducer_ingest.parse_xle import parse_xle


def test_decode_cp1252_units(fixtures_dir: Path) -> None:
    parsed = parse_xle(fixtures_dir / "v3_3ch_uncompensated.xle")
    units = [channel.unit for channel in parsed.metadata.channels]
    assert units == ["m", "°C", "µS/cm"]


def test_v3_layout(fixtures_dir: Path) -> None:
    parsed = parse_xle(fixtures_dir / "v3_3ch_uncompensated.xle")
    assert parsed.error is None
    assert parsed.metadata.serial_number == "1096041"
    assert parsed.metadata.created_by == "DataGrabber V3.001"
    assert len(parsed.metadata.channels) == 3
    assert parsed.log_count == 3
    assert len(parsed.readings) == 9
    assert parsed.readings[0].ts.microsecond == 0
    assert parsed.readings[3].ts.microsecond == 500_000


def test_v4_layout(fixtures_dir: Path) -> None:
    parsed = parse_xle(fixtures_dir / "v4_2ch_uncompensated.xle")
    assert parsed.error is None
    assert parsed.metadata.serial_number == "2201817"
    assert parsed.metadata.created_by == "Version 4.7.0"
    assert parsed.metadata.location == "MCMW-03_AT"
    assert len(parsed.metadata.channels) == 2
    assert parsed.log_count == 2
    assert len(parsed.readings) == 4


def test_kpa_barometer(fixtures_dir: Path) -> None:
    parsed = parse_xle(fixtures_dir / "v4_kpa_baro.xle")
    assert parsed.metadata.channels[0].identification == "LEVEL"
    assert parsed.metadata.channels[0].unit == "kPa"


def test_compensation_detected_by_metadata(fixtures_dir: Path) -> None:
    parsed = parse_xle(fixtures_dir / "v4_compensated.xle")
    assert parsed.metadata.is_compensated is True
    assert parsed.readings == []


def test_filename_is_not_authoritative(tmp_path: Path, fixtures_dir: Path) -> None:
    source = fixtures_dir / "v4_2ch_uncompensated.xle"
    target = tmp_path / "2201817_MCMW-03_AT_2025_09_30_121636Compensated.xle"
    target.write_bytes(source.read_bytes())

    parsed = parse_xle(target)

    assert parsed.metadata.is_compensated is False
    assert parsed.metadata.filename_says_compensated is True


def test_compensation_ignores_plain_filename(tmp_path: Path, fixtures_dir: Path) -> None:
    source = fixtures_dir / "v4_compensated.xle"
    target = tmp_path / "1096042_JC-6500_2025_10_09_16_25_52.xle"
    target.write_bytes(source.read_bytes())

    parsed = parse_xle(target)

    assert parsed.metadata.is_compensated is True
    assert parsed.metadata.filename_says_compensated is False


def test_empty_file(fixtures_dir: Path) -> None:
    parsed = parse_xle(fixtures_dir / "v4_empty.xle")
    assert parsed.error is None
    assert parsed.log_count == 0
    assert parsed.readings == []


def test_malformed_file(fixtures_dir: Path) -> None:
    parsed = parse_xle(fixtures_dir / "v4_malformed.xle")
    assert parsed.error is not None
    assert "malformed XML" in parsed.error
    assert parsed.raw_log_count == 1
    assert parsed.readings == []


def test_missing_serial(tmp_path: Path) -> None:
    target = tmp_path / "2201817_MCMW-03_AT_2025_01_01_000000.xle"
    target.write_bytes(
        b"<Body_xle><Instrument_info></Instrument_info><Data /></Body_xle>"
    )

    parsed = parse_xle(target)

    assert parsed.error == "missing <Serial_number>"


def test_internal_duplicate_keeps_last(fixtures_dir: Path) -> None:
    parsed = parse_xle(fixtures_dir / "v4_warnings.xle")
    assert parsed.internal_duplicates == 2
    first = next(
        reading
        for reading in parsed.readings
        if reading.channel_no == 1 and reading.ts.microsecond == 0
    )
    assert first.value == pytest.approx(1.1)


def test_invalid_record_is_skipped(fixtures_dir: Path) -> None:
    parsed = parse_xle(fixtures_dir / "v4_warnings.xle")
    assert parsed.invalid_records == 1
    assert any("skipped as invalid" in warning for warning in parsed.warnings)
