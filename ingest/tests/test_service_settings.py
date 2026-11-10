from pathlib import Path

import pytest

from transducer_ingest.db import DatabaseConfig
from transducer_ingest.service.settings import (
    IngestSettings,
    SettingsError,
    parse_token_list,
)

ROLE_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "UPLOAD_USER",
    "UPLOAD_PASSWORD",
    "INGEST_USER",
    "INGEST_PASSWORD",
    "READ_USER",
    "READ_PASSWORD",
    "INGEST_API_TOKENS",
    "SPOOL_DIR",
)


def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ROLE_VARS:
        monkeypatch.delenv(name, raising=False)


def test_parse_token_list_empty() -> None:
    assert parse_token_list(None) == {}
    assert parse_token_list("   ") == {}


def test_parse_token_list_labeled() -> None:
    assert parse_token_list("alice:one,bob:two") == {
        "alice": "one",
        "bob": "two",
    }


def test_parse_token_list_bare_token() -> None:
    assert parse_token_list("secret") == {"default": "secret"}


def test_parse_token_list_rejects_duplicates() -> None:
    with pytest.raises(SettingsError, match="duplicate token label"):
        parse_token_list("alice:one,alice:two")
    with pytest.raises(SettingsError, match="duplicate token value"):
        parse_token_list("alice:same,bob:same")


def test_parse_token_list_rejects_empty_parts() -> None:
    with pytest.raises(SettingsError, match="non-empty"):
        parse_token_list("alice:")


def test_role_configs_use_distinct_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("UPLOAD_USER", "uploader")
    monkeypatch.setenv("UPLOAD_PASSWORD", "upload-secret")
    monkeypatch.setenv("INGEST_USER", "loader")
    monkeypatch.setenv("INGEST_PASSWORD", "ingest-secret")
    monkeypatch.setenv("READ_USER", "reader")
    monkeypatch.setenv("READ_PASSWORD", "read-secret")

    upload = DatabaseConfig.from_env_for_role("upload")
    ingest = DatabaseConfig.from_env_for_role("ingest")
    read = DatabaseConfig.from_env_for_role("read")

    assert (upload.user, upload.password) == ("uploader", "upload-secret")
    assert (ingest.user, ingest.password) == ("loader", "ingest-secret")
    assert (read.user, read.password) == ("reader", "read-secret")
    assert ingest == DatabaseConfig.from_env()


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown database role"):
        DatabaseConfig.from_env_for_role("admin")


def test_ingest_settings_requires_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("UPLOAD_PASSWORD", "upload-secret")

    with pytest.raises(SettingsError, match="INGEST_API_TOKENS"):
        IngestSettings.from_env()

    settings = IngestSettings.from_env(require_tokens=False)
    assert settings.tokens == {}
    assert settings.database.user == "transducer_upload"


def test_ingest_settings_reads_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("UPLOAD_PASSWORD", "upload-secret")
    monkeypatch.setenv("INGEST_API_TOKENS", "desk:token")
    monkeypatch.setenv("SPOOL_DIR", "/srv/spool")
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "2048")
    monkeypatch.setenv("WORKER_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("INGEST_DOCS_ENABLED", "yes")

    settings = IngestSettings.from_env()

    assert settings.tokens == {"desk": "token"}
    assert settings.spool_dir == Path("/srv/spool")
    assert settings.max_upload_bytes == 2048
    assert settings.worker_max_attempts == 5
    assert settings.docs_enabled is True
