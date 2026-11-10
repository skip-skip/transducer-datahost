import pytest

from transducer_api.settings import (
    ReadSettings,
    SettingsError,
    parse_token_list,
)

ENV_VARS = (
    "READ_API_TOKENS",
    "READ_USER",
    "READ_PASSWORD",
    "READ_MAX_ROWS",
    "READ_MAX_WINDOW_DAYS",
    "API_CORS_ORIGINS",
    "API_DOCS_ENABLED",
    "SITE_TIMEZONE",
)


def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_parse_token_list() -> None:
    assert parse_token_list(None) == {}
    assert parse_token_list("a:one,b:two") == {"a": "one", "b": "two"}
    assert parse_token_list("bare") == {"default": "bare"}


def test_parse_token_list_rejects_duplicates() -> None:
    with pytest.raises(SettingsError, match="duplicate token label"):
        parse_token_list("a:one,a:two")
    with pytest.raises(SettingsError, match="duplicate token value"):
        parse_token_list("a:same,b:same")


def test_settings_require_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("READ_PASSWORD", "read-secret")

    with pytest.raises(SettingsError, match="READ_API_TOKENS"):
        ReadSettings.from_env()


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("READ_API_TOKENS", "analyst:token")
    monkeypatch.setenv("READ_PASSWORD", "read-secret")

    settings = ReadSettings.from_env()

    assert settings.tokens == {"analyst": "token"}
    assert settings.max_rows == 10_000
    assert settings.max_window_days == 31
    assert settings.cors_origins == ()
    assert settings.docs_enabled is False
    assert settings.database.user == "transducer_read"
    assert settings.database.password == "read-secret"


def test_settings_reads_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("READ_API_TOKENS", "analyst:token")
    monkeypatch.setenv("READ_MAX_ROWS", "250")
    monkeypatch.setenv("READ_MAX_WINDOW_DAYS", "7")
    monkeypatch.setenv("API_CORS_ORIGINS", "https://dash.example, https://ops.example")
    monkeypatch.setenv("API_DOCS_ENABLED", "true")

    settings = ReadSettings.from_env()

    assert settings.max_rows == 250
    assert settings.max_window_days == 7
    assert settings.cors_origins == (
        "https://dash.example",
        "https://ops.example",
    )
    assert settings.docs_enabled is True


def test_settings_reject_non_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("READ_API_TOKENS", "analyst:token")
    monkeypatch.setenv("READ_MAX_ROWS", "many")

    with pytest.raises(SettingsError, match="READ_MAX_ROWS"):
        ReadSettings.from_env()
