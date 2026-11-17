"""Shared scene and figure operations over the version 2 state document.

The domain layer is plain Python so it can be tested without Panel. Every
function mutates the state dictionary in place; persistence is the caller's job
through :class:`transducer_dashboard.state.StateStore`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from .state import (
    DEFAULT_RANGE,
    GRID_COLUMNS,
    MIN_FIGURE_HEIGHT,
    MIN_FIGURE_WIDTH,
    RANGES,
    UI_KEYS,
    StateError,
    default_ui,
    validate_layout,
    validate_ui,
)

DEFAULT_FIGURE_TITLE = "Untitled figure"
DEFAULT_SCENE_PREFIX = "Scene"
UNSET: object = object()


class SceneError(RuntimeError):
    pass


class SceneNotFound(SceneError):
    pass


class FigureNotFound(SceneError):
    pass


class SceneFull(SceneError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return uuid.uuid4().hex


def _require_label(label: str) -> None:
    if not isinstance(label, str) or not label.strip():
        raise SceneError("token label must be a non-empty string")


def _optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SceneError(f"{field} must be an integer or null")
    return value


def find_scene(state: dict[str, Any], scene_id: str) -> dict[str, Any] | None:
    for scene in state.get("scenes", []):
        if scene.get("id") == scene_id:
            return scene
    return None


def get_scene(state: dict[str, Any], scene_id: str) -> dict[str, Any]:
    scene = find_scene(state, scene_id)
    if scene is None:
        raise SceneNotFound("scene not found")
    return scene


def find_figure(scene: dict[str, Any], figure_id: str) -> dict[str, Any] | None:
    for figure in scene.get("figures", []):
        if figure.get("id") == figure_id:
            return figure
    return None


def get_figure(
    state: dict[str, Any], scene_id: str, figure_id: str
) -> dict[str, Any]:
    scene = get_scene(state, scene_id)
    figure = find_figure(scene, figure_id)
    if figure is None:
        raise FigureNotFound("figure not found")
    return figure


def next_scene_name(state: dict[str, Any]) -> str:
    names = {scene.get("name") for scene in state.get("scenes", [])}
    index = 1
    while f"{DEFAULT_SCENE_PREFIX} {index}" in names:
        index += 1
    return f"{DEFAULT_SCENE_PREFIX} {index}"


def create_scene(state: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    if name is None:
        cleaned = next_scene_name(state)
    else:
        if not isinstance(name, str) or not name.strip():
            raise SceneError("scene name must not be blank")
        cleaned = name.strip()

    now = _now()
    scene = {
        "id": new_id(),
        "name": cleaned,
        "figures": [],
        "created_at": now,
        "updated_at": now,
    }
    state.setdefault("scenes", []).append(scene)
    return scene


def delete_scene(state: dict[str, Any], scene_id: str) -> None:
    scenes = state.setdefault("scenes", [])
    for index, scene in enumerate(scenes):
        if scene.get("id") == scene_id:
            removed = scenes.pop(index)
            break
    else:
        raise SceneNotFound("scene not found")

    removed_figures = {
        figure.get("id") for figure in removed.get("figures", [])
    }
    for ui in state.setdefault("ui", {}).values():
        if ui.get("selected_scene_id") == scene_id:
            ui["selected_scene_id"] = None
            ui["selected_figure_id"] = None
        elif ui.get("selected_figure_id") in removed_figures:
            ui["selected_figure_id"] = None


def _overlaps(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        first["x"] < second["x"] + second["w"]
        and second["x"] < first["x"] + first["w"]
        and first["y"] < second["y"] + second["h"]
        and second["y"] < first["y"] + first["h"]
    )


def _cell_is_free(
    figures: list[dict[str, Any]], x: int, y: int, width: int, height: int
) -> bool:
    candidate = {"x": x, "y": y, "w": width, "h": height}
    return not any(_overlaps(candidate, figure["layout"]) for figure in figures)


def find_free_slot(
    figures: list[dict[str, Any]],
    width: int = MIN_FIGURE_WIDTH,
    height: int = MIN_FIGURE_HEIGHT,
) -> tuple[int, int] | None:
    """Return the first free cell that fits a figure, scanning top to bottom."""

    if width > GRID_COLUMNS or width < MIN_FIGURE_WIDTH or height < MIN_FIGURE_HEIGHT:
        return None
    lowest = max(
        (figure["layout"]["y"] + figure["layout"]["h"] for figure in figures),
        default=0,
    )
    for y in range(0, lowest + 1):
        for x in range(0, GRID_COLUMNS - width + 1):
            if _cell_is_free(figures, x, y, width, height):
                return x, y
    return None


def add_figure(
    state: dict[str, Any], scene_id: str, x: int, y: int
) -> dict[str, Any]:
    scene = get_scene(state, scene_id)
    if isinstance(x, bool) or not isinstance(x, int) or x < 0:
        raise SceneError("x must be a non-negative integer")
    if isinstance(y, bool) or not isinstance(y, int) or y < 0:
        raise SceneError("y must be a non-negative integer")
    if x >= GRID_COLUMNS:
        raise SceneError(f"x must be less than {GRID_COLUMNS}")

    figures = scene.setdefault("figures", [])
    width = MIN_FIGURE_WIDTH
    height = MIN_FIGURE_HEIGHT
    if x + width > GRID_COLUMNS or not _cell_is_free(figures, x, y, width, height):
        slot = find_free_slot(figures, width, height)
        if slot is None:
            raise SceneFull("the scene has no room for another figure")
        x, y = slot

    now = _now()
    figure = {
        "id": new_id(),
        "title": DEFAULT_FIGURE_TITLE,
        "location_id": None,
        "series_id": None,
        "range": DEFAULT_RANGE,
        "layout": {"x": x, "y": y, "w": width, "h": height},
        "created_at": now,
        "updated_at": now,
    }
    figures.append(figure)
    scene["updated_at"] = now
    return figure


def update_figure(
    state: dict[str, Any],
    scene_id: str,
    figure_id: str,
    *,
    title: str | None = None,
    location_id: int | None | object = UNSET,
    series_id: int | None | object = UNSET,
    range: str | None = None,
) -> dict[str, Any]:
    figure = get_figure(state, scene_id, figure_id)

    if title is not None:
        if not isinstance(title, str) or not title.strip():
            raise SceneError("title must not be blank")
        figure["title"] = title.strip()
    if location_id is not UNSET:
        figure["location_id"] = _optional_int(location_id, "location_id")
    if series_id is not UNSET:
        figure["series_id"] = _optional_int(series_id, "series_id")
    if range is not None:
        if range not in RANGES:
            raise SceneError(f"range must be one of {', '.join(RANGES)}")
        figure["range"] = range

    now = _now()
    figure["updated_at"] = now
    get_scene(state, scene_id)["updated_at"] = now
    return figure


def delete_figure(
    state: dict[str, Any], scene_id: str, figure_id: str
) -> None:
    scene = get_scene(state, scene_id)
    figures = scene.setdefault("figures", [])
    for index, figure in enumerate(figures):
        if figure.get("id") == figure_id:
            del figures[index]
            break
    else:
        raise FigureNotFound("figure not found")

    scene["updated_at"] = _now()
    for ui in state.setdefault("ui", {}).values():
        if ui.get("selected_figure_id") == figure_id:
            ui["selected_figure_id"] = None


def update_layout(
    state: dict[str, Any], scene_id: str, layouts: dict[str, Any]
) -> None:
    scene = get_scene(state, scene_id)
    if not isinstance(layouts, dict):
        raise SceneError("layouts must be an object")

    figures = scene.setdefault("figures", [])
    by_id = {figure["id"]: figure for figure in figures}
    pending: dict[str, dict[str, int]] = {}
    for figure_id, layout in layouts.items():
        if figure_id not in by_id:
            raise FigureNotFound(f"figure not found: {figure_id}")
        try:
            validate_layout(layout, f"layout[{figure_id!r}]")
        except StateError as exc:
            raise SceneError(str(exc)) from exc
        pending[figure_id] = {
            "x": layout["x"],
            "y": layout["y"],
            "w": layout["w"],
            "h": layout["h"],
        }

    final: dict[str, dict[str, int]] = {
        figure_id: dict(figure["layout"]) for figure_id, figure in by_id.items()
    }
    final.update(pending)
    items = list(final.items())
    for index, (figure_id, layout) in enumerate(items):
        for other_id, other in items[index + 1 :]:
            if _overlaps(layout, other):
                raise SceneError(f"figures {figure_id} and {other_id} overlap")

    if not pending:
        return

    now = _now()
    for figure_id, layout in pending.items():
        by_id[figure_id]["layout"] = layout
        by_id[figure_id]["updated_at"] = now
    scene["updated_at"] = now


def get_ui(state: dict[str, Any], label: str) -> dict[str, Any]:
    _require_label(label)
    ui = default_ui()
    stored = state.get("ui", {}).get(label)
    if isinstance(stored, dict):
        for key in UI_KEYS:
            if key in stored:
                ui[key] = stored[key]
    return ui


def set_ui(state: dict[str, Any], label: str, **changes: Any) -> dict[str, Any]:
    _require_label(label)
    unknown = set(changes) - UI_KEYS
    if unknown:
        raise SceneError(f"unknown UI fields: {', '.join(sorted(unknown))}")

    ui = get_ui(state, label)
    ui.update(changes)
    try:
        validate_ui(ui, f"ui[{label!r}]")
    except StateError as exc:
        raise SceneError(str(exc)) from exc

    scene_id = ui["selected_scene_id"]
    figure_id = ui["selected_figure_id"]
    if scene_id is None:
        if figure_id is not None:
            raise SceneError("a figure cannot be selected without a scene")
    else:
        scene = find_scene(state, scene_id)
        if scene is None:
            raise SceneError("selected scene not found")
        if figure_id is not None and find_figure(scene, figure_id) is None:
            raise SceneError("selected figure is not in the selected scene")

    state.setdefault("ui", {})[label] = ui
    return ui
