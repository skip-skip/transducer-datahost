import os
from datetime import datetime
from pathlib import Path

import pytest

from transducer_ingest.load import derive_location, parse_since, prepare_files
from transducer_ingest.parse_xle import parse_xle
from transducer_ingest.report import scan
from transducer_ingest.validate import Status, validate_file

TRUNCATE = (
    "TRUNCATE transducer.reading, transducer.import_file, transducer.series, "
    "transducer.location, transducer.logger RESTART IDENTITY CASCADE"
)


def test_derive_location() -> None:
    assert derive_location("MCMW-03_AT") == ("MCMW-03", True)
    assert derive_location("JC-6500") == ("JC-6500", False)
    assert derive_location("  SC-Baro  ") == ("SC-Baro", False)


def test_parse_since() -> None:
    assert parse_since(None) is None
    assert parse_since("") is None
    assert parse_since("2025-10-01") == datetime(2025, 10, 1)
    assert parse_since("2025/10/01") == datetime(2025, 10, 1)
    assert parse_since("2025-10-01 12:30:00") == datetime(2025, 10, 1, 12, 30)


def test_parse_since_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_since("not-a-date")


def test_prepare_files_statuses(fixtures_dir: Path) -> None:
    prepared = prepare_files(scan(fixtures_dir))
    statuses = {
        item.parsed.metadata.filename: item.status for item in prepared
    }

    assert statuses["v3_3ch_uncompensated.xle"] is Status.IMPORTED
    assert statuses["v4_2ch_uncompensated.xle"] is Status.IMPORTED
    assert statuses["v4_kpa_baro.xle"] is Status.IMPORTED
    assert statuses["v4_compensated.xle"] is Status.SKIPPED_COMPENSATED
    assert statuses["v4_empty.xle"] is Status.EMPTY
    assert statuses["v4_malformed.xle"] is Status.ERROR
    assert statuses["v4_warnings.xle"] is Status.WARNING


def test_prepare_files_orders_by_download_time(fixtures_dir: Path) -> None:
    prepared = prepare_files(scan(fixtures_dir))
    download_times = [item.download_time for item in prepared]
    assert download_times == sorted(download_times)


def test_since_filters_by_download_time(fixtures_dir: Path) -> None:
    prepared = prepare_files(scan(fixtures_dir), since=datetime(2025, 10, 1))
    names = {item.parsed.metadata.filename for item in prepared}

    assert names == {
        "v3_3ch_uncompensated.xle",
        "v4_kpa_baro.xle",
        "v4_compensated.xle",
        "v4_malformed.xle",
    }


def test_blank_channel_is_error(
    tmp_path: Path, fixtures_dir: Path
) -> None:
    source = fixtures_dir / "v4_2ch_uncompensated.xle"
    target = tmp_path / "2201817_MCMW-03_AT_2025_09_30_121636.xle"
    target.write_bytes(source.read_bytes().replace(b"<Unit>ft</Unit>", b"<Unit></Unit>"))

    status, _warnings, error = validate_file(parse_xle(target))

    assert status is Status.ERROR
    assert error is not None
    assert "blank identification or unit" in error


@pytest.mark.integration
def test_load_fixtures_into_database(fixtures_dir: Path) -> None:
    dsn = os.environ.get("TRANSDUCER_TEST_DSN")
    if not dsn:
        pytest.skip("TRANSDUCER_TEST_DSN is not set")
    psycopg = pytest.importorskip("psycopg")

    from transducer_ingest.load import load_export

    with psycopg.connect(dsn, autocommit=False) as conn:
        try:
            with conn.transaction():
                conn.execute(TRUNCATE)

            prepared = prepare_files(scan(fixtures_dir))

            dry = load_export(conn, prepared, dry_run=True)
            assert dry.new_readings == 20
            assert dry.unique_samples == 9
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM transducer.reading")
                assert cur.fetchone()[0] == 0

            summary = load_export(conn, prepared)
            assert summary.new_readings == 20
            assert summary.unique_samples == 9
            assert summary.imported_files == 3
            assert summary.warning_files == 1
            assert summary.empty_files == 1
            assert summary.error_files == 1
            assert summary.compensated_files == 1
            assert summary.redundant_files == 0

            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM transducer.reading")
                assert cur.fetchone()[0] == 20
                cur.execute("SELECT count(*) FROM transducer.import_file")
                assert cur.fetchone()[0] == 7
                cur.execute(
                    "SELECT count(*) FROM transducer.import_file "
                    "WHERE status = 'skipped_compensated'"
                )
                assert cur.fetchone()[0] == 1

            second = load_export(conn, prepare_files(scan(fixtures_dir)))
            assert second.new_readings == 0
            assert second.duplicate_files == 7
        finally:
            with conn.transaction():
                conn.execute(TRUNCATE)
