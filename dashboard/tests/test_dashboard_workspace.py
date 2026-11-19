import json
from datetime import datetime
from typing import Any

import panel as pn
import pytest

from transducer_dashboard import scenes
from transducer_dashboard.auth import Session
from transducer_dashboard.grid import GRID_BACKGROUND
from transducer_dashboard.read_client import ReadApiError
from transducer_dashboard.settings import DashboardSettings
from transducer_dashboard.state import GRID_COLUMNS, GRID_ROW_HEIGHT, StateStore
from transducer_dashboard.workspace import (
    DRAWER_CSS,
    DRAWER_TITLE_CSS,
    RAIL_CSS,
    RAIL_TAB_WIDTH,
    DashboardShell,
)

LOCATIONS = [
    {"location_id": 1, "name": "MCMW-04"},
    {"location_id": 2, "name": "MCDMW-04"},
]
SERIES = [
    {
        "series_id": 10,
        "serial_number": "1096043",
        "channel_no": 1,
        "identification": "LEVEL",
        "unit": "ft",
        "data_kind": "level",
    },
    {
        "series_id": 11,
        "serial_number": "1096043",
        "channel_no": 2,
        "identification": "TEMPERATURE",
        "unit": "C",
        "data_kind": "temperature",
    },
    {
        "series_id": 12,
        "serial_number": "1096044",
        "channel_no": 1,
        "identification": "LEVEL",
        "unit": "ft",
        "data_kind": "level",
    },
]
SERIES_BY_LOCATION = {1: SERIES[:2], 2: SERIES[2:]}
POINTS = [
    {"ts": "2026-11-10T00:00:00", "value": 1.5},
    {"ts": "2026-11-10T01:00:00", "value": 2.5},
]


class FakeReadClient:
    def whoami(self, token: str) -> str:
        return "alice"

    def locations(self, token: str) -> list[dict]:
        return list(LOCATIONS)

    def series(self, token: str, *, serial=None, limit: int = 1000) -> list[dict]:
        return list(SERIES)

    def series_at_location(
        self, token: str, location_id: int, *, limit: int = 1000
    ) -> list[dict]:
        return list(SERIES_BY_LOCATION.get(location_id, []))

    def series_detail(self, token: str, series_id: int) -> dict:
        for row in SERIES:
            if row["series_id"] == series_id:
                return dict(row)
        raise ReadApiError(404, "series not found")

    def points(
        self,
        token: str,
        series_id: int,
        *,
        start: str,
        end: str,
        points: int,
        tz: str = "local",
        location_id: int | None = None,
    ) -> list[dict]:
        return [dict(row) for row in POINTS]


def make_shell(
    tmp_path,
    label: str = "alice",
    token: str = "good",
    default_window_days: int = 7,
) -> tuple[DashboardShell, StateStore]:
    store = StateStore(tmp_path)
    store.ensure()
    settings = DashboardSettings(
        read_api_url="http://read-api:8080",
        secret_key="x" * 32,
        state_dir=tmp_path,
        default_window_days=default_window_days,
        cookie_secure=False,
    )
    shell = DashboardShell(
        store,
        FakeReadClient(),  # type: ignore[arg-type]
        Session(label=label, token=token),
        settings,
    )
    return shell, store


def test_empty_state_shows_the_create_prompt(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    assert shell.selected_scene_id is None
    assert shell.scene_buttons == {}
    assert "Create a scene" in shell.canvas_body.object
    assert "Select a figure" in shell.settings_body.objects[0].object
    assert shell.delete_scene_button.disabled is True
    assert shell.tray_card.collapsed is False
    assert shell.settings_card.collapsed is False
    assert shell.rail.objects == [shell.tray_card, shell.settings_card]


def test_rail_is_a_full_height_ribbon_row(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    for card, body in (
        (shell.tray_card, shell.tray_body),
        (shell.settings_card, shell.settings_body),
    ):
        assert card is not None
        assert body is not None
        assert body.css_classes == ["td-card-body"]
        assert card.css_classes == ["td-rail-card"]
        assert card.stylesheets == [DRAWER_CSS]
        assert card.header is not None
        assert card.header.css_classes == ["td-drawer-title"]
        assert card.header.stylesheets == [DRAWER_TITLE_CSS]

    assert "flex-direction: row" in DRAWER_CSS
    assert f"flex: 0 0 {RAIL_TAB_WIDTH}px" in DRAWER_CSS
    assert "align-self: stretch" in DRAWER_CSS
    assert "overflow-y: auto" in DRAWER_CSS
    assert "writing-mode: vertical-rl" in DRAWER_TITLE_CSS
    assert shell.rail is not None
    assert isinstance(shell.rail, pn.Row)
    assert shell.rail.objects == [shell.tray_card, shell.settings_card]
    assert shell.rail.stylesheets == [RAIL_CSS]
    assert shell.rail.styles["flex"] == "0 0 auto"
    assert shell.rail.styles["width"] == "max-content"
    assert shell.rail.styles["height"] == "100%"
    assert shell.view.styles["height"] == "100vh"


def test_scenes_ribbon_is_left_of_the_settings_ribbon(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    assert shell.rail.objects == [shell.tray_card, shell.settings_card]
    assert shell.tray_card.header.object is not None
    assert "Scenes" in shell.tray_card.header.object
    assert "Figure settings" in shell.settings_card.header.object


def test_the_rail_overrides_the_panel_minimum_width(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    assert shell.rail.stylesheets == [RAIL_CSS]
    assert "min-width: 0 !important" in RAIL_CSS
    assert "width: max-content !important" in RAIL_CSS


def test_create_scene_selects_and_persists_it(tmp_path) -> None:
    shell, store = make_shell(tmp_path)

    scene = shell.create_scene()

    assert scene is not None
    assert shell.selected_scene_id == scene["id"]
    assert shell.scene_buttons[scene["id"]].button_type == "primary"
    assert scene["name"] in shell.header.object
    assert "0 figures" in shell.header.object
    assert shell.grid is not None
    assert shell.figure_cards == {}

    stored = store.load()
    assert stored["scenes"][0]["id"] == scene["id"]
    assert stored["ui"]["alice"]["selected_scene_id"] == scene["id"]


def test_the_header_has_brand_scene_and_user(tmp_path) -> None:
    shell, _ = make_shell(tmp_path, label="alice")
    scene = shell.create_scene(name="Demo")
    assert scene is not None
    shell.create_figure(0, 0)

    text = shell.header.object
    assert "display:grid" in text
    assert "grid-template-columns:1fr auto 1fr" in text
    assert "td-header-left" in text
    assert "Transducer dashboard" in text
    assert "td-header-center" in text
    assert "Demo" in text
    assert "· 1 figure</span>" in text
    assert "td-header-right" in text
    assert "Signed in as alice" in text
    assert text.index("Transducer dashboard") < text.index("Demo")
    assert text.index("Demo") < text.index("Signed in as")


def test_the_header_shows_no_scene_before_one_is_selected(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    assert "No scene selected" in shell.header.object
    assert "figure" not in shell.header.object


def test_the_canvas_uses_the_grid_background(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()

    canvas = shell.view.objects[1].objects[1]
    assert canvas.styles["background"] == GRID_BACKGROUND


def test_the_notice_is_a_fixed_toast(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    assert shell.notice.visible is False
    assert shell.view.objects[-1] is shell.notice
    styles = " ".join(str(sheet) for sheet in shell.notice.stylesheets)
    assert "position: fixed" in styles
    assert "right: 1rem" in styles
    assert "bottom: 1rem" in styles
    assert "animation: td-notice-dismiss 3s" in styles


def test_only_the_selected_scene_is_highlighted(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    first = shell.create_scene()
    second = shell.create_scene()

    assert first is not None
    assert second is not None
    assert shell.scene_buttons[first["id"]].button_type == "default"
    assert shell.scene_buttons[second["id"]].button_type == "primary"

    shell.select_scene(first["id"])

    assert shell.scene_buttons[first["id"]].button_type == "primary"
    assert shell.scene_buttons[second["id"]].button_type == "default"
    assert shell.selected_scene_id == first["id"]


def test_scenes_are_shared_but_selection_is_per_user(tmp_path) -> None:
    alice, _ = make_shell(tmp_path, label="alice")
    first = alice.create_scene()
    second = alice.create_scene()
    assert first is not None
    assert second is not None

    bob, store = make_shell(tmp_path, label="bob")
    assert set(bob.scene_buttons) == {first["id"], second["id"]}
    assert bob.selected_scene_id == first["id"]

    bob.select_scene(second["id"])

    alice_reloaded, _ = make_shell(tmp_path, label="alice")
    assert alice_reloaded.selected_scene_id == second["id"]
    stored = store.load()
    assert stored["ui"]["alice"]["selected_scene_id"] == second["id"]
    assert stored["ui"]["bob"]["selected_scene_id"] == second["id"]


def test_existing_scenes_default_to_the_first_scene(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    _, scene = store.mutate(lambda data: scenes.create_scene(data))

    shell, _ = make_shell(tmp_path)

    assert shell.selected_scene_id == scene["id"]
    assert store.load()["ui"]["alice"]["selected_scene_id"] == scene["id"]


def test_a_stale_selection_falls_back_to_the_first_scene(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()
    _, scene = store.mutate(lambda data: scenes.create_scene(data))
    store.mutate(
        lambda data: data["ui"].update(
            {
                "alice": {
                    "selected_scene_id": "gone",
                    "selected_figure_id": None,
                    "scenes_tray_collapsed": False,
                }
            }
        )
    )

    shell, _ = make_shell(tmp_path)

    assert shell.selected_scene_id == scene["id"]


def test_tray_collapse_persists(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()

    shell.toggle_scenes_tray()

    assert shell.tray_card.collapsed is True
    assert shell.tray_collapsed is True
    assert store.load()["ui"]["alice"]["scenes_tray_collapsed"] is True

    reloaded, _ = make_shell(tmp_path)
    assert reloaded.tray_card.collapsed is True


def test_tray_collapse_survives_a_rebuild(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    shell.toggle_scenes_tray()

    shell.create_scene()

    assert shell.tray_card.collapsed is True


def test_settings_collapse_is_ephemeral(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()

    shell.settings_card.collapsed = True
    shell.create_scene()

    assert shell.settings_card.collapsed is True

    reloaded, _ = make_shell(tmp_path)
    assert reloaded.settings_card.collapsed is False


def test_delete_scene_requires_confirmation(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    scene = shell.create_scene()
    assert scene is not None

    shell.request_delete_scene()

    assert shell.confirm_delete_button is not None
    assert shell.cancel_delete_button is not None
    assert store.load()["scenes"][0]["id"] == scene["id"]

    shell.cancel_delete_scene()

    assert shell.confirm_delete_button is None
    assert shell.cancel_delete_button is None
    assert store.load()["scenes"][0]["id"] == scene["id"]


def test_confirm_delete_selects_the_next_scene(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    first = shell.create_scene()
    second = shell.create_scene()
    assert first is not None
    assert second is not None
    assert shell.selected_scene_id == second["id"]

    shell.request_delete_scene()
    shell.confirm_delete_scene()

    assert shell.selected_scene_id == first["id"]
    assert [scene["id"] for scene in store.load()["scenes"]] == [first["id"]]
    assert store.load()["ui"]["alice"]["selected_scene_id"] == first["id"]
    assert shell.scene_buttons[first["id"]].button_type == "primary"


def test_deleting_the_last_scene_returns_to_the_empty_state(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()

    shell.request_delete_scene()
    shell.confirm_delete_scene()

    assert shell.selected_scene_id is None
    assert shell.scene_buttons == {}
    assert "Create a scene" in shell.canvas_body.object
    assert store.load()["ui"]["alice"]["selected_scene_id"] is None


def test_deleting_a_scene_clears_another_users_selection(tmp_path) -> None:
    alice, store = make_shell(tmp_path, label="alice")
    scene = alice.create_scene()
    assert scene is not None
    bob, _ = make_shell(tmp_path, label="bob")
    bob.select_scene(scene["id"])

    alice.request_delete_scene()
    alice.confirm_delete_scene()

    stored = store.load()
    assert stored["ui"]["bob"]["selected_scene_id"] is None
    reloaded_bob, _ = make_shell(tmp_path, label="bob")
    assert reloaded_bob.selected_scene_id is None


def test_scene_buttons_are_wired(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    shell.new_scene_button.clicks += 1
    first = shell.selected_scene_id
    assert first is not None
    assert len(shell.scene_buttons) == 1

    shell.new_scene_button.clicks += 1
    second = shell.selected_scene_id
    assert second is not None and second != first

    shell.scene_buttons[first].clicks += 1

    assert shell.selected_scene_id == first


def test_delete_and_cancel_buttons_are_wired(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.new_scene_button.clicks += 1
    scene_id = shell.selected_scene_id

    shell.delete_scene_button.clicks += 1
    assert shell.confirm_delete_button is not None

    assert shell.cancel_delete_button is not None
    shell.cancel_delete_button.clicks += 1
    assert shell.confirm_delete_button is None
    assert shell.selected_scene_id == scene_id

    shell.delete_scene_button.clicks += 1
    assert shell.confirm_delete_button is not None
    shell.confirm_delete_button.clicks += 1

    assert shell.selected_scene_id is None
    assert shell.scene_buttons == {}


def test_tray_toggle_is_wired(tmp_path) -> None:
    shell, store = make_shell(tmp_path)

    shell.tray_card.collapsed = True
    assert shell.tray_collapsed is True
    assert store.load()["ui"]["alice"]["scenes_tray_collapsed"] is True

    shell.tray_card.collapsed = False
    assert shell.tray_collapsed is False
    assert store.load()["ui"]["alice"]["scenes_tray_collapsed"] is False


def test_scene_names_are_escaped_in_the_header(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    scene = shell.create_scene(name="<script>alert(1)</script>")

    assert scene is not None
    assert "<script>" not in shell.header.object
    assert "&lt;script&gt;" in shell.header.object
    assert shell.scene_buttons[scene["id"]].label == "<script>alert(1)</script>"


def test_selecting_an_unknown_scene_shows_an_error(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()

    shell.select_scene("missing")

    assert shell.notice.visible is True
    assert "selected scene not found" in shell.notice.object


def test_deleting_without_a_selected_scene_shows_an_error(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    shell.request_delete_scene()

    assert shell.notice.visible is True
    assert "Select a scene to delete" in shell.notice.object


@pytest.mark.parametrize("label", ["alice", "bob"])
def test_actions_use_the_session_label(tmp_path, label: str) -> None:
    shell, store = make_shell(tmp_path, label=label)

    shell.create_scene()

    assert store.load()["ui"][label]["selected_scene_id"] is not None


def grid_items(shell, layouts):
    return [
        {
            "id": str(id(shell.figure_cards[figure_id])),
            "x0": layout["x"],
            "y0": layout["y"],
            "x1": layout["x"] + layout["w"],
            "y1": layout["y"] + layout["h"],
        }
        for figure_id, layout in layouts.items()
    ]


def figure_layouts(store):
    return {
        figure["id"]: figure["layout"]
        for figure in store.load()["scenes"][0]["figures"]
    }


def test_a_selected_scene_shows_an_empty_grid(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    shell.create_scene()

    assert shell.grid is not None
    assert shell.grid.ncols == GRID_COLUMNS
    assert shell.grid.nrows == 6
    assert shell.grid.sizing_mode == "stretch_both"
    assert shell.canvas_body.height == 6 * GRID_ROW_HEIGHT
    assert shell.figure_cards == {}


def test_clicking_an_empty_cell_creates_a_minimum_size_figure(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    scene = shell.create_scene()
    assert scene is not None

    figure = shell.create_figure(4, 3)

    assert figure is not None
    assert figure["layout"] == {"x": 4, "y": 3, "w": 3, "h": 2}
    assert shell.selected_figure_id == figure["id"]
    assert "figure-selected" in shell.figure_cards[figure["id"]].css_classes
    assert "· 1 figure</span>" in shell.header.object

    stored = store.load()
    assert stored["scenes"][0]["figures"][0]["layout"] == {
        "x": 4,
        "y": 3,
        "w": 3,
        "h": 2,
    }
    assert stored["ui"]["alice"]["selected_figure_id"] == figure["id"]


def test_the_grid_cell_click_bridge_creates_a_figure(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()

    assert shell.grid is not None
    shell.grid.cell_click = json.dumps({"x": 6, "y": 2, "n": 1})

    stored = store.load()
    figures = stored["scenes"][0]["figures"]
    assert len(figures) == 1
    assert figures[0]["layout"] == {"x": 6, "y": 2, "w": 3, "h": 2}
    assert shell.selected_figure_id == figures[0]["id"]


def test_a_figure_falls_back_to_the_first_free_slot(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()

    first = shell.create_figure(4, 3)
    second = shell.create_figure(4, 3)

    assert first is not None
    assert second is not None
    assert first["layout"] == {"x": 4, "y": 3, "w": 3, "h": 2}
    assert second["layout"] == {"x": 0, "y": 0, "w": 3, "h": 2}


def test_a_full_scene_reports_instead_of_overlapping(tmp_path, monkeypatch) -> None:
    shell, _ = make_shell(tmp_path)
    scene = shell.create_scene()
    assert scene is not None
    shell.create_figure(0, 0)

    monkeypatch.setattr(scenes, "find_free_slot", lambda *args, **kwargs: None)
    figure = shell.create_figure(0, 0)

    assert figure is None
    assert shell.selected_scene_id == scene["id"]
    assert shell.notice.visible is True
    assert "no room for another figure" in shell.notice.object
    assert len(shell.figure_cards) == 1


def test_the_ribbon_selects_the_figure_and_updates_the_settings_card(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    first = shell.create_figure(0, 0)
    second = shell.create_figure(3, 0)
    assert first is not None
    assert second is not None
    assert shell.selected_figure_id == second["id"]

    shell.figure_cards[first["id"]].ribbon.clicks += 1

    assert shell.selected_figure_id == first["id"]
    assert "figure-selected" in shell.figure_cards[first["id"]].css_classes
    assert "figure-selected" not in shell.figure_cards[second["id"]].css_classes
    assert first["title"] in shell.settings_title.value


def test_selecting_a_figure_does_not_change_the_settings_card(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    first = shell.create_figure(0, 0)
    second = shell.create_figure(3, 0)
    assert first is not None
    assert second is not None

    shell.settings_card.collapsed = True
    shell.figure_cards[first["id"]].ribbon.clicks += 1

    assert shell.selected_figure_id == first["id"]
    assert shell.settings_card.collapsed is True
    assert first["title"] in shell.settings_title.value

    shell.settings_card.collapsed = False
    shell.figure_cards[second["id"]].ribbon.clicks += 1

    assert shell.selected_figure_id == second["id"]
    assert shell.settings_card.collapsed is False


def test_creating_a_figure_does_not_change_the_settings_card(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    shell.settings_card.collapsed = True

    shell.create_figure(0, 0)

    assert shell.settings_card.collapsed is True


def test_figure_selection_persists_per_user(tmp_path) -> None:
    shell, _ = make_shell(tmp_path, label="alice")
    shell.create_scene()
    first = shell.create_figure(0, 0)
    second = shell.create_figure(3, 0)
    assert first is not None
    assert second is not None

    shell.select_figure(first["id"])

    reloaded, _ = make_shell(tmp_path, label="alice")
    assert reloaded.selected_figure_id == first["id"]
    assert "figure-selected" in reloaded.figure_cards[first["id"]].css_classes
    assert first["title"] in reloaded.settings_title.value

    bob, _ = make_shell(tmp_path, label="bob")
    assert bob.selected_figure_id is None


def test_dragging_a_figure_persists_the_layout(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    first = shell.create_figure(0, 0)
    second = shell.create_figure(3, 0)
    assert first is not None
    assert second is not None

    shell.apply_grid_state(
        grid_items(
            shell,
            {
                first["id"]: {"x": 0, "y": 4, "w": 3, "h": 2},
                second["id"]: {"x": 6, "y": 0, "w": 3, "h": 2},
            },
        )
    )

    layouts = figure_layouts(store)
    assert layouts[first["id"]] == {"x": 0, "y": 4, "w": 3, "h": 2}
    assert layouts[second["id"]] == {"x": 6, "y": 0, "w": 3, "h": 2}
    assert shell.notice.visible is False

    reloaded, _ = make_shell(tmp_path)
    assert reloaded.grid is not None
    assert (4, 0, 6, 3) in reloaded.grid.objects
    assert (0, 6, 2, 9) in reloaded.grid.objects


def test_resizing_a_figure_persists_the_layout(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.apply_grid_state(
        grid_items(shell, {figure["id"]: {"x": 0, "y": 0, "w": 6, "h": 4}})
    )

    assert figure_layouts(store)[figure["id"]] == {
        "x": 0,
        "y": 0,
        "w": 6,
        "h": 4,
    }


def test_an_identical_grid_state_is_not_rewritten(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None
    before = store.path.read_text(encoding="utf-8")

    shell.apply_grid_state(
        grid_items(shell, {figure["id"]: {"x": 0, "y": 0, "w": 3, "h": 2}})
    )

    assert store.path.read_text(encoding="utf-8") == before
    assert shell.notice.visible is False


def test_an_overlapping_layout_is_rejected(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    first = shell.create_figure(0, 0)
    second = shell.create_figure(3, 0)
    assert first is not None
    assert second is not None
    before = figure_layouts(store)

    shell.apply_grid_state(
        grid_items(
            shell,
            {
                first["id"]: {"x": 3, "y": 0, "w": 3, "h": 2},
                second["id"]: {"x": 3, "y": 0, "w": 3, "h": 2},
            },
        )
    )

    assert shell.notice.visible is True
    assert "overlap" in shell.notice.object
    assert figure_layouts(store) == before


def test_switching_scenes_rebuilds_the_grid(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    first_scene = shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert first_scene is not None
    assert figure is not None

    shell.create_scene()

    assert shell.figure_cards == {}

    shell.select_scene(first_scene["id"])

    assert figure["id"] in shell.figure_cards
    assert shell.selected_figure_id is None


def option_values(select) -> list:
    return list(select.options.values())


def test_the_settings_card_shows_the_selected_figure(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    assert shell.settings_title is not None
    assert shell.settings_title.value == figure["title"]
    assert shell.settings_location is not None
    assert shell.settings_location.options["All locations"] == ""
    assert shell.settings_location.options.get("MCMW-04") == "1"
    assert shell.settings_location.value == ""
    assert shell.settings_analyte is not None
    assert shell.settings_analyte.value == ""
    assert option_values(shell.settings_analyte) == ["", "10", "11", "12"]
    assert "LEVEL · 1096043 · ch1 · [ft]" in shell.settings_analyte.options
    assert shell.settings_range is not None
    assert shell.settings_range.value == "7d"
    assert shell.settings_publish is not None
    assert shell.settings_publish.label == "Published"
    assert shell.settings_publish.disabled is True
    assert shell.has_unpublished_changes(figure["id"]) is False
    assert shell.settings_delete is not None
    assert shell.settings_message is not None
    assert "Choose a location and analyte" in shell.settings_message.object


def test_editing_a_figure_keeps_the_change_local_until_published(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None
    original_title = figure["title"]

    shell.settings_title.value = "Well level"

    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["title"] == original_title
    assert shell.has_unpublished_changes(figure["id"]) is True
    assert shell.settings_publish.label == "Publish"
    assert shell.settings_publish.disabled is False
    assert shell.figure_cards[figure["id"]].ribbon.label == "Well level"
    assert "figure-unpublished" in shell.figure_cards[figure["id"]].css_classes


def test_publishing_updates_the_figure_and_the_ribbon(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.settings_title.value = "Well level"
    shell.settings_location.value = "1"
    shell.settings_analyte.value = "10"
    shell.settings_range.value = "24h"
    shell.settings_publish.clicks += 1

    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["title"] == "Well level"
    assert stored["location_id"] == 1
    assert stored["series_id"] == 10
    assert stored["range"] == "24h"
    assert shell.figure_cards[figure["id"]].ribbon.label == "Well level"
    assert shell.has_unpublished_changes(figure["id"]) is False
    assert shell.settings_publish.label == "Published"
    assert shell.settings_publish.disabled is True
    assert "figure-unpublished" not in shell.figure_cards[figure["id"]].css_classes
    assert shell.notice.visible is True
    assert "Published" in shell.notice.object


def test_reverting_a_field_to_the_stored_value_clears_the_draft(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.settings_title.value = "Changed"
    assert shell.has_unpublished_changes(figure["id"]) is True

    shell.settings_title.value = figure["title"]

    assert shell.has_unpublished_changes(figure["id"]) is False
    assert shell.settings_publish.label == "Published"
    assert shell.settings_publish.disabled is True
    assert "figure-unpublished" not in shell.figure_cards[figure["id"]].css_classes


def test_unpublished_border_turns_orange_when_selected(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    first = shell.create_figure(0, 0)
    second = shell.create_figure(3, 0)
    assert first is not None
    assert second is not None

    shell.settings_title.value = "Second"
    second_card = shell.figure_cards[second["id"]]
    assert "figure-unpublished" in second_card.css_classes
    assert "figure-selected" in second_card.css_classes

    shell.select_figure(first["id"])

    assert "figure-unpublished" in second_card.css_classes
    assert "figure-selected" not in second_card.css_classes
    assert "figure-unpublished" not in shell.figure_cards[first["id"]].css_classes


def test_publishing_writes_only_the_changed_fields(tmp_path, monkeypatch) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    shell.create_figure(0, 0)
    shell.settings_location.value = "1"
    shell.settings_analyte.value = "10"
    shell.settings_publish.clicks += 1

    calls: list[dict] = []
    original = scenes.update_figure

    def spy(data, scene_id, figure_id, **kwargs):
        calls.append(kwargs)
        return original(data, scene_id, figure_id, **kwargs)

    monkeypatch.setattr(scenes, "update_figure", spy)
    shell.settings_title.value = "Renamed"
    shell.settings_publish.clicks += 1

    assert calls == [{"title": "Renamed"}]


def test_publishing_a_new_location_writes_the_analyte_pair(
    tmp_path, monkeypatch
) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    shell.create_figure(0, 0)
    shell.settings_location.value = "1"
    shell.settings_analyte.value = "10"
    shell.settings_publish.clicks += 1

    calls: list[dict] = []
    original = scenes.update_figure

    def spy(data, scene_id, figure_id, **kwargs):
        calls.append(kwargs)
        return original(data, scene_id, figure_id, **kwargs)

    monkeypatch.setattr(scenes, "update_figure", spy)
    shell.settings_location.value = "2"
    shell.settings_analyte.value = "12"
    shell.settings_publish.clicks += 1

    assert calls == [{"location_id": 2, "series_id": 12}]


def test_publishing_requires_an_analyte(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.settings_title.value = "Named but empty"
    shell.settings_publish.clicks += 1

    assert shell.settings_message.visible is True
    assert "Choose an analyte" in shell.settings_message.object
    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["title"] == figure["title"]
    assert stored["series_id"] is None
    assert shell.has_unpublished_changes(figure["id"]) is True
    assert shell.settings_publish.label == "Publish"


def test_publishing_rejects_a_blank_title(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.settings_title.value = "   "
    shell.settings_analyte.value = "10"
    shell.settings_publish.clicks += 1

    assert "Title must not be blank" in shell.settings_message.object
    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["title"] == figure["title"]
    assert shell.has_unpublished_changes(figure["id"]) is True


def test_publishing_rejects_an_analyte_without_readings_at_the_location(
    tmp_path, monkeypatch
) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.settings_location.value = "1"
    shell.settings_analyte.value = "10"
    monkeypatch.setattr(
        shell.client, "series_at_location", lambda *args, **kwargs: []
    )
    shell.settings_publish.clicks += 1

    assert "no readings at this location" in shell.settings_message.object
    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["series_id"] is None
    assert shell.has_unpublished_changes(figure["id"]) is True


def test_changing_the_location_refreshes_the_analyte_options(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    shell.create_figure(0, 0)

    shell.settings_location.value = "1"
    assert option_values(shell.settings_analyte) == ["", "10", "11"]
    shell.settings_analyte.value = "10"

    shell.settings_location.value = "2"

    assert option_values(shell.settings_analyte) == ["", "12"]
    assert shell.settings_analyte.value == ""


def test_deleting_a_figure_requires_confirmation(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.settings_delete.clicks += 1

    assert shell.settings_delete is None
    assert shell.settings_confirm_delete is not None
    assert shell.settings_cancel_delete is not None
    assert len(store.load()["scenes"][0]["figures"]) == 1

    shell.settings_cancel_delete.clicks += 1

    assert shell.settings_delete is not None
    assert shell.settings_confirm_delete is None
    assert len(store.load()["scenes"][0]["figures"]) == 1

    shell.settings_delete.clicks += 1
    shell.settings_confirm_delete.clicks += 1

    assert store.load()["scenes"][0]["figures"] == []
    assert shell.selected_figure_id is None
    assert shell.settings_title is None
    assert "Select a figure" in shell.settings_body.objects[0].object


def test_a_failed_location_load_is_reported_in_the_settings_card(
    tmp_path, monkeypatch
) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()

    def fail(token: str):
        raise ReadApiError(502, "read API is unreachable")

    monkeypatch.setattr(shell.client, "locations", fail)
    shell.create_figure(0, 0)

    assert shell.settings_location is not None
    assert shell.settings_location.options == {"All locations": ""}
    assert shell.settings_message.visible is True
    assert "Could not load locations" in shell.settings_message.object


def test_a_draft_selection_renders_a_bokeh_chart(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.settings_analyte.value = "10"

    assert isinstance(shell.figure_cards[figure["id"]].chart, pn.pane.Bokeh)
    assert store.load()["scenes"][0]["figures"][0]["series_id"] is None


@pytest.mark.parametrize(
    ("range_key", "hours"), [("24h", 24), ("7d", 24 * 7), ("30d", 24 * 30)]
)
def test_a_chart_requests_the_configured_window_and_point_cap(
    tmp_path, monkeypatch, range_key: str, hours: int
) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    shell.create_figure(0, 0)
    calls: list[dict] = []

    def points(token: str, series_id: int, **kwargs) -> list[dict]:
        calls.append({"series_id": series_id, **kwargs})
        return [dict(row) for row in POINTS]

    monkeypatch.setattr(shell.client, "points", points)
    shell.settings_analyte.value = "10"
    shell.settings_location.value = "1"
    shell.settings_range.value = range_key

    assert calls
    last = calls[-1]
    assert last["series_id"] == 10
    assert last["points"] == 2000
    assert last["tz"] == "local"
    assert last["location_id"] == 1
    span = datetime.fromisoformat(last["end"]) - datetime.fromisoformat(
        last["start"]
    )
    assert span.total_seconds() == hours * 3600


def test_a_figure_without_data_shows_an_inline_message(tmp_path, monkeypatch) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    monkeypatch.setattr(shell.client, "points", lambda *args, **kwargs: [])
    shell.settings_analyte.value = "10"

    chart = shell.figure_cards[figure["id"]].chart
    assert isinstance(chart, pn.pane.HTML)
    assert "No data available for this range" in chart.object


def test_a_failed_chart_load_does_not_break_the_scene(tmp_path, monkeypatch) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    def fail(*args, **kwargs):
        raise ReadApiError(502, "read API is unreachable")

    monkeypatch.setattr(shell.client, "points", fail)
    shell.settings_analyte.value = "10"

    chart = shell.figure_cards[figure["id"]].chart
    assert isinstance(chart, pn.pane.HTML)
    assert "Could not load chart" in chart.object
    assert "read API is unreachable" in chart.object
    assert figure["id"] in shell.figure_cards


def test_opening_a_scene_loads_a_chart_for_every_figure(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    first = shell.create_figure(0, 0)
    second = shell.create_figure(3, 0)
    assert first is not None
    assert second is not None

    shell.select_figure(first["id"])
    shell.settings_analyte.value = "10"
    shell.settings_publish.clicks += 1
    shell.select_figure(second["id"])
    shell.settings_analyte.value = "11"
    shell.settings_publish.clicks += 1

    shell.refresh()

    assert isinstance(shell.figure_cards[first["id"]].chart, pn.pane.Bokeh)
    assert isinstance(shell.figure_cards[second["id"]].chart, pn.pane.Bokeh)


def test_an_unconfigured_figure_shows_the_choose_prompt(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    chart = shell.figure_cards[figure["id"]].chart
    assert isinstance(chart, pn.pane.HTML)
    assert "Choose a location and analyte" in chart.object


@pytest.mark.parametrize(
    ("days", "expected"), [(1, "24h"), (7, "7d"), (30, "30d")]
)
def test_new_figures_use_the_configured_default_range(
    tmp_path, days: int, expected: str
) -> None:
    shell, _ = make_shell(tmp_path, default_window_days=days)
    shell.create_scene()

    figure = shell.create_figure(0, 0)

    assert figure is not None
    assert figure["range"] == expected


def configure_and_publish(
    shell: DashboardShell,
    *,
    title: str = "Figure",
    location: str = "1",
    analyte: str = "10",
    range_key: str = "7d",
) -> None:
    shell.settings_title.value = title
    shell.settings_location.value = location
    shell.settings_analyte.value = analyte
    shell.settings_range.value = range_key
    shell.settings_publish.clicks += 1


def test_a_layout_change_does_not_create_a_draft(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    card_id = str(id(shell.figure_cards[figure["id"]]))
    shell.apply_grid_state([{"id": card_id, "x0": 3, "y0": 0, "x1": 6, "y1": 2}])

    assert shell.has_unpublished_changes(figure["id"]) is False
    assert shell.settings_publish.label == "Published"
    assert shell.settings_publish.disabled is True
    assert store.load()["scenes"][0]["figures"][0]["layout"]["x"] == 3


def _sync_widget_to_model(widget: Any) -> None:
    """Emulate the property sync Panel performs when a widget is first rendered."""

    widget._process_param_change({"options": widget.options, "value": widget.value})


def test_selecting_a_figure_does_not_create_a_draft(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    first = shell.create_figure(0, 0)
    second = shell.create_figure(3, 0)
    assert first is not None
    assert second is not None

    shell.select_figure(second["id"])
    # Rebuilding the settings form must not look like an edit: Panel pushes the
    # fresh widgets to the browser, and a Select whose value is not among its
    # options is reset to None during that sync.
    _sync_widget_to_model(shell.settings_location)
    _sync_widget_to_model(shell.settings_analyte)
    _sync_widget_to_model(shell.settings_range)

    assert shell.has_unpublished_changes(first["id"]) is False
    assert shell.has_unpublished_changes(second["id"]) is False
    assert shell.settings_publish.label == "Published"
    assert shell.settings_publish.disabled is True
    assert "figure-unpublished" not in shell.figure_cards[second["id"]].css_classes


def test_drafts_survive_a_scene_switch(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    first_scene = shell.create_scene(name="First")
    figure = shell.create_figure(0, 0)
    assert first_scene is not None
    assert figure is not None
    shell.settings_title.value = "Draft title"

    shell.create_scene(name="Second")
    shell.select_scene(first_scene["id"])
    shell.select_figure(figure["id"])

    assert shell.has_unpublished_changes(figure["id"]) is True
    assert shell.settings_title.value == "Draft title"
    assert shell.settings_publish.label == "Publish"
    assert "figure-unpublished" in shell.figure_cards[figure["id"]].css_classes


def test_a_new_session_does_not_inherit_drafts(tmp_path) -> None:
    shell, _ = make_shell(tmp_path, label="alice")
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None
    shell.settings_title.value = "Draft title"

    reloaded, _ = make_shell(tmp_path, label="alice")

    assert reloaded.has_unpublished_changes(figure["id"]) is False
    assert reloaded.figure_cards[figure["id"]].ribbon.label == figure["title"]
    reloaded.select_figure(figure["id"])
    assert reloaded.settings_publish.label == "Published"
    assert "figure-unpublished" not in reloaded.figure_cards[figure["id"]].css_classes


def test_deleting_a_figure_drops_its_draft(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None
    shell.settings_title.value = "Draft"

    shell.settings_delete.clicks += 1
    shell.settings_confirm_delete.clicks += 1

    assert shell.has_unpublished_changes(figure["id"]) is False
    assert store.load()["scenes"][0]["figures"] == []


def test_a_conflicting_publish_opens_the_modal_and_writes_nothing(tmp_path) -> None:
    shell_a, store = make_shell(tmp_path, label="alice")
    shell_a.create_scene()
    figure = shell_a.create_figure(0, 0)
    assert figure is not None
    configure_and_publish(shell_a, title="Original")

    shell_a.settings_title.value = "From Alice"

    shell_b, _ = make_shell(tmp_path, label="bob")
    shell_b.select_figure(figure["id"])
    shell_b.settings_title.value = "From Bob"
    shell_b.settings_publish.clicks += 1

    shell_a.settings_publish.clicks += 1

    assert shell_a.conflict_modal is not None
    assert shell_a.conflict_modal.open is True
    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["title"] == "From Bob"
    assert shell_a.has_unpublished_changes(figure["id"]) is True
    assert shell_a.settings_publish.label == "Publish"


def test_overwriting_a_conflict_publishes_the_draft(tmp_path) -> None:
    shell_a, store = make_shell(tmp_path, label="alice")
    shell_a.create_scene()
    figure = shell_a.create_figure(0, 0)
    assert figure is not None
    configure_and_publish(shell_a, title="Original")

    shell_a.settings_title.value = "From Alice"

    shell_b, _ = make_shell(tmp_path, label="bob")
    shell_b.select_figure(figure["id"])
    shell_b.settings_title.value = "From Bob"
    shell_b.settings_publish.clicks += 1

    shell_a.settings_publish.clicks += 1
    assert shell_a.conflict_overwrite is not None
    shell_a.conflict_overwrite.clicks += 1

    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["title"] == "From Alice"
    assert shell_a.conflict_modal.open is False
    assert shell_a.has_unpublished_changes(figure["id"]) is False
    assert shell_a.settings_publish.label == "Published"


def test_reverting_a_conflict_restores_the_server_figure(tmp_path) -> None:
    shell_a, store = make_shell(tmp_path, label="alice")
    shell_a.create_scene()
    figure = shell_a.create_figure(0, 0)
    assert figure is not None
    configure_and_publish(shell_a, title="Original")

    shell_a.settings_title.value = "From Alice"

    shell_b, _ = make_shell(tmp_path, label="bob")
    shell_b.select_figure(figure["id"])
    shell_b.settings_title.value = "From Bob"
    shell_b.settings_publish.clicks += 1

    shell_a.settings_publish.clicks += 1
    assert shell_a.conflict_revert is not None
    shell_a.conflict_revert.clicks += 1

    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["title"] == "From Bob"
    assert shell_a.conflict_modal.open is False
    assert shell_a.has_unpublished_changes(figure["id"]) is False
    assert shell_a.settings_title.value == "From Bob"
    assert shell_a.figure_cards[figure["id"]].ribbon.label == "From Bob"


def test_a_non_overlapping_server_change_does_not_conflict(tmp_path) -> None:
    shell_a, store = make_shell(tmp_path, label="alice")
    shell_a.create_scene()
    figure = shell_a.create_figure(0, 0)
    assert figure is not None
    configure_and_publish(shell_a, title="Original")

    shell_a.settings_title.value = "From Alice"

    shell_b, _ = make_shell(tmp_path, label="bob")
    shell_b.select_figure(figure["id"])
    shell_b.settings_range.value = "24h"
    shell_b.settings_publish.clicks += 1

    shell_a.settings_publish.clicks += 1

    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["title"] == "From Alice"
    assert stored["range"] == "24h"
    assert shell_a.conflict_modal.open is False
    assert shell_a.has_unpublished_changes(figure["id"]) is False


def test_publishing_a_remotely_deleted_figure_reports_it(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    scene = shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert scene is not None
    assert figure is not None
    configure_and_publish(shell, title="Original")
    shell.settings_title.value = "Draft"

    store.mutate(
        lambda data: scenes.delete_figure(data, scene["id"], figure["id"])
    )
    shell.settings_publish.clicks += 1

    assert shell.conflict_modal.open is False
    assert shell.notice.visible is True
    assert "deleted" in shell.notice.object.lower()
    assert shell.has_unpublished_changes(figure["id"]) is False
