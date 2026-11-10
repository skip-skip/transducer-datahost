"""Environment-backed settings for the ingest API and worker.

All secrets stay in the process environment. The module deliberately has no
FastAPI dependency so the worker, migration runner, and tests can import it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..db import DEFAULT_SITE_TIMEZONE, DatabaseConfig


class SettingsError(RuntimeError):
    """Raised when required service configuration is missing or invalid."""


def parse_token_list(raw: str | None) -> dict[str, str]:
    """Parse ``label:token,label2:token2`` into an ordered mapping.

    A bare token is accepted as ``default`` for ad hoc use. Labels and tokens
    must be unique and non-empty. Tokens are compared in constant time by the
    auth layer, not here.
    """

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
        label = label.strip()
        token = token.strip()
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


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number") from exc


@dataclass(frozen=True)
class IngestSettings:
    database: DatabaseConfig
    tokens: dict[str, str]
    spool_dir: Path
    max_upload_bytes: int = 10 * 1024 * 1024
    site_timezone: str = DEFAULT_SITE_TIMEZONE
    worker_poll_seconds: float = 2.0
    worker_lock_timeout_seconds: int = 600
    worker_max_attempts: int = 3
    worker_backoff_seconds: int = 30
    worker_heartbeat_timeout_seconds: int = 60
    spool_processed_retention_days: int = 30
    spool_failed_retention_days: int = 90
    docs_enabled: bool = False
    worker_id: str = field(default="")

    @classmethod
    def from_env(
        cls,
        *,
        role: str = "upload",
        require_tokens: bool = True,
    ) -> IngestSettings:
        tokens = parse_token_list(os.environ.get("INGEST_API_TOKENS"))
        if require_tokens and not tokens:
            raise SettingsError(
                "INGEST_API_TOKENS must contain at least one label:token entry"
            )

        spool_dir = Path(os.environ.get("SPOOL_DIR", "/var/lib/transducer"))
        return cls(
            database=DatabaseConfig.from_env_for_role(role),
            tokens=tokens,
            spool_dir=spool_dir,
            max_upload_bytes=_env_int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024),
            site_timezone=os.environ.get("SITE_TIMEZONE", DEFAULT_SITE_TIMEZONE),
            worker_poll_seconds=_env_float("WORKER_POLL_SECONDS", 2.0),
            worker_lock_timeout_seconds=_env_int(
                "WORKER_LOCK_TIMEOUT_SECONDS", 600
            ),
            worker_max_attempts=_env_int("WORKER_MAX_ATTEMPTS", 3),
            worker_backoff_seconds=_env_int("WORKER_BACKOFF_SECONDS", 30),
            worker_heartbeat_timeout_seconds=_env_int(
                "WORKER_HEARTBEAT_TIMEOUT_SECONDS", 60
            ),
            spool_processed_retention_days=_env_int(
                "SPOOL_PROCESSED_RETENTION_DAYS", 30
            ),
            spool_failed_retention_days=_env_int(
                "SPOOL_FAILED_RETENTION_DAYS", 90
            ),
            docs_enabled=_env_bool("INGEST_DOCS_ENABLED", False),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
