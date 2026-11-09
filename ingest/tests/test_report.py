import json
from pathlib import Path

import pytest

from transducer_ingest.report import main, scan
from transducer_ingest.validate import build_report


def _build(fixtures_dir: Path):
    return build_report(fixtures_dir, scan(fixtures_dir))


def test_fixture_totals(fixtures_dir: Path) -> None:
    report = _build(fixtures_dir)
    totals = report.totals

    assert totals.files_total == 7
    assert totals.compensated_files == 1
    assert totals.uncompensated_files == 6
    assert totals.parsed_files == 5
    assert totals.error_files == 1
    assert totals.empty_files == 1
    assert totals.log_records == 11
    assert totals.parsed_log_records == 10
    assert totals.raw_log_records == 1
    assert totals.unique_samples == 9
    assert totals.series_readings == 20
    assert totals.redundant_files == 0
    assert totals.anomaly_files == 2
    assert totals.internal_duplicates == 2
    assert totals.invalid_records == 1


def test_json_serializable(fixtures_dir: Path) -> None:
    payload = json.dumps(_build(fixtures_dir).to_dict())
    assert '"log_records": 11' in payload


def test_check_fixtures_fails(fixtures_dir: Path) -> None:
    assert main(["--source", str(fixtures_dir), "--check"]) == 1


def test_scan_is_recursive(tmp_path: Path, fixtures_dir: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "v4_empty.xle").write_bytes(
        (fixtures_dir / "v4_empty.xle").read_bytes()
    )

    assert len(scan(tmp_path)) == 1


def test_mismatches_are_reported(fixtures_dir: Path) -> None:
    metrics = {metric for metric, _, _ in _build(fixtures_dir).mismatches()}
    assert "uncompensated_files" in metrics
    assert "log_records" in metrics


def test_report_module_does_not_import_a_database_driver() -> None:
    import ast

    import transducer_ingest.report as report_module

    tree = ast.parse(
        Path(report_module.__file__).read_text(encoding="utf-8")
    )
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not any(name.split(".")[0] == "psycopg" for name in imported)


@pytest.mark.integration
def test_full_export_totals(export_dir: Path) -> None:
    report = build_report(export_dir, scan(export_dir))
    totals = report.totals

    assert totals.files_total == 147
    assert totals.compensated_files == 46
    assert totals.uncompensated_files == 101
    assert totals.log_records == 173_508
    assert totals.unique_samples == 113_177
    assert totals.series_readings == 233_855
    assert totals.empty_files == 1
    assert totals.anomaly_files == 13
    assert totals.redundant_files == 31
    assert report.mismatches() == []
