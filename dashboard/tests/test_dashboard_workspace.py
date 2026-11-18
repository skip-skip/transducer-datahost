import json
from datetime import datetime

import panel as pn
import pytest

from transducer_dashboard import scenes
from transducer_dashboard.auth import Session
from transducer_dashboard.read_client import ReadApiError
from transducer_dashboard.settings import DashboardSettings
from transducer_dashboard.state import GRID_COLUMNS, GRID_ROW_HEIGHT, StateStore
from transducer_dashboard.workspace import DashboardShell

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


def test_create_scene_selects_and_persists_it(tmp_path) -> None:
    shell, store = make_shell(tmp_path)

    scene = shell.create_scene()

    assert scene is not None
    assert shell.selected_scene_id == scene["id"]
    assert shell.scene_buttons[scene["id"]].button_type == "primary"
    assert scene["name"] in shell.scene_title.object
    assert "0 figures" in shell.scene_summary.object
    assert "Click an empty cell" in shell.scene_summary.object
    assert shell.grid is not None
    assert shell.figure_cards == {}

    stored = store.load()
    assert stored["scenes"][0]["id"] == scene["id"]
    assert stored["ui"]["alice"]["selected_scene_id"] == scene["id"]


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


def test_scene_names_are_escaped_in_the_canvas(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    scene = shell.create_scene(name="<script>alert(1)</script>")

    assert scene is not None
    assert "<script>" not in shell.scene_title.object
    assert "&lt;script&gt;" in shell.scene_title.object
    assert shell.scene_buttons[scene["id"]].label == "<script>alert(1)</script>"


def test_selecting_an_unknown_scene_shows_an_error(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()

    shell.select_scene("missing")

    assert shell.status_pane.visible is True
    assert "selected scene not found" in shell.status_pane.object


def test_deleting_without_a_selected_scene_shows_an_error(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)

    shell.request_delete_scene()

    assert shell.status_pane.visible is True
    assert "Select a scene to delete" in shell.status_pane.object


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
    assert "1 figure" in shell.scene_summary.object

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
    assert shell.status_pane.visible is True
    assert "no room for another figure" in shell.status_pane.object
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


def test_selecting_a_figure_expands_the_settings_card(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    first = shell.create_figure(0, 0)
    second = shell.create_figure(3, 0)
    assert first is not None
    assert second is not None
    shell.settings_card.collapsed = True

    shell.figure_cards[first["id"]].ribbon.clicks += 1

    assert shell.selected_figure_id == first["id"]
    assert shell.settings_card.collapsed is False


def test_creating_a_figure_expands_the_settings_card(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    shell.settings_card.collapsed = True

    shell.create_figure(0, 0)

    assert shell.settings_card.collapsed is False


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
    assert shell.status_pane.visible is True
    assert "Saved" in shell.status_pane.object

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
    assert shell.status_pane.visible is False


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

    assert shell.status_pane.visible is True
    assert "overlap" in shell.status_pane.object
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
    return [value for _, value in select.options]


def test_the_settings_card_shows_the_selected_figure(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    assert shell.settings_title is not None
    assert shell.settings_title.value == figure["title"]
    assert shell.settings_location is not None
    assert shell.settings_location.options[0] == ("All locations", "")
    assert ("MCMW-04", "1") in shell.settings_location.options
    assert shell.settings_location.value == ""
    assert shell.settings_analyte is not None
    assert shell.settings_analyte.value == ""
    assert option_values(shell.settings_analyte) == ["", "10", "11", "12"]
    assert "LEVEL · 1096043 · ch1 · [ft]" in [
        label for label, _ in shell.settings_analyte.options
    ]
    assert shell.settings_range is not None
    assert shell.settings_range.value == "7d"
    assert shell.settings_save is not None
    assert shell.settings_delete is not None
    assert shell.settings_message is not None
    assert "Choose a location and analyte" in shell.settings_message.object


def test_saving_the_settings_updates_the_figure_and_the_ribbon(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.settings_title.value = "Well level"
    shell.settings_location.value = "1"
    shell.settings_analyte.value = "10"
    shell.settings_range.value = "24h"
    shell.settings_save.clicks += 1

    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["title"] == "Well level"
    assert stored["location_id"] == 1
    assert stored["series_id"] == 10
    assert stored["range"] == "24h"
    assert shell.figure_cards[figure["id"]].ribbon.label == "Well level"
    assert shell.status_pane.visible is True
    assert "Saved" in shell.status_pane.object


def test_saving_requires_an_analyte(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.settings_title.value = "Named but empty"
    shell.settings_save.clicks += 1

    assert shell.settings_message.visible is True
    assert "Choose an analyte" in shell.settings_message.object
    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["title"] == figure["title"]
    assert stored["series_id"] is None


def test_saving_rejects_a_blank_title(tmp_path) -> None:
    shell, store = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.settings_title.value = "   "
    shell.settings_analyte.value = "10"
    shell.settings_save.clicks += 1

    assert "Title must not be blank" in shell.settings_message.object
    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["title"] == figure["title"]


def test_saving_rejects_an_analyte_without_readings_at_the_location(
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
    shell.settings_save.clicks += 1

    assert "no readings at this location" in shell.settings_message.object
    stored = store.load()["scenes"][0]["figures"][0]
    assert stored["series_id"] is None


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
    assert shell.settings_location.options == [("All locations", "")]
    assert shell.settings_message.visible is True
    assert "Could not load locations" in shell.settings_message.object


def test_saving_a_selection_renders_a_bokeh_chart(tmp_path) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    shell.settings_analyte.value = "10"
    shell.settings_save.clicks += 1

    assert isinstance(shell.figure_cards[figure["id"]].chart, pn.pane.Bokeh)


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
    shell.settings_save.clicks += 1

    assert len(calls) == 1
    assert calls[0]["series_id"] == 10
    assert calls[0]["points"] == 2000
    assert calls[0]["tz"] == "local"
    assert calls[0]["location_id"] == 1
    span = datetime.fromisoformat(calls[0]["end"]) - datetime.fromisoformat(
        calls[0]["start"]
    )
    assert span.total_seconds() == hours * 3600


def test_a_figure_without_data_shows_an_inline_message(tmp_path, monkeypatch) -> None:
    shell, _ = make_shell(tmp_path)
    shell.create_scene()
    figure = shell.create_figure(0, 0)
    assert figure is not None

    monkeypatch.setattr(shell.client, "points", lambda *args, **kwargs: [])
    shell.settings_analyte.value = "10"
    shell.settings_save.clicks += 1

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
    shell.settings_save.clicks += 1

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
    shell.settings_save.clicks += 1
    shell.select_figure(second["id"])
    shell.settings_analyte.value = "11"
    shell.settings_save.clicks += 1

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
