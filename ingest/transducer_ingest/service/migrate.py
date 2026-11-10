"""Apply versioned SQL migrations as the admin role.

The Postgres image only runs ``db/init`` on an empty volume. This runner is the
supported path for an existing database: it creates/updates the application
roles, records applied files in ``public.schema_migrations``, and applies each
``db/migrations/*.sql`` file in its own transaction.

Run from the Compose ``migrate`` service::

    python -m transducer_ingest.service.migrate

or locally::

    python scripts/migrate.py
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg
from psycopg import Connection, sql

from .settings import SettingsError

ROLES = (
    (
        "transducer_upload",
        "UPLOAD_USER",
        "UPLOAD_PASSWORD",
        "pg_catalog, ingest",
    ),
    (
        "transducer_ingest",
        "INGEST_USER",
        "INGEST_PASSWORD",
        "pg_catalog, transducer, ingest",
    ),
    (
        "transducer_read",
        "READ_USER",
        "READ_PASSWORD",
        "pg_catalog, transducer",
    ),
)


def default_migrations_dir() -> Path:
    configured = os.environ.get("MIGRATIONS_DIR")
    if configured:
        return Path(configured)
    container = Path("/app/db/migrations")
    if container.is_dir():
        return container
    # .../ingest/transducer_ingest/service/migrate.py -> repository root
    return Path(__file__).resolve().parents[3] / "db" / "migrations"


def admin_connect() -> Connection:
    password = os.environ.get("POSTGRES_PASSWORD", "")
    if not password:
        raise SettingsError("POSTGRES_PASSWORD must be set for migrations")
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "transducer"),
        user=os.environ.get("POSTGRES_USER", "transducer_admin"),
        password=password,
        autocommit=False,
    )


def ensure_roles(conn: Connection) -> list[str]:
    """Create or update the three application roles from the environment."""

    changed: list[str] = []
    conn.execute("SET password_encryption = 'scram-sha-256'")

    for role, user_var, password_var, search_path in ROLES:
        user = os.environ.get(user_var, role)
        password = os.environ.get(password_var, "")
        if not password:
            raise SettingsError(
                f"{password_var} must be set to create or update {user}"
            )
        if user != role:
            raise SettingsError(
                f"{user_var}={user!r} is not supported; the database role is {role}"
            )

        exists = conn.execute(
            "SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (role,)
        ).fetchone()
        if exists is None:
            conn.execute(
                sql.SQL("CREATE ROLE {}").format(sql.Identifier(role))
            )
        # PostgreSQL only accepts a string literal here (``PASSWORD 'x'``),
        # not a bind parameter: the extended protocol rejects ``PASSWORD $1``
        # with "syntax error at or near \"$1\"". Render the value client-side
        # with sql.Literal so quoting and escaping stay correct.
        conn.execute(
            sql.SQL(
                "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} SET search_path = {}").format(
                sql.Identifier(role), sql.SQL(search_path)
            )
        )
        changed.append(role)

    return changed


def ensure_migrations_table(conn: Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version    text PRIMARY KEY,
            checksum   text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def apply_migrations(conn: Connection, directory: Path) -> tuple[list[str], list[str]]:
    applied: list[str] = []
    skipped: list[str] = []

    for path in sorted(directory.glob("*.sql")):
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        row = conn.execute(
            "SELECT checksum FROM public.schema_migrations WHERE version = %s",
            (path.name,),
        ).fetchone()
        if row is not None:
            if row[0] != checksum:
                raise RuntimeError(
                    f"migration {path.name} was already applied with a different "
                    "checksum; create a new migration instead of editing it"
                )
            skipped.append(path.name)
            continue

        text = path.read_text(encoding="utf-8")
        with conn.transaction():
            conn.execute(text)
            conn.execute(
                "INSERT INTO public.schema_migrations (version, checksum) "
                "VALUES (%s, %s)",
                (path.name, checksum),
            )
        applied.append(path.name)

    return applied, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply transducer DB migrations.")
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=default_migrations_dir(),
        help="Directory containing ordered .sql files",
    )
    parser.add_argument(
        "--skip-roles",
        action="store_true",
        help="Do not create or update application roles",
    )
    args = parser.parse_args(argv)

    if not args.migrations_dir.is_dir():
        print(f"migrations directory not found: {args.migrations_dir}", file=sys.stderr)
        return 2

    with admin_connect() as conn:
        if not args.skip_roles:
            roles = ensure_roles(conn)
            print("roles ensured: " + ", ".join(roles))
        ensure_migrations_table(conn)
        applied, skipped = apply_migrations(conn, args.migrations_dir)
        conn.commit()

    if applied:
        print("applied: " + ", ".join(applied))
    if skipped:
        print("already applied: " + ", ".join(skipped))
    if not applied and not skipped:
        print("no migrations found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
