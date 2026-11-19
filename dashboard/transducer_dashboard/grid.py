"""Grid canvas components for the Panel scene workspace.

``SceneGrid`` extends Panel's ``GridStack`` with four additions:

- the figure ribbon is the only drag handle;
- a lock toggle that freezes moving, resizing, and painting;
- painting empty cells reports the painted span back to Python through a string
  parameter, so the shell can create a figure there.

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

from .state import (
    GRID_COLUMNS,
    GRID_ROW_HEIGHT,
    GRID_ROWS,
    MIN_FIGURE_HEIGHT,
    MIN_FIGURE_WIDTH,
)

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
  /* The render script publishes the cell size GridStack actually snapped to,
     so the lines stay on cell edges at any width or row height. The values
     below are only fallbacks for the first paint. */
  background-size:
    var(--td-cell-width, calc(100% / {GRID_COLUMNS}))
    var(--td-cell-height, {GRID_ROW_HEIGHT}px);
  box-shadow: inset -1px -1px 0 {GRID_LINE};
}}
.grid-stack.td-mode-edit {{ cursor: crosshair; user-select: none; }}
.grid-stack.td-mode-edit .grid-stack-item {{ cursor: default; }}
.grid-stack.td-mode-lock .grid-stack-item {{ cursor: default; }}
.grid-stack.td-mode-lock .figure-ribbon {{ cursor: default; }}
.grid-stack > .td-paint {{
  position: absolute;
  z-index: 0;
  display: none;
  box-sizing: border-box;
  border: 2px dashed #1f6feb;
  border-radius: 0.25rem;
  background: rgba(31, 111, 235, 0.14);
  pointer-events: none;
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

    def set_locked(self, locked: bool) -> None:
        self.ribbon.disabled = locked

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
    """GridStack with ribbon-only dragging and a layout lock."""

    locked = param.Boolean(
        default=False,
        doc="Freeze moving, resizing, painting, and figure selection.",
    )

    figure_span = param.String(
        default="",
        doc="JSON span painted on empty cells, consumed once by the shell.",
    )

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
        // Paint the cell grid from the geometry GridStack is using. Row heights
        // are pixels and column widths are percentages, so measuring both keeps
        // the background on the same snapping intervals as the figures.
        function sync_cell_size() {
          const cell_width = gridstack.cellWidth();
          const cell_height = gridstack.getCellHeight();
          if (Number.isFinite(cell_width) && cell_width > 0) {
            grid.style.setProperty('--td-cell-width', `${cell_width}px`);
          }
          if (Number.isFinite(cell_height) && cell_height > 0) {
            grid.style.setProperty('--td-cell-height', `${cell_height}px`);
          }
        }

        const paint = document.createElement('div');
        paint.className = 'td-paint';
        grid.prepend(paint);

        let paintAnchor = null;
        let paintRect = null;
        let painting = false;

        function cell_from_event(event) {
          const cell = gridstack.getCellFromPixel(
            {left: event.pageX, top: event.pageY}, true
          );
          if (!cell || !Number.isFinite(cell.x) || !Number.isFinite(cell.y)) {
            return null;
          }
          if (cell.x < 0 || cell.x >= data.ncols) {
            return null;
          }
          if (cell.y < 0 || cell.y >= data.nrows) {
            return null;
          }
          return {x: cell.x, y: cell.y};
        }

        function area_is_free(x, y, w, h) {
          if (x < 0 || y < 0 || x + w > data.ncols || y + h > data.nrows) {
            return false;
          }
          return gridstack.isAreaEmpty(x, y, w, h);
        }

        function minimum_rect(cell) {
          const w = data.min_figure_width;
          const h = data.min_figure_height;
          if (cell.x + w > data.ncols || cell.y + h > data.nrows) {
            return null;
          }
          return area_is_free(cell.x, cell.y, w, h)
            ? {x: cell.x, y: cell.y, w: w, h: h}
            : null;
        }

        function grow_rect(anchor, cursor) {
          let rect = minimum_rect(anchor);
          if (!rect) {
            return null;
          }
          const desired_w = Math.max(
            data.min_figure_width, Math.abs(cursor.x - anchor.x) + 1
          );
          const desired_h = Math.max(
            data.min_figure_height, Math.abs(cursor.y - anchor.y) + 1
          );
          const grow_left = cursor.x < anchor.x;
          const grow_up = cursor.y < anchor.y;
          while (rect.w < desired_w) {
            const candidate = grow_left
              ? {x: rect.x - 1, y: rect.y, w: rect.w + 1, h: rect.h}
              : {x: rect.x, y: rect.y, w: rect.w + 1, h: rect.h};
            if (!area_is_free(candidate.x, candidate.y, candidate.w, candidate.h)) {
              break;
            }
            rect = candidate;
          }
          while (rect.h < desired_h) {
            const candidate = grow_up
              ? {x: rect.x, y: rect.y - 1, w: rect.w, h: rect.h + 1}
              : {x: rect.x, y: rect.y, w: rect.w, h: rect.h + 1};
            if (!area_is_free(candidate.x, candidate.y, candidate.w, candidate.h)) {
              break;
            }
            rect = candidate;
          }
          return rect;
        }

        function show_paint(rect) {
          if (!rect) {
            paint.style.display = 'none';
            paintRect = null;
            return;
          }
          paintRect = rect;
          const cell_width = gridstack.cellWidth();
          const cell_height = gridstack.getCellHeight();
          paint.style.left = `${rect.x * cell_width}px`;
          paint.style.top = `${rect.y * cell_height}px`;
          paint.style.width = `${rect.w * cell_width}px`;
          paint.style.height = `${rect.h * cell_height}px`;
          paint.style.display = 'block';
        }

        function clear_paint() {
          paint.style.display = 'none';
          paintRect = null;
          paintAnchor = null;
          painting = false;
        }

        state.sync_cell_size = sync_cell_size;
        sync_cell_size();
        if (typeof ResizeObserver !== 'undefined') {
          if (state.cell_resize_observer) {
            state.cell_resize_observer.disconnect();
          }
          state.cell_resize_observer = new ResizeObserver(() => {
            sync_cell_size();
            if (paintRect) {
              show_paint(paintRect);
            }
          });
          state.cell_resize_observer.observe(grid);
        }

        function apply_lock(locked) {
          gridstack.enableMove(!locked);
          gridstack.enableResize(!locked);
          grid.classList.toggle('td-mode-edit', !locked);
          grid.classList.toggle('td-mode-lock', locked);
          if (locked) {
            clear_paint();
          }
        }
        state.apply_lock = apply_lock;
        apply_lock(data.locked);

        grid.addEventListener('pointerdown', (event) => {
          if (data.locked || event.button !== 0) {
            return;
          }
          if (event.target.closest('.grid-stack-item')) {
            return;
          }
          const cell = cell_from_event(event);
          if (!cell) {
            return;
          }
          const rect = minimum_rect(cell);
          if (!rect) {
            return;
          }
          paintAnchor = cell;
          painting = true;
          show_paint(rect);
          event.preventDefault();
          if (grid.setPointerCapture) {
            grid.setPointerCapture(event.pointerId);
          }
        });

        grid.addEventListener('pointermove', (event) => {
          if (data.locked) {
            return;
          }
          if (painting && paintAnchor) {
            const cell = cell_from_event(event);
            if (cell) {
              show_paint(grow_rect(paintAnchor, cell));
            }
            return;
          }
          if (event.target.closest('.grid-stack-item')) {
            show_paint(null);
            return;
          }
          const cell = cell_from_event(event);
          show_paint(cell ? minimum_rect(cell) : null);
        });

        grid.addEventListener('pointerup', (event) => {
          if (!painting) {
            return;
          }
          const rect = paintRect;
          painting = false;
          paintAnchor = null;
          clear_paint();
          if (rect) {
            data.figure_span = JSON.stringify({
              x: rect.x, y: rect.y, w: rect.w, h: rect.h, n: Date.now()
            });
          }
          if (grid.releasePointerCapture) {
            try {
              grid.releasePointerCapture(event.pointerId);
            } catch (error) {
              // The pointer may already be released.
            }
          }
        });

        grid.addEventListener('pointercancel', () => {
          clear_paint();
        });

        grid.addEventListener('pointerleave', () => {
          if (!painting) {
            clear_paint();
          }
        });

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
        gridstack.on('dragstop', (event, el) => {
          sync_state()
        })
        gridstack.on('resizestop', (event, el) => {
          sync_state()
          view.invalidate_layout()
        })
        sync_state()
        state.gridstack = gridstack
        state.init = false
        """,
        "after_layout": """
        self.nrows()
        if (!state.init) {
          state.init = true
          view.invalidate_layout()
        }
        state.gridstack.engine._notify()
        if (state.sync_cell_size) {
          state.sync_cell_size()
        }
        """,
        "locked": """
        if (state.apply_lock) {
          state.apply_lock(data.locked)
        }
        """,
    }

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self._figure_span_handler: Callable[[int, int, int, int], Any] | None = None

    def set_figure_span_handler(
        self, handler: Callable[[int, int, int, int], Any]
    ) -> None:
        self._figure_span_handler = handler

    @param.depends("figure_span", watch=True)
    def _process_figure_span(self) -> None:
        raw = self.figure_span
        if not raw:
            return
        self.figure_span = ""
        try:
            payload = json.loads(raw)
            x = int(payload["x"])
            y = int(payload["y"])
            width = int(payload["w"])
            height = int(payload["h"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return
        handler = getattr(self, "_figure_span_handler", None)
        if handler is not None:
            handler(x, y, width, height)


def grid_height(rows: int = GRID_ROWS) -> int:
    return rows * GRID_ROW_HEIGHT


__all__ = [
    "FigureCard",
    "GRID_BACKGROUND",
    "GRID_COLUMNS",
    "GRID_CSS",
    "GRID_LINE",
    "GRID_ROWS",
    "MIN_FIGURE_HEIGHT",
    "MIN_FIGURE_WIDTH",
    "SceneGrid",
    "chart_placeholder",
    "grid_height",
]
