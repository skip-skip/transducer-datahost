"""Database-free .xle parser report.

Run with ``python -m transducer_ingest.report --source <folder>``. The report
parses every ``.xle`` file recursively, classifies each one, and compares the
aggregate totals with the expected current export. It never imports a database
driver, so it can be used before PostgreSQL is available.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .validate import (
    Report,
    build_report,
    has_location_mismatch,
    is_anomaly,
)

VOLUME_METRICS = (
    ("compensated_files", "Compensated files"),
    ("uncompensated_files", "Uncompensated files"),
    ("log_records", "Log records"),
    ("unique_samples", "Unique samples (serial, ts)"),
    ("series_readings", "Series readings (serial, channel, ts)"),
    ("empty_files", "Empty files"),
    ("anomaly_files", "Anomalous files"),
    ("redundant_files", "Fully redundant files"),
)


def scan(source: Path) -> list[Path]:
    """Return every .xle file under ``source``, sorted by path."""

    return sorted(
        path
        for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() == ".xle"
    )


def _render(report: Report, console: Console, show_all: bool) -> None:
    totals = report.totals
    measured = report.measured()

    console.print("[bold]XLE parser report[/bold]")
    console.print(f"Source: {report.source}")
    console.print(
        f"Files: {totals.files_total} "
        f"({totals.compensated_files} compensated, "
        f"{totals.uncompensated_files} uncompensated)"
    )
    console.print()

    volume = Table(title="Volume", show_header=True, header_style="bold")
    volume.add_column("Metric")
    volume.add_column("Measured", justify="right")
    volume.add_column("Expected", justify="right")

    for metric, label in VOLUME_METRICS:
        expected = report.expected.get(metric)
        value = measured.get(metric, 0)
        expected_text = "-" if expected is None else f"{expected:,}"
        if expected is not None and value != expected:
            expected_text = f"[red]{expected_text}[/red]"
        volume.add_row(label, f"{value:,}", expected_text)

    volume.add_row("Parsed log records", f"{totals.parsed_log_records:,}", "-")
    volume.add_row("Raw logs in error files", f"{totals.raw_log_records:,}", "-")
    volume.add_row("Internal duplicates", f"{totals.internal_duplicates:,}", "-")
    volume.add_row("Invalid records", f"{totals.invalid_records:,}", "-")
    console.print(volume)

    anomalies = [
        file_report
        for file_report in report.files
        if not file_report.is_compensated and is_anomaly(file_report)
    ]
    if anomalies:
        console.print()
        anomaly_table = Table(
            title=f"Anomalies ({len(anomalies)})",
            show_header=True,
            header_style="bold",
        )
        anomaly_table.add_column("File")
        anomaly_table.add_column("Status")
        anomaly_table.add_column("Detail")
        for file_report in anomalies:
            detail = "; ".join(file_report.warnings)
            if file_report.error:
                detail = (
                    f"{file_report.error}; {detail}"
                    if detail
                    else file_report.error
                )
            anomaly_table.add_row(
                file_report.filename, file_report.status.value, detail
            )
        console.print(anomaly_table)

    location_mismatches = [
        file_report
        for file_report in report.files
        if not file_report.is_compensated and has_location_mismatch(file_report)
    ]
    if location_mismatches:
        console.print()
        location_table = Table(
            title=f"Location mismatches ({len(location_mismatches)})",
            show_header=True,
            header_style="bold",
        )
        location_table.add_column("File")
        location_table.add_column("Detail")
        for file_report in location_mismatches:
            detail = "; ".join(
                warning
                for warning in file_report.warnings
                if warning.startswith("filename location ")
            )
            location_table.add_row(file_report.filename, detail)
        console.print(location_table)

    if report.redundant_files:
        console.print()
        console.print(
            f"[bold]Fully redundant files ({len(report.redundant_files)})[/bold]"
        )
        if show_all:
            for filename in report.redundant_files:
                console.print(f"  {filename}")
        else:
            shown = ", ".join(report.redundant_files[:5])
            suffix = " ..." if len(report.redundant_files) > 5 else ""
            console.print(f"  {shown}{suffix}")

    if show_all:
        console.print()
        all_files = Table(
            title=f"All files ({len(report.files)})",
            show_header=True,
            header_style="bold",
        )
        all_files.add_column("File")
        all_files.add_column("Status")
        all_files.add_column("Logs", justify="right")
        all_files.add_column("Readings", justify="right")
        all_files.add_column("Warnings", justify="right")
        for file_report in report.files:
            all_files.add_row(
                file_report.filename,
                file_report.status.value,
                f"{file_report.log_count:,}",
                f"{file_report.reading_count:,}",
                str(len(file_report.warnings)),
            )
        console.print(all_files)

    console.print()
    mismatches = report.mismatches()
    if mismatches:
        console.print("[bold red]Expected totals do not match[/bold red]")
        for metric, actual, expected in mismatches:
            console.print(
                f"  {metric}: measured {actual:,}, expected {expected:,}"
            )
    else:
        console.print("[bold green]Expected totals match[/bold green]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="transducer-report",
        description=(
            "Parse .xle exports and report validation results without a database."
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Folder to scan recursively for .xle files",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full report as JSON",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when totals differ from the expected export",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show every file and every redundant filename",
    )
    args = parser.parse_args(argv)

    if not args.source.is_dir():
        print(f"source folder not found: {args.source}", file=sys.stderr)
        return 2

    report = build_report(args.source, scan(args.source))
    if args.json:
        json.dump(report.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _render(report, Console(), show_all=args.all)

    if args.check and report.mismatches():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
