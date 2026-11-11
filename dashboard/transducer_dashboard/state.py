"""Durable dashboard state stored as a single JSON file on a Docker volume.

The dashboard deliberately keeps its state out of PostgreSQL. Every read-modify-
write cycle takes a file lock, writes a temporary file, fsyncs it, keeps the
previous version as ``state.json.bak``, and atomically replaces ``state.json``.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from filelock import FileLock

STATE_VERSION = 1
STATE_FILENAME = "state.json"
BACKUP_FILENAME = "state.json.bak"
LOCK_FILENAME = ".state.lock"


class StateError(RuntimeError):
    pass


def default_ui() -> dict[str, Any]:
    return {
        "view": "personal",
        "personal": {"selected_figure_id": None, "folders_collapsed": False},
        "shared": {"selected_figure_id": None, "folders_collapsed": False},
        "tz": "local",
    }


def default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "shared": {"figures": []},
        "personal": {},
    }


def _ensure_figure(figure: Any, where: str) -> dict[str, Any]:
    if not isinstance(figure, dict):
        raise StateError(f"{where} must be an object")
    for key in ("id", "name"):
        if not isinstance(figure.get(key), str) or not figure[key].strip():
            raise StateError(f"{where}.{key} must be a non-empty string")
    series_id = figure.get("series_id")
    if series_id is not None and not isinstance(series_id, int):
        raise StateError(f"{where}.series_id must be an integer or null")
    # Optional since location-based figure selection; figures saved before it
    # (or explicitly un-scoped) carry no location and mean "all locations".
    location_id = figure.get("location_id")
    if location_id is not None and not isinstance(location_id, int):
        raise StateError(f"{where}.location_id must be an integer or null")
    position = figure.get("position", 0)
    if not isinstance(position, int):
        raise StateError(f"{where}.position must be an integer")
    config = figure.get("config", {})
    if not isinstance(config, dict):
        raise StateError(f"{where}.config must be an object")
    return figure


def validate_state(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise StateError("state must be a JSON object")
    if data.get("version") != STATE_VERSION:
        raise StateError(f"unsupported state version {data.get('version')!r}")

    shared = data.get("shared")
    if not isinstance(shared, dict) or not isinstance(shared.get("figures"), list):
        raise StateError("state.shared.figures must be a list")
    for index, figure in enumerate(shared["figures"]):
        _ensure_figure(figure, f"state.shared.figures[{index}]")

    personal = data.get("personal")
    if not isinstance(personal, dict):
        raise StateError("state.personal must be an object")
    for label, entry in personal.items():
        if not isinstance(label, str):
            raise StateError("state.personal keys must be strings")
        if not isinstance(entry, dict):
            raise StateError(f"state.personal[{label!r}] must be an object")
        if not isinstance(entry.get("figures"), list):
            raise StateError(f"state.personal[{label!r}].figures must be a list")
        for index, figure in enumerate(entry["figures"]):
            _ensure_figure(figure, f"state.personal[{label!r}].figures[{index}]")
        ui = entry.get("ui")
        if ui is not None and not isinstance(ui, dict):
            raise StateError(f"state.personal[{label!r}].ui must be an object")

    return data


class StateStore:
    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.path = self.directory / STATE_FILENAME
        self.backup_path = self.directory / BACKUP_FILENAME
        self.lock_path = self.directory / LOCK_FILENAME
        self._lock = FileLock(str(self.lock_path))

    def ensure(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        with self._lock:
            if not self.path.exists():
                self._write(default_state())

    def load(self) -> dict[str, Any]:
        with self._lock:
            return self._load_unlocked()

    def mutate(
        self, operation: Callable[[dict[str, Any]], Any]
    ) -> tuple[dict[str, Any], Any]:
        with self._lock:
            data = self._load_unlocked()
            result = operation(data)
            validate_state(data)
            self._write(data)
            return data, result

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return default_state()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StateError(f"cannot read {self.path}: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StateError(f"{self.path} is not valid JSON: {exc}") from exc
        return validate_state(data)

    def _write(self, data: dict[str, Any]) -> None:
        validate_state(data)
        text = json.dumps(data, indent=2, sort_keys=False) + "\n"
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        if self.path.exists():
            try:
                shutil.copy2(self.path, self.backup_path)
            except OSError:
                pass

        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
