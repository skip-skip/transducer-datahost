"""Tests for the migration runner's role SQL.

PostgreSQL's ``ALTER ROLE ... PASSWORD`` accepts only a string literal, not a
bind parameter: the extended protocol rejects ``PASSWORD $1`` with
``syntax error at or near "$1"``. The runner must therefore render the
password client-side with ``sql.Literal``. These tests use a recording
connection, so no database is required.
"""

from __future__ import annotations

from typing import Any

import pytest
from psycopg import sql

from transducer_ingest.service import migrate
from transducer_ingest.service.settings import SettingsError


class RecordingConnection:
    """Minimal stand-in that records rendered SQL statements."""

    def __init__(self, role_exists: bool = True) -> None:
        self.role_exists = role_exists
        self.statements: list[str] = []
        self._row: tuple[int, ...] | None = None

    def execute(self, query: Any, params: Any = None) -> RecordingConnection:
        rendered = (
            query.as_string(None) if isinstance(query, sql.Composed) else query
        )
        self.statements.append(rendered)
        self._row = (
            (1,)
            if "FROM pg_catalog.pg_roles" in rendered and self.role_exists
            else None
        )
        return self

    def fetchone(self) -> tuple[int, ...] | None:
        return self._row


def set_role_passwords(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPLOAD_PASSWORD", "upload-secret")
    monkeypatch.setenv("INGEST_PASSWORD", "ingest-secret")
    monkeypatch.setenv("READ_PASSWORD", "read-secret")


def test_role_passwords_are_rendered_as_literals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_role_passwords(monkeypatch)
    conn = RecordingConnection()

    changed = migrate.ensure_roles(conn)

    assert changed == [
        "transducer_upload",
        "transducer_ingest",
        "transducer_read",
    ]
    script = "\n".join(conn.statements)
    assert "$1" not in script
    assert "PASSWORD %s" not in script
    assert "PASSWORD 'upload-secret'" in script
    assert "PASSWORD 'ingest-secret'" in script
    assert "PASSWORD 'read-secret'" in script
    assert 'ALTER ROLE "transducer_upload"' in script


def test_password_quotes_are_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    set_role_passwords(monkeypatch)
    monkeypatch.setenv("UPLOAD_PASSWORD", "it's secret")
    conn = RecordingConnection()

    migrate.ensure_roles(conn)

    script = "\n".join(conn.statements)
    assert "PASSWORD 'it''s secret'" in script


def test_missing_role_password_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_role_passwords(monkeypatch)
    monkeypatch.delenv("UPLOAD_PASSWORD")

    with pytest.raises(SettingsError, match="UPLOAD_PASSWORD"):
        migrate.ensure_roles(RecordingConnection())


def test_missing_role_is_created(monkeypatch: pytest.MonkeyPatch) -> None:
    set_role_passwords(monkeypatch)
    conn = RecordingConnection(role_exists=False)

    migrate.ensure_roles(conn)

    script = "\n".join(conn.statements)
    assert 'CREATE ROLE "transducer_upload"' in script
    assert 'CREATE ROLE "transducer_ingest"' in script
    assert 'CREATE ROLE "transducer_read"' in script
