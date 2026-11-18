"""Local-only fixtures for the fast dashboard development loops.

This module is not part of the ``transducer_dashboard`` package and is not
copied into the Docker image. It supplies a deterministic fake read client plus
helpers for serving the workspace with ``panel serve`` or the full FastAPI app
with uvicorn.
"""

from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from transducer_dashboard import scenes
from transducer_dashboard.app import create_app
from transducer_dashboard.auth import Session
from transducer_dashboard.read_client import ReadApiError
from transducer_dashboard.settings import DEFAULT_SITE_TIMEZONE, DashboardSettings
from transducer_dashboard.state import StateStore
from transducer_dashboard.workspace import DashboardShell

DEV_TOKEN = "dev"
DEV_LABEL = "dev"
DEV_SECRET = "dev-only-secret-key-1234567890"
DEV_STATE_DIR_ENV = "DASHBOARD_DEV_STATE_DIR"
DEV_REAL_API_ENV = "DASHBOARD_DEV_REAL_API"
DEV_SEED_ENV = "DASHBOARD_DEV_SEED"
DEV_STATE_DIR_NAME = "transducer-dashboard-dev"
POINT_LIMIT = 96

LOCATIONS = [
    {"location_id": 1, "name": "MCMW-04"},
    {"location_id": 2, "name": "MCDMW-04"},
    {"location_id": 3, "name": "OFFSITE-01"},
]
SERIES = [
    {
        "series_id": 10,
        "serial_number": "1096043",
        "channel_no": 1,
        "identification": "LEVEL",
        "unit": "ft",
        "data_kind": "level",
    },
    {
        "series_id": 11,
        "serial_number": "1096043",
        "channel_no": 2,
        "identification": "TEMPERATURE",
        "unit": "C",
        "data_kind": "temperature",
    },
    {
        "series_id": 12,
        "serial_number": "1096044",
        "channel_no": 1,
        "identification": "LEVEL",
        "unit": "ft",
        "data_kind": "level",
    },
]
SERIES_BY_LOCATION = {1: [10, 11], 2: [12], 3: []}


class FakeReadClient:
    """Deterministic in-memory stand-in for ``ReadClient``."""

    def __init__(self, token: str = DEV_TOKEN) -> None:
        self.token = token

    def close(self) -> None:
        return None

    def _check(self, token: str) -> None:
        if token != self.token:
            raise ReadApiError(401, "invalid or expired token")

    def whoami(self, token: str) -> str:
        self._check(token)
        return DEV_LABEL

    def loggers(self, token: str) -> list[dict[str, Any]]:
        self._check(token)
        return []

    def locations(self, token: str) -> list[dict[str, Any]]:
        self._check(token)
        return [dict(row) for row in LOCATIONS]

    def series(
        self,
        token: str,
        *,
        serial: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        self._check(token)
        rows = SERIES
        if serial is not None:
            rows = [row for row in rows if row["serial_number"] == serial]
        return [dict(row) for row in rows[:limit]]

    def series_at_location(
        self, token: str, location_id: int, *, limit: int = 1000
    ) -> list[dict[str, Any]]:
        self._check(token)
        series_ids = SERIES_BY_LOCATION.get(location_id, [])
        rows = [row for row in SERIES if row["series_id"] in series_ids]
        return [dict(row) for row in rows[:limit]]

    def series_detail(self, token: str, series_id: int) -> dict[str, Any]:
        self._check(token)
        for row in SERIES:
            if row["series_id"] == series_id:
                return dict(row)
        raise ReadApiError(404, "series not found")

    def points(
        self,
        token: str,
        series_id: int,
        *,
        start: str,
        end: str,
        points: int,
        tz: str = "local",
        location_id: int | None = None,
    ) -> list[dict[str, Any]]:
        self._check(token)
        if not any(row["series_id"] == series_id for row in SERIES):
            raise ReadApiError(404, "series not found")

        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
        except ValueError as exc:
            raise ReadApiError(400, "start and end must be ISO timestamps") from exc
        if end_dt <= start_dt:
            return []

        count = max(2, min(points, POINT_LIMIT))
        step = (end_dt - start_dt) / (count - 1)
        base = 2.0 + (series_id % 7)
        amplitude = 0.6 + (series_id % 5) * 0.2
        phase = series_id * 0.7

        rows: list[dict[str, Any]] = []
        for index in range(count):
            timestamp = start_dt + step * index
            value = (
                base
                + amplitude * math.sin(index / 6.0 + phase)
                + 0.15 * math.sin(index / 1.7 + phase)
            )
            rows.append(
                {
                    "ts": timestamp.replace(microsecond=0).isoformat(),
                    "value": round(value, 4),
                }
            )
        return rows


def default_state_dir() -> Path:
    configured = os.environ.get(DEV_STATE_DIR_ENV)
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / DEV_STATE_DIR_NAME


def build_settings(state_dir: Path | str) -> DashboardSettings:
    return DashboardSettings(
        read_api_url="http://fake-read-api",
        secret_key=DEV_SECRET,
        state_dir=Path(state_dir),
        site_timezone=os.environ.get("SITE_TIMEZONE", DEFAULT_SITE_TIMEZONE),
        cookie_secure=False,
    )


def seed_enabled() -> bool:
    raw = os.environ.get(DEV_SEED_ENV, "1")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def seed_state(store: StateStore, label: str = DEV_LABEL) -> None:
    """Add a small demo scene when the state has no scenes yet."""

    def operation(data: dict[str, Any]) -> None:
        if data.get("scenes"):
            return

        scene = scenes.create_scene(data, "Demo")
        level = scenes.add_figure(data, scene["id"], 0, 0, range_key="7d")
        scenes.update_figure(
            data,
            scene["id"],
            level["id"],
            title="Water level",
            location_id=1,
            series_id=10,
            range="7d",
        )
        temperature = scenes.add_figure(data, scene["id"], 3, 0, range_key="7d")
        scenes.update_figure(
            data,
            scene["id"],
            temperature["id"],
            title="Temperature",
            location_id=1,
            series_id=11,
            range="7d",
        )
        scenes.set_ui(
            data,
            label,
            selected_scene_id=scene["id"],
            selected_figure_id=level["id"],
        )

    store.mutate(operation)


def build_store(
    state_dir: Path | str, *, seed: bool | None = None
) -> StateStore:
    store = StateStore(state_dir)
    store.ensure()
    if seed is None:
        seed = seed_enabled()
    if seed:
        seed_state(store)
    return store


def build_shell(
    state_dir: Path | str | None = None,
    *,
    label: str = DEV_LABEL,
    token: str = DEV_TOKEN,
    seed: bool | None = None,
) -> DashboardShell:
    directory = Path(state_dir) if state_dir is not None else default_state_dir()
    store = build_store(directory, seed=seed)
    return DashboardShell(
        store,
        FakeReadClient(token),  # type: ignore[arg-type]
        Session(label=label, token=token),
        build_settings(directory),
    )


def build_app(
    state_dir: Path | str | None = None, *, seed: bool | None = None
) -> FastAPI:
    directory = Path(state_dir) if state_dir is not None else default_state_dir()
    build_store(directory, seed=seed)
    return create_app(build_settings(directory), FakeReadClient())  # type: ignore[arg-type]


__all__ = [
    "DEV_LABEL",
    "DEV_REAL_API_ENV",
    "DEV_SECRET",
    "DEV_SEED_ENV",
    "DEV_STATE_DIR_ENV",
    "DEV_TOKEN",
    "FakeReadClient",
    "build_app",
    "build_settings",
    "build_shell",
    "build_store",
    "default_state_dir",
    "seed_enabled",
    "seed_state",
]
