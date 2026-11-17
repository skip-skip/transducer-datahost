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
from .read_client import ReadClient
from .settings import DashboardSettings
from .state import StateError, StateStore

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
            self._state = self.store.load()
            self._selected_scene_id = self._resolve_scene(None)
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

    # -- status ------------------------------------------------------------

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self._status = message
        self._status_is_error = error

    def _clear_status(self) -> None:
        self._status = ""
        self._status_is_error = False

    def _recover(self, message: str) -> None:
        """Reload the latest state after a failed operation and show the error."""

        self._state = self.store.load()
        self._selected_scene_id = self._resolve_scene(None)
        self._selected_figure_id = None
        self._delete_confirm = False
        self._set_status(message, error=True)
        self.refresh()

    # -- rendering ---------------------------------------------------------

    def refresh(self) -> None:
        self._scene_buttons = {}
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
        if not self._status:
            self.status_pane = pn.pane.HTML("", visible=False, sizing_mode="stretch_width")
            return self.status_pane
        css = "td-status td-status-error" if self._status_is_error else "td-status"
        self.status_pane = pn.pane.HTML(
            f'<p class="{css}">{html.escape(self._status)}</p>',
            sizing_mode="stretch_width",
        )
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
            self.scene_title = pn.pane.HTML(f"<h2>{html.escape(scene['name'])}</h2>")
            self.scene_summary = pn.pane.HTML(
                '<p class="td-muted">'
                f"{count} figure{'s' if count != 1 else ''}"
                "</p>"
            )
            if count == 0:
                self.canvas_body = pn.pane.HTML(
                    '<div class="td-empty">No figures yet.</div>',
                    sizing_mode="stretch_width",
                )
            else:
                titles = "".join(
                    f"<li>{html.escape(figure['title'])}</li>"
                    for figure in scene["figures"]
                )
                self.canvas_body = pn.pane.HTML(
                    f"<ul>{titles}</ul>",
                    sizing_mode="stretch_width",
                )

        return pn.Column(
            self.scene_title,
            self.scene_summary,
            self.canvas_body,
            css_classes=["td-canvas"],
            sizing_mode="stretch_width",
            min_height=420,
        )

    def _render_settings(self) -> pn.Column:
        self.settings_body = pn.Column(
            pn.pane.HTML('<p class="td-muted">Select a figure to edit it.</p>'),
            sizing_mode="stretch_width",
        )
        return pn.Column(
            pn.pane.HTML("<strong>Figure settings</strong>"),
            self.settings_body,
            css_classes=["td-settings"],
            width=300,
            min_height=420,
        )
