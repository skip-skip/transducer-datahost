"""Read-role PostgreSQL connection configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

import psycopg
from psycopg import Connection


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
            user=os.environ.get("READ_USER", "transducer_read"),
            password=os.environ.get("READ_PASSWORD", ""),
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
