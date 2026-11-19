"""Grid canvas components for the Panel scene workspace.

``SceneGrid`` extends Panel's ``GridStack`` with three additions:

- the figure ribbon is the only drag handle;
- figures have a minimum size of 3 x 2 cells;
- clicking empty grid space reports the clicked cell back to Python through a
  string parameter, so the shell can create a minimum-size figure there.

``FigureCard`` is the Panel view placed in each grid cell. Its ribbon button is
both the selection control and the drag handle.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from html import escape
from typing import Any

import panel as pn
import param

from .state import GRID_COLUMNS, GRID_ROW_HEIGHT, MIN_FIGURE_HEIGHT, MIN_FIGURE_WIDTH

GRID_MIN_ROWS = 6
GRID_EXTRA_ROWS = 3

GRID_BACKGROUND = "#f7f8fa"
GRID_LINE = "#e2e6ec"

GRID_CSS = """
.figure-card {
  background: #fff;
  border: 1px solid #d8dde6;
  border-radius: 0.35rem;
  overflow: hidden;
}
.figure-card.figure-selected {
  border-color: #1f6feb;
  box-shadow: 0 0 0 2px rgba(31, 111, 235, 0.18);
}
.figure-card.figure-unpublished {
  border-color: #d4a017;
}
.figure-card.figure-unpublished.figure-selected {
  border-color: #e8710a;
  box-shadow: 0 0 0 2px rgba(232, 113, 10, 0.22);
}
.figure-ribbon.bk-btn {
  width: 100%;
  margin: 0;
  text-align: left;
  border: 0;
  border-radius: 0;
  background: #eef2f7;
  font-weight: 600;
  cursor: move;
}
.figure-chart-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #5b6472;
}
.figure-chart-error { color: #b42318; }
""" + f"""
:host(.scene-grid) {{ background: {GRID_BACKGROUND}; }}
.grid-stack {{
  background-color: {GRID_BACKGROUND};
  background-image:
    linear-gradient(to right, {GRID_LINE} 1px, transparent 1px),
    linear-gradient(to bottom, {GRID_LINE} 1px, transparent 1px);
  background-size: calc(100% / {GRID_COLUMNS}) {GRID_ROW_HEIGHT}px;
  box-shadow: inset -1px -1px 0 {GRID_LINE};
}}
"""


def chart_placeholder(message: str, *, error: bool = False) -> pn.pane.HTML:
    """Return the inline message shown while a chart is empty or unavailable."""

    css = "figure-chart-placeholder figure-chart-error" if error else "figure-chart-placeholder"
    return pn.pane.HTML(
        f'<div class="{css}">{escape(message)}</div>',
        sizing_mode="stretch_both",
    )


class FigureCard(pn.Column):
    """A figure ribbon plus chart area, rendered inside one grid cell."""

    def __init__(
        self,
        figure: dict[str, Any],
        on_select: Callable[[str], None],
        chart: Any | None = None,
    ) -> None:
        ribbon = pn.widgets.Button(
            label=figure["title"],
            color="light",
            css_classes=["figure-ribbon"],
            sizing_mode="stretch_width",
            margin=(0, 0, 0, 0),
        )
        body = chart if chart is not None else chart_placeholder("Loading chart…")
        super().__init__(
            ribbon,
            body,
            css_classes=["figure-card"],
            sizing_mode="stretch_both",
            margin=(0, 0, 0, 0),
        )

        self.figure_id: str = figure["id"]
        self.ribbon = ribbon
        self.chart = body
        self._selected = False
        self._unpublished = False

        def handler(event: Any) -> None:
            on_select(self.figure_id)

        ribbon.on_click(handler)
        self._apply_classes()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_classes()

    def set_unpublished(self, unpublished: bool) -> None:
        self._unpublished = unpublished
        self._apply_classes()

    def _apply_classes(self) -> None:
        classes = ["figure-card"]
        if self._unpublished:
            classes.append("figure-unpublished")
        if self._selected:
            classes.append("figure-selected")
        self.css_classes = classes

    def set_title(self, title: str) -> None:
        self.ribbon.label = title

    def set_chart(self, chart: Any) -> None:
        self.objects = [self.ribbon, chart]
        self.chart = chart


class SceneGrid(pn.layout.GridStack):
    """GridStack with ribbon-only dragging and an empty-cell click bridge."""

    cell_click = param.String(default="")

    min_figure_width = param.Integer(
        default=MIN_FIGURE_WIDTH, bounds=(1, None), doc="Minimum figure width."
    )
    min_figure_height = param.Integer(
        default=MIN_FIGURE_HEIGHT, bounds=(1, None), doc="Minimum figure height."
    )

    _template = """
    <div id="grid" class="grid-stack" style="width: 100%; height: 100%">
    {% for key, obj in objects.items() %}
      <div data-id="{{ id(obj) }}" class="grid-stack-item" gs-h="{{ (key[2] or nrows)-(key[0] or 0) }}" gs-w="{{ (key[3] or ncols)-(key[1] or 0) }}" gs-y="{{ (key[0] or 0) }}" gs-x="{{ (key[1] or 0) }}" gs-min-w="{{ min_figure_width }}" gs-min-h="{{ min_figure_height }}">
        <div id="content" class="grid-stack-item-content">${obj}</div>
      </div>
    {% endfor %}
    </div>
    """  # noqa: E501

    _scripts = {
        **pn.layout.GridStack._scripts,
        "render": """
        const options = {
          column: data.ncols,
          disableResize: !data.allow_resize,
          disableDrag: !data.allow_drag,
          float: true,
          margin: 0,
          handle: '.figure-ribbon'
        }
        if (data.nrows) {
          options.row = data.nrows
          const height = model.height || grid.offsetHeight;
          options.cellHeight = Math.floor(height/data.nrows);
        }
        const gridstack = GridStack.init(options, grid);
        function sync_state(load=false) {
          const items = []
          for (const node of gridstack.engine.nodes) {
            items.push({
              id: node.el.getAttribute('data-id'),
              x0: node.x, y0: node.y,
              x1: node.x+node.w, y1: node.y+node.h
            })
          }
          data.state = items
        }
        let dragStart = null
        let dragged = false
        gridstack.on('dragstart', (event, el) => {
          const node = el ? el.gridstackNode : null
          dragStart = node ? {x: node.x, y: node.y} : null
        })
        gridstack.on('dragstop', (event, el) => {
          const node = el ? el.gridstackNode : null
          dragged = !!(dragStart && node && (node.x !== dragStart.x || node.y !== dragStart.y))
          dragStart = null
          sync_state()
          if (dragged) {
            setTimeout(() => { dragged = false }, 250)
          }
        })
        gridstack.on('resizestop', (event, el) => {
          sync_state()
          view.invalidate_layout()
        })
        grid.addEventListener('click', (event) => {
          if (dragged && event.target.closest('.figure-ribbon')) {
            event.stopPropagation()
            event.preventDefault()
            return
          }
          if (event.target.closest('.grid-stack-item')) {
            return
          }
          const cell = gridstack.getCellFromPixel({left: event.pageX, top: event.pageY}, true)
          if (!cell || !Number.isFinite(cell.x) || !Number.isFinite(cell.y)) {
            return
          }
          if (cell.x < 0 || cell.x >= data.ncols || cell.y < 0 || cell.y >= data.nrows) {
            return
          }
          data.cell_click = JSON.stringify({x: cell.x, y: cell.y, n: Date.now()})
        }, true)
        sync_state()
        state.gridstack = gridstack
        state.init = false
        """,
    }

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self._cell_click_handler: Callable[[int, int], Any] | None = None

    def set_cell_click_handler(self, handler: Callable[[int, int], Any]) -> None:
        self._cell_click_handler = handler

    @param.depends("cell_click", watch=True)
    def _process_cell_click(self) -> None:
        raw = self.cell_click
        if not raw:
            return
        self.cell_click = ""
        try:
            payload = json.loads(raw)
            x = int(payload["x"])
            y = int(payload["y"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return
        handler = getattr(self, "_cell_click_handler", None)
        if handler is not None:
            handler(x, y)


def grid_rows(figures: list[dict[str, Any]]) -> int:
    """Return the row count needed for the figures plus empty working room."""

    lowest = max(
        (figure["layout"]["y"] + figure["layout"]["h"] for figure in figures),
        default=0,
    )
    return max(GRID_MIN_ROWS, lowest + GRID_EXTRA_ROWS)


def grid_height(rows: int) -> int:
    return rows * GRID_ROW_HEIGHT


__all__ = [
    "FigureCard",
    "GRID_BACKGROUND",
    "GRID_COLUMNS",
    "GRID_CSS",
    "GRID_EXTRA_ROWS",
    "GRID_LINE",
    "GRID_MIN_ROWS",
    "MIN_FIGURE_HEIGHT",
    "MIN_FIGURE_WIDTH",
    "SceneGrid",
    "chart_placeholder",
    "grid_height",
    "grid_rows",
]
