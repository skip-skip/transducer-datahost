"""Panel scene workspace: scene tray, canvas frame, and settings tray.

The shell owns one browser session's view of the shared scene state. Scenes are
shared between every read token, while the selected scene, selected figure, and
scene-tray collapse state are stored per token label in ``state["ui"]``.
"""

from __future__ import annotations

import html
from typing import Any

import panel as pn

from . import scenes
from .auth import Session
from .grid import GRID_CSS, FigureCard, SceneGrid, grid_height, grid_rows
from .read_client import ReadClient
from .settings import DashboardSettings
from .state import GRID_COLUMNS, StateError, StateStore

SHELL_CSS = """
.td-shell { background: #f5f6f8; min-height: 100vh; }
.td-header { background: #fff; border-bottom: 1px solid #d8dde6; padding: 0.65rem 1rem; }
.td-tray { background: #fff; border-right: 1px solid #d8dde6; padding: 0.75rem; }
.td-canvas { background: #fff; padding: 1rem 1.25rem; }
.td-settings { background: #fff; border-left: 1px solid #d8dde6; padding: 0.75rem; }
.td-muted { color: #5b6472; }
.td-empty {
  border: 1px dashed #b8c0cc;
  border-radius: 0.4rem;
  padding: 2rem 1rem;
  text-align: center;
  color: #5b6472;
}
.td-status { margin: 0.25rem 1rem; }
.td-status-error { color: #a11616; }
.td-scene-button { text-align: left; }
"""


class DashboardShell:
    """Build and update the Panel workspace for one signed-in browser session."""

    def __init__(
        self,
        store: StateStore,
        client: ReadClient,
        session: Session,
        settings: DashboardSettings,
    ) -> None:
        self.store = store
        self.client = client
        self.session = session
        self.settings = settings

        self._state: dict[str, Any] = {}
        self._selected_scene_id: str | None = None
        self._selected_figure_id: str | None = None
        self._tray_collapsed = False
        self._delete_confirm = False
        self._status = ""
        self._status_is_error = False

        self._scene_buttons: dict[str, pn.widgets.Button] = {}
        self.confirm_delete_button: pn.widgets.Button | None = None
        self.cancel_delete_button: pn.widgets.Button | None = None
        self.status_pane: pn.pane.HTML | None = None
        self._grid: SceneGrid | None = None
        self._figure_cards: dict[str, FigureCard] = {}
        self._grid_figure_ids: dict[str, str] = {}
        self.settings_body: pn.Column | None = None
        self.canvas_body: Any = None
        self._view = pn.Column(
            sizing_mode="stretch_width",
            css_classes=["td-shell"],
            stylesheets=[SHELL_CSS],
        )

        self._load()
        self.refresh()

    # -- public read-only state -------------------------------------------

    @property
    def view(self) -> pn.Column:
        return self._view

    @property
    def selected_scene_id(self) -> str | None:
        return self._selected_scene_id

    @property
    def selected_figure_id(self) -> str | None:
        return self._selected_figure_id

    @property
    def tray_collapsed(self) -> bool:
        return self._tray_collapsed

    @property
    def scene_buttons(self) -> dict[str, pn.widgets.Button]:
        return dict(self._scene_buttons)

    @property
    def grid(self) -> SceneGrid | None:
        return self._grid

    @property
    def figure_cards(self) -> dict[str, FigureCard]:
        return dict(self._figure_cards)

    # -- state loading -----------------------------------------------------

    def _load(self) -> None:
        self._state = self.store.load()
        ui = scenes.get_ui(self._state, self.session.label)

        self._tray_collapsed = bool(ui["scenes_tray_collapsed"])
        stored_scene = ui["selected_scene_id"]
        stored_figure = ui["selected_figure_id"]
        self._selected_scene_id = self._resolve_scene(stored_scene)
        self._selected_figure_id = self._resolve_figure(stored_figure)

        if (
            stored_scene != self._selected_scene_id
            or stored_figure != self._selected_figure_id
        ):
            self._save_ui()

    def _resolve_scene(self, stored: str | None) -> str | None:
        if stored is not None and scenes.find_scene(self._state, stored) is not None:
            return stored
        scene_list = self._state.get("scenes", [])
        if scene_list:
            return scene_list[0]["id"]
        return None

    def _resolve_figure(self, stored: str | None) -> str | None:
        if stored is None or self._selected_scene_id is None:
            return None
        scene = scenes.find_scene(self._state, self._selected_scene_id)
        if scene is None or scenes.find_figure(scene, stored) is None:
            return None
        return stored

    def _save_ui(self) -> None:
        label = self.session.label
        scene_id = self._selected_scene_id
        figure_id = self._selected_figure_id
        collapsed = self._tray_collapsed
        try:
            self._state, _ = self.store.mutate(
                lambda data: scenes.set_ui(
                    data,
                    label,
                    selected_scene_id=scene_id,
                    selected_figure_id=figure_id,
                    scenes_tray_collapsed=collapsed,
                )
            )
        except (scenes.SceneError, StateError) as exc:
            previous = self._selected_scene_id
            self._state = self.store.load()
            self._selected_scene_id = self._resolve_scene(previous)
            self._selected_figure_id = None
            self._set_status(str(exc), error=True)

    # -- public actions ----------------------------------------------------

    def select_scene(self, scene_id: str, event: Any = None) -> None:
        label = self.session.label

        def operation(data: dict[str, Any]) -> None:
            scenes.set_ui(
                data,
                label,
                selected_scene_id=scene_id,
                selected_figure_id=None,
            )

        try:
            self._state, _ = self.store.mutate(operation)
        except (scenes.SceneError, StateError) as exc:
            self._recover(str(exc))
            return

        self._selected_scene_id = scene_id
        self._selected_figure_id = None
        self._delete_confirm = False
        self._clear_status()
        self.refresh()

    def _on_create_scene(self, event: Any = None) -> None:
        self.create_scene()

    def create_scene(
        self, event: Any = None, *, name: str | None = None
    ) -> dict[str, Any] | None:
        label = self.session.label

        def operation(data: dict[str, Any]) -> dict[str, Any]:
            scene = scenes.create_scene(data, name)
            scenes.set_ui(
                data,
                label,
                selected_scene_id=scene["id"],
                selected_figure_id=None,
            )
            return scene

        try:
            self._state, scene = self.store.mutate(operation)
        except (scenes.SceneError, StateError) as exc:
            self._recover(str(exc))
            return None

        self._selected_scene_id = scene["id"]
        self._selected_figure_id = None
        self._delete_confirm = False
        self._clear_status()
        self.refresh()
        return scene

    def request_delete_scene(self, event: Any = None) -> None:
        if self._selected_scene_id is None:
            self._set_status("Select a scene to delete.", error=True)
            self.refresh()
            return
        self._delete_confirm = True
        self._clear_status()
        self.refresh()

    def cancel_delete_scene(self, event: Any = None) -> None:
        self._delete_confirm = False
        self._clear_status()
        self.refresh()

    def confirm_delete_scene(self, event: Any = None) -> None:
        scene_id = self._selected_scene_id
        if scene_id is None:
            self._delete_confirm = False
            self.refresh()
            return

        label = self.session.label

        def operation(data: dict[str, Any]) -> str | None:
            scenes.delete_scene(data, scene_id)
            remaining = data.get("scenes", [])
            next_id = remaining[0]["id"] if remaining else None
            scenes.set_ui(
                data,
                label,
                selected_scene_id=next_id,
                selected_figure_id=None,
            )
            return next_id

        try:
            self._state, next_id = self.store.mutate(operation)
        except (scenes.SceneError, StateError) as exc:
            self._recover(str(exc))
            return

        self._selected_scene_id = next_id
        self._selected_figure_id = None
        self._delete_confirm = False
        self._clear_status()
        self.refresh()

    def toggle_scenes_tray(self, event: Any = None) -> None:
        self._tray_collapsed = not self._tray_collapsed
        self._save_ui()
        self.refresh()

    # -- figure actions ----------------------------------------------------

    def select_figure(self, figure_id: str, event: Any = None) -> None:
        scene_id = self._selected_scene_id
        if scene_id is None:
            return
        label = self.session.label

        def operation(data: dict[str, Any]) -> None:
            scenes.set_ui(
                data,
                label,
                selected_scene_id=scene_id,
                selected_figure_id=figure_id,
            )

        try:
            self._state, _ = self.store.mutate(operation)
        except (scenes.SceneError, StateError) as exc:
            self._recover(str(exc))
            return

        self._selected_figure_id = figure_id
        self._clear_status()
        self._update_selection()

    def create_figure(self, x: int, y: int) -> dict[str, Any] | None:
        scene_id = self._selected_scene_id
        if scene_id is None:
            return None
        label = self.session.label

        def operation(data: dict[str, Any]) -> dict[str, Any]:
            figure = scenes.add_figure(data, scene_id, x, y)
            scenes.set_ui(
                data,
                label,
                selected_scene_id=scene_id,
                selected_figure_id=figure["id"],
            )
            return figure

        try:
            self._state, figure = self.store.mutate(operation)
        except (scenes.SceneError, StateError) as exc:
            self._recover(str(exc))
            return None

        self._selected_figure_id = figure["id"]
        self._delete_confirm = False
        self._clear_status()
        self.refresh()
        return figure

    def _on_grid_state(self, event: Any) -> None:
        self.apply_grid_state(event.new or [])

    def apply_grid_state(self, items: list[dict[str, Any]]) -> None:
        """Persist the layouts reported by the browser after a drag or resize."""

        scene_id = self._selected_scene_id
        if self._grid is None or scene_id is None:
            return

        layouts: dict[str, dict[str, int]] = {}
        for item in items:
            try:
                figure_id = self._grid_figure_ids.get(str(item["id"]))
                x0 = int(item["x0"])
                y0 = int(item["y0"])
                x1 = int(item["x1"])
                y1 = int(item["y1"])
            except (KeyError, TypeError, ValueError):
                continue
            if figure_id is None:
                continue
            layouts[figure_id] = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}

        scene = scenes.find_scene(self._state, scene_id)
        if scene is None or not layouts:
            return
        current = {
            figure["id"]: dict(figure["layout"]) for figure in scene["figures"]
        }
        if layouts == current:
            return

        try:
            self._state, _ = self.store.mutate(
                lambda data: scenes.update_layout(data, scene_id, layouts)
            )
        except (scenes.SceneError, StateError) as exc:
            self._recover(str(exc))
            return
        self._set_status("Saved")

    def _selected_figure(self) -> dict[str, Any] | None:
        if self._selected_scene_id is None or self._selected_figure_id is None:
            return None
        scene = scenes.find_scene(self._state, self._selected_scene_id)
        if scene is None:
            return None
        return scenes.find_figure(scene, self._selected_figure_id)

    def _update_selection(self) -> None:
        for figure_id, card in self._figure_cards.items():
            card.set_selected(figure_id == self._selected_figure_id)
        if self.settings_body is not None:
            self._fill_settings()

    # -- status ------------------------------------------------------------

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self._status = message
        self._status_is_error = error
        self._refresh_status()

    def _clear_status(self) -> None:
        self._status = ""
        self._status_is_error = False
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self.status_pane is None:
            return
        if not self._status:
            self.status_pane.object = ""
            self.status_pane.visible = False
            return
        css = "td-status td-status-error" if self._status_is_error else "td-status"
        self.status_pane.object = (
            f'<p class="{css}">{html.escape(self._status)}</p>'
        )
        self.status_pane.visible = True

    def _recover(self, message: str) -> None:
        """Reload the latest state after a failed operation and show the error."""

        previous = self._selected_scene_id
        self._state = self.store.load()
        self._selected_scene_id = self._resolve_scene(previous)
        self._selected_figure_id = None
        self._delete_confirm = False
        self._set_status(message, error=True)
        self.refresh()

    # -- rendering ---------------------------------------------------------

    def refresh(self) -> None:
        self._scene_buttons = {}
        self._grid = None
        self._figure_cards = {}
        self._grid_figure_ids = {}
        self._view.objects = [
            self._render_header(),
            self._render_status(),
            pn.Row(
                self._render_tray(),
                self._render_canvas(),
                self._render_settings(),
                sizing_mode="stretch_width",
            ),
        ]

    def _render_header(self) -> pn.pane.HTML:
        label = html.escape(self.session.label)
        return pn.pane.HTML(
            '<div class="td-header">'
            "<strong>Transducer dashboard</strong> "
            f'<span class="td-muted">Signed in as {label}</span>'
            '<form method="post" action="logout" style="display:inline; float:right;">'
            '<button type="submit">Sign out</button></form>'
            "</div>",
            sizing_mode="stretch_width",
        )

    def _render_status(self) -> pn.pane.HTML:
        self.status_pane = pn.pane.HTML(
            "", visible=False, sizing_mode="stretch_width"
        )
        self._refresh_status()
        return self.status_pane

    def _render_tray(self) -> pn.Column:
        collapsed = self._tray_collapsed
        toggle = pn.widgets.Button(
            label="▶" if collapsed else "◀",
            color="light",
            width=38,
            margin=(0, 0, 0, 0),
        )
        toggle.on_click(self.toggle_scenes_tray)
        self.tray_toggle = toggle

        header = pn.Row(
            pn.pane.HTML(
                "<strong>Scenes</strong>",
                visible=not collapsed,
                margin=(0, 0, 0, 0),
            ),
            toggle,
            sizing_mode="stretch_width",
            styles={"align-items": "center"},
        )

        self.tray_body = pn.Column(
            self._render_scene_list(),
            self._render_scene_actions(),
            sizing_mode="stretch_width",
            visible=not collapsed,
        )

        return pn.Column(
            header,
            self.tray_body,
            css_classes=["td-tray"],
            width=44 if collapsed else 240,
        )

    def _render_scene_list(self) -> pn.Column:
        self._scene_buttons = {}
        items: list[Any] = []
        for scene in self._state.get("scenes", []):
            selected = scene["id"] == self._selected_scene_id
            button = pn.widgets.Button(
                label=scene["name"],
                color="primary" if selected else "default",
                sizing_mode="stretch_width",
                css_classes=["td-scene-button"]
                + (["td-scene-selected"] if selected else []),
            )
            scene_id = scene["id"]

            def handler(event: Any, selected_id: str = scene_id) -> None:
                self.select_scene(selected_id)

            button.on_click(handler)
            self._scene_buttons[scene["id"]] = button
            items.append(button)

        if not items:
            items.append(pn.pane.HTML('<p class="td-muted">No scenes yet.</p>'))

        self.scene_list = pn.Column(
            *items,
            scroll=True,
            max_height=360,
            sizing_mode="stretch_width",
        )
        return self.scene_list

    def _render_scene_actions(self) -> pn.Column:
        self.new_scene_button = pn.widgets.Button(
            label="+ New scene",
            color="primary",
            sizing_mode="stretch_width",
        )
        self.new_scene_button.on_click(self._on_create_scene)

        self.delete_scene_button = pn.widgets.Button(
            label="Delete scene",
            color="danger",
            sizing_mode="stretch_width",
            disabled=self._selected_scene_id is None,
        )
        self.delete_scene_button.on_click(self.request_delete_scene)

        items: list[Any] = [self.new_scene_button]
        scene = (
            scenes.find_scene(self._state, self._selected_scene_id)
            if self._selected_scene_id is not None
            else None
        )
        if self._delete_confirm and scene is not None:
            self.confirm_delete_button = pn.widgets.Button(
                label="Delete",
                color="danger",
                width=90,
            )
            self.confirm_delete_button.on_click(self.confirm_delete_scene)
            self.cancel_delete_button = pn.widgets.Button(
                label="Cancel",
                color="default",
                width=90,
            )
            self.cancel_delete_button.on_click(self.cancel_delete_scene)
            items.append(
                pn.Column(
                    pn.pane.HTML(
                        '<p class="td-muted">'
                        f"Delete “{html.escape(scene['name'])}” and its figures?"
                        "</p>"
                    ),
                    pn.Row(self.confirm_delete_button, self.cancel_delete_button),
                    sizing_mode="stretch_width",
                )
            )
        else:
            self.confirm_delete_button = None
            self.cancel_delete_button = None
            items.append(self.delete_scene_button)

        return pn.Column(*items, sizing_mode="stretch_width")

    def _render_canvas(self) -> pn.Column:
        scene = (
            scenes.find_scene(self._state, self._selected_scene_id)
            if self._selected_scene_id is not None
            else None
        )
        if scene is None:
            self.scene_title = pn.pane.HTML(
                '<p class="td-muted">No scene selected.</p>'
            )
            self.scene_summary = pn.pane.HTML("")
            self.canvas_body = pn.pane.HTML(
                '<div class="td-empty">Create a scene to get started.</div>',
                sizing_mode="stretch_width",
            )
        else:
            count = len(scene["figures"])
            hint = (
                " · Click an empty cell to add one." if count == 0 else ""
            )
            self.scene_title = pn.pane.HTML(
                f"<h2>{html.escape(scene['name'])}</h2>"
            )
            self.scene_summary = pn.pane.HTML(
                '<p class="td-muted">'
                f"{count} figure{'s' if count != 1 else ''}{hint}"
                "</p>"
            )
            self.canvas_body = self._render_grid(scene)

        return pn.Column(
            self.scene_title,
            self.scene_summary,
            self.canvas_body,
            css_classes=["td-canvas"],
            sizing_mode="stretch_width",
            min_height=420,
        )

    def _render_grid(self, scene: dict[str, Any]) -> pn.Column:
        figures = scene["figures"]
        rows = grid_rows(figures)
        grid = SceneGrid(
            ncols=GRID_COLUMNS,
            nrows=rows,
            sizing_mode="stretch_both",
            css_classes=["scene-grid"],
            stylesheets=[GRID_CSS],
        )

        cards: dict[str, FigureCard] = {}
        for figure in figures:
            card = FigureCard(figure, on_select=self.select_figure)
            card.set_selected(figure["id"] == self._selected_figure_id)
            layout = figure["layout"]
            grid[
                layout["y"] : layout["y"] + layout["h"],
                layout["x"] : layout["x"] + layout["w"],
            ] = card
            cards[figure["id"]] = card

        grid.set_cell_click_handler(self.create_figure)
        grid.param.watch(self._on_grid_state, "state")
        self._grid = grid
        self._figure_cards = cards
        self._grid_figure_ids = {
            str(id(card)): figure_id for figure_id, card in cards.items()
        }
        return pn.Column(
            grid,
            height=grid_height(rows),
            sizing_mode="stretch_width",
            margin=(0, 0, 0, 0),
        )

    def _render_settings(self) -> pn.Column:
        self.settings_body = pn.Column(sizing_mode="stretch_width")
        self._fill_settings()
        return pn.Column(
            pn.pane.HTML("<strong>Figure settings</strong>"),
            self.settings_body,
            css_classes=["td-settings"],
            width=300,
            min_height=420,
        )

    def _fill_settings(self) -> None:
        if self.settings_body is None:
            return
        self.settings_body.objects = self._settings_objects()

    def _settings_objects(self) -> list[Any]:
        figure = self._selected_figure()
        if figure is None:
            return [
                pn.pane.HTML('<p class="td-muted">Select a figure to edit it.</p>')
            ]

        if figure["location_id"] is None:
            location = "All locations"
        else:
            location = f"Location {figure['location_id']}"
        if figure["series_id"] is None:
            analyte = "Not chosen"
        else:
            analyte = f"Series {figure['series_id']}"

        return [
            pn.pane.HTML(f"<strong>{html.escape(figure['title'])}</strong>"),
            pn.pane.HTML(
                '<p class="td-muted">'
                f"Location: {html.escape(location)}<br>"
                f"Analyte: {html.escape(analyte)}<br>"
                f"Range: {html.escape(figure['range'])}"
                "</p>"
            ),
        ]
