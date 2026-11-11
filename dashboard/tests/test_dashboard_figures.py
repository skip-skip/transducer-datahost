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
