import pytest

from transducer_dashboard import scenes
from transducer_dashboard.state import (
    GRID_COLUMNS,
    MIN_FIGURE_HEIGHT,
    MIN_FIGURE_WIDTH,
    StateStore,
    default_state,
    validate_state,
)


def make_scene(state, name: str | None = None):
    return scenes.create_scene(state, name)


def test_create_scene_defaults_to_the_next_free_name() -> None:
    state = default_state()

    first = scenes.create_scene(state)
    second = scenes.create_scene(state)

    assert first["name"] == "Scene 1"
    assert second["name"] == "Scene 2"
    assert first["figures"] == []
    assert first["id"] != second["id"]
    validate_state(state)


def test_create_scene_accepts_a_name_and_rejects_blank() -> None:
    state = default_state()

    scene = scenes.create_scene(state, "  Aquifer tests  ")

    assert scene["name"] == "Aquifer tests"
    with pytest.raises(scenes.SceneError, match="blank"):
        scenes.create_scene(state, "   ")


def test_delete_scene_removes_it_and_clears_ui_selections() -> None:
    state = default_state()
    scene = make_scene(state)
    other = make_scene(state)
    figure = scenes.add_figure(state, scene["id"], 0, 0)
    scenes.set_ui(
        state,
        "alice",
        selected_scene_id=scene["id"],
        selected_figure_id=figure["id"],
    )
    scenes.set_ui(state, "bob", selected_scene_id=other["id"])

    scenes.delete_scene(state, scene["id"])

    assert scenes.find_scene(state, scene["id"]) is None
    assert scenes.get_ui(state, "alice")["selected_scene_id"] is None
    assert scenes.get_ui(state, "alice")["selected_figure_id"] is None
    assert scenes.get_ui(state, "bob")["selected_scene_id"] == other["id"]
    validate_state(state)


def test_delete_scene_clears_a_stale_figure_selection() -> None:
    state = default_state()
    scene = make_scene(state)
    other = make_scene(state)
    figure = scenes.add_figure(state, scene["id"], 0, 0)
    state["ui"]["carol"] = {
        "selected_scene_id": other["id"],
        "selected_figure_id": figure["id"],
        "scenes_tray_collapsed": False,
    }

    scenes.delete_scene(state, scene["id"])

    assert state["ui"]["carol"]["selected_figure_id"] is None
    assert state["ui"]["carol"]["selected_scene_id"] == other["id"]


def test_delete_unknown_scene_raises() -> None:
    state = default_state()

    with pytest.raises(scenes.SceneNotFound):
        scenes.delete_scene(state, "missing")


def test_add_figure_places_a_minimum_size_figure() -> None:
    state = default_state()
    scene = make_scene(state)

    figure = scenes.add_figure(state, scene["id"], 2, 1)

    assert figure["layout"] == {
        "x": 2,
        "y": 1,
        "w": MIN_FIGURE_WIDTH,
        "h": MIN_FIGURE_HEIGHT,
    }
    assert figure["title"] == scenes.DEFAULT_FIGURE_TITLE
    assert figure["range"] == "7d"
    assert figure["location_id"] is None
    assert figure["series_id"] is None
    assert scene["figures"] == [figure]
    validate_state(state)


def test_add_figure_uses_the_requested_default_range() -> None:
    state = default_state()
    scene = make_scene(state)

    figure = scenes.add_figure(state, scene["id"], 0, 0, range_key="24h")

    assert figure["range"] == "24h"
    with pytest.raises(scenes.SceneError, match="range"):
        scenes.add_figure(state, scene["id"], 3, 0, range_key="1y")


def test_add_figure_uses_the_first_free_slot_when_the_cell_is_taken() -> None:
    state = default_state()
    scene = make_scene(state)

    first = scenes.add_figure(state, scene["id"], 0, 0)
    second = scenes.add_figure(state, scene["id"], 0, 0)

    assert first["layout"] == {"x": 0, "y": 0, "w": 3, "h": 2}
    assert second["layout"] == {"x": 3, "y": 0, "w": 3, "h": 2}


def test_add_figure_falls_back_to_the_next_row() -> None:
    state = default_state()
    scene = make_scene(state)

    for x in (0, 3, 6, 9):
        scenes.add_figure(state, scene["id"], x, 0)

    figure = scenes.add_figure(state, scene["id"], 0, 0)

    assert figure["layout"] == {"x": 0, "y": 2, "w": 3, "h": 2}
    validate_state(state)


@pytest.mark.parametrize(("x", "y"), [(-1, 0), (0, -1), (GRID_COLUMNS, 0)])
def test_add_figure_rejects_invalid_cells(x: int, y: int) -> None:
    state = default_state()
    scene = make_scene(state)

    with pytest.raises(scenes.SceneError):
        scenes.add_figure(state, scene["id"], x, y)


def test_add_figure_to_an_unknown_scene_raises() -> None:
    state = default_state()

    with pytest.raises(scenes.SceneNotFound):
        scenes.add_figure(state, "missing", 0, 0)


def test_add_figure_at_keeps_the_painted_span() -> None:
    state = default_state()
    scene = make_scene(state)

    figure = scenes.add_figure_at(state, scene["id"], 2, 1, 5, 4, range_key="24h")

    assert figure["layout"] == {"x": 2, "y": 1, "w": 5, "h": 4}
    assert figure["range"] == "24h"
    assert scene["figures"] == [figure]
    validate_state(state)


def test_add_figure_at_does_not_fall_back_to_a_free_slot() -> None:
    state = default_state()
    scene = make_scene(state)
    scenes.add_figure_at(state, scene["id"], 0, 0, 3, 2)

    with pytest.raises(scenes.SceneError, match="occupied"):
        scenes.add_figure_at(state, scene["id"], 0, 0, 3, 2)

    assert len(scene["figures"]) == 1


@pytest.mark.parametrize(
    ("x", "y", "w", "h"),
    [
        (-1, 0, 3, 2),
        (0, -1, 3, 2),
        (0, 0, MIN_FIGURE_WIDTH - 1, 2),
        (0, 0, 3, MIN_FIGURE_HEIGHT - 1),
        (GRID_COLUMNS - 2, 0, 3, 2),
    ],
)
def test_add_figure_at_rejects_invalid_spans(
    x: int, y: int, w: int, h: int
) -> None:
    state = default_state()
    scene = make_scene(state)

    with pytest.raises(scenes.SceneError):
        scenes.add_figure_at(state, scene["id"], x, y, w, h)


def test_add_figure_at_to_an_unknown_scene_raises() -> None:
    state = default_state()

    with pytest.raises(scenes.SceneNotFound):
        scenes.add_figure_at(state, "missing", 0, 0, 3, 2)


def test_find_free_slot_rejects_impossible_sizes() -> None:
    assert scenes.find_free_slot([], width=GRID_COLUMNS + 1) is None
    assert scenes.find_free_slot([], width=MIN_FIGURE_WIDTH - 1) is None
    assert scenes.find_free_slot([], height=MIN_FIGURE_HEIGHT - 1) is None


def test_update_figure_changes_fields_and_preserves_unset_values() -> None:
    state = default_state()
    scene = make_scene(state)
    figure = scenes.add_figure(state, scene["id"], 0, 0)

    updated = scenes.update_figure(
        state,
        scene["id"],
        figure["id"],
        title="  Water level  ",
        location_id=7,
        series_id=42,
        range="24h",
    )

    assert updated["title"] == "Water level"
    assert updated["location_id"] == 7
    assert updated["series_id"] == 42
    assert updated["range"] == "24h"

    scenes.update_figure(state, scene["id"], figure["id"], title="Renamed")

    stored = scenes.get_figure(state, scene["id"], figure["id"])
    assert stored["title"] == "Renamed"
    assert stored["location_id"] == 7
    assert stored["series_id"] == 42
    assert stored["range"] == "24h"

    scenes.update_figure(
        state, scene["id"], figure["id"], location_id=None, series_id=None
    )

    assert stored["location_id"] is None
    assert stored["series_id"] is None
    validate_state(state)


def test_update_figure_rejects_blank_title_and_bad_values() -> None:
    state = default_state()
    scene = make_scene(state)
    figure = scenes.add_figure(state, scene["id"], 0, 0)

    with pytest.raises(scenes.SceneError, match="blank"):
        scenes.update_figure(state, scene["id"], figure["id"], title="   ")
    with pytest.raises(scenes.SceneError, match="range"):
        scenes.update_figure(state, scene["id"], figure["id"], range="1y")
    with pytest.raises(scenes.SceneError, match="location_id"):
        scenes.update_figure(state, scene["id"], figure["id"], location_id="7")


def test_update_figure_rejects_an_unknown_figure() -> None:
    state = default_state()
    scene = make_scene(state)

    with pytest.raises(scenes.FigureNotFound):
        scenes.update_figure(state, scene["id"], "missing", title="x")


def test_delete_figure_clears_the_selection_but_keeps_the_scene() -> None:
    state = default_state()
    scene = make_scene(state)
    figure = scenes.add_figure(state, scene["id"], 0, 0)
    scenes.set_ui(
        state,
        "alice",
        selected_scene_id=scene["id"],
        selected_figure_id=figure["id"],
    )

    scenes.delete_figure(state, scene["id"], figure["id"])

    assert scene["figures"] == []
    assert scenes.get_ui(state, "alice")["selected_scene_id"] == scene["id"]
    assert scenes.get_ui(state, "alice")["selected_figure_id"] is None
    validate_state(state)


def test_delete_unknown_figure_raises() -> None:
    state = default_state()
    scene = make_scene(state)

    with pytest.raises(scenes.FigureNotFound):
        scenes.delete_figure(state, scene["id"], "missing")


def test_update_layout_applies_a_batch_and_keeps_omitted_figures() -> None:
    state = default_state()
    scene = make_scene(state)
    first = scenes.add_figure(state, scene["id"], 0, 0)
    second = scenes.add_figure(state, scene["id"], 3, 0)

    scenes.update_layout(
        state,
        scene["id"],
        {
            first["id"]: {"x": 6, "y": 0, "w": 3, "h": 2},
            second["id"]: {"x": 9, "y": 0, "w": 3, "h": 2},
        },
    )

    assert first["layout"] == {"x": 6, "y": 0, "w": 3, "h": 2}
    assert second["layout"] == {"x": 9, "y": 0, "w": 3, "h": 2}

    scenes.update_layout(
        state, scene["id"], {first["id"]: {"x": 0, "y": 4, "w": 3, "h": 2}}
    )

    assert first["layout"] == {"x": 0, "y": 4, "w": 3, "h": 2}
    assert second["layout"] == {"x": 9, "y": 0, "w": 3, "h": 2}
    validate_state(state)


def test_update_layout_rejects_overlap_without_mutating() -> None:
    state = default_state()
    scene = make_scene(state)
    first = scenes.add_figure(state, scene["id"], 0, 0)
    scenes.add_figure(state, scene["id"], 3, 0)

    with pytest.raises(scenes.SceneError, match="overlap"):
        scenes.update_layout(
            state, scene["id"], {first["id"]: {"x": 0, "y": 0, "w": 6, "h": 2}}
        )

    assert first["layout"] == {"x": 0, "y": 0, "w": 3, "h": 2}


@pytest.mark.parametrize(
    ("layout", "match"),
    [
        ({"x": 0, "y": 0, "w": 2, "h": 2}, "at least 3"),
        ({"x": 0, "y": 0, "w": 3, "h": 1}, "at least 2"),
        ({"x": 10, "y": 0, "w": 3, "h": 2}, "fit within"),
        ({"x": -1, "y": 0, "w": 3, "h": 2}, "non-negative"),
    ],
)
def test_update_layout_rejects_invalid_bounds(
    layout: dict[str, int], match: str
) -> None:
    state = default_state()
    scene = make_scene(state)
    figure = scenes.add_figure(state, scene["id"], 0, 0)

    with pytest.raises(scenes.SceneError, match=match):
        scenes.update_layout(state, scene["id"], {figure["id"]: layout})


def test_update_layout_rejects_an_unknown_figure() -> None:
    state = default_state()
    scene = make_scene(state)

    with pytest.raises(scenes.FigureNotFound):
        scenes.update_layout(
            state, scene["id"], {"missing": {"x": 0, "y": 0, "w": 3, "h": 2}}
        )


def test_ui_defaults_and_updates() -> None:
    state = default_state()
    scene = make_scene(state)
    figure = scenes.add_figure(state, scene["id"], 0, 0)

    assert scenes.get_ui(state, "alice") == {
        "selected_scene_id": None,
        "selected_figure_id": None,
        "scenes_tray_collapsed": False,
    }

    ui = scenes.set_ui(
        state,
        "alice",
        selected_scene_id=scene["id"],
        selected_figure_id=figure["id"],
        scenes_tray_collapsed=True,
    )

    assert ui["selected_scene_id"] == scene["id"]
    assert ui["selected_figure_id"] == figure["id"]
    assert ui["scenes_tray_collapsed"] is True
    assert scenes.get_ui(state, "bob")["selected_scene_id"] is None
    validate_state(state)


def test_set_ui_preserves_existing_fields() -> None:
    state = default_state()
    scene = make_scene(state)
    figure = scenes.add_figure(state, scene["id"], 0, 0)
    scenes.set_ui(
        state, "alice", selected_scene_id=scene["id"], scenes_tray_collapsed=True
    )

    scenes.set_ui(state, "alice", selected_figure_id=figure["id"])

    ui = scenes.get_ui(state, "alice")
    assert ui["selected_scene_id"] == scene["id"]
    assert ui["selected_figure_id"] == figure["id"]
    assert ui["scenes_tray_collapsed"] is True


def test_set_ui_rejects_unknown_fields_and_bad_references() -> None:
    state = default_state()
    scene = make_scene(state)
    other = make_scene(state)
    other_figure = scenes.add_figure(state, other["id"], 0, 0)

    with pytest.raises(scenes.SceneError, match="unknown UI fields"):
        scenes.set_ui(state, "alice", view="personal")
    with pytest.raises(scenes.SceneError, match="selected scene not found"):
        scenes.set_ui(state, "alice", selected_scene_id="missing")
    with pytest.raises(scenes.SceneError, match="without a scene"):
        scenes.set_ui(state, "alice", selected_figure_id=other_figure["id"])
    with pytest.raises(scenes.SceneError, match="not in the selected scene"):
        scenes.set_ui(
            state,
            "alice",
            selected_scene_id=scene["id"],
            selected_figure_id=other_figure["id"],
        )
    with pytest.raises(scenes.SceneError, match="boolean"):
        scenes.set_ui(state, "alice", scenes_tray_collapsed="yes")


def test_scene_changes_persist_through_the_store(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure()

    state, scene = store.mutate(lambda data: scenes.create_scene(data))
    _, figure = store.mutate(
        lambda data: scenes.add_figure(data, scene["id"], 0, 0)
    )
    store.mutate(
        lambda data: scenes.set_ui(
            data,
            "alice",
            selected_scene_id=scene["id"],
            selected_figure_id=figure["id"],
        )
    )

    reloaded = store.load()

    assert reloaded["scenes"][0]["name"] == "Scene 1"
    assert reloaded["scenes"][0]["figures"][0]["id"] == figure["id"]
    assert reloaded["ui"]["alice"]["selected_figure_id"] == figure["id"]
