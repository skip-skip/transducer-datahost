import pytest

from transducer_ingest.db import DatabaseConfig

CANONICAL = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "INGEST_USER",
    "INGEST_PASSWORD",
)
LEGACY = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")


def clear_connection_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CANONICAL + LEGACY + ("POSTGRES_USER", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def test_from_env_uses_canonical_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_connection_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_HOST", "db.example")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_DB", "warehouse")
    monkeypatch.setenv("INGEST_USER", "loader")
    monkeypatch.setenv("INGEST_PASSWORD", "loader-secret")

    config = DatabaseConfig.from_env()

    assert config == DatabaseConfig(
        host="db.example",
        port=6543,
        dbname="warehouse",
        user="loader",
        password="loader-secret",
    )


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_connection_env(monkeypatch)

    config = DatabaseConfig.from_env()

    assert config == DatabaseConfig(
        host="127.0.0.1",
        port=5432,
        dbname="transducer",
        user="transducer_ingest",
        password="",
    )


def test_from_env_ignores_legacy_pg_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_connection_env(monkeypatch)
    monkeypatch.setenv("PGHOST", "legacy-host")
    monkeypatch.setenv("PGPORT", "6543")
    monkeypatch.setenv("PGDATABASE", "legacy-db")
    monkeypatch.setenv("PGUSER", "legacy-user")
    monkeypatch.setenv("PGPASSWORD", "legacy-password")

    config = DatabaseConfig.from_env()

    assert config.host == "127.0.0.1"
    assert config.port == 5432
    assert config.dbname == "transducer"
    assert config.user == "transducer_ingest"
    assert config.password == ""


def test_from_env_uses_ingest_role_not_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_connection_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_USER", "transducer_admin")
    monkeypatch.setenv("POSTGRES_PASSWORD", "admin-secret")
    monkeypatch.setenv("INGEST_USER", "transducer_ingest")
    monkeypatch.setenv("INGEST_PASSWORD", "ingest-secret")

    config = DatabaseConfig.from_env()

    assert config.user == "transducer_ingest"
    assert config.password == "ingest-secret"
