"""Environment-backed settings for the dashboard service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SITE_TIMEZONE = "America/New_York"
DEFAULT_STATE_DIR = "/var/lib/transducer-dashboard"
MIN_SECRET_LENGTH = 16


class SettingsError(RuntimeError):
    pass


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


def _env_list(name: str) -> frozenset[str]:
    raw = os.environ.get(name, "")
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class DashboardSettings:
    read_api_url: str
    secret_key: str
    state_dir: Path
    session_max_age: int = 28_800
    default_window_days: int = 7
    max_points: int = 2_000
    shared_editors: frozenset[str] = frozenset()
    site_timezone: str = DEFAULT_SITE_TIMEZONE
    cookie_secure: bool = True
    request_timeout: float = 15.0

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"

    @classmethod
    def from_env(cls) -> DashboardSettings:
        secret_key = os.environ.get("DASHBOARD_SECRET_KEY", "").strip()
        if not secret_key:
            raise SettingsError("DASHBOARD_SECRET_KEY must be set")
        if len(secret_key) < MIN_SECRET_LENGTH:
            raise SettingsError(
                f"DASHBOARD_SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters"
            )

        return cls(
            read_api_url=os.environ.get("READ_API_URL", "http://read-api:8080").rstrip("/"),
            secret_key=secret_key,
            state_dir=Path(
                os.environ.get("DASHBOARD_STATE_DIR", DEFAULT_STATE_DIR)
            ),
            session_max_age=_env_int("DASHBOARD_SESSION_MAX_AGE", 28_800),
            default_window_days=_env_int("DASHBOARD_DEFAULT_WINDOW_DAYS", 7),
            max_points=_env_int("DASHBOARD_MAX_POINTS", 2_000),
            shared_editors=_env_list("DASHBOARD_SHARED_EDITORS"),
            site_timezone=os.environ.get("SITE_TIMEZONE", DEFAULT_SITE_TIMEZONE),
            cookie_secure=_env_bool("DASHBOARD_COOKIE_SECURE", True),
            request_timeout=float(os.environ.get("DASHBOARD_REQUEST_TIMEOUT", "15")),
        )
