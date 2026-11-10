"""Manual HTTPS uploader for the VM-hosted ingest gateway.

This module is the only part of the package that talks to the ingest API. It
needs ``httpx`` and a bearer token, never a database credential.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import socket
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

from .report import scan

COMPLETENESS_SUFFIX = b"</Body_xle>"
TAIL_BYTES = 1024 * 1024
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class UploadOptions:
    source: Path
    api: str
    token: str
    ca: str | None = None
    settle_seconds: int = 120
    wait: bool = True
    timeout: float = 60.0
    wait_timeout: float = 300.0
    retries: int = 3
    client_name: str = ""
    verbose: bool = False


@dataclass
class UploadOutcome:
    filename: str
    sha256: str
    action: str
    status: str | None = None
    import_status: str | None = None
    job_id: int | None = None
    error: str | None = None


@dataclass
class UploadSummary:
    source: str
    api: str
    files_seen: int = 0
    skipped_settle: int = 0
    skipped_incomplete: int = 0
    skipped_known: int = 0
    uploaded: int = 0
    duplicates: int = 0
    done: int = 0
    failed: int = 0
    queued: int = 0
    results: list[UploadOutcome] = field(default_factory=list)


def is_complete(path: Path) -> bool:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > TAIL_BYTES:
                handle.seek(size - TAIL_BYTES)
            tail = handle.read()
    except OSError:
        return False
    return tail.rstrip().endswith(COMPLETENESS_SUFFIX)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def api_base(value: str) -> str:
    return value.rstrip("/")


def resolve_token(explicit: str | None) -> str:
    token = explicit or os.environ.get("INGEST_API_TOKEN")
    if token:
        return token.strip()
    if sys.stdin.isatty():
        return getpass.getpass("Ingest API token: ").strip()
    raise SystemExit("INGEST_API_TOKEN is not set")


def _retry_after(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    return min(60.0, 2.0**attempt)


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retries: int,
    **kwargs: Any,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_error = exc
            if attempt >= retries:
                raise
            time.sleep(min(60.0, 2.0**attempt))
            continue

        if response.status_code in RETRYABLE_STATUS and attempt < retries:
            time.sleep(_retry_after(response, attempt))
            continue
        return response

    raise last_error or RuntimeError("request failed")


def _wait_for_job(
    client: httpx.Client,
    base: str,
    headers: dict[str, str],
    job_id: int,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    url = f"{base}/v1/uploads/{job_id}"
    while time.monotonic() < deadline:
        response = _request(client, "GET", url, retries=2, headers=headers)
        if response.status_code == 401:
            raise PermissionError("ingest API rejected the bearer token")
        if response.status_code == 200:
            job = response.json()
            if job.get("status") in ("done", "failed"):
                return job
        time.sleep(2.0)
    return {"job_id": job_id, "status": "queued", "error": "wait timed out"}


def upload(
    options: UploadOptions,
    *,
    console: Console | None = None,
) -> UploadSummary:
    console = console or Console()
    source = options.source
    if not source.is_dir():
        raise SystemExit(f"source folder not found: {source}")

    base = api_base(options.api)
    headers = {"Authorization": f"Bearer {options.token}"}
    summary = UploadSummary(source=str(source), api=base)
    paths = scan(source)
    summary.files_seen = len(paths)
    client_name = options.client_name or socket.gethostname()
    now = time.time()

    verify: bool | str = options.ca if options.ca else True
    with httpx.Client(
        verify=verify,
        timeout=options.timeout,
        follow_redirects=False,
    ) as client:
        for path in paths:
            try:
                stat = path.stat()
            except OSError as exc:
                summary.failed += 1
                summary.results.append(
                    UploadOutcome(
                        filename=path.name,
                        sha256="",
                        action="failed",
                        error=f"cannot stat file: {exc}",
                    )
                )
                continue

            if options.settle_seconds > 0 and now - stat.st_mtime < options.settle_seconds:
                summary.skipped_settle += 1
                summary.results.append(
                    UploadOutcome(
                        filename=path.name,
                        sha256="",
                        action="skipped_settle",
                        error="file is newer than the settle window",
                    )
                )
                continue

            if not is_complete(path):
                summary.skipped_incomplete += 1
                summary.results.append(
                    UploadOutcome(
                        filename=path.name,
                        sha256="",
                        action="skipped_incomplete",
                        error="final content is not </Body_xle>",
                    )
                )
                continue

            try:
                digest = sha256_file(path)
            except OSError as exc:
                summary.failed += 1
                summary.results.append(
                    UploadOutcome(
                        filename=path.name,
                        sha256="",
                        action="failed",
                        error=f"cannot read file: {exc}",
                    )
                )
                continue

            head = _request(
                client,
                "HEAD",
                f"{base}/v1/files/{digest}",
                retries=options.retries,
                headers=headers,
            )
            if head.status_code == 401:
                raise SystemExit("ingest API rejected the bearer token")
            if head.status_code == 204:
                summary.skipped_known += 1
                summary.results.append(
                    UploadOutcome(
                        filename=path.name,
                        sha256=digest,
                        action="skipped_known",
                        status="known",
                    )
                )
                continue

            upload_headers = {
                **headers,
                "X-Content-SHA256": digest,
                "X-Original-Mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "X-Client-Name": client_name,
            }
            try:
                # Read once so a transport retry sends the same bytes rather
                # than an exhausted file handle. Uploads are capped at 10 MB.
                payload = path.read_bytes()
                response = _request(
                    client,
                    "POST",
                    f"{base}/v1/uploads",
                    retries=options.retries,
                    headers=upload_headers,
                    files={
                        "file": (
                            path.name,
                            payload,
                            "application/xml",
                        )
                    },
                )
            except (OSError, httpx.HTTPError) as exc:
                summary.failed += 1
                summary.results.append(
                    UploadOutcome(
                        filename=path.name,
                        sha256=digest,
                        action="failed",
                        error=str(exc),
                    )
                )
                continue

            if response.status_code == 401:
                raise SystemExit("ingest API rejected the bearer token")
            if response.status_code not in (200, 201, 202):
                summary.failed += 1
                summary.results.append(
                    UploadOutcome(
                        filename=path.name,
                        sha256=digest,
                        action="failed",
                        error=f"HTTP {response.status_code}: {response.text[:500]}",
                    )
                )
                continue

            job = response.json()
            job_id = job.get("job_id")
            duplicate = response.status_code == 200
            if duplicate:
                summary.duplicates += 1
            else:
                summary.uploaded += 1

            outcome = UploadOutcome(
                filename=path.name,
                sha256=digest,
                action="duplicate" if duplicate else "uploaded",
                status=job.get("status"),
                import_status=job.get("import_status"),
                job_id=job_id,
                error=job.get("error"),
            )

            if options.wait and isinstance(job_id, int):
                try:
                    final = _wait_for_job(
                        client, base, headers, job_id, options.wait_timeout
                    )
                except PermissionError as exc:
                    raise SystemExit(str(exc)) from exc
                outcome.status = final.get("status")
                outcome.import_status = final.get("import_status")
                outcome.error = final.get("error")

            if outcome.status == "done":
                summary.done += 1
            elif outcome.status == "failed":
                summary.failed += 1
            else:
                summary.queued += 1

            summary.results.append(outcome)

    return summary


def render(summary: UploadSummary, console: Console, *, show_all: bool) -> None:
    console.print("[bold]Ingest upload complete[/bold]")
    console.print(f"Source: {summary.source}")
    console.print(f"API: {summary.api}")
    console.print()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Result")
    table.add_column("Count", justify="right")
    for label, value in (
        ("Files seen", summary.files_seen),
        ("Uploaded", summary.uploaded),
        ("Duplicate jobs", summary.duplicates),
        ("Already known (HEAD)", summary.skipped_known),
        ("Skipped (settle window)", summary.skipped_settle),
        ("Skipped (incomplete)", summary.skipped_incomplete),
        ("Jobs done", summary.done),
        ("Jobs failed", summary.failed),
        ("Jobs queued", summary.queued),
    ):
        table.add_row(label, f"{value:,}")
    console.print(table)

    if show_all:
        console.print()
        files = Table(title="Files", show_header=True, header_style="bold")
        files.add_column("File")
        files.add_column("Action")
        files.add_column("Job", justify="right")
        files.add_column("Import status")
        files.add_column("Detail")
        for outcome in summary.results:
            files.add_row(
                outcome.filename,
                outcome.action,
                "" if outcome.job_id is None else str(outcome.job_id),
                outcome.import_status or outcome.status or "",
                outcome.error or "",
            )
        console.print(files)


def main(options: UploadOptions, *, show_json: bool = False, show_all: bool = False) -> int:
    summary = upload(options)
    if show_json:
        json.dump(asdict(summary), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        render(summary, Console(), show_all=show_all)
    return 1 if summary.failed else 0
