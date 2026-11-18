"""Panel scene workspace: full-height ribbon drawers plus the canvas.

The shell owns one browser session's view of the shared scene state. Scenes are
shared between every read token, while the selected scene, selected figure, and
scene-card collapse state are stored per token label in ``state["ui"]``. The
settings-card collapse state is session-only and never persisted.

Both cards are ``pn.Card`` ribbons in a row on the left edge of the screen.
Their vertical headers stay docked as 44px tabs while their bodies open to the
right and span the full viewport height. The scenes ribbon is leftmost; opening
it pushes the figure settings ribbon (and the canvas) to the right.
"""

from __future__ import annotations

import html
from typing import Any

import panel as pn

from . import charts, scenes
from .auth import Session
from .grid import (
    GRID_CSS,
    FigureCard,
    SceneGrid,
    chart_placeholder,
    grid_height,
    grid_rows,
)
from .read_client import ReadApiError, ReadClient
from .settings import DashboardSettings
from .state import (
    DEFAULT_RANGE,
    GRID_COLUMNS,
    RANGES,
    StateError,
    StateStore,
)

RAIL_TAB_WIDTH = 44
DRAWER_WIDTH = 340

SHELL_CSS = """
.td-shell { background: #f5f6f8; }
.td-header { background: #fff; border-bottom: 1px solid #d8dde6; padding: 0.65rem 1rem; }
.td-rail { background: #fff; }
.td-rail-card { border: 0; }
.td-canvas { background: #fff; padding: 1rem 1.25rem; }
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

RAIL_CSS = """
:host {
  min-width: 0 !important;
  max-width: none !important;
  width: max-content !important;
}
"""

DRAWER_CSS = f"""
:host {{
  display: flex !important;
  flex-direction: row !important;
  align-items: stretch !important;
  align-self: stretch !important;
  flex: 0 0 auto !important;
  gap: 0 !important;
  overflow: visible !important;
  height: auto !important;
  width: auto !important;
  min-width: 0 !important;
  max-width: none !important;
  background: #fff;
  box-shadow: none !important;
  outline: none !important;
}}
.card-header {{
  flex: 0 0 {RAIL_TAB_WIDTH}px !important;
  width: {RAIL_TAB_WIDTH}px !important;
  min-height: 0 !important;
  height: auto !important;
  align-self: stretch !important;
  margin-right: 0 !important;
  padding: 0.5rem 0 !important;
  position: static !important;
  display: flex !important;
  flex-direction: column !important;
  align-items: center !important;
  justify-content: flex-start !important;
  border: 0 !important;
  border-right: 1px solid #d8dde6;
  border-bottom: 1px solid #d8dde6;
  border-radius: 0 !important;
  background: #fff !important;
}}
.card-button {{
  margin: 0 0 0.5rem 0 !important;
  flex: none;
}}
.card-header-row {{
  align-self: stretch !important;
  justify-content: center !important;
  align-items: center !important;
  margin: 0 !important;
  padding: 0 !important;
  width: auto !important;
  height: auto !important;
  max-height: none !important;
}}
.td-card-body {{
  flex: 0 0 {DRAWER_WIDTH}px !important;
  width: {DRAWER_WIDTH}px !important;
  max-width: calc(100vw - {RAIL_TAB_WIDTH}px) !important;
  min-width: 0 !important;
  height: auto !important;
  align-self: stretch !important;
  overflow-y: auto !important;
  background: #fff;
  border: 1px solid #d8dde6;
  border-left: 0;
  padding: 0.75rem;
  box-shadow: 6px 0 16px rgba(0, 0, 0, 0.12);
}}
"""

DRAWER_TITLE_CSS = """
:host {
  writing-mode: vertical-rl;
  transform: rotate(180deg);
  align-self: center !important;
  font-size: 1rem;
  font-weight: 600;
  white-space: nowrap;
  margin: 0 !important;
  padding: 0 !important;
}
"""


def drawer_title(text: str) -> pn.pane.HTML:
    """A vertical tab label that reads bottom-to-top."""

    return pn.pane.HTML(
        f"<span>{html.escape(text)}</span>",
        css_classes=["td-drawer-title"],
        stylesheets=[DRAWER_TITLE_CSS],
        margin=0,
    )


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
        self._settings_collapsed = False
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
        self.tray_card: pn.Card | None = None
        self.tray_body: pn.Column | None = None
        self.settings_card: pn.Card | None = None
        self.settings_body: pn.Column | None = None
        self.rail: pn.Row | None = None
        self.canvas_body: Any = None
        self.settings_title: pn.widgets.TextInput | None = None
        self.settings_location: pn.widgets.Select | None = None
        self.settings_analyte: pn.widgets.Select | None = None
        self.settings_range: pn.widgets.RadioButtonGroup | None = None
        self.settings_message: pn.pane.HTML | None = None
        self.settings_save: pn.widgets.Button | None = None
        self.settings_delete: pn.widgets.Button | None = None
        self.settings_confirm_delete: pn.widgets.Button | None = None
        self.settings_cancel_delete: pn.widgets.Button | None = None
        self._figure_delete_confirm_id: str | None = None
        self._locations_cache: list[dict[str, Any]] | None = None
        self._view = pn.Column(
            sizing_mode="stretch_both",
            css_classes=["td-shell"],
            stylesheets=[SHELL_CSS],
            styles={
                "height": "100vh",
                "min-height": "0",
                "max-height": "100vh",
                "overflow": "hidden",
            },
            margin=0,
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
        card = self.tray_card
        if card is None:
            self._tray_collapsed = not self._tray_collapsed
            self._save_ui()
            return
        card.collapsed = not card.collapsed

    def _on_tray_toggle(self, event: Any) -> None:
        self._tray_collapsed = bool(event.new)
        self._save_ui()

    def _set_settings_collapsed(self, collapsed: bool) -> None:
        self._settings_collapsed = collapsed
        card = self.settings_card
        if card is not None and card.collapsed != collapsed:
            card.collapsed = collapsed

    def _on_settings_toggle(self, event: Any) -> None:
        self._settings_collapsed = bool(event.new)

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

    def _default_range(self) -> str:
        days = self.settings.default_window_days
        if days <= 1:
            return "24h"
        if days <= 7:
            return "7d"
        return "30d"

    def create_figure(self, x: int, y: int) -> dict[str, Any] | None:
        scene_id = self._selected_scene_id
        if scene_id is None:
            return None
        label = self.session.label

        def operation(data: dict[str, Any]) -> dict[str, Any]:
            figure = scenes.add_figure(
                data, scene_id, x, y, range_key=self._default_range()
            )
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
        self._settings_collapsed = False
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
        if self._selected_figure_id is not None:
            self._set_settings_collapsed(False)
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
        self.tray_card = None
        self.tray_body = None
        self.settings_card = None
        self.settings_body = None
        self.rail = None
        self._view.objects = [
            self._render_header(),
            self._render_status(),
            pn.Row(
                self._render_rail(),
                self._render_canvas(),
                sizing_mode="stretch_both",
                styles={"min-height": "0"},
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

    def _render_rail(self) -> pn.Row:
        self.rail = pn.Row(
            self._render_tray(),
            self._render_settings(),
            css_classes=["td-rail"],
            stylesheets=[RAIL_CSS],
            margin=(0, 0, 0, 0),
            styles={
                "position": "relative",
                "z-index": "2",
                "overflow": "visible",
                "flex": "0 0 auto",
                "width": "max-content",
                "height": "100%",
            },
        )
        return self.rail

    def _render_tray(self) -> pn.Card:
        self.tray_body = pn.Column(
            self._render_scene_list(),
            self._render_scene_actions(),
            sizing_mode="stretch_width",
            css_classes=["td-card-body"],
        )
        card = pn.Card(
            self.tray_body,
            header=drawer_title("Scenes"),
            collapsed=self._tray_collapsed,
            header_background="#fff",
            header_color="#1d2433",
            css_classes=["td-rail-card"],
            stylesheets=[DRAWER_CSS],
            margin=(0, 0, 0, 0),
        )
        card.param.watch(self._on_tray_toggle, "collapsed")
        self.tray_card = card
        return card

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
            sizing_mode="stretch_both",
            styles={
                "position": "relative",
                "z-index": "1",
                "min-height": "0",
                "overflow-y": "auto",
            },
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
        self._schedule_chart_load(scene["id"])
        return pn.Column(
            grid,
            height=grid_height(rows),
            sizing_mode="stretch_width",
            margin=(0, 0, 0, 0),
        )

    def _schedule_chart_load(self, scene_id: str) -> None:
        """Load every chart in the scene after the shell has rendered.

        Outside a server session (tests, scripts) ``pn.state.execute`` runs the
        callback immediately, so charts are ready when the shell is built. In
        the browser the cards first show their loading placeholder and are
        filled in on the next document tick.
        """

        if not self._figure_cards:
            return
        pn.state.execute(lambda: self.load_charts(scene_id))

    def load_charts(self, scene_id: str) -> None:
        """Fetch and render the chart for every figure in ``scene_id``."""

        if scene_id != self._selected_scene_id:
            return
        scene = scenes.find_scene(self._state, scene_id)
        if scene is None:
            return
        for figure in scene["figures"]:
            card = self._figure_cards.get(figure["id"])
            if card is None:
                continue
            card.set_chart(self._render_chart(figure))

    def _render_chart(self, figure: dict[str, Any]) -> Any:
        series_id = figure["series_id"]
        if series_id is None:
            return chart_placeholder("Choose a location and analyte.")

        start, end = charts.window(
            figure["range"], site_timezone=self.settings.site_timezone
        )
        try:
            rows = self.client.points(
                self.session.token,
                series_id,
                start=start,
                end=end,
                points=self.settings.max_points,
                tz="local",
                location_id=figure["location_id"],
            )
        except ReadApiError as exc:
            return chart_placeholder(
                f"Could not load chart: {exc.detail}", error=True
            )

        plot = charts.build_chart(rows, site_timezone=self.settings.site_timezone)
        if plot is None:
            return chart_placeholder("No data available for this range.")
        return pn.pane.Bokeh(plot, sizing_mode="stretch_both")

    def _render_settings(self) -> pn.Card:
        self.settings_body = pn.Column(
            sizing_mode="stretch_width",
            css_classes=["td-card-body"],
        )
        self._fill_settings()
        card = pn.Card(
            self.settings_body,
            header=drawer_title("Figure settings"),
            collapsed=self._settings_collapsed,
            header_background="#fff",
            header_color="#1d2433",
            css_classes=["td-rail-card"],
            stylesheets=[DRAWER_CSS],
            margin=(0, 0, 0, 0),
        )
        card.param.watch(self._on_settings_toggle, "collapsed")
        self.settings_card = card
        return card

    def _fill_settings(self) -> None:
        if self.settings_body is None:
            return
        self.settings_body.objects = self._settings_objects()

    def _settings_objects(self) -> list[Any]:
        figure = self._selected_figure()
        if figure is None:
            self.settings_title = None
            self.settings_location = None
            self.settings_analyte = None
            self.settings_range = None
            self.settings_message = None
            self.settings_save = None
            self.settings_delete = None
            self.settings_confirm_delete = None
            self.settings_cancel_delete = None
            return [
                pn.pane.HTML('<p class="td-muted">Select a figure to edit it.</p>')
            ]

        message = pn.pane.HTML("", visible=False, sizing_mode="stretch_width")
        self.settings_message = message

        title = pn.widgets.TextInput(
            label="Title",
            value=figure["title"],
            sizing_mode="stretch_width",
        )
        self.settings_title = title

        location_options: list[tuple[str, str]] = [("All locations", "")]
        location_error = ""
        try:
            for row in self._load_locations():
                location_id = row.get("location_id")
                if not isinstance(location_id, int) or isinstance(location_id, bool):
                    continue
                label = str(row.get("name") or f"Location {location_id}")
                location_options.append((label, str(location_id)))
        except ReadApiError as exc:
            location_error = f"Could not load locations: {exc.detail}"

        location_values = [value for _, value in location_options]
        location_value = (
            "" if figure["location_id"] is None else str(figure["location_id"])
        )
        if location_value not in location_values:
            location_value = ""
        location = pn.widgets.Select(
            label="Location",
            options=location_options,
            value=location_value,
            sizing_mode="stretch_width",
        )
        self.settings_location = location
        location.param.watch(self._on_settings_location_change, "value")

        analyte_options: list[tuple[str, str]] = [("Choose an analyte", "")]
        analyte_error = ""
        try:
            for row in self._load_series(self._selected_id(location_value)):
                series_id = row.get("series_id")
                if not isinstance(series_id, int) or isinstance(series_id, bool):
                    continue
                analyte_options.append((self._series_label(row), str(series_id)))
        except ReadApiError as exc:
            analyte_error = f"Could not load analytes: {exc.detail}"

        analyte_values = [value for _, value in analyte_options]
        series_value = "" if figure["series_id"] is None else str(figure["series_id"])
        if series_value not in analyte_values:
            series_value = ""
        analyte = pn.widgets.Select(
            label="Analyte",
            options=analyte_options,
            value=series_value,
            sizing_mode="stretch_width",
        )
        self.settings_analyte = analyte

        range_value = figure["range"] if figure["range"] in RANGES else DEFAULT_RANGE
        range_group = pn.widgets.RadioButtonGroup(
            label="Range",
            options=list(RANGES),
            value=range_value,
            sizing_mode="stretch_width",
        )
        self.settings_range = range_group

        save = pn.widgets.Button(
            label="Save",
            color="primary",
            sizing_mode="stretch_width",
        )
        save.on_click(self._on_save_figure)
        self.settings_save = save
        self.settings_delete = None
        self.settings_confirm_delete = None
        self.settings_cancel_delete = None

        delete_controls: list[Any]
        if self._figure_delete_confirm_id == figure["id"]:
            confirm = pn.widgets.Button(
                label="Confirm delete",
                color="danger",
                sizing_mode="stretch_width",
            )
            cancel = pn.widgets.Button(
                label="Cancel",
                color="light",
                sizing_mode="stretch_width",
            )
            confirm.on_click(self._on_confirm_delete_figure)
            cancel.on_click(self._on_cancel_delete_figure)
            self.settings_confirm_delete = confirm
            self.settings_cancel_delete = cancel
            delete_controls = [
                pn.pane.HTML('<p class="td-muted">Delete this figure?</p>'),
                confirm,
                cancel,
            ]
        else:
            delete = pn.widgets.Button(
                label="Delete figure",
                color="danger",
                sizing_mode="stretch_width",
            )
            delete.on_click(self._on_request_delete_figure)
            self.settings_delete = delete
            delete_controls = [delete]

        if location_error:
            self._set_settings_message(location_error, error=True)
        elif analyte_error:
            self._set_settings_message(analyte_error, error=True)
        elif len(analyte_options) == 1:
            self._set_settings_message("No analytes are available for this selection.")
        elif figure["series_id"] is None:
            self._set_settings_message("Choose a location and analyte.")

        return [
            title,
            location,
            analyte,
            range_group,
            save,
            *delete_controls,
            message,
        ]

    def _load_locations(self) -> list[dict[str, Any]]:
        if self._locations_cache is None:
            self._locations_cache = self.client.locations(self.session.token)
        return self._locations_cache

    def _load_series(self, location_id: int | None) -> list[dict[str, Any]]:
        if location_id is None:
            return self.client.series(self.session.token)
        return self.client.series_at_location(self.session.token, location_id)

    @staticmethod
    def _series_label(row: dict[str, Any]) -> str:
        parts = [str(row.get("identification") or "Series")]
        serial = row.get("serial_number")
        if serial:
            parts.append(str(serial))
        channel = row.get("channel_no")
        if channel:
            parts.append(f"ch{channel}")
        unit = row.get("unit")
        if unit:
            parts.append(f"[{unit}]")
        return " · ".join(parts)

    @staticmethod
    def _selected_id(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _set_settings_message(self, message: str, *, error: bool = False) -> None:
        pane = self.settings_message
        if pane is None:
            return
        if not message:
            pane.object = ""
            pane.visible = False
            return
        css = "td-status td-status-error" if error else "td-muted"
        pane.object = f'<p class="{css}">{html.escape(message)}</p>'
        pane.visible = True

    def _on_settings_location_change(self, event: Any) -> None:
        analyte = self.settings_analyte
        if analyte is None:
            return
        current = analyte.value
        location_id = self._selected_id(event.new)
        try:
            rows = self._load_series(location_id)
        except ReadApiError as exc:
            analyte.options = [("Choose an analyte", "")]
            analyte.value = ""
            self._set_settings_message(
                f"Could not load analytes: {exc.detail}", error=True
            )
            return

        options: list[tuple[str, str]] = [("Choose an analyte", "")]
        for row in rows:
            series_id = row.get("series_id")
            if isinstance(series_id, int) and not isinstance(series_id, bool):
                options.append((self._series_label(row), str(series_id)))
        analyte.options = options
        analyte.value = current if current in [value for _, value in options] else ""
        if len(options) == 1:
            self._set_settings_message("No analytes are available for this location.")
        else:
            self._set_settings_message("")

    def _on_save_figure(self, event: Any = None) -> None:
        scene_id = self._selected_scene_id
        figure_id = self._selected_figure_id
        if scene_id is None or figure_id is None:
            return

        title = (self.settings_title.value if self.settings_title else "") or ""
        title = title.strip()
        location_id = self._selected_id(
            self.settings_location.value if self.settings_location else None
        )
        series_id = self._selected_id(
            self.settings_analyte.value if self.settings_analyte else None
        )
        range_value = self.settings_range.value if self.settings_range else DEFAULT_RANGE

        if not title:
            self._set_settings_message("Title must not be blank.", error=True)
            return
        if series_id is None:
            self._set_settings_message("Choose an analyte.", error=True)
            return
        if range_value not in RANGES:
            self._set_settings_message("Choose a range.", error=True)
            return

        error = self._validate_figure_selection(location_id, series_id)
        if error is not None:
            self._set_settings_message(error, error=True)
            return

        def operation(data: dict[str, Any]) -> dict[str, Any]:
            return scenes.update_figure(
                data,
                scene_id,
                figure_id,
                title=title,
                location_id=location_id,
                series_id=series_id,
                range=range_value,
            )

        try:
            self._state, figure = self.store.mutate(operation)
        except (scenes.SceneError, StateError) as exc:
            self._recover(str(exc))
            return

        card = self._figure_cards.get(figure_id)
        if card is not None:
            card.set_title(figure["title"])
            card.set_chart(self._render_chart(figure))
        self._figure_delete_confirm_id = None
        self._set_status("Saved")
        self._fill_settings()

    def _validate_figure_selection(
        self, location_id: int | None, series_id: int
    ) -> str | None:
        try:
            if location_id is None:
                self.client.series_detail(self.session.token, series_id)
            else:
                rows = self.client.series_at_location(self.session.token, location_id)
                if series_id not in {row.get("series_id") for row in rows}:
                    return "That analyte has no readings at this location."
        except ReadApiError as exc:
            return f"Could not verify the selection: {exc.detail}"
        return None

    def _on_request_delete_figure(self, event: Any = None) -> None:
        if self._selected_figure_id is None:
            return
        self._figure_delete_confirm_id = self._selected_figure_id
        self._fill_settings()

    def _on_cancel_delete_figure(self, event: Any = None) -> None:
        self._figure_delete_confirm_id = None
        self._fill_settings()

    def _on_confirm_delete_figure(self, event: Any = None) -> None:
        self.delete_selected_figure()

    def delete_selected_figure(self) -> None:
        scene_id = self._selected_scene_id
        figure_id = self._selected_figure_id
        if scene_id is None or figure_id is None:
            return

        try:
            self._state, _ = self.store.mutate(
                lambda data: scenes.delete_figure(data, scene_id, figure_id)
            )
        except (scenes.SceneError, StateError) as exc:
            self._recover(str(exc))
            return

        self._selected_figure_id = None
        self._figure_delete_confirm_id = None
        self._clear_status()
        self.refresh()
