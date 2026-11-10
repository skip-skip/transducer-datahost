import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from transducer_ingest import upload as upload_module
from transducer_ingest.upload import (
    UploadOptions,
    UploadSummary,
    api_base,
    is_complete,
    resolve_token,
    sha256_file,
    upload,
)

COMPLETE = b"<Body_xle><Log /></Body_xle>"
_REAL_CLIENT = httpx.Client


def no_request_factory(*args, **kwargs):
    kwargs.pop("verify", None)
    kwargs.pop("transport", None)
    return _REAL_CLIENT(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("no request expected")
        ),
        **kwargs,
    )


def make_file(directory: Path, name: str, content: bytes = COMPLETE) -> Path:
    path = directory / name
    path.write_bytes(content)
    old = (datetime.now() - timedelta(hours=1)).timestamp()
    os.utime(path, (old, old))
    return path


def test_helpers(tmp_path: Path) -> None:
    path = make_file(tmp_path, "a.xle")

    assert is_complete(path)
    assert sha256_file(path) == hashlib.sha256(COMPLETE).hexdigest()
    assert api_base("https://host/ingest/") == "https://host/ingest"
    assert not is_complete(make_file(tmp_path, "partial.xle", b"<Body_xle>"))


def test_resolve_token_prefers_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_API_TOKEN", "env-token")

    assert resolve_token("explicit") == "explicit"
    assert resolve_token(None) == "env-token"


def run_upload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    handler,
    **option_kwargs,
) -> UploadSummary:
    source = tmp_path / "source"
    source.mkdir()
    make_file(source, "a.xle")

    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(upload_module.httpx, "Client", factory)
    options = UploadOptions(
        source=source,
        api="https://example.test/ingest",
        token="token",
        settle_seconds=0,
        wait=False,
        **option_kwargs,
    )
    return upload(options)


def test_upload_posts_new_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        if request.method == "HEAD":
            return httpx.Response(404)
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "job_id": 7,
                    "status": "queued",
                    "import_status": None,
                    "error": None,
                },
            )
        raise AssertionError(f"unexpected method {request.method}")

    summary = run_upload(monkeypatch, tmp_path, handler)

    assert summary.files_seen == 1
    assert summary.uploaded == 1
    assert summary.queued == 1
    assert summary.results[0].job_id == 7


def test_upload_skips_known_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    summary = run_upload(monkeypatch, tmp_path, handler)

    assert summary.skipped_known == 1
    assert summary.uploaded == 0


def test_upload_skips_recent_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    path = source / "a.xle"
    path.write_bytes(COMPLETE)

    monkeypatch.setattr(
        upload_module.httpx,
        "Client",
        no_request_factory,
    )
    summary = upload(
        UploadOptions(
            source=source,
            api="https://example.test/ingest",
            token="token",
            settle_seconds=3600,
            wait=False,
        )
    )

    assert summary.skipped_settle == 1
    assert summary.uploaded == 0


def test_upload_skips_incomplete_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    make_file(source, "a.xle", b"<Body_xle>")

    monkeypatch.setattr(
        upload_module.httpx,
        "Client",
        no_request_factory,
    )
    summary = upload(
        UploadOptions(
            source=source,
            api="https://example.test/ingest",
            token="token",
            settle_seconds=0,
            wait=False,
        )
    )

    assert summary.skipped_incomplete == 1
    assert summary.uploaded == 0


def test_upload_records_server_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(404)
        return httpx.Response(500, text="boom")

    summary = run_upload(monkeypatch, tmp_path, handler, retries=0)

    assert summary.failed == 1
    assert "HTTP 500" in (summary.results[0].error or "")


def test_upload_rejects_bad_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with pytest.raises(SystemExit, match="rejected the bearer token"):
        run_upload(monkeypatch, tmp_path, handler)
