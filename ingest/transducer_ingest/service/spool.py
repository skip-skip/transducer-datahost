"""Spool storage for uploaded .xle files.

Layout::

    <spool>/incoming/<uuid>.part   upload in progress
    <spool>/queued/<sha256>.xle    waiting for the worker
    <spool>/processed/<sha256>.xle successfully processed
    <spool>/failed/<sha256>.xle    terminal failure, kept for review

Files are named by content hash, so a re-upload cannot create a second copy
and an orphan can be reconciled from its filename.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

COMPLETENESS_SUFFIX = b"</Body_xle>"
TAIL_BYTES = 1024 * 1024
STATES = ("incoming", "queued", "processed", "failed")


class SpoolError(RuntimeError):
    """Base class for spool failures."""


class UploadTooLarge(SpoolError):
    pass


class EmptyUpload(SpoolError):
    pass


class IncompleteUpload(SpoolError):
    pass


@dataclass(frozen=True)
class SavedUpload:
    path: Path
    sha256: str
    byte_size: int


class Spool:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.incoming = self.root / "incoming"
        self.queued = self.root / "queued"
        self.processed = self.root / "processed"
        self.failed = self.root / "failed"

    def ensure(self) -> None:
        for directory in (self.incoming, self.queued, self.processed, self.failed):
            directory.mkdir(parents=True, exist_ok=True)

    def path(self, state: str, sha256: str) -> Path:
        if state not in STATES:
            raise ValueError(f"unknown spool state {state!r}")
        return getattr(self, state) / f"{sha256}.xle"

    def save_stream(self, stream: BinaryIO, max_bytes: int) -> SavedUpload:
        """Write a stream to incoming while hashing and enforcing the limit."""

        self.ensure()
        temp = self.incoming / f"{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with temp.open("wb") as handle:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadTooLarge(
                            f"upload exceeds {max_bytes} bytes"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            if size == 0:
                raise EmptyUpload("upload is empty")
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        return SavedUpload(path=temp, sha256=digest.hexdigest(), byte_size=size)

    def is_complete(self, path: Path) -> bool:
        """Return True when the final non-whitespace bytes are ``</Body_xle>``."""

        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                if size > TAIL_BYTES:
                    handle.seek(size - TAIL_BYTES)
                tail = handle.read()
        except OSError:
            return False
        return tail.rstrip().endswith(COMPLETENESS_SUFFIX)

    def finalize(self, saved: SavedUpload) -> Path:
        """Atomically move a validated upload into ``queued``."""

        destination = self.queued / f"{saved.sha256}.xle"
        os.replace(saved.path, destination)
        return destination

    def move(self, sha256: str, source: str, destination: str) -> Path | None:
        """Move a file between states, tolerating a missing source."""

        src = self.path(source, sha256)
        dst = self.path(destination, sha256)
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
            return dst
        return dst if dst.exists() else None

    def queued_files(self) -> list[Path]:
        if not self.queued.is_dir():
            return []
        return sorted(self.queued.glob("*.xle"))

    def cleanup_old(self, retention_days: int, *, state: str) -> int:
        """Delete files in ``processed`` or ``failed`` older than retention."""

        if state not in ("processed", "failed"):
            raise ValueError("cleanup only applies to processed or failed")
        cutoff = time.time() - retention_days * 86400
        removed = 0
        for path in getattr(self, state).glob("*.xle"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def writable(self) -> bool:
        try:
            self.ensure()
            probe = self.incoming / f".probe-{uuid.uuid4().hex}"
            probe.write_bytes(b"")
            probe.unlink()
            return True
        except OSError:
            return False

    def disk_free_bytes(self) -> int:
        try:
            return shutil.disk_usage(self.root).free
        except OSError:
            return 0
