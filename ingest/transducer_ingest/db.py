"""PostgreSQL connection helpers for the transducer loader.

Connection settings come from the canonical ``POSTGRES_*`` environment variables
used by the rest of the repository. The loader authenticates with the ingest
role (``INGEST_USER`` / ``INGEST_PASSWORD``) so it never needs the admin
password. Passwords are read from the environment and are never accepted on the
command line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psycopg
from psycopg import Connection

DEFAULT_SITE_TIMEZONE = "America/New_York"


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> DatabaseConfig:
        return cls(
            host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "transducer"),
            user=os.environ.get("INGEST_USER", "transducer_ingest"),
            password=os.environ.get("INGEST_PASSWORD", ""),
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


def site_timezone() -> str:
    """Return the interpretation zone stored on new locations."""

    return os.environ.get("SITE_TIMEZONE", DEFAULT_SITE_TIMEZONE)
