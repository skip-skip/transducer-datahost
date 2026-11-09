from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EXPORT_DIR = Path(__file__).resolve().parents[2] / "OneDrive_1_11-9-2026"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def export_dir() -> Path:
    if not EXPORT_DIR.is_dir():
        pytest.skip(f"full export not present at {EXPORT_DIR}")
    return EXPORT_DIR
