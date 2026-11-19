"""Durable dashboard state stored as a single JSON file on a Docker volume.

The dashboard deliberately keeps its state out of PostgreSQL. Every read-modify-
write cycle takes a process-wide re-entrant lock and a file lock, writes a
temporary file, fsyncs it, keeps the previous version as ``state.json.bak``, and
atomically replaces ``state.json``.

Version 1 documents (personal/shared figures) are archived to ``state.v1.json``
and replaced with a fresh version 2 document. Version 1 personal figures are
never published into the shared scenes of version 2.

An intermediate version 2 draft used top-level ``shared``/``personal`` keys
instead of ``scenes``/``ui``. Those documents are archived to
``state.v2-legacy.json`` and replaced with a fresh version 2 document so a
container left on the abandoned schema still starts.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from filelock import FileLock

STATE_VERSION = 2
STATE_FILENAME = "state.json"
BACKUP_FILENAME = "state.json.bak"
ARCHIVE_V1_FILENAME = "state.v1.json"
ARCHIVE_V2_LEGACY_FILENAME = "state.v2-legacy.json"
LOCK_FILENAME = ".state.lock"

# Grid configuration for the MVP. Per-scene grids are a follow-up.
GRID_COLUMNS = 12
GRID_ROWS = 6
GRID_ROW_HEIGHT = 220
MIN_FIGURE_WIDTH = 1
MIN_FIGURE_HEIGHT = 1
# Non-interactive callers (demo fixtures, scripts) still get a readable figure.
# The canvas creates whatever span the user paints, down to one cell.
DEFAULT_FIGURE_WIDTH = 3
DEFAULT_FIGURE_HEIGHT = 2

RANGES = ("24h", "7d", "30d")
DEFAULT_RANGE = "7d"

STATE_KEYS = frozenset({"version", "scenes", "ui"})
SCENE_KEYS = frozenset({"id", "name", "figures", "created_at", "updated_at"})
FIGURE_KEYS = frozenset(
    {
        "id",
        "title",
        "location_id",
        "series_id",
        "range",
        "layout",
        "created_at",
        "updated_at",
    }
)
LAYOUT_KEYS = frozenset({"x", "y", "w", "h"})
UI_KEYS = frozenset(
    {"selected_scene_id", "selected_figure_id", "scenes_tray_collapsed"}
)


class StateError(RuntimeError):
    pass


def default_ui() -> dict[str, Any]:
    return {
        "selected_scene_id": None,
        "selected_figure_id": None,
        "scenes_tray_collapsed": False,
    }


def default_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "scenes": [], "ui": {}}


def _check_keys(
    value: Mapping[Any, Any],
    allowed: frozenset[str],
    where: str,
    *,
    required: bool,
) -> None:
    keys = set(value)
    unknown = sorted(str(key) for key in keys - allowed)
    if unknown:
        raise StateError(f"{where} has unknown keys: {', '.join(unknown)}")
    if required:
        missing = sorted(allowed - keys)
        if missing:
            raise StateError(f"{where} is missing keys: {', '.join(missing)}")


def _non_empty_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateError(f"{where} must be a non-empty string")
    return value


def _optional_string(value: Any, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise StateError(f"{where} must be a non-empty string or null")
    return value


def _integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StateError(f"{where} must be an integer")
    return value


def _optional_int(value: Any, where: str) -> int | None:
    if value is None:
        return None
    return _integer(value, where)


def validate_layout(layout: Any, where: str = "layout") -> None:
    if not isinstance(layout, dict):
        raise StateError(f"{where} must be an object")
    _check_keys(layout, LAYOUT_KEYS, where, required=True)

    x = _integer(layout["x"], f"{where}.x")
    y = _integer(layout["y"], f"{where}.y")
    width = _integer(layout["w"], f"{where}.w")
    height = _integer(layout["h"], f"{where}.h")

    if x < 0 or y < 0:
        raise StateError(f"{where}.x and {where}.y must be non-negative")
    if width < MIN_FIGURE_WIDTH:
        raise StateError(f"{where}.w must be at least {MIN_FIGURE_WIDTH}")
    if height < MIN_FIGURE_HEIGHT:
        raise StateError(f"{where}.h must be at least {MIN_FIGURE_HEIGHT}")
    if x + width > GRID_COLUMNS:
        raise StateError(f"{where} must fit within {GRID_COLUMNS} columns")
    if y + height > GRID_ROWS:
        raise StateError(f"{where} must fit within {GRID_ROWS} rows")


def _validate_figure(figure: Any, where: str) -> None:
    if not isinstance(figure, dict):
        raise StateError(f"{where} must be an object")
    _check_keys(figure, FIGURE_KEYS, where, required=True)

    _non_empty_string(figure["id"], f"{where}.id")
    _non_empty_string(figure["title"], f"{where}.title")
    _optional_int(figure["location_id"], f"{where}.location_id")
    _optional_int(figure["series_id"], f"{where}.series_id")
    if figure["range"] not in RANGES:
        raise StateError(f"{where}.range must be one of {', '.join(RANGES)}")
    validate_layout(figure["layout"], f"{where}.layout")
    _non_empty_string(figure["created_at"], f"{where}.created_at")
    _non_empty_string(figure["updated_at"], f"{where}.updated_at")


def _validate_scene(scene: Any, where: str) -> None:
    if not isinstance(scene, dict):
        raise StateError(f"{where} must be an object")
    _check_keys(scene, SCENE_KEYS, where, required=True)

    _non_empty_string(scene["id"], f"{where}.id")
    _non_empty_string(scene["name"], f"{where}.name")
    _non_empty_string(scene["created_at"], f"{where}.created_at")
    _non_empty_string(scene["updated_at"], f"{where}.updated_at")

    figures = scene["figures"]
    if not isinstance(figures, list):
        raise StateError(f"{where}.figures must be a list")
    seen: set[str] = set()
    for index, figure in enumerate(figures):
        figure_where = f"{where}.figures[{index}]"
        _validate_figure(figure, figure_where)
        figure_id = figure["id"]
        if figure_id in seen:
            raise StateError(f"{figure_where}.id is a duplicate")
        seen.add(figure_id)


def validate_ui(entry: Any, where: str = "ui") -> None:
    if not isinstance(entry, dict):
        raise StateError(f"{where} must be an object")
    _check_keys(entry, UI_KEYS, where, required=False)

    if "selected_scene_id" in entry:
        _optional_string(entry["selected_scene_id"], f"{where}.selected_scene_id")
    if "selected_figure_id" in entry:
        _optional_string(entry["selected_figure_id"], f"{where}.selected_figure_id")
    if "scenes_tray_collapsed" in entry and not isinstance(
        entry["scenes_tray_collapsed"], bool
    ):
        raise StateError(f"{where}.scenes_tray_collapsed must be a boolean")


def _is_legacy_v2(data: Mapping[Any, Any]) -> bool:
    """Return True for the abandoned v2 draft with shared/personal scopes."""

    version = data.get("version")
    return (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version == STATE_VERSION
        and ("shared" in data or "personal" in data)
        and "scenes" not in data
        and "ui" not in data
    )


def _fit_layout_rows(data: dict[str, Any]) -> bool:
    """Pull legacy layouts into the fixed grid instead of rejecting them.

    Earlier versions let the grid grow with the figures. A document from one of
    those versions may contain figures below ``GRID_ROWS``. Move each one up
    into the fixed canvas, shrinking it only when it is taller than the whole
    grid, and write the normalized document back.
    """

    changed = False
    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        return False
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        figures = scene.get("figures")
        if not isinstance(figures, list):
            continue
        for figure in figures:
            if not isinstance(figure, dict):
                continue
            layout = figure.get("layout")
            if not isinstance(layout, dict):
                continue
            y = layout.get("y")
            height = layout.get("h")
            if (
                isinstance(y, bool)
                or not isinstance(y, int)
                or isinstance(height, bool)
                or not isinstance(height, int)
                or y < 0
                or height < 1
                or y + height <= GRID_ROWS
            ):
                continue
            fitted_height = min(height, GRID_ROWS)
            layout["h"] = fitted_height
            layout["y"] = GRID_ROWS - fitted_height
            changed = True
    return changed


def validate_state(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise StateError("state must be a JSON object")
    if data.get("version") != STATE_VERSION:
        raise StateError(f"unsupported state version {data.get('version')!r}")
    _check_keys(data, STATE_KEYS, "state", required=True)

    scenes = data["scenes"]
    if not isinstance(scenes, list):
        raise StateError("state.scenes must be a list")

    scene_ids: set[str] = set()
    figure_ids: set[str] = set()
    for index, scene in enumerate(scenes):
        where = f"state.scenes[{index}]"
        _validate_scene(scene, where)
        scene_id = scene["id"]
        if scene_id in scene_ids:
            raise StateError(f"{where}.id is a duplicate")
        scene_ids.add(scene_id)
        for figure in scene["figures"]:
            figure_id = figure["id"]
            if figure_id in figure_ids:
                raise StateError(f"figure id {figure_id!r} is a duplicate")
            figure_ids.add(figure_id)

    ui = data["ui"]
    if not isinstance(ui, dict):
        raise StateError("state.ui must be an object")
    for label, entry in ui.items():
        if not isinstance(label, str) or not label.strip():
            raise StateError("state.ui keys must be non-empty strings")
        validate_ui(entry, f"state.ui[{label!r}]")

    return data


class StateStore:
    """Load, validate, and atomically persist the dashboard state file."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.path = self.directory / STATE_FILENAME
        self.backup_path = self.directory / BACKUP_FILENAME
        self.archive_v1_path = self.directory / ARCHIVE_V1_FILENAME
        self.archive_v2_legacy_path = self.directory / ARCHIVE_V2_LEGACY_FILENAME
        self.lock_path = self.directory / LOCK_FILENAME
        self._lock = FileLock(str(self.lock_path))
        self._mutex = threading.RLock()

    def ensure(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        with self._mutex, self._lock:
            if not self.path.exists():
                self._write_unlocked(default_state())
            else:
                self._load_unlocked()

    def load(self) -> dict[str, Any]:
        with self._mutex, self._lock:
            return self._load_unlocked()

    def mutate(
        self, operation: Callable[[dict[str, Any]], Any]
    ) -> tuple[dict[str, Any], Any]:
        with self._mutex, self._lock:
            data = self._load_unlocked()
            result = operation(data)
            validate_state(data)
            self._write_unlocked(data)
            return data, result

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return default_state()
        data = self._read_unlocked()
        version = data.get("version")
        if isinstance(version, int) and not isinstance(version, bool) and version == 1:
            self._archive_unlocked(self.archive_v1_path, "version 1")
            fresh = default_state()
            self._write_unlocked(fresh)
            return fresh
        if _is_legacy_v2(data):
            self._archive_unlocked(self.archive_v2_legacy_path, "legacy version 2")
            fresh = default_state()
            self._write_unlocked(fresh)
            return fresh
        if _fit_layout_rows(data):
            self._write_unlocked(data)
        return validate_state(data)

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StateError(f"cannot read {self.path}: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StateError(f"{self.path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise StateError("state must be a JSON object")
        return data

    def _archive_unlocked(self, destination: Path, label: str) -> None:
        try:
            shutil.copy2(self.path, destination)
        except OSError as exc:
            raise StateError(f"cannot archive {label} state: {exc}") from exc

    def _write_unlocked(self, data: dict[str, Any]) -> None:
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
