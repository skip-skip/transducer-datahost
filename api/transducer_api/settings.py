"""Environment-backed settings for the read-only API."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .db import DatabaseConfig

DEFAULT_SITE_TIMEZONE = "America/New_York"


class SettingsError(RuntimeError):
    pass


def parse_token_list(raw: str | None) -> dict[str, str]:
    tokens: dict[str, str] = {}
    if raw is None or not raw.strip():
        return tokens

    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        label, separator, token = item.partition(":")
        if not separator:
            label, token = "default", label
        label, token = label.strip(), token.strip()
        if not label or not token:
            raise SettingsError("token entries must be non-empty 'label:token' pairs")
        if label in tokens:
            raise SettingsError(f"duplicate token label {label!r}")
        if token in tokens.values():
            raise SettingsError(f"duplicate token value for label {label!r}")
        tokens[label] = token
    return tokens


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ReadSettings:
    database: DatabaseConfig
    tokens: dict[str, str]
    max_rows: int = 10_000
    max_window_days: int = 31
    site_timezone: str = DEFAULT_SITE_TIMEZONE
    cors_origins: tuple[str, ...] = ()
    docs_enabled: bool = False

    @classmethod
    def from_env(cls) -> ReadSettings:
        tokens = parse_token_list(os.environ.get("READ_API_TOKENS"))
        if not tokens:
            raise SettingsError(
                "READ_API_TOKENS must contain at least one label:token entry"
            )

        origins = tuple(
            origin.strip()
            for origin in os.environ.get("API_CORS_ORIGINS", "").split(",")
            if origin.strip()
        )
        return cls(
            database=DatabaseConfig.from_env(),
            tokens=tokens,
            max_rows=_env_int("READ_MAX_ROWS", 10_000),
            max_window_days=_env_int("READ_MAX_WINDOW_DAYS", 31),
            site_timezone=os.environ.get("SITE_TIMEZONE", DEFAULT_SITE_TIMEZONE),
            cors_origins=origins,
            docs_enabled=_env_bool("API_DOCS_ENABLED", False),
        )
