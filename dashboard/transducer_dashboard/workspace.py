"""Panel scene workspace: full-height ribbon drawers plus the canvas.

The shell owns one browser session's view of the shared scene state. Scenes are
shared between every read token, while the selected scene, selected figure, and
scene-card collapse state are stored per token label in ``state["ui"]``. The
settings-card collapse state is session-only and never persisted.

Figure edits are kept in a session-local draft until the user presses Publish.
The draft previews in the settings form and the figure chart, and the card gets
a yellow (unselected) or orange (selected) border while it has unpublished
changes. Publishing writes only the changed fields; a conflicting server-side
edit opens a modal offering overwrite or revert.

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
    GRID_BACKGROUND,
    GRID_CSS,
    FigureCard,
    SceneGrid,
    chart_placeholder,
    grid_height,
)
from .read_client import ReadApiError, ReadClient
from .settings import DashboardSettings
from .state import (
    DEFAULT_RANGE,
    GRID_COLUMNS,
    GRID_ROWS,
    RANGES,
    StateError,
    StateStore,
)

RAIL_TAB_WIDTH = 44
DRAWER_WIDTH = 340

SHELL_CSS = """
.td-shell { background: #f5f6f8; }
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

# The header is an HTML pane, whose content lives in its own shadow root, so
# shell stylesheets do not reach it. Inline styles keep it one banner row.
HEADER_GRID_STYLE = (
    "display:grid;grid-template-columns:1fr auto 1fr;align-items:center;"
    "gap:1rem;background:#fff;border-bottom:1px solid #d8dde6;"
    "padding:0.65rem 1rem;"
)
HEADER_CENTER_STYLE = (
    "justify-self:center;min-width:0;max-width:60vw;text-align:center;"
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
)
HEADER_RIGHT_STYLE = (
    "justify-self:end;display:flex;align-items:center;gap:0.75rem;"
)
MUTED_STYLE = "color:#5b6472;"

# The notice is an HTML pane too, so it carries its own stylesheet. Fixing it to
# the viewport keeps transient messages out of the shell's layout.
NOTICE_CSS = """
:host {
  position: fixed;
  right: 7.5rem;
  bottom: 1rem;
  z-index: 9999;
  width: auto;
  max-width: min(28rem, calc(100vw - 9rem));
  pointer-events: none;
}
.td-notice {
  margin: 0;
  padding: 0.5rem 0.9rem;
  border: 1px solid #d8dde6;
  border-radius: 0.4rem;
  background: #fff;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.16);
  color: #1d2433;
  animation: td-notice-dismiss 3s forwards;
}
.td-notice-error { color: #a11616; border-color: #e3b4b4; }
@keyframes td-notice-dismiss {
  0%, 85% { opacity: 1; }
  100% { opacity: 0; visibility: hidden; }
}
"""

# The lock toggle is fixed to the viewport, like the notice, so it overlays the
# grid instead of taking layout space. The notice sits to its left.
LOCK_CSS = """
:host {
  position: fixed;
  right: 1rem;
  bottom: 1rem;
  z-index: 9000;
  width: max-content !important;
  height: auto !important;
}
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


class PublishConflict(Exception):
    """Raised inside a state mutation when publishing would overwrite a change."""


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
        self._locked = False

        # Session-local drafts keyed by figure ID. A draft holds only the
        # settings fields the user has changed, so publishing writes just those
        # fields. Baselines capture the stored values when the draft started and
        # are used to detect a conflicting change from another session.
        self._drafts: dict[str, dict[str, Any]] = {}
        self._draft_baselines: dict[str, tuple[Any, ...]] = {}
        self._suspend_settings = False
        self._pending_publish: tuple[str, str] | None = None

        self._scene_buttons: dict[str, pn.widgets.Button] = {}
        self.confirm_delete_button: pn.widgets.Button | None = None
        self.cancel_delete_button: pn.widgets.Button | None = None
        self.notice: pn.pane.HTML
        self.lock_control: pn.Column
        self.lock_button: pn.widgets.Button
        self.header: pn.pane.HTML | None = None
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
        self.settings_publish: pn.widgets.Button | None = None
        self.settings_delete: pn.widgets.Button | None = None
        self.settings_confirm_delete: pn.widgets.Button | None = None
        self.settings_cancel_delete: pn.widgets.Button | None = None
        self.conflict_message: pn.pane.HTML | None = None
        self.conflict_modal: pn.Modal | None = None
        self.conflict_overwrite: pn.widgets.Button | None = None
        self.conflict_revert: pn.widgets.Button | None = None
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

        self.conflict_modal = self._build_conflict_modal()
        self._notice_seq = 0
        self.notice = pn.pane.HTML(
            "",
            visible=False,
            stylesheets=[NOTICE_CSS],
            margin=0,
        )
        self.lock_control = self._build_lock_control()

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
    def locked(self) -> bool:
        return self._locked

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
            self._notify(str(exc), error=True)

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
        self._cancel_pending_publish()
        self._delete_confirm = False
        self._clear_notice()
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
        self._clear_notice()
        self.refresh()
        return scene

    def request_delete_scene(self, event: Any = None) -> None:
        if self._selected_scene_id is None:
            self._notify("Select a scene to delete.", error=True)
            self.refresh()
            return
        self._delete_confirm = True
        self._clear_notice()
        self.refresh()

    def cancel_delete_scene(self, event: Any = None) -> None:
        self._delete_confirm = False
        self._clear_notice()
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
        self._clear_notice()
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

    def _on_settings_toggle(self, event: Any) -> None:
        self._settings_collapsed = bool(event.new)

    # -- figure actions ----------------------------------------------------

    def select_figure(self, figure_id: str, event: Any = None) -> None:
        scene_id = self._selected_scene_id
        if scene_id is None or self._locked:
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
        self._cancel_pending_publish()
        self._clear_notice()
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
        if scene_id is None or self._locked:
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

        self._refresh_after_create(figure)
        return figure

    def create_figure_span(
        self, x: int, y: int, width: int, height: int
    ) -> dict[str, Any] | None:
        """Create a figure with the exact span painted on the canvas."""

        scene_id = self._selected_scene_id
        if scene_id is None or self._locked:
            return None
        label = self.session.label

        def operation(data: dict[str, Any]) -> dict[str, Any]:
            figure = scenes.add_figure_at(
                data,
                scene_id,
                x,
                y,
                width,
                height,
                range_key=self._default_range(),
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

        self._refresh_after_create(figure)
        return figure

    def _refresh_after_create(self, figure: dict[str, Any]) -> None:
        """Show a new figure without rebuilding the whole shell."""

        self._selected_figure_id = figure["id"]
        self._delete_confirm = False
        self._clear_notice()
        if not self._append_figure_card(figure):
            self._rebuild_canvas()
            return
        self._view.objects = [self._render_header(), *self._view.objects[1:]]
        self._update_selection()
        self._schedule_figure_chart_load(figure["id"])

    def _append_figure_card(self, figure: dict[str, Any]) -> bool:
        """Add the card to the live grid without rebuilding it."""

        if self._grid is None or self._selected_scene_id is None:
            return False
        scene = scenes.find_scene(self._state, self._selected_scene_id)
        if scene is None:
            return False
        display = dict(self._effective_figure(figure))
        display["title"] = self._figure_title(display)
        card = FigureCard(display, on_select=self.select_figure)
        card.set_selected(figure["id"] == self._selected_figure_id)
        card.set_unpublished(figure["id"] in self._drafts)
        card.set_locked(self._locked)
        layout = figure["layout"]
        self._grid[
            layout["y"] : layout["y"] + layout["h"],
            layout["x"] : layout["x"] + layout["w"],
        ] = card
        self._figure_cards[figure["id"]] = card
        self._grid_figure_ids[str(id(card))] = figure["id"]
        return True

    def _rebuild_canvas(self) -> None:
        """Fall back to a canvas-only rebuild when no live grid can be used."""

        objects = list(self._view.objects)
        if len(objects) < 2 or not isinstance(objects[1], pn.Row):
            self.refresh()
            return
        row = objects[1]
        if len(row.objects) < 2:
            self.refresh()
            return
        row.objects = [row.objects[0], self._render_canvas()]
        objects[0] = self._render_header()
        self._view.objects = objects
        self._update_selection()

    def _schedule_figure_chart_load(self, figure_id: str) -> None:
        pn.state.execute(lambda: self._refresh_figure_chart(figure_id))

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

    def _selected_figure(self) -> dict[str, Any] | None:
        if self._selected_scene_id is None or self._selected_figure_id is None:
            return None
        scene = scenes.find_scene(self._state, self._selected_scene_id)
        if scene is None:
            return None
        return scenes.find_figure(scene, self._selected_figure_id)

    # -- unpublished drafts ------------------------------------------------

    @staticmethod
    def _figure_title(figure: dict[str, Any]) -> str:
        return str(figure.get("title") or "Untitled figure")

    @staticmethod
    def _settings_signature(figure: dict[str, Any]) -> tuple[Any, ...]:
        return (
            figure["title"],
            figure["location_id"],
            figure["series_id"],
            figure["range"],
        )

    def _find_figure(self, figure_id: str) -> dict[str, Any] | None:
        for scene in self._state.get("scenes", []):
            figure = scenes.find_figure(scene, figure_id)
            if figure is not None:
                return figure
        return None

    def _effective_figure(self, figure: dict[str, Any]) -> dict[str, Any]:
        """Overlay the session-local draft on a stored figure."""

        draft = self._drafts.get(figure["id"])
        if not draft:
            return figure
        effective = dict(figure)
        effective.update(draft)
        return effective

    def has_unpublished_changes(self, figure_id: str) -> bool:
        return figure_id in self._drafts

    def _drop_draft(self, figure_id: str) -> None:
        self._drafts.pop(figure_id, None)
        self._draft_baselines.pop(figure_id, None)
        if self._pending_publish is not None and self._pending_publish[1] == figure_id:
            self._pending_publish = None

    def _cancel_pending_publish(self) -> None:
        if self._pending_publish is None:
            return
        self._pending_publish = None
        if self.conflict_modal is not None:
            self.conflict_modal.open = False

    def _set_draft_field(
        self, figure_id: str, *, refresh_chart: bool = False, **fields: Any
    ) -> None:
        stored = self._find_figure(figure_id)
        if stored is None:
            return
        if figure_id not in self._drafts:
            self._draft_baselines[figure_id] = self._settings_signature(stored)
        self._drafts.setdefault(figure_id, {}).update(fields)
        self._sync_draft(figure_id, refresh_chart=refresh_chart)

    def _sync_draft(self, figure_id: str, *, refresh_chart: bool = False) -> None:
        """Drop overrides that match the stored figure and update the card."""

        draft = self._drafts.get(figure_id)
        stored = self._find_figure(figure_id)
        if draft is None or stored is None:
            self._drafts.pop(figure_id, None)
            self._draft_baselines.pop(figure_id, None)
        else:
            cleaned = {
                key: value
                for key, value in draft.items()
                if stored.get(key) != value
            }
            if cleaned:
                self._drafts[figure_id] = cleaned
            else:
                self._drafts.pop(figure_id, None)
                self._draft_baselines.pop(figure_id, None)

        self._update_figure_indicators()
        if refresh_chart:
            self._refresh_figure_chart(figure_id)

    def _update_figure_indicators(self) -> None:
        for figure_id, card in self._figure_cards.items():
            card.set_unpublished(figure_id in self._drafts)
            figure = self._find_figure(figure_id)
            if figure is not None:
                card.set_title(self._figure_title(self._effective_figure(figure)))
        self._update_publish_button()

    def _update_publish_button(self) -> None:
        button = self.settings_publish
        if button is None:
            return
        unpublished = (
            self._selected_figure_id is not None
            and self._selected_figure_id in self._drafts
        )
        if unpublished:
            button.label = "Publish"
            button.color = "primary"
            button.disabled = False
        else:
            button.label = "Published"
            button.color = "default"
            button.disabled = True

    def _refresh_figure_chart(self, figure_id: str) -> None:
        card = self._figure_cards.get(figure_id)
        stored = self._find_figure(figure_id)
        if card is None or stored is None:
            return
        card.set_chart(self._render_chart(self._effective_figure(stored)))

    def _prune_drafts(self) -> None:
        if not self._drafts:
            return
        existing: set[str] = set()
        for scene in self._state.get("scenes", []):
            for figure in scene.get("figures", []):
                existing.add(figure["id"])
        for figure_id in list(self._drafts):
            if figure_id not in existing:
                self._drafts.pop(figure_id, None)
                self._draft_baselines.pop(figure_id, None)
        if (
            self._pending_publish is not None
            and self._pending_publish[1] not in existing
        ):
            self._pending_publish = None

    def _update_selection(self) -> None:
        for figure_id, card in self._figure_cards.items():
            card.set_selected(figure_id == self._selected_figure_id)
        if self.settings_body is not None:
            self._fill_settings()

    # -- layout lock -------------------------------------------------------

    def _build_lock_control(self) -> pn.Column:
        self.lock_button = pn.widgets.Button(
            label="Lock",
            css_classes=["td-lock-button"],
            margin=0,
            styles={
                "border-radius": "999px",
                "padding": "0.45rem 0.9rem",
                "font-weight": "600",
                "box-shadow": "0 4px 16px rgba(0, 0, 0, 0.16)",
            },
        )
        self.lock_button.on_click(self._on_lock_toggle)
        return pn.Column(
            self.lock_button,
            stylesheets=[LOCK_CSS],
            margin=0,
        )

    def _on_lock_toggle(self, event: Any) -> None:
        self.set_locked(not self._locked)

    def _refresh_lock(self) -> None:
        self.lock_button.disabled = self._selected_scene_id is None
        self.lock_button.label = "Unlock" if self._locked else "Lock"
        self.lock_button.button_type = "warning" if self._locked else "light"

    def set_locked(self, locked: bool) -> None:
        self._locked = bool(locked)
        self._refresh_lock()
        if self._grid is not None:
            self._grid.locked = self._locked
        for card in self._figure_cards.values():
            card.set_locked(self._locked)

    # -- notices -----------------------------------------------------------

    def _notify(self, message: str, *, error: bool = False) -> None:
        css = "td-notice td-notice-error" if error else "td-notice"
        # The sequence number restarts the CSS fade when the same message is
        # shown again before the pane's value would otherwise be unchanged.
        self._notice_seq += 1
        self.notice.object = (
            f'<p class="{css}" data-seq="{self._notice_seq}">'
            f"{html.escape(message)}</p>"
        )
        self.notice.visible = True

    def _clear_notice(self) -> None:
        self.notice.object = ""
        self.notice.visible = False

    def _recover(self, message: str) -> None:
        """Reload the latest state after a failed operation and show the error."""

        previous = self._selected_scene_id
        self._state = self.store.load()
        self._selected_scene_id = self._resolve_scene(previous)
        self._selected_figure_id = None
        self._delete_confirm = False
        self._notify(message, error=True)
        self.refresh()

    # -- rendering ---------------------------------------------------------

    def refresh(self) -> None:
        self._prune_drafts()
        self._scene_buttons = {}
        self._grid = None
        self._figure_cards = {}
        self._grid_figure_ids = {}
        self.tray_card = None
        self.tray_body = None
        self.settings_card = None
        self.settings_body = None
        self.rail = None
        self._refresh_lock()
        self._view.objects = [
            self._render_header(),
            pn.Row(
                self._render_rail(),
                self._render_canvas(),
                sizing_mode="stretch_both",
                styles={"min-height": "0"},
            ),
            self.conflict_modal,
            self.notice,
            self.lock_control,
        ]

    def _render_header(self) -> pn.pane.HTML:
        label = html.escape(self.session.label)
        scene = (
            scenes.find_scene(self._state, self._selected_scene_id)
            if self._selected_scene_id is not None
            else None
        )
        if scene is None:
            summary = (
                f'<span class="td-muted" style="{MUTED_STYLE}">'
                "No scene selected</span>"
            )
        else:
            count = len(scene["figures"])
            summary = (
                f"<strong>{html.escape(scene['name'])}</strong> "
                f'<span class="td-muted" style="{MUTED_STYLE}">· {count} '
                f'figure{"s" if count != 1 else ""}</span>'
            )
        self.header = pn.pane.HTML(
            f'<div class="td-header" style="{HEADER_GRID_STYLE}">'
            '<div class="td-header-left" style="justify-self:start">'
            "<strong>Transducer dashboard</strong>"
            "</div>"
            f'<div class="td-header-center" style="{HEADER_CENTER_STYLE}">'
            f"{summary}</div>"
            f'<div class="td-header-right" style="{HEADER_RIGHT_STYLE}">'
            f'<span class="td-muted" style="{MUTED_STYLE}">'
            f"Signed in as {label}</span>"
            '<form method="post" action="logout" style="margin:0">'
            '<button type="submit">Sign out</button></form>'
            "</div>"
            "</div>",
            sizing_mode="stretch_width",
        )
        return self.header

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
            self.canvas_body = pn.pane.HTML(
                '<div class="td-empty">Create a scene to get started.</div>',
                sizing_mode="stretch_width",
            )
        else:
            self.canvas_body = self._render_grid(scene)

        return pn.Column(
            self.canvas_body,
            css_classes=["td-canvas"],
            sizing_mode="stretch_both",
            styles={
                "position": "relative",
                "z-index": "1",
                "min-height": "0",
                "overflow-y": "auto",
                "background": GRID_BACKGROUND,
                "padding-bottom": "5rem",
            },
        )

    def _render_grid(self, scene: dict[str, Any]) -> pn.Column:
        figures = scene["figures"]
        rows = GRID_ROWS
        grid = SceneGrid(
            ncols=GRID_COLUMNS,
            nrows=rows,
            locked=self._locked,
            height=grid_height(rows),
            sizing_mode="stretch_width",
            css_classes=["scene-grid"],
            stylesheets=[GRID_CSS],
        )

        cards: dict[str, FigureCard] = {}
        for figure in figures:
            display = dict(self._effective_figure(figure))
            display["title"] = self._figure_title(display)
            card = FigureCard(display, on_select=self.select_figure)
            card.set_selected(figure["id"] == self._selected_figure_id)
            card.set_unpublished(figure["id"] in self._drafts)
            card.set_locked(self._locked)
            layout = figure["layout"]
            grid[
                layout["y"] : layout["y"] + layout["h"],
                layout["x"] : layout["x"] + layout["w"],
            ] = card
            cards[figure["id"]] = card

        grid.param.watch(self._on_grid_state, "state")
        grid.set_figure_span_handler(self.create_figure_span)
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
            card.set_chart(
                self._render_chart(self._effective_figure(figure))
            )

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
        stored = self._selected_figure()
        if stored is None:
            self.settings_title = None
            self.settings_location = None
            self.settings_analyte = None
            self.settings_range = None
            self.settings_message = None
            self.settings_publish = None
            self.settings_delete = None
            self.settings_confirm_delete = None
            self.settings_cancel_delete = None
            return [
                pn.pane.HTML('<p class="td-muted">Select a figure to edit it.</p>')
            ]

        figure = self._effective_figure(stored)
        figure_id = stored["id"]

        message = pn.pane.HTML("", visible=False, sizing_mode="stretch_width")
        self.settings_message = message

        title = pn.widgets.TextInput(
            label="Title",
            value=figure["title"],
            sizing_mode="stretch_width",
        )
        self.settings_title = title

        location_rows: list[tuple[str, str]] = [("All locations", "")]
        location_error = ""
        try:
            for row in self._load_locations():
                location_id = row.get("location_id")
                if not isinstance(location_id, int) or isinstance(location_id, bool):
                    continue
                label = str(row.get("name") or f"Location {location_id}")
                location_rows.append((label, str(location_id)))
        except ReadApiError as exc:
            location_error = f"Could not load locations: {exc.detail}"

        location_options = self._option_map(location_rows)
        location_value = (
            "" if figure["location_id"] is None else str(figure["location_id"])
        )
        if location_value not in location_options.values():
            location_value = ""
        location = pn.widgets.Select(
            label="Location",
            options=location_options,
            value=location_value,
            sizing_mode="stretch_width",
        )
        self.settings_location = location

        analyte_rows: list[tuple[str, str]] = [("Choose an analyte", "")]
        analyte_error = ""
        try:
            for row in self._load_series(self._selected_id(location_value)):
                series_id = row.get("series_id")
                if not isinstance(series_id, int) or isinstance(series_id, bool):
                    continue
                analyte_rows.append((self._series_label(row), str(series_id)))
        except ReadApiError as exc:
            analyte_error = f"Could not load analytes: {exc.detail}"

        analyte_options = self._option_map(analyte_rows)
        series_value = "" if figure["series_id"] is None else str(figure["series_id"])
        if series_value not in analyte_options.values():
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

        publish = pn.widgets.Button(
            label="Published",
            color="default",
            disabled=True,
            sizing_mode="stretch_width",
        )
        publish.on_click(self._on_publish_figure)
        self.settings_publish = publish
        self.settings_delete = None
        self.settings_confirm_delete = None
        self.settings_cancel_delete = None

        delete_controls: list[Any]
        if self._figure_delete_confirm_id == figure_id:
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

        def on_title(event: Any, target: str = figure_id) -> None:
            self._on_settings_title_change(event, target)

        def on_location(event: Any, target: str = figure_id) -> None:
            self._on_settings_location_change(event, target)

        def on_analyte(event: Any, target: str = figure_id) -> None:
            self._on_settings_analyte_change(event, target)

        def on_range(event: Any, target: str = figure_id) -> None:
            self._on_settings_range_change(event, target)

        # Watchers are attached after the widgets are populated so building the
        # form does not create drafts.
        title.param.watch(on_title, "value")
        location.param.watch(on_location, "value")
        analyte.param.watch(on_analyte, "value")
        range_group.param.watch(on_range, "value")

        if location_error:
            self._set_settings_message(location_error, error=True)
        elif analyte_error:
            self._set_settings_message(analyte_error, error=True)
        elif len(analyte_options) == 1:
            self._set_settings_message("No analytes are available for this selection.")
        elif figure["series_id"] is None:
            self._set_settings_message("Choose a location and analyte.")

        self._update_publish_button()

        return [
            title,
            location,
            analyte,
            range_group,
            publish,
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

    @staticmethod
    def _option_map(rows: list[tuple[str, str]]) -> dict[str, str]:
        """Build ``{label: value}`` options, which is what Panel's Select expects.

        A list of ``(label, value)`` tuples is *not* treated as label/value pairs
        by Panel: the values become the tuples themselves and the value is reset
        to ``None`` on first render. Suffix duplicate labels so every option stays
        addressable.
        """

        options: dict[str, str] = {}
        for label, value in rows:
            candidate = label
            suffix = 2
            while candidate in options:
                candidate = f"{label} ({suffix})"
                suffix += 1
            options[candidate] = value
        return options

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

    def _on_settings_title_change(self, event: Any, figure_id: str) -> None:
        if self._suspend_settings:
            return
        self._set_draft_field(figure_id, title=(event.new or "").strip())

    def _on_settings_range_change(self, event: Any, figure_id: str) -> None:
        if self._suspend_settings or event.new not in RANGES:
            return
        self._set_draft_field(figure_id, refresh_chart=True, range=event.new)

    def _on_settings_analyte_change(self, event: Any, figure_id: str) -> None:
        if self._suspend_settings:
            return
        self._set_draft_field(
            figure_id,
            refresh_chart=True,
            series_id=self._selected_id(event.new),
        )

    def _on_settings_location_change(self, event: Any, figure_id: str) -> None:
        if self._suspend_settings:
            return
        analyte = self.settings_analyte
        if analyte is None:
            return
        current = analyte.value
        location_id = self._selected_id(event.new)
        try:
            rows = self._load_series(location_id)
        except ReadApiError as exc:
            self._suspend_settings = True
            try:
                analyte.options = {"Choose an analyte": ""}
                analyte.value = ""
            finally:
                self._suspend_settings = False
            self._set_settings_message(
                f"Could not load analytes: {exc.detail}", error=True
            )
            self._set_draft_field(
                figure_id,
                refresh_chart=True,
                location_id=location_id,
                series_id=None,
            )
            return

        analyte_rows: list[tuple[str, str]] = [("Choose an analyte", "")]
        for row in rows:
            series_id = row.get("series_id")
            if isinstance(series_id, int) and not isinstance(series_id, bool):
                analyte_rows.append((self._series_label(row), str(series_id)))
        options = self._option_map(analyte_rows)
        analyte_value = current if current in options.values() else ""
        self._suspend_settings = True
        try:
            analyte.options = options
            analyte.value = analyte_value
        finally:
            self._suspend_settings = False

        if len(options) == 1:
            self._set_settings_message("No analytes are available for this location.")
        else:
            self._set_settings_message("")
        self._set_draft_field(
            figure_id,
            refresh_chart=True,
            location_id=location_id,
            series_id=self._selected_id(analyte_value),
        )

    def _validate_publish(self, effective: dict[str, Any]) -> str | None:
        if not str(effective["title"]).strip():
            return "Title must not be blank."
        if effective["range"] not in RANGES:
            return "Choose a range."
        series_id = effective["series_id"]
        if series_id is None:
            return "Choose an analyte."
        return self._validate_figure_selection(effective["location_id"], series_id)

    def _on_publish_figure(self, event: Any = None) -> None:
        self._publish_figure()

    def _publish_figure(
        self,
        *,
        force: bool = False,
        scene_id: str | None = None,
        figure_id: str | None = None,
    ) -> None:
        target_scene = scene_id if scene_id is not None else self._selected_scene_id
        target_figure = (
            figure_id if figure_id is not None else self._selected_figure_id
        )
        if target_scene is None or target_figure is None:
            return
        draft = self._drafts.get(target_figure)
        if not draft:
            return

        if force:
            # Re-read the shared state so the retry validates against the
            # values another session just published.
            self._state = self.store.load()

        scene = scenes.find_scene(self._state, target_scene)
        stored = (
            scenes.find_figure(scene, target_figure) if scene is not None else None
        )
        if stored is None:
            self._drop_draft(target_figure)
            self._notify("This figure was deleted.", error=True)
            self.refresh()
            return

        fields = dict(draft)
        if "title" in fields:
            fields["title"] = str(fields["title"]).strip()
        effective = self._effective_figure(stored)

        error = self._validate_publish(effective)
        if error is not None:
            self._set_settings_message(error, error=True)
            return

        baseline = self._draft_baselines.get(
            target_figure, self._settings_signature(stored)
        )

        def operation(data: dict[str, Any]) -> dict[str, Any]:
            scene_data = scenes.find_scene(data, target_scene)
            figure = (
                scenes.find_figure(scene_data, target_figure)
                if scene_data is not None
                else None
            )
            if figure is None:
                raise scenes.FigureNotFound("figure not found")

            if not force:
                current = self._settings_signature(figure)
                changed: list[str] = []
                if "title" in fields and current[0] != baseline[0]:
                    changed.append("title")
                if "range" in fields and current[3] != baseline[3]:
                    changed.append("range")
                if (
                    "location_id" in fields or "series_id" in fields
                ) and current[1:3] != baseline[1:3]:
                    changed.append("location or analyte")
                if changed:
                    raise PublishConflict(
                        "This figure changed on the server ("
                        + ", ".join(changed)
                        + ")."
                    )

            updates: dict[str, Any] = {}
            if "title" in fields:
                updates["title"] = fields["title"]
            if "range" in fields:
                updates["range"] = fields["range"]
            if "location_id" in fields or "series_id" in fields:
                updates["location_id"] = (
                    fields["location_id"]
                    if "location_id" in fields
                    else figure["location_id"]
                )
                updates["series_id"] = (
                    fields["series_id"]
                    if "series_id" in fields
                    else figure["series_id"]
                )
            return scenes.update_figure(
                data, target_scene, target_figure, **updates
            )

        try:
            self._state, figure = self.store.mutate(operation)
        except PublishConflict as exc:
            self._open_conflict_modal(target_scene, target_figure, str(exc))
            return
        except scenes.FigureNotFound:
            self._drop_draft(target_figure)
            self._state = self.store.load()
            self._selected_scene_id = self._resolve_scene(target_scene)
            self._selected_figure_id = None
            self._notify("This figure was deleted.", error=True)
            self.refresh()
            return
        except (scenes.SceneError, StateError) as exc:
            self._recover(str(exc))
            return

        self._drop_draft(target_figure)
        card = self._figure_cards.get(target_figure)
        if card is not None:
            card.set_chart(self._render_chart(figure))
        self._update_figure_indicators()
        self._set_settings_message("")
        self._notify("Published")

    # -- publish conflict modal --------------------------------------------

    def _build_conflict_modal(self) -> pn.Modal:
        self.conflict_message = pn.pane.HTML("", sizing_mode="stretch_width")
        overwrite = pn.widgets.Button(
            label="Overwrite",
            color="primary",
            sizing_mode="stretch_width",
        )
        overwrite.on_click(self._on_conflict_overwrite)
        revert = pn.widgets.Button(
            label="Revert to server state",
            color="default",
            sizing_mode="stretch_width",
        )
        revert.on_click(self._on_conflict_revert)
        self.conflict_overwrite = overwrite
        self.conflict_revert = revert
        modal = pn.Modal(
            pn.pane.HTML("<h3>Figure changed on the server</h3>"),
            self.conflict_message,
            pn.Row(overwrite, revert, sizing_mode="stretch_width"),
            width=520,
            show_close_button=True,
            background_close=True,
        )
        modal.param.watch(self._on_conflict_close, "open")
        return modal

    def _open_conflict_modal(
        self, scene_id: str, figure_id: str, message: str
    ) -> None:
        self._pending_publish = (scene_id, figure_id)
        if self.conflict_message is not None:
            self.conflict_message.object = (
                f"<p>{html.escape(message)}</p>"
                "<p>Overwrite your changes, or revert to the published figure?</p>"
            )
        if self.conflict_modal is not None:
            self.conflict_modal.open = True

    def _on_conflict_close(self, event: Any) -> None:
        if not event.new:
            self._pending_publish = None

    def _on_conflict_overwrite(self, event: Any = None) -> None:
        target = self._pending_publish
        self._pending_publish = None
        if self.conflict_modal is not None:
            self.conflict_modal.open = False
        if target is None:
            return
        scene_id, figure_id = target
        self._publish_figure(force=True, scene_id=scene_id, figure_id=figure_id)

    def _on_conflict_revert(self, event: Any = None) -> None:
        target = self._pending_publish
        self._pending_publish = None
        if self.conflict_modal is not None:
            self.conflict_modal.open = False
        if target is None:
            return
        scene_id, figure_id = target
        self._drop_draft(figure_id)
        self._state = self.store.load()
        self._selected_scene_id = self._resolve_scene(scene_id)
        if self._selected_scene_id == scene_id:
            scene = scenes.find_scene(self._state, scene_id)
            found = (
                scenes.find_figure(scene, figure_id) if scene is not None else None
            )
            self._selected_figure_id = figure_id if found is not None else None
        else:
            self._selected_figure_id = None
        self._notify("Reverted to the published figure.")
        self.refresh()

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
        self._drop_draft(figure_id)
        self._clear_notice()
        self.refresh()
