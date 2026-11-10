"""Command-line interface for loading .xle exports into PostgreSQL.

``ingest`` is the Phase 3 loader. ``report`` delegates to the database-free
Phase 2 report so a single entry point covers both workflows. The loader and
psycopg are imported lazily, so ``report`` still works without a database
driver.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .report import scan
from .validate import EXPECTED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transducer-ingest",
        description=(
            "Parse Solinst .xle exports and load uncompensated readings into "
            "PostgreSQL."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest",
        help="Load .xle files into PostgreSQL",
    )
    ingest.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Folder to scan recursively for .xle files",
    )
    ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be loaded, then roll back",
    )
    ingest.add_argument(
        "--since",
        help="Only files with header download time on or after this value",
    )
    ingest.add_argument(
        "--json",
        action="store_true",
        help="Emit the load summary as JSON",
    )
    ingest.add_argument(
        "--all",
        action="store_true",
        help="Show every file result",
    )

    report = subparsers.add_parser(
        "report",
        help="Parse .xle files and report validation results without a database",
    )
    report.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Folder to scan recursively for .xle files",
    )
    report.add_argument("--json", action="store_true", help="Emit JSON")
    report.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when totals differ from the expected export",
    )
    report.add_argument(
        "--all",
        action="store_true",
        help="Show every file and every redundant filename",
    )

    upload = subparsers.add_parser(
        "upload",
        help="Upload .xle files to the VM-hosted ingest API (HTTPS)",
    )
    upload.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Folder to scan recursively for .xle files",
    )
    upload.add_argument(
        "--api",
        default=os.environ.get("INGEST_API_URL"),
        help="API base URL, for example https://transducer.internal:8443/ingest",
    )
    upload.add_argument(
        "--ca",
        default=os.environ.get("INGEST_CA_CERT") or None,
        help="Path to the CA certificate used to verify the API (optional)",
    )
    upload.add_argument(
        "--token",
        help="Bearer token. Prefer INGEST_API_TOKEN or the interactive prompt.",
    )
    upload.add_argument(
        "--settle-seconds",
        type=int,
        default=None,
        help="Skip files modified within this many seconds (default: env or 120)",
    )
    upload.add_argument(
        "--wait",
        dest="wait",
        action="store_true",
        default=True,
        help="Wait for each job to finish (default)",
    )
    upload.add_argument(
        "--no-wait",
        dest="wait",
        action="store_false",
        help="Enqueue only and return immediately",
    )
    upload.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout")
    upload.add_argument(
        "--wait-timeout",
        type=float,
        default=300.0,
        help="Maximum seconds to wait for a job",
    )
    upload.add_argument("--retries", type=int, default=3, help="Retries for 429/5xx")
    upload.add_argument(
        "--client-name",
        default="",
        help="Label sent with uploads (default: hostname)",
    )
    upload.add_argument("--json", action="store_true", help="Emit the summary as JSON")
    upload.add_argument(
        "--all",
        action="store_true",
        help="Show every file result",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "report":
        from . import report as report_module

        report_argv = ["--source", str(args.source)]
        if args.json:
            report_argv.append("--json")
        if args.check:
            report_argv.append("--check")
        if args.all:
            report_argv.append("--all")
        return report_module.main(report_argv)

    if args.command == "upload":
        return _run_upload(args)

    return _run_ingest(args)


def _run_upload(args: argparse.Namespace) -> int:
    if not args.api:
        print(
            "--api or INGEST_API_URL is required (for example "
            "https://transducer.internal:8443/ingest)",
            file=sys.stderr,
        )
        return 2
    if not args.source.is_dir():
        print(f"source folder not found: {args.source}", file=sys.stderr)
        return 2

    from .upload import UploadOptions, resolve_token
    from .upload import main as upload_main

    settle_seconds = args.settle_seconds
    if settle_seconds is None:
        try:
            settle_seconds = int(os.environ.get("INGEST_SETTLE_SECONDS", "120"))
        except ValueError:
            print("INGEST_SETTLE_SECONDS must be an integer", file=sys.stderr)
            return 2

    try:
        token = resolve_token(args.token)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    options = UploadOptions(
        source=args.source,
        api=args.api,
        token=token,
        ca=args.ca,
        settle_seconds=settle_seconds,
        wait=args.wait,
        timeout=args.timeout,
        wait_timeout=args.wait_timeout,
        retries=args.retries,
        client_name=args.client_name,
    )
    return upload_main(options, show_json=args.json, show_all=args.all)


def _run_ingest(args: argparse.Namespace) -> int:
    if not args.source.is_dir():
        print(f"source folder not found: {args.source}", file=sys.stderr)
        return 2

    from .load import parse_since

    try:
        since = parse_since(args.since)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    from .db import connect, site_timezone
    from .load import load_export, prepare_files

    prepared = prepare_files(scan(args.source), since=since)

    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001 - connection errors are user-facing
        print(f"database connection failed: {exc}", file=sys.stderr)
        return 2

    try:
        summary = load_export(
            conn,
            prepared,
            dry_run=args.dry_run,
            site_timezone=site_timezone(),
        )
    finally:
        conn.close()

    if args.json:
        json.dump(asdict(summary), sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _render_summary(summary, Console(), show_all=args.all, since=since)

    return 0


def _render_summary(summary, console: Console, *, show_all: bool, since) -> None:
    title = "Ingest dry run" if summary.dry_run else "Ingest complete"
    console.print(f"[bold]{title}[/bold]")
    console.print(
        f"Files selected: {summary.files_total:,} "
        f"({summary.compensated_files:,} compensated, "
        f"{summary.uncompensated_files:,} uncompensated)"
    )
    console.print()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Result")
    table.add_column("Count", justify="right")

    rows = (
        ("Imported files", summary.imported_files),
        ("Warning files", summary.warning_files),
        ("Empty files", summary.empty_files),
        ("Error files", summary.error_files),
        ("Duplicate files (skipped)", summary.duplicate_files),
        ("Fully redundant files", summary.redundant_files),
        ("New reading rows", summary.new_readings),
        ("Overlapping reading rows", summary.overlapping_readings),
        ("Unique samples", summary.unique_samples),
    )
    for label, value in rows:
        table.add_row(label, f"{value:,}")
    console.print(table)

    if show_all:
        console.print()
        files = Table(
            title=f"Files ({len(summary.results):,})",
            show_header=True,
            header_style="bold",
        )
        files.add_column("File")
        files.add_column("Status")
        files.add_column("Logs", justify="right")
        files.add_column("Readings", justify="right")
        files.add_column("New", justify="right")
        files.add_column("Overlap", justify="right")
        for result in summary.results:
            files.add_row(
                result.filename,
                result.status.value,
                f"{result.log_count:,}",
                f"{result.reading_count:,}",
                f"{result.new_rows:,}",
                f"{result.overlapping_rows:,}",
            )
        console.print(files)

    if since is None:
        console.print()
        console.print(
            "[bold]Full-export reference[/bold]: "
            f"{EXPECTED['series_readings']:,} reading rows, "
            f"{EXPECTED['unique_samples']:,} unique samples, "
            f"{EXPECTED['compensated_files']:,} compensated files"
        )


if __name__ == "__main__":
    raise SystemExit(main())
