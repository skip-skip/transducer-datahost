import json

import panel as pn

from transducer_dashboard.grid import (
    DEFAULT_TOOL_MODE,
    GRID_BACKGROUND,
    GRID_CSS,
    GRID_LINE,
    TOOL_MODES,
    FigureCard,
    SceneGrid,
    chart_placeholder,
    grid_height,
    grid_rows,
)
from transducer_dashboard.state import (
    GRID_COLUMNS,
    GRID_ROW_HEIGHT,
    MIN_FIGURE_HEIGHT,
    MIN_FIGURE_WIDTH,
)


def make_figure(figure_id: str = "fig", title: str = "Untitled figure"):
    return {
        "id": figure_id,
        "title": title,
        "location_id": None,
        "series_id": None,
        "range": "7d",
        "layout": {"x": 0, "y": 0, "w": 3, "h": 2},
    }


def test_grid_rows_adds_working_room_below_the_figures() -> None:
    assert grid_rows([]) == 6

    figures = [make_figure()]
    assert grid_rows(figures) == 6

    figures[0]["layout"]["y"] = 8
    assert grid_rows(figures) == 13


def test_grid_height_uses_the_configured_row_height() -> None:
    assert grid_height(6) == 6 * GRID_ROW_HEIGHT


def test_the_grid_background_tracks_the_cell_size() -> None:
    assert "--td-cell-width" in GRID_CSS
    assert "--td-cell-height" in GRID_CSS
    assert f"calc(100% / {GRID_COLUMNS})" in GRID_CSS
    assert f"{GRID_ROW_HEIGHT}px" in GRID_CSS
    assert GRID_BACKGROUND in GRID_CSS
    assert GRID_LINE in GRID_CSS
    assert GRID_CSS.count("linear-gradient") == 2
    assert ":host(.scene-grid)" in GRID_CSS


def test_the_render_script_publishes_the_measured_cell_size() -> None:
    render = SceneGrid._scripts["render"]

    assert "gridstack.cellWidth()" in render
    assert "gridstack.getCellHeight()" in render
    assert "'--td-cell-width'" in render
    assert "'--td-cell-height'" in render
    assert "ResizeObserver" in render
    assert "state.sync_cell_size()" in SceneGrid._scripts["after_layout"]


def test_scene_grid_enforces_minimum_figure_size() -> None:
    grid = SceneGrid(ncols=GRID_COLUMNS, nrows=6, height=grid_height(6))

    assert grid.min_figure_width == MIN_FIGURE_WIDTH
    assert grid.min_figure_height == MIN_FIGURE_HEIGHT
    assert 'gs-min-w="{{ min_figure_width }}"' in SceneGrid._template
    assert 'gs-min-h="{{ min_figure_height }}"' in SceneGrid._template


def test_scene_grid_uses_the_ribbon_as_the_drag_handle() -> None:
    render = SceneGrid._scripts["render"]

    assert "handle: '.figure-ribbon'" in render
    assert "float: true" in render


def test_scene_grid_defaults_to_arrange_mode() -> None:
    grid = SceneGrid(ncols=GRID_COLUMNS, nrows=6)

    assert grid.tool_mode == DEFAULT_TOOL_MODE == "arrange"
    assert tuple(grid.param.tool_mode.objects) == TOOL_MODES


def test_the_mode_script_enables_movement_only_while_arranging() -> None:
    mode_script = SceneGrid._scripts["tool_mode"]
    render = SceneGrid._scripts["render"]

    assert "state.apply_mode(data.tool_mode)" in mode_script
    assert "gridstack.enableMove(arranging)" in render
    assert "gridstack.enableResize(arranging)" in render
    assert "td-mode-add" in render
    assert "td-mode-lock" in render


def test_the_render_script_paints_a_free_span_in_add_mode() -> None:
    render = SceneGrid._scripts["render"]

    assert "pointerdown" in render
    assert "pointermove" in render
    assert "pointerup" in render
    assert "isAreaEmpty" in render
    assert "data.figure_span = JSON.stringify" in render
    assert "classList.toggle('td-mode-add'" in render


def test_the_paint_overlay_is_hidden_and_ignores_the_pointer() -> None:
    assert ".grid-stack > .td-paint" in GRID_CSS
    assert "pointer-events: none" in GRID_CSS
    assert "display: none" in GRID_CSS


def test_figure_span_reports_the_painted_span_and_resets() -> None:
    grid = SceneGrid(ncols=GRID_COLUMNS, nrows=6)
    calls: list[tuple[int, int, int, int]] = []
    grid.set_figure_span_handler(lambda x, y, w, h: calls.append((x, y, w, h)))

    grid.figure_span = json.dumps({"x": 2, "y": 1, "w": 5, "h": 4, "n": 1})

    assert calls == [(2, 1, 5, 4)]
    assert grid.figure_span == ""


def test_figure_span_ignores_invalid_payloads() -> None:
    grid = SceneGrid(ncols=GRID_COLUMNS, nrows=6)
    calls: list[tuple[int, int, int, int]] = []
    grid.set_figure_span_handler(lambda x, y, w, h: calls.append((x, y, w, h)))

    grid.figure_span = "not-json"

    assert calls == []
    assert grid.figure_span == ""


def test_scene_grid_places_a_card_in_the_requested_cells() -> None:
    grid = SceneGrid(ncols=GRID_COLUMNS, nrows=6, height=grid_height(6))
    card = FigureCard(make_figure(), on_select=lambda figure_id: None)

    grid[1:3, 3:6] = card

    assert len(grid.objects) == 1
    (key, value) = next(iter(grid.objects.items()))
    assert key == (1, 3, 3, 6)
    assert value is card


def test_figure_card_ribbon_selects_the_figure() -> None:
    calls: list[str] = []
    card = FigureCard(make_figure("fig-1"), on_select=calls.append)

    card.ribbon.clicks += 1

    assert calls == ["fig-1"]


def test_figure_card_selection_toggles_the_highlight() -> None:
    card = FigureCard(make_figure(), on_select=lambda figure_id: None)

    assert card.css_classes == ["figure-card"]

    card.set_selected(True)
    assert card.css_classes == ["figure-card", "figure-selected"]

    card.set_selected(False)
    assert card.css_classes == ["figure-card"]


def test_figure_card_title_is_editable() -> None:
    card = FigureCard(make_figure(), on_select=lambda figure_id: None)

    card.set_title("Temperature")

    assert card.ribbon.label == "Temperature"


def test_figure_card_starts_with_a_loading_placeholder() -> None:
    card = FigureCard(make_figure(), on_select=lambda figure_id: None)

    assert isinstance(card.chart, pn.pane.HTML)
    assert "Loading chart" in card.chart.object


def test_figure_card_replaces_the_chart_in_place() -> None:
    card = FigureCard(make_figure(), on_select=lambda figure_id: None)
    chart = pn.pane.HTML("chart")

    card.set_chart(chart)

    assert card.chart is chart
    assert card.objects[1] is chart
    assert card.ribbon is card.objects[0]


def test_chart_placeholder_escapes_its_message() -> None:
    pane = chart_placeholder("<b>bad</b>", error=True)

    assert "&lt;b&gt;bad&lt;/b&gt;" in pane.object
    assert "figure-chart-error" in pane.object
