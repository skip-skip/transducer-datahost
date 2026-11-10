"""PostgreSQL connection helpers for the transducer loader and services.

Connection settings come from the canonical ``POSTGRES_*`` environment variables
used by the rest of the repository. Each caller authenticates with its own
least-privilege role:

``ingest``
    ``INGEST_USER`` / ``INGEST_PASSWORD`` -- the loader and worker.
``upload``
    ``UPLOAD_USER`` / ``UPLOAD_PASSWORD`` -- the upload API; queue only.
``read``
    ``READ_USER`` / ``READ_PASSWORD`` -- the read API.

Passwords are read from the environment and are never accepted on the command
line. ``DatabaseConfig.from_env()`` keeps its historical meaning (the ingest
role) so the existing CLI and tests are unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psycopg
from psycopg import Connection

DEFAULT_SITE_TIMEZONE = "America/New_York"

# role -> (user variable, password variable, default user)
_ROLE_ENV: dict[str, tuple[str, str, str]] = {
    "ingest": ("INGEST_USER", "INGEST_PASSWORD", "transducer_ingest"),
    "upload": ("UPLOAD_USER", "UPLOAD_PASSWORD", "transducer_upload"),
    "read": ("READ_USER", "READ_PASSWORD", "transducer_read"),
}


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> DatabaseConfig:
        """Return the ingest-role configuration (historical default)."""

        return cls.from_env_for_role("ingest")

    @classmethod
    def from_env_for_role(cls, role: str) -> DatabaseConfig:
        """Return the configuration for ``ingest``, ``upload``, or ``read``."""

        try:
            user_var, password_var, default_user = _ROLE_ENV[role]
        except KeyError as exc:
            raise ValueError(f"unknown database role {role!r}") from exc

        return cls(
            host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "transducer"),
            user=os.environ.get(user_var, default_user),
            password=os.environ.get(password_var, ""),
        )

    def connect(self) -> Connection:
        return psycopg.connect(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=self.user,
            password=self.password,
            autocommit=False,
        )


def connect() -> Connection:
    """Open a connection using the canonical ``POSTGRES_*`` environment variables."""

    return DatabaseConfig.from_env().connect()


def connect_for_role(role: str) -> Connection:
    """Open a connection as ``ingest``, ``upload``, or ``read``."""

    return DatabaseConfig.from_env_for_role(role).connect()


def site_timezone() -> str:
    """Return the interpretation zone stored on new locations."""

    return os.environ.get("SITE_TIMEZONE", DEFAULT_SITE_TIMEZONE)
