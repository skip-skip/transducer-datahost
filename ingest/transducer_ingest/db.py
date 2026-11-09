"""PostgreSQL connection helpers for the transducer loader.

Configuration comes from the standard ``PG*`` environment variables so the same
code works on the ingest host and in Compose. Passwords are read from the
environment and are never accepted on the command line.
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
            host=os.environ.get("PGHOST", "127.0.0.1"),
            port=int(os.environ.get("PGPORT", "5432")),
            dbname=os.environ.get("PGDATABASE", "transducer"),
            user=os.environ.get("PGUSER", "transducer_ingest"),
            password=os.environ.get("PGPASSWORD", ""),
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
    """Open a connection using the ``PG*`` environment variables."""

    return DatabaseConfig.from_env().connect()


def site_timezone() -> str:
    """Return the interpretation zone stored on new locations."""

    return os.environ.get("SITE_TIMEZONE", DEFAULT_SITE_TIMEZONE)
