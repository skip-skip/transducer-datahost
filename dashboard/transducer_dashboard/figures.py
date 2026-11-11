"""Personal and shared figure operations over the state document."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from .state import default_ui

SCOPES = ("personal", "shared")
DEFAULT_FIGURE_NAME = "Untitled figure"
UNSET: object = object()


class FigureError(RuntimeError):
    pass


class FigureNotFound(FigureError):
    pass


class FigurePermissionError(FigureError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return uuid.uuid4().hex


def personal_entry(state: dict[str, Any], label: str, create: bool = True) -> dict[str, Any] | None:
    personal = state.setdefault("personal", {})
    entry = personal.get(label)
    if entry is None and create:
        entry = {"figures": [], "ui": default_ui()}
        personal[label] = entry
    if entry is not None:
        entry.setdefault("figures", [])
        entry.setdefault("ui", default_ui())
    return entry


def get_ui(state: dict[str, Any], label: str) -> dict[str, Any]:
    entry = personal_entry(state, label)
    assert entry is not None
    ui = entry["ui"]
    base = default_ui()
    base.update({key: value for key, value in ui.items() if key not in {"personal", "shared"}})
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


def _figure_list(state: dict[str, Any], scope: str, label: str) -> list[dict[str, Any]]:
    if scope == "shared":
        return state.setdefault("shared", {"figures": []}).setdefault("figures", [])
    if scope == "personal":
        entry = personal_entry(state, label)
        assert entry is not None
        return entry["figures"]
    raise FigureError(f"unknown scope {scope!r}")


def list_figures(state: dict[str, Any], scope: str, label: str) -> list[dict[str, Any]]:
    return sorted(
        _figure_list(state, scope, label),
        key=lambda figure: (figure.get("position", 0), figure.get("created_at", "")),
    )


def get_figure(
    state: dict[str, Any], scope: str, label: str, figure_id: str
) -> dict[str, Any] | None:
    for figure in _figure_list(state, scope, label):
        if figure.get("id") == figure_id:
            return figure
    return None


def can_edit_shared(label: str, shared_editors: frozenset[str]) -> bool:
    return not shared_editors or label in shared_editors


def can_edit(scope: str, label: str, shared_editors: frozenset[str]) -> bool:
    if scope == "personal":
        return True
    if scope == "shared":
        return can_edit_shared(label, shared_editors)
    return False


def unique_name(existing: list[dict[str, Any]], requested: str) -> str:
    names = {figure.get("name") for figure in existing}
    if requested not in names:
        return requested
    index = 2
    while f"{requested} {index}" in names:
        index += 1
    return f"{requested} {index}"


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

    figures = _figure_list(state, scope, label)
    requested = (name or DEFAULT_FIGURE_NAME).strip() or DEFAULT_FIGURE_NAME
    now = _now()
    figure = {
        "id": new_id(),
        "name": unique_name(figures, requested),
        "series_id": series_id,
        "position": max((item.get("position", 0) for item in figures), default=-1) + 1,
        "config": config or {},
        "created_by": label,
        "created_at": now,
        "updated_at": now,
    }
    figures.append(figure)
    set_ui(state, label, view=scope, **{scope: {"selected_figure_id": figure["id"]}})
    return figure


def update_figure(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    figure_id: str,
    *,
    name: str | None = None,
    series_id: int | None | object = UNSET,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not can_edit(scope, label, shared_editors):
        raise FigurePermissionError("this token may not edit shared figures")

    figure = get_figure(state, scope, label, figure_id)
    if figure is None:
        raise FigureNotFound("figure not found")

    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise FigureError("name must not be blank")
        figure["name"] = cleaned
    if series_id is not UNSET:
        figure["series_id"] = series_id
    if config is not None:
        figure["config"] = config
    figure["updated_at"] = _now()
    set_ui(state, label, view=scope, **{scope: {"selected_figure_id": figure_id}})
    return figure


def delete_figure(
    state: dict[str, Any],
    scope: str,
    label: str,
    shared_editors: frozenset[str],
    figure_id: str,
) -> None:
    if not can_edit(scope, label, shared_editors):
        raise FigurePermissionError("this token may not edit shared figures")

    figures = _figure_list(state, scope, label)
    for index, figure in enumerate(figures):
        if figure.get("id") == figure_id:
            del figures[index]
            break
    else:
        raise FigureNotFound("figure not found")

    # Clear stale selections for everyone who was pointing at the figure.
    for entry in state.get("personal", {}).values():
        ui = entry.get("ui")
        if not isinstance(ui, dict):
            continue
        scoped = ui.get(scope)
        if isinstance(scoped, dict) and scoped.get("selected_figure_id") == figure_id:
            scoped["selected_figure_id"] = None
