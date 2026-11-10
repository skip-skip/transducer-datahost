#!/usr/bin/env python3
"""Thin wrapper for the packaged migration runner.

Usage::

    python scripts/migrate.py [--migrations-dir PATH] [--skip-roles]

Requires the admin ``POSTGRES_*`` variables plus ``UPLOAD_PASSWORD``,
``INGEST_PASSWORD``, and ``READ_PASSWORD`` unless ``--skip-roles`` is used.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INGEST_DIR = REPO_ROOT / "ingest"
if str(INGEST_DIR) not in sys.path:
    sys.path.insert(0, str(INGEST_DIR))

from transducer_ingest.service.migrate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
