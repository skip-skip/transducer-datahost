"""Scene and figure operations over the dashboard state document.

State version 2 stores flat scenes per scope. A scene owns a list of figures
with a grid layout. The earlier single-figure helpers remain as compatibility
wrappers that operate on a default scene, so existing routes and templates keep
working while the scene UI is built.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from .state import (
    DEFAULT_FIGURE_H,
    DEFAULT_FIGURE_W,
    DEFAULT_GRID_COLS,
    DEFAULT_GRID_ROW_HEIGHT,
    DEFAULT_SCENE_NAME,
    SCOPES,
    default_ui,
    initial_layout,
)

DEFAULT_FIGURE_NAME = "Untitled figure"
UNSET: object = object()


class FigureError(RuntimeError):
    pass


class FigureNotFound(FigureError):
    pass


class SceneNotFound(FigureError):
    pass


class FigurePermissionError(FigureError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return uuid.uuid4().hex


def personal_entry(
    state: dict[str, Any], label: str, create: bool = True
) -> dict[str, Any] | None:
    personal = state.setdefault("personal", {})
    entry = personal.get(label)
    if entry is None and create:
        entry = {"scenes": [], "ui": default_ui()}
        personal[label] = entry
    if entry is not None:
        entry.setdefault("scenes", [])
        entry.setdefault("ui", default_ui())
    return entry


def get_ui(state: dict[str, Any], label: str) -> dict[str, Any]:
    entry = personal_entry(state, label)
    assert entry is not None
    ui = entry["ui"]
    base = default_ui()
    base.update(
        {key: value for key, value in ui.items() if key not in {"personal", "shared"}}
    )
    for scope in SCOPES:
        merged = dict(default_ui()[scope])
        if isinstance(ui.get(scope), dict):
            merged.update(ui[scope])
        base[scope] = merged
    return base


def set_ui(state: dict[str, Any], label: str, **changes: Any) -> dict[str, Any]:
    entry = personal_entry(state, label)
    assert entry is not None
    ui = get_ui(state, label)
    for key, value in changes.items():
        if key in SCOPES:
            if not isinstance(value, dict):
                raise FigureError(f"{key} UI state must be an object")
            ui[key].update(value)
        else:
            ui[key] = value
    entry["ui"] = ui
    return ui


def can_edit_shared(label: str, shared_editors: frozenset[str]) -> bool:
    return not shared_editors or label in shared_editors


def can_edit(scope: str, label: str, shared_editors: frozenset[str]) -> bool:
    if scope == "personal":
        return True
    if scope == "shared":
        return can_edit_shared(label, shared_editors)
    return False


def unique_name(existing: list[dict[str, Any]], requested: str) -> str:
    names = {item.get("name") for item in existing}
    if requested not in names:
        return requested
    index = 2
    while f"{requested} {index}" in names:
        index += 1
    return f"{requested} {index}"


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------


def _scene_list(
    state: dict[str, Any], scope: str, label: str
) -> list[dict[str, Any]]:
    if scope == "shared":
        return state.setdefault("shared", {"scenes": []}).setdefault("scenes", [])
    if scope == "personal":
        entry = personal_entry(state, label)
        assert entry is not None
        return entry["scenes"]
    raise FigureError(f"unknown scope {scope!r}")


def list_scenes(
    state: dict[str, Any], scope: str, label: str
) -> list[dict[str, Any]]:
    return sorted(
        _scene_list(state, scope, label),
        key=lambda scene: (
            scene.get("position", 0),
            scene.get("created_at", ""),
            scene.get("name", ""),
        ),
    )


def get_scene(
    state: dict[str, Any], scope: str, label: str, scene_id: str
) -> dict[str, Any] | None:
    for scene in _scene_list(state, scope, label):
        if scene.get("id") == scene_id:
            return scene
    return None


def _new_scene(
    state: dict[str, Any], scope: str, label: str, name: str
) -> dict[str, Any]:
    scenes = _scene_list(state, scope, label)
    now = _now()
    scene: dict[str, Any] = {
        "id": new_id(),
        "name": unique_name(scenes, name),
        "position": max(
            (scene.get("position", 0) for scene in scenes), default=-1
        )
        + 1,
        "grid": {"cols": DEFAULT_GRID_COLS, "row_height": DEFAULT_GRID_ROW_HEIGHT},
        "figures": [],
        "created_by": label,
        "created_at": now,
        "updated_at": now,
    }
    scenes.append(scene)
    return scene


def _default_scene(
    state: dict[str, Any], scope: str, label: str, *, create: bool
) -> dict[str, Any] | None:
    for scene in _scene_list(state, scope, label):
        if scene.get("name") == DEFAULT_SCENE_NAME:
            return scene
    if not create:
        return None
    return _new_scene(state, scope, label, DEFAULT_SCENE_NAME)


def ensure_default_scene(
    state: dict[str, Any], scope: str, label: str
) -> dict[str, Any]:
    scene = _default_scene(state, scope, label, create=True)
    assert scene is not None
    return scene


def create_scene(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    name: str | None = None,
) -> dict[str, Any]:
    if not can_edit(scope, label, shared_editors):
        raise FigurePermissionError("this token may not edit shared scenes")

    requested = (name or DEFAULT_SCENE_NAME).strip() or DEFAULT_SCENE_NAME
    scene = _new_scene(state, scope, label, requested)
    set_ui(
        state,
        label,
        view=scope,
        **{
            scope: {
                "selected_scene_id": scene["id"],
                "selected_figure_id": None,
            }
        },
    )
    return scene


def update_scene(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    scene_id: str,
    *,
    name: str | None = None,
    grid: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not can_edit(scope, label, shared_editors):
        raise FigurePermissionError("this token may not edit shared scenes")

    scene = get_scene(state, scope, label, scene_id)
    if scene is None:
        raise SceneNotFound("scene not found")

    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise FigureError("name must not be blank")
        scene["name"] = cleaned

    if grid is not None:
        if not isinstance(grid, dict):
            raise FigureError("grid must be an object")
        cols = grid.get("cols")
        row_height = grid.get("row_height")
        if not isinstance(cols, int) or cols < 1:
            raise FigureError("grid cols must be a positive integer")
        if not isinstance(row_height, int) or row_height < 1:
            raise FigureError("grid row_height must be a positive integer")
        scene["grid"] = {"cols": cols, "row_height": row_height}
        _fit_layouts(scene)

    scene["updated_at"] = _now()
    ui = get_ui(state, label)
    selected_figure_id = ui[scope].get("selected_figure_id")
    if selected_figure_id and not any(
        figure.get("id") == selected_figure_id
        for figure in scene.get("figures", [])
    ):
        selected_figure_id = None
    set_ui(
        state,
        label,
        view=scope,
        **{
            scope: {
                "selected_scene_id": scene_id,
                "selected_figure_id": selected_figure_id,
            }
        },
    )
    return scene


def update_scene_layouts(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    scene_id: str,
    layouts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply a batch of figure layouts, validating every entry before saving."""

    if not can_edit(scope, label, shared_editors):
        raise FigurePermissionError("this token may not edit shared scenes")

    scene = get_scene(state, scope, label, scene_id)
    if scene is None:
        raise SceneNotFound("scene not found")
    if not isinstance(layouts, list):
        raise FigureError("layouts must be a list")

    cols = scene.get("grid", {}).get("cols", DEFAULT_GRID_COLS)
    by_id = {
        figure.get("id"): figure
        for figure in scene.get("figures", [])
        if isinstance(figure.get("id"), str)
    }
    validated: dict[str, dict[str, int]] = {}
    for item in layouts:
        if not isinstance(item, dict):
            raise FigureError("layout item must be an object")
        figure_id = item.get("id")
        if not isinstance(figure_id, str) or figure_id not in by_id:
            raise FigureNotFound("figure not found")
        if figure_id in validated:
            raise FigureError("duplicate figure id in layout payload")
        validated[figure_id] = _validated_layout(item, cols)

    now = _now()
    for figure_id, layout in validated.items():
        figure = by_id[figure_id]
        figure["layout"] = layout
        figure["updated_at"] = now
    if validated:
        scene["updated_at"] = now
    return scene


def delete_scene(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    scene_id: str,
) -> None:
    if not can_edit(scope, label, shared_editors):
        raise FigurePermissionError("this token may not edit shared scenes")

    scenes = _scene_list(state, scope, label)
    for index, scene in enumerate(scenes):
        if scene.get("id") == scene_id:
            removed = scenes.pop(index)
            break
    else:
        raise SceneNotFound("scene not found")

    figure_ids = {
        figure.get("id")
        for figure in removed.get("figures", [])
        if isinstance(figure.get("id"), str)
    }
    _clear_selections(state, scene_id=scene_id, figure_ids=figure_ids)


def _next_layout(figures: list[dict[str, Any]], cols: int) -> dict[str, int]:
    """Return the first non-overlapping grid slot for a new figure."""

    occupied = [
        figure["layout"]
        for figure in figures
        if isinstance(figure.get("layout"), dict)
        and all(key in figure["layout"] for key in ("x", "y", "w", "h"))
    ]

    def overlaps(candidate: dict[str, int]) -> bool:
        return any(
            candidate["x"] < other["x"] + other["w"]
            and other["x"] < candidate["x"] + candidate["w"]
            and candidate["y"] < other["y"] + other["h"]
            and other["y"] < candidate["y"] + candidate["h"]
            for other in occupied
        )

    index = 0
    while True:
        candidate = initial_layout(index, cols)
        if not overlaps(candidate):
            return candidate
        index += 1


def _fit_layouts(scene: dict[str, Any]) -> None:
    cols = scene.get("grid", {}).get("cols", DEFAULT_GRID_COLS)
    for figure in scene.get("figures", []):
        layout = figure.get("layout")
        if not isinstance(layout, dict):
            continue
        width = min(max(1, int(layout.get("w", DEFAULT_FIGURE_W))), cols)
        height = max(1, int(layout.get("h", DEFAULT_FIGURE_H)))
        x = min(max(0, int(layout.get("x", 0))), cols - width)
        y = max(0, int(layout.get("y", 0)))
        figure["layout"] = {"x": x, "y": y, "w": width, "h": height}


# ---------------------------------------------------------------------------
# Figures inside a scene
# ---------------------------------------------------------------------------


def list_scene_figures(scene: dict[str, Any]) -> list[dict[str, Any]]:
    def key(figure: dict[str, Any]) -> tuple[int, int, str]:
        raw_layout = figure.get("layout")
        layout: dict[str, Any] = raw_layout if isinstance(raw_layout, dict) else {}
        return (
            int(layout.get("y", 0)),
            int(layout.get("x", 0)),
            str(figure.get("created_at", "")),
        )

    return sorted(scene.get("figures", []), key=key)


def get_scene_figure(
    state: dict[str, Any],
    scope: str,
    label: str,
    scene_id: str,
    figure_id: str,
) -> dict[str, Any] | None:
    scene = get_scene(state, scope, label, scene_id)
    if scene is None:
        return None
    for figure in scene.get("figures", []):
        if figure.get("id") == figure_id:
            return figure
    return None


def find_figure(
    state: dict[str, Any], scope: str, label: str, figure_id: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for scene in _scene_list(state, scope, label):
        for figure in scene.get("figures", []):
            if figure.get("id") == figure_id:
                return scene, figure
    return None


def create_scene_figure(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    scene_id: str,
    name: str | None = None,
    series_id: int | None = None,
    location_id: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not can_edit(scope, label, shared_editors):
        raise FigurePermissionError("this token may not edit shared figures")

    scene = get_scene(state, scope, label, scene_id)
    if scene is None:
        raise SceneNotFound("scene not found")

    figures = scene.setdefault("figures", [])
    requested = (name or DEFAULT_FIGURE_NAME).strip() or DEFAULT_FIGURE_NAME
    now = _now()
    cols = scene.get("grid", {}).get("cols", DEFAULT_GRID_COLS)
    figure = {
        "id": new_id(),
        "name": unique_name(figures, requested),
        "series_id": series_id,
        "location_id": location_id,
        "config": config or {},
        "layout": _next_layout(figures, cols),
        "created_by": label,
        "created_at": now,
        "updated_at": now,
    }
    figures.append(figure)
    scene["updated_at"] = now
    set_ui(
        state,
        label,
        view=scope,
        **{
            scope: {
                "selected_scene_id": scene_id,
                "selected_figure_id": figure["id"],
            }
        },
    )
    return figure


def update_scene_figure(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    scene_id: str,
    figure_id: str,
    *,
    name: str | None = None,
    series_id: int | None | object = UNSET,
    location_id: int | None | object = UNSET,
    config: dict[str, Any] | None = None,
    layout: dict[str, Any] | object = UNSET,
) -> dict[str, Any]:
    if not can_edit(scope, label, shared_editors):
        raise FigurePermissionError("this token may not edit shared figures")

    scene = get_scene(state, scope, label, scene_id)
    if scene is None:
        raise SceneNotFound("scene not found")

    figure = None
    for item in scene.get("figures", []):
        if item.get("id") == figure_id:
            figure = item
            break
    if figure is None:
        raise FigureNotFound("figure not found")

    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise FigureError("name must not be blank")
        figure["name"] = cleaned
    if series_id is not UNSET:
        figure["series_id"] = series_id
    if location_id is not UNSET:
        figure["location_id"] = location_id
    if config is not None:
        figure["config"] = config
    if layout is not UNSET:
        cols = scene.get("grid", {}).get("cols", DEFAULT_GRID_COLS)
        figure["layout"] = _validated_layout(layout, cols)

    figure["updated_at"] = _now()
    scene["updated_at"] = figure["updated_at"]
    set_ui(
        state,
        label,
        view=scope,
        **{
            scope: {
                "selected_scene_id": scene_id,
                "selected_figure_id": figure_id,
            }
        },
    )
    return figure


def delete_scene_figure(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    scene_id: str,
    figure_id: str,
) -> None:
    if not can_edit(scope, label, shared_editors):
        raise FigurePermissionError("this token may not edit shared figures")

    scene = get_scene(state, scope, label, scene_id)
    if scene is None:
        raise SceneNotFound("scene not found")

    figures = scene.get("figures", [])
    for index, figure in enumerate(figures):
        if figure.get("id") == figure_id:
            del figures[index]
            break
    else:
        raise FigureNotFound("figure not found")

    scene["updated_at"] = _now()
    _clear_selections(state, figure_ids={figure_id})


def _validated_layout(layout: Any, cols: int) -> dict[str, int]:
    if not isinstance(layout, dict):
        raise FigureError("layout must be an object")
    try:
        x = int(layout.get("x", 0))
        y = int(layout.get("y", 0))
        width = int(layout.get("w", DEFAULT_FIGURE_W))
        height = int(layout.get("h", DEFAULT_FIGURE_H))
    except (TypeError, ValueError) as exc:
        raise FigureError("layout values must be integers") from exc
    if x < 0 or y < 0:
        raise FigureError("layout x and y must be non-negative")
    if width < 1 or height < 1:
        raise FigureError("layout w and h must be positive")
    if width > cols:
        raise FigureError(f"layout w must not exceed {cols} columns")
    if x + width > cols:
        raise FigureError(f"layout extends past {cols} grid columns")
    return {"x": x, "y": y, "w": width, "h": height}


def _clear_selections(
    state: dict[str, Any],
    *,
    scene_id: str | None = None,
    figure_ids: set[str] | frozenset[str] = frozenset(),
) -> None:
    for entry in state.get("personal", {}).values():
        ui = entry.get("ui") if isinstance(entry, dict) else None
        if not isinstance(ui, dict):
            continue
        for scope in SCOPES:
            block = ui.get(scope)
            if not isinstance(block, dict):
                continue
            if scene_id is not None and block.get("selected_scene_id") == scene_id:
                block["selected_scene_id"] = None
            if block.get("selected_figure_id") in figure_ids:
                block["selected_figure_id"] = None


# ---------------------------------------------------------------------------
# Compatibility facade over the default scene
# ---------------------------------------------------------------------------


def list_figures(
    state: dict[str, Any], scope: str, label: str
) -> list[dict[str, Any]]:
    scene = _default_scene(state, scope, label, create=False)
    return list_scene_figures(scene) if scene is not None else []


def get_figure(
    state: dict[str, Any], scope: str, label: str, figure_id: str
) -> dict[str, Any] | None:
    found = find_figure(state, scope, label, figure_id)
    return found[1] if found is not None else None


def create_figure(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    name: str | None = None,
    series_id: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not can_edit(scope, label, shared_editors):
        raise FigurePermissionError("this token may not edit shared figures")

    scene = _default_scene(state, scope, label, create=True)
    assert scene is not None
    return create_scene_figure(
        state,
        scope,
        label,
        shared_editors,
        scene["id"],
        name=name,
        series_id=series_id,
        config=config,
    )


def update_figure(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    figure_id: str,
    *,
    name: str | None = None,
    series_id: int | None | object = UNSET,
    location_id: int | None | object = UNSET,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    found = find_figure(state, scope, label, figure_id)
    if found is None:
        raise FigureNotFound("figure not found")
    scene, _ = found
    return update_scene_figure(
        state,
        scope,
        label,
        shared_editors,
        scene["id"],
        figure_id,
        name=name,
        series_id=series_id,
        location_id=location_id,
        config=config,
    )


def delete_figure(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    figure_id: str,
) -> None:
    found = find_figure(state, scope, label, figure_id)
    if found is None:
        raise FigureNotFound("figure not found")
    scene, _ = found
    delete_scene_figure(
        state, scope, label, shared_editors, scene["id"], figure_id
    )
