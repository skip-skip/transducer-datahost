import io
import os
import time
from pathlib import Path

import pytest

from transducer_ingest.service.spool import (
    EmptyUpload,
    Spool,
    UploadTooLarge,
)

COMPLETE = b"<Body_xle><Log /></Body_xle>"


def test_save_stream_hashes_and_finalizes(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    saved = spool.save_stream(io.BytesIO(COMPLETE), max_bytes=1024)

    assert saved.byte_size == len(COMPLETE)
    assert saved.path.exists()
    assert spool.is_complete(saved.path)

    destination = spool.finalize(saved)
    assert destination == spool.path("queued", saved.sha256)
    assert destination.exists()
    assert not saved.path.exists()


def test_save_stream_rejects_empty(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    with pytest.raises(EmptyUpload):
        spool.save_stream(io.BytesIO(b""), max_bytes=1024)


def test_save_stream_rejects_oversize(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    with pytest.raises(UploadTooLarge):
        spool.save_stream(io.BytesIO(b"x" * 2048), max_bytes=1024)
    assert list(spool.incoming.iterdir()) == []


def test_incomplete_upload_is_detected(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    path = tmp_path / "partial.xle"
    path.write_bytes(b"<Body_xle><Log />")

    assert not spool.is_complete(path)


def test_completeness_allows_trailing_whitespace(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    path = tmp_path / "trailing.xle"
    path.write_bytes(COMPLETE + b"\r\n  \n")

    assert spool.is_complete(path)


def test_move_between_states(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    saved = spool.save_stream(io.BytesIO(COMPLETE), max_bytes=1024)
    spool.finalize(saved)

    moved = spool.move(saved.sha256, "queued", "processed")

    assert moved == spool.path("processed", saved.sha256)
    assert moved is not None and moved.exists()
    assert not spool.path("queued", saved.sha256).exists()
    assert spool.move(saved.sha256, "queued", "processed") == moved


def test_unknown_state_is_rejected(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    with pytest.raises(ValueError, match="unknown spool state"):
        spool.path("bogus", "0" * 64)


def test_cleanup_removes_only_old_files(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    spool.ensure()

    old = spool.processed / ("a" * 64 + ".xle")
    fresh = spool.processed / ("b" * 64 + ".xle")
    old.write_bytes(COMPLETE)
    fresh.write_bytes(COMPLETE)
    past = time.time() - 10 * 86400
    os.utime(old, (past, past))

    removed = spool.cleanup_old(1, state="processed")

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()
    with pytest.raises(ValueError, match="cleanup only applies"):
        spool.cleanup_old(1, state="queued")


def test_writable_creates_layout(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "nested")
    assert spool.writable() is True
    for state in ("incoming", "queued", "processed", "failed"):
        assert spool.path(state, "0" * 64).parent.is_dir()


def test_uploaded_file_that_is_not_xml_is_incomplete(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    saved = spool.save_stream(io.BytesIO(b"not xml"), max_bytes=1024)
    assert not spool.is_complete(saved.path)
