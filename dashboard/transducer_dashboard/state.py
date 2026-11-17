"""Durable dashboard state stored as a single JSON file on a Docker volume.

State version 2 introduces **scenes**: flat containers of figures with a grid
layout. Documents written by the earlier single-figure dashboard (version 1)
are migrated deterministically on load.

The dashboard deliberately keeps its state out of PostgreSQL. Every read-modify-
write cycle takes a file lock, writes a temporary file, fsyncs it, keeps the
previous version as ``state.json.bak``, and atomically replaces ``state.json``.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

STATE_VERSION = 2
LEGACY_STATE_VERSION = 1
STATE_FILENAME = "state.json"
BACKUP_FILENAME = "state.json.bak"
LOCK_FILENAME = ".state.lock"

SCOPES = ("personal", "shared")
DEFAULT_SCENE_NAME = "Default scene"
DEFAULT_GRID_COLS = 12
DEFAULT_GRID_ROW_HEIGHT = 220
DEFAULT_FIGURE_W = 6
DEFAULT_FIGURE_H = 2


class StateError(RuntimeError):
    pass


def default_ui() -> dict[str, Any]:
    def pane() -> dict[str, Any]:
        return {
            "selected_scene_id": None,
            "selected_figure_id": None,
            "folders_collapsed": False,
            "options_collapsed": False,
        }

    return {
        "view": "personal",
        "personal": pane(),
        "shared": pane(),
        "tz": "local",
    }


def default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "shared": {"scenes": []},
        "personal": {},
    }


def initial_layout(index: int, cols: int = DEFAULT_GRID_COLS) -> dict[str, int]:
    """Return a non-overlapping grid layout for the ``index``-th figure."""

    width = min(DEFAULT_FIGURE_W, cols)
    per_row = max(1, cols // width)
    return {
        "x": (index % per_row) * width,
        "y": (index // per_row) * DEFAULT_FIGURE_H,
        "w": width,
        "h": DEFAULT_FIGURE_H,
    }


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _non_empty_string(value: Any, where: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise StateError(f"{where} must be a non-empty string")


def _optional_string(value: Any, where: str) -> None:
    if value is not None and not isinstance(value, str):
        raise StateError(f"{where} must be a string or null")


def _optional_int(value: Any, where: str) -> None:
    if value is not None and not isinstance(value, int):
        raise StateError(f"{where} must be an integer or null")


def _ensure_layout(layout: Any, where: str, cols: int) -> None:
    if not isinstance(layout, dict):
        raise StateError(f"{where} must be an object")
    for key in ("x", "y"):
        if not isinstance(layout.get(key), int) or layout[key] < 0:
            raise StateError(f"{where}.{key} must be a non-negative integer")
    for key in ("w", "h"):
        if not isinstance(layout.get(key), int) or layout[key] < 1:
            raise StateError(f"{where}.{key} must be a positive integer")
    if layout["x"] + layout["w"] > cols:
        raise StateError(f"{where} extends past {cols} grid columns")


def _ensure_figure(figure: Any, where: str, cols: int) -> None:
    if not isinstance(figure, dict):
        raise StateError(f"{where} must be an object")
    _non_empty_string(figure.get("id"), f"{where}.id")
    _non_empty_string(figure.get("name"), f"{where}.name")
    _optional_int(figure.get("series_id"), f"{where}.series_id")
    _optional_int(figure.get("location_id"), f"{where}.location_id")
    config = figure.get("config", {})
    if not isinstance(config, dict):
        raise StateError(f"{where}.config must be an object")
    _ensure_layout(figure.get("layout"), f"{where}.layout", cols)
    for key in ("created_by", "created_at", "updated_at"):
        _optional_string(figure.get(key), f"{where}.{key}")


def _ensure_grid(grid: Any, where: str) -> int:
    if not isinstance(grid, dict):
        raise StateError(f"{where} must be an object")
    cols = grid.get("cols")
    if not isinstance(cols, int) or cols < 1:
        raise StateError(f"{where}.cols must be a positive integer")
    row_height = grid.get("row_height")
    if not isinstance(row_height, int) or row_height < 1:
        raise StateError(f"{where}.row_height must be a positive integer")
    return cols


def _ensure_scene(scene: Any, where: str) -> None:
    if not isinstance(scene, dict):
        raise StateError(f"{where} must be an object")
    _non_empty_string(scene.get("id"), f"{where}.id")
    _non_empty_string(scene.get("name"), f"{where}.name")
    position = scene.get("position", 0)
    if not isinstance(position, int) or position < 0:
        raise StateError(f"{where}.position must be a non-negative integer")
    cols = _ensure_grid(scene.get("grid"), f"{where}.grid")
    figures = scene.get("figures")
    if not isinstance(figures, list):
        raise StateError(f"{where}.figures must be a list")
    for index, figure in enumerate(figures):
        _ensure_figure(figure, f"{where}.figures[{index}]", cols)
    for key in ("created_by", "created_at", "updated_at"):
        _optional_string(scene.get(key), f"{where}.{key}")


def _ensure_ui(ui: Any, where: str) -> None:
    if not isinstance(ui, dict):
        raise StateError(f"{where} must be an object")
    view = ui.get("view")
    if view is not None and view not in SCOPES:
        raise StateError(f"{where}.view must be one of {SCOPES}")
    _optional_string(ui.get("tz"), f"{where}.tz")
    for scope in SCOPES:
        block = ui.get(scope)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise StateError(f"{where}.{scope} must be an object")
        _optional_string(
            block.get("selected_scene_id"), f"{where}.{scope}.selected_scene_id"
        )
        _optional_string(
            block.get("selected_figure_id"), f"{where}.{scope}.selected_figure_id"
        )
        for flag in ("folders_collapsed", "options_collapsed"):
            value = block.get(flag)
            if value is not None and not isinstance(value, bool):
                raise StateError(f"{where}.{scope}.{flag} must be a boolean")


def validate_state(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise StateError("state must be a JSON object")
    if data.get("version") != STATE_VERSION:
        raise StateError(f"unsupported state version {data.get('version')!r}")

    shared = data.get("shared")
    if not isinstance(shared, dict) or not isinstance(shared.get("scenes"), list):
        raise StateError("state.shared.scenes must be a list")
    for index, scene in enumerate(shared["scenes"]):
        _ensure_scene(scene, f"state.shared.scenes[{index}]")

    personal = data.get("personal")
    if not isinstance(personal, dict):
        raise StateError("state.personal must be an object")
    for label, entry in personal.items():
        if not isinstance(label, str):
            raise StateError("state.personal keys must be strings")
        if not isinstance(entry, dict):
            raise StateError(f"state.personal[{label!r}] must be an object")
        scenes = entry.get("scenes")
        if not isinstance(scenes, list):
            raise StateError(f"state.personal[{label!r}].scenes must be a list")
        for index, scene in enumerate(scenes):
            _ensure_scene(scene, f"state.personal[{label!r}].scenes[{index}]")
        ui = entry.get("ui")
        if ui is not None:
            _ensure_ui(ui, f"state.personal[{label!r}].ui")

    return data


# ---------------------------------------------------------------------------
# Version 1 -> version 2 migration
# ---------------------------------------------------------------------------


def _migrate_figure(figure: Any, index: int, cols: int) -> dict[str, Any]:
    if not isinstance(figure, dict):
        raise StateError("version 1 figures must be objects")
    return {
        "id": figure.get("id"),
        "name": figure.get("name"),
        "series_id": figure.get("series_id"),
        "location_id": figure.get("location_id"),
        "config": dict(figure.get("config") or {}),
        "layout": initial_layout(index, cols),
        "created_by": figure.get("created_by"),
        "created_at": figure.get("created_at"),
        "updated_at": figure.get("updated_at"),
    }


def _migrate_scope_scenes(figures: Any) -> list[dict[str, Any]]:
    if not isinstance(figures, list):
        raise StateError("version 1 figures must be a list")
    if not figures:
        return []

    cols = DEFAULT_GRID_COLS
    migrated = [_migrate_figure(figure, index, cols) for index, figure in enumerate(figures)]
    created_at = migrated[0].get("created_at") or _now()
    return [
        {
            "id": uuid.uuid4().hex,
            "name": DEFAULT_SCENE_NAME,
            "position": 0,
            "grid": {"cols": cols, "row_height": DEFAULT_GRID_ROW_HEIGHT},
            "figures": migrated,
            "created_by": migrated[0].get("created_by") or "migrated",
            "created_at": created_at,
            "updated_at": _now(),
        }
    ]


def _migrate_ui(
    ui: Any,
    personal_scenes: list[dict[str, Any]],
    shared_scenes: list[dict[str, Any]],
) -> dict[str, Any]:
    base = default_ui()
    if not isinstance(ui, dict):
        return base

    if ui.get("view") in SCOPES:
        base["view"] = ui["view"]

    for scope, scenes in (("personal", personal_scenes), ("shared", shared_scenes)):
        raw_old = ui.get(scope)
        old: dict[str, Any] = raw_old if isinstance(raw_old, dict) else {}
        figure_id = old.get("selected_figure_id")
        if not isinstance(figure_id, str):
            figure_id = None

        scene_id = None
        if figure_id:
            for scene in scenes:
                if any(
                    figure.get("id") == figure_id
                    for figure in scene.get("figures", [])
                ):
                    scene_id = scene.get("id")
                    break
        if scene_id is None:
            # A stale v1 selection must not point at a figure that no scene
            # contains after migration.
            figure_id = None

        base[scope] = {
            "selected_scene_id": scene_id,
            "selected_figure_id": figure_id,
            "folders_collapsed": bool(old.get("folders_collapsed", False)),
            "options_collapsed": bool(old.get("options_collapsed", False)),
        }

    if isinstance(ui.get("tz"), str):
        base["tz"] = ui["tz"]
    return base


def migrate_v1_state(data: Any) -> dict[str, Any]:
    """Return a version 2 document derived from a version 1 document."""

    try:
        shared = data.get("shared") or {}
        personal = data.get("personal") or {}
        if not isinstance(shared, dict) or not isinstance(personal, dict):
            raise StateError("version 1 state must contain shared and personal objects")

        shared_scenes = _migrate_scope_scenes(shared.get("figures") or [])
        migrated: dict[str, Any] = {
            "version": STATE_VERSION,
            "shared": {"scenes": shared_scenes},
            "personal": {},
        }

        for label, entry in personal.items():
            if not isinstance(entry, dict):
                raise StateError(f"state.personal[{label!r}] must be an object")
            scenes = _migrate_scope_scenes(entry.get("figures") or [])
            migrated["personal"][label] = {
                "scenes": scenes,
                "ui": _migrate_ui(entry.get("ui"), scenes, shared_scenes),
            }

        return migrated
    except StateError:
        raise
    except (AttributeError, KeyError, TypeError) as exc:
        raise StateError(f"cannot migrate state version 1: {exc}") from exc


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

        version = data.get("version") if isinstance(data, dict) else None
        if version == LEGACY_STATE_VERSION:
            data = migrate_v1_state(data)
            validate_state(data)
            # Persist the migration once so subsequent loads use version 2.
            self._write(data)
            return data
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
