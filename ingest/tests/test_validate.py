from pathlib import Path

from transducer_ingest.parse_xle import parse_xle
from transducer_ingest.validate import Status, validate_file


def _status(path: Path) -> tuple[Status, list[str], str | None]:
    return validate_file(parse_xle(path))


def test_imported_status(fixtures_dir: Path) -> None:
    status, warnings, error = _status(fixtures_dir / "v3_3ch_uncompensated.xle")
    assert status is Status.IMPORTED
    assert warnings == []
    assert error is None


def test_empty_status(fixtures_dir: Path) -> None:
    status, warnings, error = _status(fixtures_dir / "v4_empty.xle")
    assert status is Status.EMPTY
    assert warnings == []
    assert error is None


def test_error_status(fixtures_dir: Path) -> None:
    status, warnings, error = _status(fixtures_dir / "v4_malformed.xle")
    assert status is Status.ERROR
    assert error is not None


def test_compensated_status(fixtures_dir: Path) -> None:
    status, warnings, error = _status(fixtures_dir / "v4_compensated.xle")
    assert status is Status.SKIPPED_COMPENSATED
    assert warnings == []
    assert error is None


def test_warning_status(fixtures_dir: Path) -> None:
    status, warnings, error = _status(fixtures_dir / "v4_warnings.xle")
    assert status is Status.WARNING
    assert error is None
    assert any("Stop_time" in warning for warning in warnings)
    assert any("Num_log" in warning for warning in warnings)
    assert any("duplicate" in warning for warning in warnings)
    assert any("skipped as invalid" in warning for warning in warnings)


def test_filename_compensation_mismatch(
    tmp_path: Path, fixtures_dir: Path
) -> None:
    target = tmp_path / "1096041_JC-6500_2025_10_09_16_25_52Compensated.xle"
    target.write_bytes((fixtures_dir / "v3_3ch_uncompensated.xle").read_bytes())

    status, warnings, _error = _status(target)

    assert status is Status.WARNING
    assert any("filename contains 'compensat'" in warning for warning in warnings)


def test_location_mismatch(tmp_path: Path, fixtures_dir: Path) -> None:
    target = tmp_path / "2201817_WRONG-LOCATION_2025_09_30_121636.xle"
    target.write_bytes((fixtures_dir / "v4_2ch_uncompensated.xle").read_bytes())

    status, warnings, _error = _status(target)

    assert status is Status.WARNING
    assert any("does not match header location" in warning for warning in warnings)
