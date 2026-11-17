import pytest

from transducer_dashboard import figures
from transducer_dashboard.state import default_state

NO_EDITORS: frozenset[str] = frozenset()


def test_personal_figures_are_isolated() -> None:
    state = default_state()

    alice = figures.create_figure(state, "personal", "alice", NO_EDITORS)
    bob = figures.create_figure(state, "personal", "bob", NO_EDITORS)

    assert [item["id"] for item in figures.list_figures(state, "personal", "alice")] == [
        alice["id"]
    ]
    assert [item["id"] for item in figures.list_figures(state, "personal", "bob")] == [
        bob["id"]
    ]
    assert figures.get_figure(state, "personal", "alice", bob["id"]) is None


def test_shared_figures_are_visible_to_everyone() -> None:
    state = default_state()

    shared = figures.create_figure(state, "shared", "alice", NO_EDITORS)

    assert figures.get_figure(state, "shared", "bob", shared["id"]) is not None
    assert figures.list_figures(state, "shared", "bob")[0]["created_by"] == "alice"


def test_create_selects_and_names_uniquely() -> None:
    state = default_state()

    first = figures.create_figure(state, "personal", "alice", NO_EDITORS)
    second = figures.create_figure(state, "personal", "alice", NO_EDITORS)

    assert first["name"] == figures.DEFAULT_FIGURE_NAME
    assert second["name"] == f"{figures.DEFAULT_FIGURE_NAME} 2"
    assert figures.get_ui(state, "alice")["personal"]["selected_figure_id"] == second["id"]


def test_update_and_delete() -> None:
    state = default_state()
    figure = figures.create_figure(state, "personal", "alice", NO_EDITORS)

    updated = figures.update_figure(
        state,
        "personal",
        "alice",
        NO_EDITORS,
        figure["id"],
        name="  Water level  ",
        series_id=42,
    )
    assert updated["name"] == "Water level"
    assert updated["series_id"] == 42

    figures.update_figure(
        state,
        "personal",
        "alice",
        NO_EDITORS,
        figure["id"],
        series_id=None,
    )
    assert figures.get_figure(state, "personal", "alice", figure["id"])["series_id"] is None

    figures.delete_figure(state, "personal", "alice", NO_EDITORS, figure["id"])
    assert figures.list_figures(state, "personal", "alice") == []


def test_update_location_scoping() -> None:
    state = default_state()
    figure = figures.create_figure(state, "personal", "alice", NO_EDITORS)

    updated = figures.update_figure(
        state,
        "personal",
        "alice",
        NO_EDITORS,
        figure["id"],
        series_id=42,
        location_id=7,
    )
    assert updated["location_id"] == 7

    # A rename without an explicit location keeps the stored scope (UNSET
    # semantics), unlike a form save which always sends the field.
    figures.update_figure(
        state, "personal", "alice", NO_EDITORS, figure["id"], name="Renamed"
    )
    stored = figures.get_figure(state, "personal", "alice", figure["id"])
    assert stored["name"] == "Renamed"
    assert stored["location_id"] == 7

    # None clears the scope back to "all locations".
    figures.update_figure(
        state, "personal", "alice", NO_EDITORS, figure["id"], location_id=None
    )
    assert (
        figures.get_figure(state, "personal", "alice", figure["id"])["location_id"]
        is None
    )


def test_update_rejects_blank_name() -> None:
    state = default_state()
    figure = figures.create_figure(state, "personal", "alice", NO_EDITORS)

    with pytest.raises(figures.FigureError, match="blank"):
        figures.update_figure(
            state,
            "personal",
            "alice",
            NO_EDITORS,
            figure["id"],
            name="   ",
        )


def test_shared_permissions_respect_allowlist() -> None:
    state = default_state()
    editors = frozenset({"alice"})

    with pytest.raises(figures.FigurePermissionError):
        figures.create_figure(state, "shared", "bob", editors)

    shared = figures.create_figure(state, "shared", "alice", editors)

    with pytest.raises(figures.FigurePermissionError):
        figures.update_figure(
            state, "shared", "bob", editors, shared["id"], name="nope"
        )
    with pytest.raises(figures.FigurePermissionError):
        figures.delete_figure(state, "shared", "bob", editors, shared["id"])

    assert figures.can_edit("shared", "alice", editors) is True
    assert figures.can_edit("shared", "bob", editors) is False


def test_deleting_shared_figure_clears_every_selection() -> None:
    state = default_state()
    shared = figures.create_figure(state, "shared", "alice", NO_EDITORS)
    figures.set_ui(
        state, "bob", view="shared", shared={"selected_figure_id": shared["id"]}
    )

    figures.delete_figure(state, "shared", "alice", NO_EDITORS, shared["id"])

    assert figures.get_ui(state, "alice")["shared"]["selected_figure_id"] is None
    assert figures.get_ui(state, "bob")["shared"]["selected_figure_id"] is None


def test_toggle_state_is_per_view() -> None:
    state = default_state()

    figures.set_ui(state, "alice", personal={"folders_collapsed": True})

    ui = figures.get_ui(state, "alice")
    assert ui["personal"]["folders_collapsed"] is True
    assert ui["shared"]["folders_collapsed"] is False


def test_create_scene_selects_and_names_uniquely() -> None:
    state = default_state()

    first = figures.create_scene(state, "personal", "alice", NO_EDITORS)
    second = figures.create_scene(state, "personal", "alice", NO_EDITORS)

    assert first["name"] == figures.DEFAULT_SCENE_NAME
    assert second["name"] == f"{figures.DEFAULT_SCENE_NAME} 2"
    assert [scene["id"] for scene in figures.list_scenes(state, "personal", "alice")] == [
        first["id"],
        second["id"],
    ]
    ui = figures.get_ui(state, "alice")
    assert ui["view"] == "personal"
    assert ui["personal"]["selected_scene_id"] == second["id"]
    assert ui["personal"]["selected_figure_id"] is None


def test_scene_figures_get_non_overlapping_layouts() -> None:
    state = default_state()
    scene = figures.create_scene(state, "personal", "alice", NO_EDITORS)

    first = figures.create_scene_figure(
        state, "personal", "alice", NO_EDITORS, scene["id"], series_id=42
    )
    second = figures.create_scene_figure(
        state, "personal", "alice", NO_EDITORS, scene["id"], series_id=43
    )
    third = figures.create_scene_figure(
        state, "personal", "alice", NO_EDITORS, scene["id"], series_id=44
    )

    layouts = [figure["layout"] for figure in (first, second, third)]
    assert len({(item["x"], item["y"]) for item in layouts}) == 3
    for left in layouts:
        for right in layouts:
            if left is right:
                continue
            assert (
                left["x"] + left["w"] <= right["x"]
                or right["x"] + right["w"] <= left["x"]
                or left["y"] + left["h"] <= right["y"]
                or right["y"] + right["h"] <= left["y"]
            )


def test_update_scene_figure_layout_is_validated() -> None:
    state = default_state()
    scene = figures.create_scene(state, "personal", "alice", NO_EDITORS)
    figure = figures.create_scene_figure(
        state, "personal", "alice", NO_EDITORS, scene["id"]
    )

    updated = figures.update_scene_figure(
        state,
        "personal",
        "alice",
        NO_EDITORS,
        scene["id"],
        figure["id"],
        layout={"x": 3, "y": 4, "w": 4, "h": 3},
    )
    assert updated["layout"] == {"x": 3, "y": 4, "w": 4, "h": 3}

    with pytest.raises(figures.FigureError, match="exceed 12 columns"):
        figures.update_scene_figure(
            state,
            "personal",
            "alice",
            NO_EDITORS,
            scene["id"],
            figure["id"],
            layout={"x": 0, "y": 0, "w": 13, "h": 2},
        )
    with pytest.raises(figures.FigureError, match="extends past 12"):
        figures.update_scene_figure(
            state,
            "personal",
            "alice",
            NO_EDITORS,
            scene["id"],
            figure["id"],
            layout={"x": 10, "y": 0, "w": 6, "h": 2},
        )


def test_update_scene_grid_fits_existing_layouts() -> None:
    state = default_state()
    scene = figures.create_scene(state, "personal", "alice", NO_EDITORS)
    figure = figures.create_scene_figure(
        state, "personal", "alice", NO_EDITORS, scene["id"]
    )
    figures.update_scene_figure(
        state,
        "personal",
        "alice",
        NO_EDITORS,
        scene["id"],
        figure["id"],
        layout={"x": 6, "y": 0, "w": 6, "h": 2},
    )

    updated = figures.update_scene(
        state,
        "personal",
        "alice",
        NO_EDITORS,
        scene["id"],
        grid={"cols": 6, "row_height": 180},
    )

    assert updated["grid"] == {"cols": 6, "row_height": 180}
    assert updated["figures"][0]["layout"] == {"x": 0, "y": 0, "w": 6, "h": 2}


def test_delete_scene_clears_selections() -> None:
    state = default_state()
    scene = figures.create_scene(state, "personal", "alice", NO_EDITORS)
    figure = figures.create_scene_figure(
        state, "personal", "alice", NO_EDITORS, scene["id"]
    )
    assert figures.get_ui(state, "alice")["personal"] == {
        "selected_scene_id": scene["id"],
        "selected_figure_id": figure["id"],
        "folders_collapsed": False,
        "options_collapsed": False,
    }

    figures.delete_scene(state, "personal", "alice", NO_EDITORS, scene["id"])

    assert figures.list_scenes(state, "personal", "alice") == []
    assert figures.get_ui(state, "alice")["personal"] == {
        "selected_scene_id": None,
        "selected_figure_id": None,
        "folders_collapsed": False,
        "options_collapsed": False,
    }


def test_shared_scene_permissions_respect_allowlist() -> None:
    state = default_state()
    editors = frozenset({"alice"})

    with pytest.raises(figures.FigurePermissionError):
        figures.create_scene(state, "shared", "bob", editors)

    scene = figures.create_scene(state, "shared", "alice", editors)

    with pytest.raises(figures.FigurePermissionError):
        figures.update_scene(
            state, "shared", "bob", editors, scene["id"], name="nope"
        )
    with pytest.raises(figures.FigurePermissionError):
        figures.delete_scene(state, "shared", "bob", editors, scene["id"])
    with pytest.raises(figures.FigurePermissionError):
        figures.create_scene_figure(
            state, "shared", "bob", editors, scene["id"]
        )

    assert figures.get_scene(state, "shared", "bob", scene["id"]) is not None
